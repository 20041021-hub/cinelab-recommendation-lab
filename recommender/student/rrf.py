"""S1 加权 RRF（Reciprocal Rank Fusion）融合模块。

设计要点（对应实验四 S1 的最低要求）：

* 只融合"名次"，不混加不同量纲的原始分数。每种算法的 score 来自不同模型
  （TF-IDF 余弦、共现余弦、Spark ALS 内积、支持度占比），直接加权求和没有
  可比性；RRF 用 ``weight / (K + rank)`` 把名次映射成同一量纲的贡献值。
* 去重：同一部电影被多路召回时只保留一条，各来源名次记录在 recall_ranks。
* 过滤：已评分、已收藏的电影不再推荐；不在冻结目录内的 movie_id 直接丢弃。
* 排序：按融合分数降序，同分按 movie_id 数值升序（与冻结契约一致）。
* 可追溯：输出每条的 recall_sources（来源算法）与 recall_ranks（各来源名次），
  以及每个来源的候选数/入榜数/权重/模型版本统计。

本模块不注册进 PROVIDERS：冻结契约的 Algorithm 枚举只有五种算法，融合是
实验四/五的扩展能力，通过 ``/api/v1/experiments/fusion`` 与
``/api/v1/me/recommendations/fusion`` 独立暴露。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ..contracts import Algorithm, RecommendationResult

FUSION_LABEL = "fusion"
MODEL_VERSION = "fusion-rrf-v1"
METHOD = "weighted_rrf"
RRF_K = 60
MIN_SOURCES = 2
MAX_WEIGHT = 10.0
# 默认来源与权重来自验证集权重对照实验（reports/lab4/fusion-tuning.json）：
# 在 4,209 位固定评价用户上，多路配置里 itemcf×6 + als×1 的 P@10 最高（0.055785），
# 因此作为实验四五的默认融合配置；调用方仍可用 algorithms/weights 查询参数覆盖。
DEFAULT_SOURCES: tuple[Algorithm, ...] = (Algorithm.itemcf, Algorithm.als)
DEFAULT_WEIGHTS: tuple[float, ...] = (6.0, 1.0)
SOURCE_ORDER: tuple[Algorithm, ...] = tuple(Algorithm)


class FusionError(Exception):
    """融合参数或来源不可用时抛出；由 API 层翻译成契约错误信封。"""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def parse_sources(value: str | None) -> tuple[Algorithm, ...]:
    """把 "content,itemcf,als" 解析成去重后的算法名次序列。"""
    raw = (value or "").strip()
    if not raw:
        return DEFAULT_SOURCES
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            raise FusionError("FUSION_SOURCES_INVALID", "算法列表含空白项，请使用英文逗号分隔")
        if name not in names:
            names.append(name)
    if len(names) < MIN_SOURCES:
        raise FusionError("FUSION_SOURCES_INSUFFICIENT",
                          f"加权 RRF 至少需要 {MIN_SOURCES} 路真实候选，当前只有 {len(names)} 路")
    if len(names) > len(SOURCE_ORDER):
        raise FusionError("FUSION_SOURCES_INVALID", "算法列表超出系统支持的算法范围")
    try:
        return tuple(Algorithm(name) for name in names)
    except ValueError:
        raise FusionError("FUSION_SOURCE_UNKNOWN",
                          "未知算法：只能使用 popular、content、itemcf、als、cold_start") from None


def parse_weights(value: str | None, count: int) -> tuple[float, ...]:
    """解析权重；缺省时用验证集选定的一组默认权重（长度不符则退回全 1.0）。"""
    raw = (value or "").strip()
    if not raw:
        if count == len(DEFAULT_WEIGHTS):
            return DEFAULT_WEIGHTS
        return tuple(1.0 for _ in range(count))
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != count:
        raise FusionError("FUSION_WEIGHTS_INVALID",
                          f"权重个数（{len(parts)}）必须与算法个数（{count}）一致")
    weights: list[float] = []
    for part in parts:
        try:
            weight = float(part)
        except ValueError:
            raise FusionError("FUSION_WEIGHTS_INVALID", "权重必须是数字，例如 weights=2,1") from None
        if not (0.0 < weight <= MAX_WEIGHT):
            raise FusionError("FUSION_WEIGHTS_INVALID", f"权重必须大于 0 且不超过 {MAX_WEIGHT:g}")
        weights.append(round(weight, 4))
    return tuple(weights)


def source_request_limit(limit: int) -> int:
    """每路候选的召回深度：比最终 Top-N 更深，融合才有素材，同时不超过契约上限 50。"""
    return max(20, min(50, limit * 3))


@dataclass(frozen=True)
class FusedItem:
    movie_id: str
    rank: int
    score: float
    sources: tuple[str, ...]
    ranks: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceStat:
    algorithm: str
    weight: float
    model_version: str
    candidate_count: int
    contributed: int
    best_rank: int | None


@dataclass(frozen=True)
class FusionOutcome:
    items: tuple[FusedItem, ...]
    stats: tuple[SourceStat, ...]
    pool_size: int
    filtered_seen: int
    filtered_out_of_catalog: int
    delivered: int


def _item_rank(item: object, index: int) -> int:
    rank = getattr(item, "rank", None)
    if isinstance(rank, int) and rank >= 1:
        return rank
    return index + 1


def fuse(results: Mapping[str, RecommendationResult], weights: Mapping[str, float], limit: int,
         excluded: Iterable[str], catalog: set[str]) -> FusionOutcome:
    """融合多路真实候选。

    results: 算法名 -> 该算法的 RecommendationResult（由既有 provider 真实产出）
    weights: 算法名 -> 权重
    excluded: 需要过滤掉的 movie_id（已评分/已收藏）
    catalog: 冻结目录，目录外的 id 一律丢弃
    """
    seen_filter = {str(movie) for movie in excluded}
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    candidate_count: dict[str, int] = {}
    best_rank: dict[str, int | None] = {}
    filtered_seen = 0
    filtered_out = 0

    for name in (algorithm.value for algorithm in SOURCE_ORDER):
        result = results.get(name)
        if result is None:
            continue
        candidate_count[name] = 0
        best_rank[name] = None
        weight = float(weights.get(name, 1.0))
        for index, item in enumerate(result.items):
            movie_id = str(item.movie_id)
            if movie_id in seen_filter:
                filtered_seen += 1
                continue
            if movie_id not in catalog:
                filtered_out += 1
                continue
            candidate_count[name] += 1
            rank = _item_rank(item, index)
            best_rank[name] = rank if best_rank[name] is None else min(best_rank[name], rank)
            scores[movie_id] = scores.get(movie_id, 0.0) + weight / (RRF_K + rank)
            ranks.setdefault(movie_id, {})[name] = rank

    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], int(pair[0])))[:limit]
    items: list[FusedItem] = []
    contributed: dict[str, int] = {name: 0 for name in candidate_count}
    for position, (movie_id, score) in enumerate(ordered, start=1):
        by_source = ranks[movie_id]
        sources = tuple(name for name in (algorithm.value for algorithm in SOURCE_ORDER) if name in by_source)
        for name in sources:
            contributed[name] += 1
        items.append(FusedItem(movie_id, position, round(score, 6), sources, dict(by_source)))

    stats = tuple(
        SourceStat(name, round(float(weights.get(name, 1.0)), 4), str(results[name].model_version),
                   candidate_count[name], contributed.get(name, 0), best_rank.get(name))
        for name in (algorithm.value for algorithm in SOURCE_ORDER) if name in results
    )
    return FusionOutcome(tuple(items), stats, len(scores), filtered_seen, filtered_out, len(items))


def exclusion_ids(history: object) -> set[str]:
    """从 UserHistory/EvaluationHistory 取出需要过滤的 movie_id（已评分 + 已收藏）。"""
    excluded = {str(feedback.movie_id) for feedback in getattr(history, "ratings", ())}
    excluded.update(str(movie_id) for movie_id in getattr(history, "favorite_movie_ids", ()))
    return excluded


def describe_sources(sources: Sequence[Algorithm]) -> list[dict[str, object]]:
    """给前端展示的来源清单（不含候选，只报可用性）。"""
    from ..contracts import PROVIDERS

    return [{"algorithm": algorithm.value, "registered": algorithm in PROVIDERS,
             "default_weight": 1.0} for algorithm in sources]
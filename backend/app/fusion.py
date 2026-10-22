"""实验四 S1：加权 RRF 融合的对外接口。

路由（均在 /api/v1 前缀下，由 main.py 在 experiments 路由之前注册，
避免 ``/me/recommendations/{algorithm}`` 抢先匹配 "fusion"）：

* ``GET /experiments/fusion/sources``：可用来源与融合参数说明。
* ``GET /me/recommendations/fusion``：当前登录账号的融合推荐。
* ``GET /demo/history-users/{ml_user_id}/recommendations/fusion``：历史用户融合推荐。

融合结果不是冻结契约里的五种算法之一，因此不复用 ``RecommendationResult``，
但字段命名与它保持一致（movie_id/rank/algorithm/model_version/score/
recall_sources/recall_ranks），方便前端复用同一套卡片组件。
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from recommender.business import read_history
from recommender.contracts import AlgorithmNotImplemented, PROVIDERS, RecommendationRequest, recommend
from recommender.data import ContractError, FrozenData
from recommender.student import rrf
from . import interest
from .config import DATASET, SPLIT
from .errors import APIError
from .schemas import IdPath, identifier
from .security import ActorDependency

router = APIRouter(tags=["实验四五扩展：加权RRF融合"])

BINDING_CODES = {"MODEL_BINDING_INVALID", "DATA_IDENTITY_INVALID", "DATA_MODE_INVALID"}


class FusionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    algorithms: str | None = Field(default=None, max_length=120)
    weights: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=10, ge=1, le=50)
    # 实验五 S3：1=按短期兴趣重排（默认），0=纯 RRF（对照实验用，便于证明"行为真的改变了推荐"）
    interest: int = Field(default=1, ge=0, le=1)
    # 实验六创新点 A：1=用短期兴趣自适应调整**各路权重**，0=固定权重（默认 0，保持实验四/五口径不变）
    adaptive: int = Field(default=0, ge=0, le=1)


class FusionItem(BaseModel):
    movie_id: str
    rank: int
    algorithm: str = rrf.FUSION_LABEL
    model_version: str = rrf.MODEL_VERSION
    score: float
    recall_sources: list[str]
    recall_ranks: dict[str, int]
    interest_match: float | None = None
    interest_boost: float | None = None


class InterestGenre(BaseModel):
    genre: str
    weight: float


class InterestBlock(BaseModel):
    """融合结果里对"为什么这样排"的交代：状态来自哪个 topic/账号、衰减后最强的类型、是否过期。"""

    requested: bool
    enabled: bool
    reason: str | None = None
    source: str = "kafka:cinelab.behavior.business"
    alpha: float | None = None
    state_version: int | None = None
    updated_at: str | None = None
    age_seconds: float | None = None
    stale: bool = False
    half_life_minutes: float | None = None
    events: int | None = None
    top_genres: list[InterestGenre] = []
    rule: str = "score' = score × (1 + alpha × match)，match 为该片类型兴趣权重之和 / 最强类型权重"


class FusionSourceStat(BaseModel):
    algorithm: str
    weight: float
    model_version: str
    candidate_count: int
    contributed: int
    best_rank: int | None


class AdaptiveSourceStat(BaseModel):
    """创新点 A 的单路证据：这一路的候选与短期兴趣多亲近、权重被放大/缩小了多少。"""

    algorithm: str
    base_weight: float
    affinity: float
    adaptive_weight: float
    candidates: int


class AdaptiveBlock(BaseModel):
    """创新点 A：短期兴趣自适应融合权重。

    与 ``interest``（重排分数）不同，这里改的是 RRF 的**权重**——按每一路候选整体
    与短期兴趣的亲近度 w' = w × (1 + beta × affinity) 重新分配话语权。
    """

    requested: bool
    enabled: bool
    reason: str | None = None
    beta: float | None = None
    state_version: int | None = None
    stale: bool = False
    rule: str = "w' = w × (1 + beta × affinity)，affinity = 该路候选中与短期兴趣匹配度的均值"
    sources: list[AdaptiveSourceStat] = []


class FusionResult(BaseModel):
    status: str = "ready"
    algorithm: str = rrf.FUSION_LABEL
    model_version: str = rrf.MODEL_VERSION
    method: str = rrf.METHOD
    rrf_k: int = rrf.RRF_K
    dataset_version: str = DATASET
    split_version: str = SPLIT
    runtime_mode: str = "business"
    subject_key: str
    batch_id: str | None = None
    generated_at: datetime
    requested_limit: int
    candidate_depth: int
    pool_size: int
    delivered: int
    filtered_seen: int
    filtered_out_of_catalog: int
    weights: dict[str, float]
    sources: list[FusionSourceStat]
    interest: InterestBlock
    adaptive: AdaptiveBlock
    items: list[FusionItem]


def _catalog() -> set[str]:
    return FrozenData().catalog()


def _request(kind: str, uid: int, limit: int) -> RecommendationRequest:
    return RecommendationRequest(kind, uid, limit, "business", DATASET, SPLIT)


def fusion_result(kind: str, uid: int, query: FusionQuery, database=None) -> FusionResult:
    try:
        sources = rrf.parse_sources(query.algorithms)
        weights = rrf.parse_weights(query.weights, len(sources))
    except rrf.FusionError as exc:
        raise APIError(exc.status, exc.code, exc.message) from None

    weight_map = {algorithm.value: weight for algorithm, weight in zip(sources, weights)}
    depth = rrf.source_request_limit(query.limit)

    # 每一路都必须来自真实 provider；任何一路未实现或绑定失败都明确报错，不静默降级。
    results = {}
    for algorithm in sources:
        try:
            results[algorithm.value] = recommend(algorithm, _request(kind, uid, depth))
        except AlgorithmNotImplemented:
            raise APIError(503, "FUSION_SOURCE_UNAVAILABLE", f"来源算法 {algorithm.value} 尚未实现", {
                "algorithm": algorithm.value, "subject_key": f"{kind}:{uid}", "reason": "not_implemented"}) from None
        except ContractError as exc:
            raise APIError(503 if exc.code in BINDING_CODES else 500, exc.code, str(exc),
                           {"algorithm": algorithm.value}) from None

    history = read_history(_request(kind, uid, query.limit), database=database)

    # 实验五 S3：真实行为事件推出的短期兴趣，按固定规则重排（无状态/过期时不加分并说明原因）
    subject_key = f"{kind}:{uid}"
    state = interest.snapshot(subject_key) if query.interest else None

    # 实验六创新点 A：用同一份短期兴趣状态调整**各路权重**（w' = w × (1 + beta × affinity)）。
    # 只有"真的拿到了未过期的状态"才会改权重，否则保持固定权重并说明原因。
    if not query.adaptive:
        adaptive_reason = "未启用（adaptive=0）"
    elif state is None:
        adaptive_reason = "需要 interest=1 且该账号的短期兴趣状态可用"
    elif state.get("stale"):
        adaptive_reason = (
            f"短期兴趣状态已过期：{float(state.get('age_seconds') or 0):.0f} 秒没有新事件"
            f"（上限 {state.get('max_age_seconds')} 秒），这次仍用固定权重")
    else:
        adaptive_reason = None
    adaptive_block = AdaptiveBlock(
        requested=bool(query.adaptive),
        enabled=False,
        reason=adaptive_reason,
        beta=interest.adaptive_beta() if query.adaptive else None,
        state_version=state.get("state_version") if state else None,
        stale=bool(state.get("stale")) if state else False,
    )
    effective_weights = dict(weight_map)
    if query.adaptive and state is not None and not adaptive_block.stale:
        candidate_genres = interest.genres_for(
            database, [item.movie_id for result in results.values() for item in result.items])
        affinities, stats = {}, []
        for algorithm in sources:
            name = algorithm.value
            candidate_ids = [item.movie_id for item in results[name].items]
            # 每一路只看**这一路自己召回的电影**与短期兴趣有多亲近
            genres_of_source = {movie_id: candidate_genres.get(movie_id, "")
                                for movie_id in candidate_ids if movie_id in candidate_genres}
            attraction = interest.source_affinity(genres_of_source, state)
            affinities[name] = attraction
            stats.append(AdaptiveSourceStat(algorithm=name, base_weight=weight_map[name],
                                            affinity=round(attraction, 6), adaptive_weight=weight_map[name],
                                            candidates=len(candidate_ids)))
        effective_weights = interest.adaptive_weights(weight_map, affinities)
        for stat in stats:
            stat.adaptive_weight = effective_weights[stat.algorithm]
        adaptive_block.enabled = True
        adaptive_block.reason = None
        adaptive_block.sources = stats

    outcome = rrf.fuse(results, effective_weights, query.limit, rrf.exclusion_ids(history), _catalog())

    block = InterestBlock(
        requested=bool(query.interest),
        enabled=bool(state),
        reason=None if state and not state.get("stale") else (
            "未启用（interest=0）" if not query.interest else
            (f"短期兴趣状态已过期（{float(state.get('age_seconds') or 0):.0f} 秒前更新，上限 {state.get('max_age_seconds')} 秒）"
             if state else
             ("短期兴趣状态未就绪（消费端尚未写出该账号的兴趣）" if interest.wanted()
              else "短期兴趣功能未启用（CINELAB_INTEREST_ENABLED=0）"))),
        alpha=interest.alpha() if state else None,
        state_version=state.get("state_version") if state else None,
        updated_at=state.get("updated_at") if state else None,
        age_seconds=state.get("age_seconds") if state else None,
        stale=bool(state.get("stale")) if state else False,
        half_life_minutes=state.get("half_life_minutes") if state else None,
        events=state.get("events") if state else None,
        top_genres=[InterestGenre(**row) for row in state.get("top_genres", [])] if state else [],
    )
    if state:
        genres = interest.genres_for(database, [item.movie_id for item in outcome.items])
        rows = interest.rerank(outcome.items, genres, state)
        items = [FusionItem(movie_id=row["item"].movie_id, rank=row["rank"], score=row["score"],
                            recall_sources=list(row["item"].sources), recall_ranks=row["item"].ranks,
                            interest_match=row["interest_match"], interest_boost=row["interest_boost"])
                 for row in rows]
    else:
        items = [FusionItem(movie_id=item.movie_id, rank=item.rank, score=item.score,
                            recall_sources=list(item.sources), recall_ranks=item.ranks)
                 for item in outcome.items]

    return FusionResult(
        subject_key=subject_key,
        generated_at=datetime.now(timezone.utc),
        requested_limit=query.limit,
        candidate_depth=depth,
        pool_size=outcome.pool_size,
        delivered=outcome.delivered,
        filtered_seen=outcome.filtered_seen,
        filtered_out_of_catalog=outcome.filtered_out_of_catalog,
        weights=effective_weights,
        sources=[FusionSourceStat(algorithm=stat.algorithm, weight=effective_weights.get(stat.algorithm, stat.weight),
                                  model_version=stat.model_version, candidate_count=stat.candidate_count,
                                  contributed=stat.contributed, best_rank=stat.best_rank)
                 for stat in outcome.stats],
        interest=block,
        adaptive=adaptive_block,
        items=items,
    )


@router.get("/experiments/fusion/sources")
def fusion_sources():
    return {"method": rrf.METHOD, "model_version": rrf.MODEL_VERSION, "rrf_k": rrf.RRF_K,
            "min_sources": rrf.MIN_SOURCES, "max_weight": rrf.MAX_WEIGHT,
            "default_sources": [algorithm.value for algorithm in rrf.DEFAULT_SOURCES],
            "default_weights": list(rrf.DEFAULT_WEIGHTS),
            "candidate_depth_rule": "max(20, min(50, limit*3))",
            "interest_rerank": {"query_param": "interest=1|0", "alpha": interest.alpha(),
                                "rule": "score' = score × (1 + alpha × match)",
                                "state_source": "Kafka 事件 → 消费端 → reports/lab5/state/interest.json"},
            "adaptive_weights": {"query_param": "adaptive=1|0", "beta": interest.adaptive_beta(),
                                 "rule": "w' = w × (1 + beta × affinity)，affinity = 该路候选匹配度的均值",
                                 "state_source": "与 interest_rerank 共用同一份短期兴趣状态"},
            "items": rrf.describe_sources(rrf.SOURCE_ORDER)}


@router.get("/me/recommendations/fusion", response_model=FusionResult)
def my_fusion(request: Request, actor: ActorDependency, query: Annotated[FusionQuery, Query()]):
    return fusion_result("app", actor.account_id, query, database=request.app.state.db)


@router.get("/demo/history-users/{ml_user_id}/recommendations/fusion", response_model=FusionResult)
def history_fusion(ml_user_id: IdPath, request: Request, query: Annotated[FusionQuery, Query()]):
    uid = identifier(ml_user_id)
    with request.app.state.db.connection() as conn:
        if conn.execute(text("SELECT ml_user_id FROM history_users WHERE ml_user_id=:id"), {"id": uid}).first() is None:
            raise APIError(404, "HISTORY_USER_NOT_FOUND", "历史匿名用户不存在")
    return fusion_result("ml", uid, query, database=request.app.state.db)
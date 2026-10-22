"""实验五 S3：短期兴趣状态读取 + 融合重排规则（API 侧）。

状态文件由 `scripts/run_behavior_consumer.py` 从 Kafka 事件推导出来（真实消费，不是 Mock）：

    reports/lab5/state/events.jsonl   逐条事件证据（topic/partition/offset/event_id/occurred_at）
    reports/lab5/state/interest.json  按账号聚合的短期兴趣（类型加权 + 时间衰减 + 已消费位移）

重排规则只有一条，写在这里，别处不再假设：

    RRF 分 → 兴趣因子：score' = score × (1 + alpha × match)
    match = clamp(该电影类型在账号兴趣里的（已衰减）权重之和 / 该账号最强类型的权重, 0, 1)

* 权重来自真实行为：评分 ≥4.0 加 (rating-3.0)/2、≤2.0 减 (3.0-rating)/2、收藏 +1.5、取消收藏 -1.5；
  评分 2.5/3.0/3.5 记为中性（只留事件证据，不改权重）。
* 衰减：weight_t = weight × 0.5 ** (Δt / half_life)，half_life 默认 30 分钟（消费者写进状态文件）。
* 状态过期（超过 ``CINELAB_INTEREST_MAX_AGE``，默认 900 秒）或没有该账号的兴趣时**不加分**，
  接口返回 ``interest.enabled=false`` 与原因——绝不伪造个性化。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_STATE = "/app/lab5-state/interest.json"
DEFAULT_EVENTS = "/app/lab5-state/events.jsonl"
TOP_GENRES = 5

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name) or default).strip().lower() in _TRUTHY


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def state_path() -> Path:
    return Path(os.environ.get("CINELAB_INTEREST_STATE") or DEFAULT_STATE)


def events_path() -> Path:
    return Path(os.environ.get("CINELAB_INTEREST_EVENTS") or DEFAULT_EVENTS)


def wanted() -> bool:
    return _flag("CINELAB_INTEREST_ENABLED")


def alpha() -> float:
    return _number("CINELAB_INTEREST_ALPHA", 0.5)


def adaptive_beta() -> float:
    """创新点 A：短期兴趣对融合**权重**的影响强度（与 alpha 分开，便于独立调参）。"""
    return _number("CINELAB_ADAPTIVE_BETA", 0.5)


def max_age_seconds() -> float:
    return _number("CINELAB_INTEREST_MAX_AGE", 900.0)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decay(seconds: float, half_life_minutes: float) -> float:
    if half_life_minutes <= 0 or seconds <= 0:
        return 1.0
    return 0.5 ** (seconds / (half_life_minutes * 60.0))


# ---- 状态文件读取（按 mtime 缓存，避免每个请求都读盘） -------------------------

_cache: dict[str, Any] = {"key": None, "state": None}


def load_state() -> dict[str, Any] | None:
    path = state_path()
    try:
        stat = path.stat()
    except OSError:
        _cache.update(key=None, state=None)
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache["key"] != key:
        try:
            _cache.update(key=key, state=json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _cache.update(key=None, state=None)
            return None
    return _cache["state"]


def snapshot(subject_key: str) -> dict[str, Any] | None:
    """该账号当前的（已衰减）短期兴趣；没有状态或未启用时返回 None。"""
    if not wanted():
        return None
    state = load_state()
    if not state:
        return None
    subject = (state.get("subjects") or {}).get(subject_key)
    if not subject:
        return None
    now = datetime.now(timezone.utc)
    updated = _parse(subject.get("updated_at")) or _parse(state.get("generated_at"))
    age = max(0.0, (now - updated).total_seconds()) if updated else None
    half_life = float(state.get("half_life_minutes") or 30.0)
    factor = _decay(age or 0.0, half_life)
    weights = {genre: round(float(weight) * factor, 6) for genre, weight in (subject.get("genres") or {}).items()}
    ranked = sorted(((g, w) for g, w in weights.items() if w > 0), key=lambda pair: (-pair[1], pair[0]))
    return {
        "subject_key": subject_key,
        "genres": weights,
        "top_genres": [{"genre": genre, "weight": weight} for genre, weight in ranked[:TOP_GENRES]],
        "positive_movies": list(subject.get("positive_movies") or []),
        "negative_movies": list(subject.get("negative_movies") or []),
        "counts": dict(subject.get("counts") or {}),
        "events": int(subject.get("events") or 0),
        "last_event_at": subject.get("last_event_at"),
        "updated_at": subject.get("updated_at"),
        "age_seconds": round(age, 1) if age is not None else None,
        "stale": bool(age is not None and age > max_age_seconds()),
        "max_age_seconds": max_age_seconds(),
        "half_life_minutes": half_life,
        "state_version": int(state.get("state_version") or 0),
        "topic": state.get("topic"),
        "group": state.get("group"),
    }


def recent_events(subject_key: str, limit: int = 10) -> list[dict[str, Any]]:
    """从事件证据文件里取该账号最近的事件（事件 → 状态的追溯链）。"""
    if limit <= 0:
        return []
    path = events_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("subject_key") != subject_key:
            continue
        found.append(record)
        if len(found) >= limit:
            break
    return list(reversed(found))


def status() -> dict[str, Any]:
    state = load_state()
    path = state_path()
    return {
        "enabled": wanted(),
        "alpha": alpha(),
        "max_age_seconds": max_age_seconds(),
        "state_path": str(path),
        "events_path": str(events_path()),
        "state_available": state is not None,
        "state_version": int(state.get("state_version") or 0) if state else None,
        "generated_at": state.get("generated_at") if state else None,
        "subjects": sorted((state.get("subjects") or {})) if state else [],
        "offsets": dict(state.get("offsets") or {}) if state else {},
        "half_life_minutes": state.get("half_life_minutes") if state else None,
        "topic": state.get("topic") if state else None,
        "group": state.get("group") if state else None,
        "events_applied": int(state.get("events_applied") or 0) if state else 0,
    }


# ---- 融合重排 ---------------------------------------------------------------

def genres_for(database, movie_ids: Iterable[str]) -> dict[str, str]:
    """从业务库取电影类型（融合候选只有 movie_id，重排需要 genres）。"""
    ids = sorted({int(mid) for mid in movie_ids})
    if not ids:
        return {}
    from sqlalchemy import bindparam, text

    statement = text("SELECT movie_id, genres FROM movies WHERE movie_id IN :ids").bindparams(
        bindparam("ids", expanding=True))
    with database.connection() as conn:
        rows = conn.execute(statement, {"ids": ids}).all()
    return {str(movie_id): genres for movie_id, genres in rows}


def movie_genres(value: str | None) -> list[str]:
    return [genre for genre in str(value or "").split("|") if genre and genre != "(no genres listed)"]


def rerank(items: list[Any], genres: dict[str, str], state: dict[str, Any]) -> list[dict[str, Any]]:
    """按兴趣因子重排融合结果，返回带证据字段的字典列表（rank 重编 1..n）。"""
    weights = {genre: float(weight) for genre, weight in (state.get("genres") or {}).items()}
    strongest = max([weight for weight in weights.values() if weight > 0] or [0.0])
    factor = alpha()
    scored: list[dict[str, Any]] = []
    for item in items:
        matched = sum(weights.get(genre, 0.0) for genre in movie_genres(genres.get(item.movie_id)))
        match = min(1.0, max(0.0, matched / strongest)) if strongest > 0 else 0.0
        boost = 1.0 if state.get("stale") else 1.0 + factor * match
        scored.append({
            "item": item,
            "score": round(float(item.score) * boost, 6),
            "interest_match": round(match, 6),
            "interest_boost": round(boost, 6),
        })
    scored.sort(key=lambda row: (-row["score"], int(row["item"].movie_id)))
    for rank, row in enumerate(scored, start=1):
        row["rank"] = rank
    return scored


def match_of(movie_id: str, genres: dict[str, str], state: dict[str, Any]) -> float:
    """单部电影与账号短期兴趣的匹配度（0..1，规则与 rerank 完全一致）。"""
    weights = {genre: float(weight) for genre, weight in (state.get("genres") or {}).items()}
    strongest = max([weight for weight in weights.values() if weight > 0] or [0.0])
    if strongest <= 0:
        return 0.0
    matched = sum(weights.get(genre, 0.0) for genre in movie_genres(genres.get(str(movie_id))))
    return min(1.0, max(0.0, matched / strongest))


def source_affinity(candidate_genres: dict[str, str], state: dict[str, Any]) -> float:
    """创新点 A：某一路候选整体与该账号短期兴趣的亲近度（候选匹配度的均值）。

    用"这一路召回出来的电影有多少命中短期兴趣"来决定它在融合里的权重，
    而不是只看最终排序：兴趣在 Comdey 上时，召回一堆 Comedy 的那一路应该更被信任。
    """
    matches = [match_of(movie_id, candidate_genres, state) for movie_id in candidate_genres]
    if not matches:
        return 0.0
    return sum(matches) / len(matches)


def adaptive_weights(base_weights: dict[str, float], affinities: dict[str, float], beta: float | None = None
                     ) -> dict[str, float]:
    """创新点 A 的权重规则：w' = w × (1 + beta × affinity)，结果保留 6 位小数。

    affinity 来自**真实消费端状态**（Kafka 事件推导），不是离线标签；没有状态时
    调用方不应使用本函数（接口会返回 adaptive.enabled=false 并说明原因）。
    """
    factor = adaptive_beta() if beta is None else beta
    return {name: round(weight * (1.0 + factor * affinities.get(name, 0.0)), 6)
            for name, weight in base_weights.items()}
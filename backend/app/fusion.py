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


class FusionItem(BaseModel):
    movie_id: str
    rank: int
    algorithm: str = rrf.FUSION_LABEL
    model_version: str = rrf.MODEL_VERSION
    score: float
    recall_sources: list[str]
    recall_ranks: dict[str, int]


class FusionSourceStat(BaseModel):
    algorithm: str
    weight: float
    model_version: str
    candidate_count: int
    contributed: int
    best_rank: int | None


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
    outcome = rrf.fuse(results, weight_map, query.limit, rrf.exclusion_ids(history), _catalog())

    return FusionResult(
        subject_key=f"{kind}:{uid}",
        generated_at=datetime.now(timezone.utc),
        requested_limit=query.limit,
        candidate_depth=depth,
        pool_size=outcome.pool_size,
        delivered=outcome.delivered,
        filtered_seen=outcome.filtered_seen,
        filtered_out_of_catalog=outcome.filtered_out_of_catalog,
        weights=weight_map,
        sources=[FusionSourceStat(**stat.__dict__) for stat in outcome.stats],
        items=[FusionItem(movie_id=item.movie_id, rank=item.rank, score=item.score,
                          recall_sources=list(item.sources), recall_ranks=item.ranks)
               for item in outcome.items],
    )


@router.get("/experiments/fusion/sources")
def fusion_sources():
    return {"method": rrf.METHOD, "model_version": rrf.MODEL_VERSION, "rrf_k": rrf.RRF_K,
            "min_sources": rrf.MIN_SOURCES, "max_weight": rrf.MAX_WEIGHT,
            "default_sources": [algorithm.value for algorithm in rrf.DEFAULT_SOURCES],
            "default_weights": list(rrf.DEFAULT_WEIGHTS),
            "candidate_depth_rule": "max(20, min(50, limit*3))",
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
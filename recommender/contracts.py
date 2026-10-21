from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Algorithm(StrEnum):
    popular = "popular"
    content = "content"
    itemcf = "itemcf"
    als = "als"
    cold_start = "cold_start"


@dataclass(frozen=True)
class RecommendationRequest:
    subject_kind: Literal["app", "ml"]
    subject_id: int
    limit: int
    runtime_mode: Literal["business", "evaluation"]
    dataset_version: str
    split_version: str


class RecommendationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movie_id: str
    rank: int = Field(ge=1)
    algorithm: Algorithm
    model_version: str = Field(min_length=1)
    score: float | None = Field(default=None, allow_inf_nan=False)
    recall_sources: list[str] | None = None
    recall_ranks: dict[str, int] | None = None


class RecommendationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready"] = "ready"
    algorithm: Algorithm
    model_version: str = Field(min_length=1)
    dataset_version: str
    split_version: str
    runtime_mode: Literal["business", "evaluation"]
    batch_id: str | None = None
    generated_at: datetime
    items: list[RecommendationItem]


class Recommender(Protocol):
    model_manifest_path: str | Path
    def recommend(self, request: RecommendationRequest) -> RecommendationResult: ...


class AlgorithmNotImplemented(Exception):
    pass


# Students implement one provider per task and explicitly register it here.
# An empty registry deliberately produces no movie list or model result.
PROVIDERS: dict[Algorithm, Recommender] = {}


def recommend(algorithm: Algorithm, request: RecommendationRequest) -> RecommendationResult:
    provider = PROVIDERS.get(algorithm)
    if provider is None:
        raise AlgorithmNotImplemented(algorithm.value)
    from .data import FrozenData, require
    from .validation import check_model, check_provider, check_request, check_result
    check_request(request)
    check_provider(provider)
    model_path = getattr(provider, "model_manifest_path", None)
    require(isinstance(model_path, (str, Path)),
            "已注册Provider须指定model_manifest_path", "MODEL_BINDING_INVALID")
    data = FrozenData()
    model = check_model(model_path, request.runtime_mode, algorithm.value, data)
    return check_result(provider.recommend(request), request, algorithm, data.catalog(), model.model_version)


# Lab 1/2 providers. Registration is explicit; ALS stays unimplemented until
# its own lab is submitted.
from .student.cold_start import ColdStartProvider  # noqa: E402
from .student.content import ContentProvider  # noqa: E402
from .student.itemcf import ItemCFProvider  # noqa: E402
from .student.popular import PopularProvider  # noqa: E402

MODEL_ROOT = Path(__file__).resolve().parents[1] / "models" / "business"

PROVIDERS[Algorithm.content] = ContentProvider(MODEL_ROOT / "content-v1" / "manifest.json")
PROVIDERS[Algorithm.popular] = PopularProvider(MODEL_ROOT / "popular-v1" / "manifest.json")
PROVIDERS[Algorithm.itemcf] = ItemCFProvider(MODEL_ROOT / "itemcf-v1" / "manifest.json")
PROVIDERS[Algorithm.cold_start] = ColdStartProvider(MODEL_ROOT / "cold-start-v1" / "manifest.json")

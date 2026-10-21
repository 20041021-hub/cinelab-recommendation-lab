"""DTO and model-sidecar checks; no ranking, training or metric calculation."""
from datetime import datetime
from pathlib import Path
from typing import Literal
import inspect
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .data import DATASET, SPLIT, SCHEMA, MANIFEST_SHA, FrozenData, ContractError, require, sha256


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    algorithm: Literal["popular", "content", "itemcf", "als", "cold_start"]
    model_version: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    runtime_mode: Literal["business", "evaluation"]
    dataset_version: Literal[DATASET]
    split_version: Literal[SPLIT]
    schema_version: Literal[SCHEMA]
    manifest_sha256: Literal[MANIFEST_SHA]
    training_files: dict[str, str]
    feature_files: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(min_length=1)
    parameters: dict = Field(default_factory=dict)
    tool_versions: dict[str, str] = Field(min_length=1)


def check_model(path: Path, mode: str, algorithm: str, data: FrozenData | None = None) -> ModelManifest:
    data = data or FrozenData()
    try:
        model = ModelManifest.model_validate_json(Path(path).read_text())
    except (OSError, ValidationError, ValueError):
        raise ContractError("MODEL_BINDING_INVALID", "模型清单缺失或格式/版本非法") from None
    require(model.runtime_mode == mode and model.algorithm == algorithm, "模型模式或算法不符", "MODEL_BINDING_INVALID")
    training = "evaluation/train.csv" if mode == "evaluation" else "business/history_ratings.csv"
    require(model.training_files == {training: data.manifest["files"][training]["sha256"]},
            "模型训练输入必须是本模式唯一批准的评分文件及摘要", "MODEL_BINDING_INVALID")
    require(set(model.feature_files) <= {"movies.csv", "training-catalog.csv"}, "不允许的模型特征输入", "MODEL_BINDING_INVALID")
    for name, digest in model.feature_files.items():
        data.checked(name)
        require(digest == data.manifest["files"][name]["sha256"], "特征摘要不符", "MODEL_BINDING_INVALID")
    # Business API checks the sealed identity, never opens evaluation/train.csv.
    if mode == "evaluation":
        data.checked(training, evaluation=True)
    base = Path(path).resolve().parent
    for name, digest in model.artifacts.items():
        artifact = (base / name).resolve()
        require(not Path(name).is_absolute() and artifact.is_relative_to(base) and artifact != Path(path).resolve(),
                "模型工件路径必须位于清单目录内且不能指向清单自身", "MODEL_BINDING_INVALID")
        require(bool(re.fullmatch(r"[a-f0-9]{64}", digest)) and artifact.is_file() and sha256(artifact) == digest,
                "模型工件缺失或摘要不符", "MODEL_BINDING_INVALID")
    return model


def check_request(request):
    from .contracts import RecommendationRequest
    require(isinstance(request, RecommendationRequest) and request.subject_kind in {"ml", "app"} and
            type(request.subject_id) is int and 0 < request.subject_id <= 2**64-1 and type(request.limit) is int and
            1 <= request.limit <= 50 and request.runtime_mode in {"business", "evaluation"} and
            request.dataset_version == DATASET and request.split_version == SPLIT,
            "推荐输入身份、数量或版本非法", "REQUEST_CONTEXT_INVALID")
    require(request.runtime_mode != "evaluation" or request.subject_kind == "ml",
            "离线评价仅允许冻结ml用户", "REQUEST_CONTEXT_INVALID")


def check_provider(provider):
    method = getattr(provider, "recommend", None)
    require(callable(method) and not inspect.iscoroutinefunction(method), "Provider须提供同步recommend(request)方法")
    try:
        inspect.signature(method).bind(object())
    except (TypeError, ValueError):
        raise ContractError("PROVIDER_OUTPUT_INVALID", "Provider方法须能接收一个RecommendationRequest") from None


def check_result(value, request, algorithm, catalog: set[str], model_version: str | None = None):
    from .contracts import RecommendationResult
    check_request(request)
    try:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="python")
        # Strict validation also rejects boolean ranks and silent identifier coercion.
        result = RecommendationResult.model_validate(value, strict=True)
    except ValidationError:
        raise ContractError("PROVIDER_OUTPUT_INVALID", "Provider输出不符合现有RecommendationResult DTO") from None
    require(result.algorithm == algorithm and result.runtime_mode == request.runtime_mode and
            result.dataset_version == request.dataset_version and result.split_version == request.split_version,
            "输出算法、模式或数据版本与输入不一致")
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", result.model_version)) and
            (model_version is None or result.model_version == model_version), "输出模型版本非法或与模型清单不符")
    require(result.generated_at.tzinfo is not None and result.generated_at.utcoffset() is not None,
            "generated_at必须有明确时区")
    require(len(result.items) <= request.limit, "输出条数超过请求limit")
    seen = set()
    for rank, item in enumerate(result.items, 1):
        require(item.movie_id in catalog and bool(re.fullmatch(r"[1-9][0-9]*", item.movie_id)), "电影ID不在批准目录")
        require(item.movie_id not in seen and type(item.rank) is int and item.rank == rank, "重复电影或名次不连续")
        require(item.algorithm == algorithm and item.model_version == result.model_version, "项目模型版本或算法不一致")
        if item.recall_ranks:
            require(all(type(n) is int and n >= 1 for n in item.recall_ranks.values()), "召回名次必须为正整数")
        seen.add(item.movie_id)
    for left, right in zip(result.items, result.items[1:]):
        require(left.score is None or right.score is None or left.score != right.score or
                int(left.movie_id) < int(right.movie_id), "同分电影须按movie_id升序")
    return result

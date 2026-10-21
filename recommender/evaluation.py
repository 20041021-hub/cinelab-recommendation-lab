"""Evaluation tasks and file/result validation only. No Precision/Recall implementation."""
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .data import DATASET, SPLIT, SCHEMA, MANIFEST_SHA, EVALUATION, FEATURES, FrozenData, require
from .validation import ModelManifest, check_model, check_result
from .contracts import Algorithm, RecommendationRequest, RecommendationResult

Partition = Literal["validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10"]


def eligible_users(data: FrozenData, partition: str) -> set[str]:
    require(partition in {"validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10"},
            "未知的冻结评价分区", "EVALUATION_INPUT_INVALID")
    if partition in {"validation", "test"}:
        rows = data.rows("evaluation/evaluation-eligibility.csv", evaluation=True)
        ids = {r["userId"] for r in rows if r["partition"] == partition and r["eligible"] == "1"}
        expected = 4209 if partition == "validation" else 4198
    else:
        ids = {r["userId"] for r in data.rows(f"evaluation/{partition}-users.csv", evaluation=True) if r["eligible"] == "1"}
        expected = 499
    require(len(ids) == expected, "固定评价用户集合数量不符", "EVALUATION_INPUT_INVALID")
    return ids


def task(data: FrozenData, partition: str) -> dict:
    users = eligible_users(data, partition)
    # Canonical decimal IDs in numeric order, one per LF-terminated line.
    users_sha = hashlib.sha256("".join(uid + "\n" for uid in sorted(users, key=int)).encode()).hexdigest()
    task_data = {"contract_version": "evaluation-task-v1", "dataset_version": DATASET, "split_version": SPLIT,
        "schema_version": SCHEMA, "manifest_sha256": MANIFEST_SHA, "runtime_mode": "evaluation", "partition": partition,
        "fit_file": "evaluation/train.csv", "positive_threshold": "4.0", "top_k": 10,
        "metrics": ["Precision@10", "Recall@10"], "aggregation": "macro over all fixed eligible users",
        "short_list_precision_denominator": 10, "catalog_file": "training-catalog.csv", "catalog_count": 8459,
        "user_count": len(users), "user_ids_sha256": users_sha,
        "candidate_rule": "catalog-minus-train" if partition == "validation" else
            "catalog-minus-train-and-validation" if partition == "test" else "catalog-minus-this-user-support",
        "files": {name: data.manifest["files"][name]["sha256"] for name in sorted(EVALUATION | FEATURES)}}
    task_data["task_sha256"] = hashlib.sha256(json.dumps(task_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return task_data


def inspect_inputs(data: FrozenData) -> dict:
    counts = {}
    for name in sorted(EVALUATION | FEATURES):
        counts[name] = sum(1 for _ in data.rows(name, evaluation=True))
    catalog = data.catalog()
    roles = {r["ml_user_id"]: r["evaluation_role"] for r in data.rows("history_users.csv")}
    train_users = set()
    for row in data.training():
        require(row["movieId"] in catalog and roles.get(row["userId"]) == "warm", "训练含目录外电影或cold用户", "EVALUATION_INPUT_INVALID")
        train_users.add(row["userId"])
    require(len(train_users) == 4500, "训练用户数量不符", "EVALUATION_INPUT_INVALID")
    return {"status": "validated_inputs_only", "counts": counts, "training_users": len(train_users),
        "tasks": [task(data, p) for p in ["validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10"]],
        "metrics_computed": False}


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["not_run", "completed"]
    runtime_mode: Literal["evaluation"]
    algorithm: Literal["popular", "content", "itemcf", "als", "cold_start"]
    model_version: str = Field(min_length=1)
    task_sha256: str
    model_manifest_sha256: str
    dataset_version: Literal[DATASET]
    split_version: Literal[SPLIT]
    schema_version: Literal[SCHEMA]
    partition: Partition
    user_count: int = Field(gt=0)
    precision_at_10: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    recall_at_10: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def metric_state(self):
        if self.status == "not_run" and (self.precision_at_10 is not None or self.recall_at_10 is not None):
            raise ValueError("未运行时指标必须为NULL")
        if self.status == "completed" and (self.precision_at_10 is None or self.recall_at_10 is None):
            raise ValueError("完成状态必须提供两个实际计算指标")
        return self


def check_evaluation_result(path: Path, model_path: Path, data: FrozenData) -> EvaluationResult:
    from .data import sha256
    result = EvaluationResult.model_validate_json(Path(path).read_text())
    current = task(data, result.partition)
    model = check_model(model_path, "evaluation", result.algorithm, data)
    require(result.task_sha256 == current["task_sha256"] and result.user_count == current["user_count"] and
            result.model_version == model.model_version and result.model_manifest_sha256 == sha256(model_path),
            "评价任务、用户集合或模型版本不一致", "EVALUATION_RESULT_INVALID")
    return result


def check_predictions(path: Path, model_path: Path, partition: str, data: FrozenData) -> dict:
    """Validate every fixed user and candidate membership. Do not intersect with relevance labels."""
    model = ModelManifest.model_validate_json(Path(model_path).read_text())
    check_model(model_path, "evaluation", model.algorithm, data)
    users, catalog = eligible_users(data, partition), data.catalog()
    exclusions = {uid: set() for uid in users}
    sources = ["evaluation/train.csv"] if partition == "validation" else ["evaluation/train.csv", "evaluation/validation.csv"] if partition == "test" else [f"evaluation/{partition}-support.csv"]
    for name in sources:
        for row in data.rows(name, evaluation=True):
            if row["userId"] in exclusions:
                exclusions[row["userId"]].add(row["movieId"])
    seen = set()
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            require(set(row) == {"ml_user_id", "result"} and type(row["ml_user_id"]) is str,
                    "预测行只能包含ml_user_id和现有result DTO", "EVALUATION_INPUT_INVALID")
            uid = row["ml_user_id"]
            require(uid in users and uid not in seen, "预测含非评价用户或重复用户", "EVALUATION_INPUT_INVALID")
            request = RecommendationRequest("ml", int(uid), 10, "evaluation", DATASET, SPLIT)
            dto = RecommendationResult.model_validate_json(json.dumps(row["result"]))
            check_result(dto, request, Algorithm(model.algorithm), catalog - exclusions[uid], model.model_version)
            seen.add(uid)
    require(seen == users, "不能漏掉无法生成推荐的用户；该用户须提交items=[]", "EVALUATION_INPUT_INVALID")
    return {"status": "validated_predictions_only", "users": len(seen), "partition": partition, "metrics_computed": False}

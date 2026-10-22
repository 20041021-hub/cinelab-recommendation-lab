"""Lab 3: the evaluation-mode history adapter.

The frozen evaluation protocol never lets a provider touch the business
database, and the API never runs in evaluation mode (see
``docs/student-algorithm-guide.md`` section 5).  Offline evaluation therefore
needs its own history source:

- ``validation``: the user's ``evaluation/train.csv`` rows;
- ``test``: train rows plus ``evaluation/validation.csv`` rows, mirroring the
  documented candidate rule (catalog minus train minus validation);
- ``cold-k0/1/5/10``: the user's ``evaluation/<partition>-support.csv`` rows,
  which the protocol allows for per-user inference/adaptation only.

A ``RecommendationRequest`` carries no partition field (``batch_id`` belongs to
the result DTO), so the offline runner marks the active frozen partition in the
``CINELAB_EVALUATION_PARTITION`` environment variable through
:func:`use_partition`.  ``python -m recommender.check selfcheck --module
recommender.student.evaluation_entry --mode evaluation`` (the self-check the
student guide asks for) sends no partition at all, so that single-process smoke
check falls back to :data:`DEFAULT_PARTITION` — the frozen tuning partition
``validation``.  An unknown value is still rejected instead of guessed.  Only one
partition is evaluated per process, and the grouped files are cached per file
name, so no run can leak another partition's history into a profile.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache

from recommender.data import FrozenData, require

PARTITIONS = ("validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10")
PARTITION_ENV = "CINELAB_EVALUATION_PARTITION"
DEFAULT_PARTITION = "validation"


@dataclass(frozen=True)
class EvaluationFeedback:
    movie_id: str
    rating: str


@dataclass(frozen=True)
class EvaluationHistory:
    subject_key: str
    ratings: tuple[EvaluationFeedback, ...]
    favorite_movie_ids: tuple[str, ...]


def exclusion_file_names(partition: str) -> tuple[str, ...]:
    """The files whose rows define the partition's candidate exclusions."""
    require(partition in PARTITIONS, "未知的冻结评价分区", "EVALUATION_INPUT_INVALID")
    if partition == "validation":
        return ("evaluation/train.csv",)
    if partition == "test":
        return ("evaluation/train.csv", "evaluation/validation.csv")
    return (f"evaluation/{partition}-support.csv",)


def query_file_name(partition: str) -> str:
    """The hold-out label file: relevance only, never an input to fitting."""
    require(partition in PARTITIONS, "未知的冻结评价分区", "EVALUATION_INPUT_INVALID")
    if partition in {"validation", "test"}:
        return f"evaluation/{partition}.csv"
    return f"evaluation/{partition}-query.csv"


@lru_cache(maxsize=8)
def by_user(name: str) -> dict[str, tuple[EvaluationFeedback, ...]]:
    """Group one frozen evaluation file by the canonical ml user id."""
    grouped: dict[str, list[EvaluationFeedback]] = {}
    for row in FrozenData().rows(name, evaluation=True):
        grouped.setdefault(row["userId"], []).append(EvaluationFeedback(row["movieId"], row["rating"]))
    return {user: tuple(items) for user, items in grouped.items()}


def history_for(partition: str, user_id: str) -> tuple[EvaluationFeedback, ...]:
    return tuple(
        feedback
        for name in exclusion_file_names(partition)
        for feedback in by_user(name).get(user_id, ())
    )


def active_partition() -> str:
    """The frozen partition this process is allowed to evaluate."""
    partition = os.environ.get(PARTITION_ENV, "") or DEFAULT_PARTITION
    require(partition in PARTITIONS,
            f"未知的冻结评价分区：{partition}（{PARTITION_ENV}）", "DATA_MODE_INVALID")
    return partition


@contextmanager
def use_partition(partition: str):
    """Mark one partition as active for the enclosed provider calls."""
    require(partition in PARTITIONS, "未知的冻结评价分区", "EVALUATION_INPUT_INVALID")
    previous = os.environ.get(PARTITION_ENV)
    os.environ[PARTITION_ENV] = partition
    try:
        yield partition
    finally:
        if previous is None:
            os.environ.pop(PARTITION_ENV, None)
        else:
            os.environ[PARTITION_ENV] = previous


def read_evaluation_history(request) -> EvaluationHistory:
    """History reader for evaluation-mode providers; rejects business mode."""
    require(request.runtime_mode == "evaluation",
            "评价历史适配器只允许evaluation模式", "DATA_MODE_INVALID")
    require(request.subject_kind == "ml", "离线评价仅允许冻结ml用户", "DATA_MODE_INVALID")
    partition = active_partition()
    return EvaluationHistory(
        subject_key=f"ml:{request.subject_id}",
        ratings=history_for(partition, str(request.subject_id)),
        favorite_movie_ids=(),
    )
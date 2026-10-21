"""Lab 2: explainable popularity ranking for business users.

The artifact is ordered by the breadth of positive feedback (ratings >= 4.0)
and never by a handful of 5-star ratings alone.  The provider filters films
the current user has already rated or favourited, but the ranking itself is
not personalised.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from recommender.contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult

ALGORITHM = Algorithm.popular
MODEL_VERSION = "popular-business-v1"
RECALL_SOURCE = "popular_positive_support"
MIN_POSITIVE_SUPPORT = 20
MAX_RECOMMENDATIONS = 10


@lru_cache(maxsize=4)
def _load_ranking(path: str) -> tuple[dict, ...]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if artifact.get("model_version") != MODEL_VERSION:
        return ()
    return tuple(artifact.get("items", []))


def _empty(request: RecommendationRequest) -> RecommendationResult:
    return RecommendationResult(
        algorithm=ALGORITHM,
        model_version=MODEL_VERSION,
        dataset_version=request.dataset_version,
        split_version=request.split_version,
        runtime_mode=request.runtime_mode,
        generated_at=datetime.now(timezone.utc),
        items=[],
    )


class PopularProvider:
    """Non-personalised positive-support popularity with per-user filtering."""

    def __init__(self, model_manifest_path: str | Path, history_reader=None):
        self.model_manifest_path = Path(model_manifest_path)
        self._artifact_path = self.model_manifest_path.with_name("popular.json")
        self._ranking: tuple[dict, ...] | None = None
        self._history_reader = history_reader

    def _get_ranking(self) -> tuple[dict, ...]:
        if self._ranking is None:
            self._ranking = _load_ranking(str(self._artifact_path))
        return self._ranking

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        ranking = self._get_ranking()
        if not ranking:
            return _empty(request)
        reader = self._history_reader
        if reader is None:
            from recommender.business import read_history
            reader = read_history
        history = reader(request)
        seen = {feedback.movie_id for feedback in history.ratings} | set(history.favorite_movie_ids)

        limit = min(request.limit, MAX_RECOMMENDATIONS)
        items: list[RecommendationItem] = []
        for row in ranking:
            support = int(row.get("positive_support", 0))
            if support < MIN_POSITIVE_SUPPORT:
                break
            movie_id = str(row["movie_id"])
            if movie_id in seen:
                continue
            rank = len(items) + 1
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=rank,
                algorithm=ALGORITHM,
                model_version=MODEL_VERSION,
                score=float(row["score"]),
                recall_sources=[RECALL_SOURCE],
                recall_ranks={RECALL_SOURCE: rank},
            ))
            if len(items) >= limit:
                break
        return RecommendationResult(
            algorithm=ALGORITHM,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )

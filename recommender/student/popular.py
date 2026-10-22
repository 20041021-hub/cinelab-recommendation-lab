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


@lru_cache(maxsize=8)
def _load_ranking(path: str) -> tuple[str, tuple[dict, ...]]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    return str(artifact.get("model_version") or MODEL_VERSION), tuple(artifact.get("items", []))


def _empty(request: RecommendationRequest, version: str = MODEL_VERSION) -> RecommendationResult:
    return RecommendationResult(
        algorithm=ALGORITHM,
        model_version=version,
        dataset_version=request.dataset_version,
        split_version=request.split_version,
        runtime_mode=request.runtime_mode,
        generated_at=datetime.now(timezone.utc),
        items=[],
    )


class PopularProvider:
    """Non-personalised positive-support popularity with per-user filtering."""

    def __init__(self, model_manifest_path: str | Path, history_reader=None,
                 model_version: str | None = None):
        self.model_manifest_path = Path(model_manifest_path)
        self._artifact_path = self.model_manifest_path.with_name("popular.json")
        self._ranking: tuple[dict, ...] | None = None
        self._history_reader = history_reader
        self._model_version = model_version or MODEL_VERSION

    def _get_ranking(self) -> tuple[dict, ...]:
        if self._ranking is None:
            version, ranking = _load_ranking(str(self._artifact_path))
            self._ranking = ranking if version == self._model_version else ()
        return self._ranking

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        ranking = self._get_ranking()
        if not ranking:
            return _empty(request, self._model_version)
        reader = self._history_reader
        if reader is None:
            from recommender.business import read_history
            reader = read_history
        history = reader(request)
        seen = {feedback.movie_id for feedback in history.ratings} | set(history.favorite_movie_ids)

        limit = min(request.limit, MAX_RECOMMENDATIONS)
        # The published contract orders equal scores by movie_id ascending, so
        # the displayed score is the primary key and the numeric id breaks ties.
        # The artifact keeps mean_rating as a diagnostic column only.
        ranked = sorted(
            (row for row in ranking if int(row.get("positive_support", 0)) >= MIN_POSITIVE_SUPPORT),
            key=lambda row: (-float(row["score"]), int(row["movie_id"])),
        )
        items: list[RecommendationItem] = []
        for row in ranked:
            movie_id = str(row["movie_id"])
            if movie_id in seen:
                continue
            rank = len(items) + 1
            items.append(RecommendationItem(
                movie_id=movie_id,
                rank=rank,
                algorithm=ALGORITHM,
                model_version=self._model_version,
                score=float(row["score"]),
                recall_sources=[RECALL_SOURCE],
                recall_ranks={RECALL_SOURCE: rank},
            ))
            if len(items) >= limit:
                break
        return RecommendationResult(
            algorithm=ALGORITHM,
            model_version=self._model_version,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )

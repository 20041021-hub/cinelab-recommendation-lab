"""Lab 2: an explicit cold-start entry.

Without positive history the provider returns the popularity ranking and
marks the source as non-personalised.  Once the user has at least one rating
>= 4.0 it switches to the Lab 1 genre-content profile, so the change in
recommendations is traceable to a real action (rating a film).

The strategy descriptor is kept in the sidecar manifest/artifact, not in
frontend-only copy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from recommender.contracts import (
    Algorithm,
    RecommendationItem,
    RecommendationRequest,
    RecommendationResult,
)
from recommender.student.content import ContentProvider
from recommender.student.popular import PopularProvider

ALGORITHM = Algorithm.cold_start
MODEL_VERSION = "coldstart-popular-content-v1"
POPULAR_SOURCE = "cold_start_popular"
CONTENT_SOURCE = "cold_start_content"
POSITIVE_RATING = 4.0
MAX_RECOMMENDATIONS = 10


def _remap(item: RecommendationItem, source: str, version: str) -> RecommendationItem:
    return RecommendationItem(
        movie_id=item.movie_id,
        rank=item.rank,
        algorithm=ALGORITHM,
        model_version=version,
        score=item.score,
        recall_sources=[source],
        recall_ranks={source: item.rank},
    )


def _result(request: RecommendationRequest, items: list[RecommendationItem],
            version: str) -> RecommendationResult:
    return RecommendationResult(
        algorithm=ALGORITHM,
        model_version=version,
        dataset_version=request.dataset_version,
        split_version=request.split_version,
        runtime_mode=request.runtime_mode,
        generated_at=datetime.now(timezone.utc),
        items=items,
    )


class ColdStartProvider:
    """Popularity until a positive signal exists, then a content profile."""

    def __init__(self, model_manifest_path: str | Path,
                 popular_manifest_path: str | Path | None = None,
                 content_manifest_path: str | Path | None = None,
                 history_reader=None,
                 model_version: str | None = None,
                 popular_model_version: str | None = None,
                 content_model_version: str | None = None):
        self.model_manifest_path = Path(model_manifest_path)
        model_root = self.model_manifest_path.parent.parent
        self._history_reader = history_reader
        self._model_version = model_version or MODEL_VERSION
        self._popular = PopularProvider(
            popular_manifest_path or model_root / "popular-v1" / "manifest.json",
            history_reader=history_reader,
            model_version=popular_model_version,
        )
        self._content = ContentProvider(
            content_manifest_path or model_root / "content-v1" / "manifest.json",
            history_reader=history_reader,
            model_version=content_model_version,
        )

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        reader = self._history_reader
        if reader is None:
            from recommender.business import read_history
            reader = read_history
        history = reader(request)
        has_positive = any(float(feedback.rating) >= POSITIVE_RATING for feedback in history.ratings)

        if has_positive:
            content = self._content.recommend(request)
            if content.items:
                return _result(request, [
                    _remap(item, CONTENT_SOURCE, self._model_version)
                    for item in content.items[:MAX_RECOMMENDATIONS]
                ], self._model_version)

        # No positive signal, or a profile that could not produce candidates:
        # stay honest about the non-personalised fallback.
        popular = self._popular.recommend(request)
        return _result(request, [
            _remap(item, POPULAR_SOURCE, self._model_version)
            for item in popular.items[:MAX_RECOMMENDATIONS]
        ], self._model_version)

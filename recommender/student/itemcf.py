"""Lab 2: item-based collaborative filtering from business interactions.

Item-item similarity is cosine over binary positive interactions.  For a
target user, every positively rated seed contributes its neighbour list,
weighted by the rating strength; already rated and favourited films are
removed before ranking.

The artifact stores only the top neighbours per item.  Items with fewer than
20 positive interactions or neighbours with fewer than 5 co-occurrences are
deliberately omitted: a single accidental co-occurrence is not evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from recommender.contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult

ALGORITHM = Algorithm.itemcf
MODEL_VERSION = "itemcf-cooccurrence-v1"
RECALL_SOURCE = "itemcf_cooccurrence"
POSITIVE_RATING = 4.0
MAX_RECOMMENDATIONS = 10


@lru_cache(maxsize=4)
def _load_neighbours(path: str) -> dict[str, dict]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if artifact.get("model_version") != MODEL_VERSION:
        return {}
    return artifact.get("items", {})


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


class ItemCFProvider:
    """Item-based CF over the frozen business history."""

    def __init__(self, model_manifest_path: str | Path, history_reader=None):
        self.model_manifest_path = Path(model_manifest_path)
        self._artifact_path = self.model_manifest_path.with_name("item-similarities.json")
        self._neighbours: dict[str, dict] | None = None
        self._history_reader = history_reader

    def _get_neighbours(self) -> dict[str, dict]:
        if self._neighbours is None:
            self._neighbours = _load_neighbours(str(self._artifact_path))
        return self._neighbours

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        neighbours = self._get_neighbours()
        if not neighbours:
            return _empty(request)
        reader = self._history_reader
        if reader is None:
            from recommender.business import read_history
            reader = read_history
        history = reader(request)
        seen = {feedback.movie_id for feedback in history.ratings} | set(history.favorite_movie_ids)
        positive = [feedback for feedback in history.ratings if float(feedback.rating) >= POSITIVE_RATING]
        if not positive:
            return _empty(request)

        scores: dict[str, float] = {}
        for feedback in positive:
            entry = neighbours.get(feedback.movie_id)
            if not entry:
                continue
            # A 5-star seed contributes more than a 4-star seed, but never zero.
            weight = max(float(feedback.rating) - 3.0, 0.1)
            for movie_id, similarity, _cooccurrence in entry.get("neighbors", []):
                movie_id = str(movie_id)
                if movie_id in seen:
                    continue
                scores[movie_id] = scores.get(movie_id, 0.0) + float(similarity) * weight
        ranked = sorted(scores.items(), key=lambda item: (-round(item[1], 6), int(item[0])))
        limit = min(request.limit, MAX_RECOMMENDATIONS)
        items = [
            RecommendationItem(
                movie_id=movie_id,
                rank=rank,
                algorithm=ALGORITHM,
                model_version=MODEL_VERSION,
                score=round(score, 6),
                recall_sources=[RECALL_SOURCE],
                recall_ranks={RECALL_SOURCE: rank},
            )
            for rank, (movie_id, score) in enumerate(ranked[:limit], 1)
        ]
        return RecommendationResult(
            algorithm=ALGORITHM,
            model_version=MODEL_VERSION,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )

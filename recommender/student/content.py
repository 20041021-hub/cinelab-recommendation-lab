"""Lab 1: content-based recommendation from movie genres.

The provider uses a TF-IDF representation of the ``genres`` column:
each movie is a sparse genre vector, and the user profile is the
rating-weighted average of the vectors of movies rated >= 4.0.  Both
sides are L2-normalised and ranked by cosine similarity.  Movies the
user has already rated (and favourites, which are also explicit
positive feedback) are removed before ranking.

No database access or artifact parsing happens at import time; the
artifact is loaded on the first recommendation request.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from recommender.contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult
from recommender.data import FrozenData

ALGORITHM = Algorithm.content
MODEL_VERSION = "content-genre-tfidf-v1"
RECALL_SOURCE = "genre_tfidf"
POSITIVE_RATING = 4.0
MAX_RECOMMENDATIONS = 10


@lru_cache(maxsize=8)
def _load_vectors(path: str) -> tuple[str, dict[str, tuple[float, ...]]]:
    """Load the genre artifact once and turn each movie into a unit vector."""
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    version = str(artifact.get("model_version") or MODEL_VERSION)
    vocabulary = list(artifact["genre_vocabulary"])
    index = {genre: position for position, genre in enumerate(vocabulary)}
    idf = tuple(float(artifact["idf"][genre]) for genre in vocabulary)

    vectors: dict[str, tuple[float, ...]] = {}
    for movie_id, genres in artifact["movie_genres"].items():
        values = [0.0] * len(vocabulary)
        for genre in genres:
            position = index.get(genre)
            if position is not None:
                values[position] = idf[position]
        norm = math.sqrt(sum(value * value for value in values))
        if norm > 0.0:
            vectors[movie_id] = tuple(value / norm for value in values)
    return version, vectors


@lru_cache(maxsize=1)
def _catalog() -> tuple[str, ...]:
    """Approved business catalogue, ordered by numeric movie id."""
    return tuple(sorted(FrozenData().catalog(), key=int))


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


class ContentProvider:
    """Content-based provider registered by ``recommender.contracts``."""

    def __init__(self, model_manifest_path: str | Path, history_reader=None,
                 model_version: str | None = None):
        self.model_manifest_path = Path(model_manifest_path)
        self._artifact_path = self.model_manifest_path.with_name("movie-genres.json")
        self._vectors: dict[str, tuple[float, ...]] | None = None
        self._history_reader = history_reader
        # The version string of the sidecar manifest; evaluation manifests use
        # their own identifier so business and evaluation fits never mix.
        self._model_version = model_version or MODEL_VERSION

    def _get_vectors(self) -> dict[str, tuple[float, ...]]:
        if self._vectors is None:
            version, vectors = _load_vectors(str(self._artifact_path))
            self._vectors = vectors if version == self._model_version else {}
        return self._vectors

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        vectors = self._get_vectors()
        if not vectors:
            return _empty(request, self._model_version)
        reader = self._history_reader
        if reader is None:
            # Imported lazily so offline checks can import the provider without
            # a database driver being installed on the host.
            from recommender.business import read_history
            reader = read_history
        history = reader(request)
        rated = {feedback.movie_id for feedback in history.ratings}
        favorites = set(history.favorite_movie_ids)
        positive = [
            feedback
            for feedback in history.ratings
            if float(feedback.rating) >= POSITIVE_RATING
        ]
        if not positive:
            return _empty(request, self._model_version)

        dimensions = len(next(iter(vectors.values())))
        profile = [0.0] * dimensions
        touched = 0
        for feedback in positive:
            vector = vectors.get(feedback.movie_id)
            if vector is None:
                continue
            weight = max(float(feedback.rating) - 3.0, 0.1)
            for position, value in enumerate(vector):
                profile[position] += weight * value
            touched += 1
        if touched == 0:
            return _empty(request, self._model_version)

        norm = math.sqrt(sum(value * value for value in profile))
        if norm == 0.0:
            return _empty(request, self._model_version)
        profile = [value / norm for value in profile]

        scored: list[tuple[str, float]] = []
        for movie_id in _catalog():
            if movie_id in rated or movie_id in favorites:
                continue
            vector = vectors.get(movie_id)
            if vector is None:
                continue
            score = sum(left * right for left, right in zip(profile, vector))
            if score <= 0.0:
                continue
            # Six decimals makes ties stable for the contract's movie-id tie-break.
            scored.append((movie_id, round(min(score, 1.0), 6)))

        scored.sort(key=lambda item: (-item[1], int(item[0])))
        items = [
            RecommendationItem(
                movie_id=movie_id,
                rank=rank,
                algorithm=ALGORITHM,
                model_version=self._model_version,
                score=score,
                recall_sources=[RECALL_SOURCE],
                recall_ranks={RECALL_SOURCE: rank},
            )
            for rank, (movie_id, score) in enumerate(
                scored[: min(request.limit, MAX_RECOMMENDATIONS)], 1
            )
        ]
        return RecommendationResult(
            algorithm=ALGORITHM,
            model_version=self._model_version,
            dataset_version=request.dataset_version,
            split_version=request.split_version,
            runtime_mode=request.runtime_mode,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )

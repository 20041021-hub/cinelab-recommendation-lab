"""Lab 3: Spark MLlib ALS matrix factorisation.

``scripts/build_lab3_als.py`` trains the factors with Spark MLlib (explicit
ratings, ``implicitPrefs=False``) and writes ``als-factors.json`` plus a
sidecar ``manifest.json``.  This module only *serves* those factors:

- warm users are looked up in the stored user-factor table;
- users absent from the table (cold-start partitions) are folded in from their
  available history: a rating-weighted mean of the item factors;
- films below the artifact's ``candidate_support_floor`` (a guardrail tuned on
  the frozen validation split) never enter the candidate pool;
- every already-rated or favourited film is filtered out of the candidates.

The provider is pure Python on purpose: the runtime image ships no numpy, and
the same code must run in the API container (business mode) and on the host
(evaluation mode).  History comes from ``recommender.business.read_history``
in business mode and from the injected evaluation adapter in evaluation mode.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from recommender.contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult

ALGORITHM = Algorithm.als
MODEL_VERSION = "als-mllib-v1"
RECALL_SOURCE = "als_factor_score"
MAX_RECOMMENDATIONS = 50  # 契约上限：调用方按 limit 请求，默认 10 条；融合需要更深候选
FOLD_IN_FLOOR = 0.5


@lru_cache(maxsize=8)
def _load_factors(path: str) -> tuple[str, dict]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    return str(artifact.get("model_version") or MODEL_VERSION), artifact


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


class AlsProvider:
    """Deterministic dot-product scoring over Spark-trained latent factors."""

    def __init__(self, model_manifest_path: str | Path, history_reader=None,
                 model_version: str | None = None):
        self.model_manifest_path = Path(model_manifest_path)
        self._artifact_path = self.model_manifest_path.with_name("als-factors.json")
        self._history_reader = history_reader
        self._model_version = model_version or MODEL_VERSION
        self._artifact: dict | None = None
        self._prepared: tuple[tuple[str, int, tuple[float, ...]], ...] | None = None
        self._factors_by_movie: dict[str, tuple[float, ...]] | None = None
        self._candidates = 0

    def _get_artifact(self) -> dict:
        if self._artifact is None:
            version, artifact = _load_factors(str(self._artifact_path))
            self._artifact = artifact if version == self._model_version else {}
        return self._artifact

    def _prepare(self, artifact: dict) -> None:
        if self._prepared is not None:
            return
        # Long-tail guardrail chosen on the frozen validation split: films whose
        # support in the fit file is below the floor are left out of the
        # candidate pool, because their latent factors are trained on too little
        # signal to rank reliably.  ``item_support`` travels inside the artifact
        # so the API container needs no access to the raw fit file.
        support = {str(movie): int(count) for movie, count in (artifact.get("item_support") or {}).items()}
        floor = int(artifact.get("candidate_support_floor") or 0) if support else 0
        prepared = []
        mapping: dict[str, tuple[float, ...]] = {}
        for movie_id, factor in (artifact.get("items") or {}).items():
            if support and support.get(str(movie_id), 0) < floor:
                continue
            values = tuple(float(value) for value in factor)
            prepared.append((str(movie_id), int(movie_id), values))
            mapping[str(movie_id)] = values
        prepared.sort(key=lambda entry: entry[1])
        self._prepared = tuple(prepared)
        self._factors_by_movie = mapping
        self._candidates = len(prepared)

    def _user_vector(self, artifact: dict, user_id: str, history) -> tuple[float, ...] | None:
        stored = (artifact.get("users") or {}).get(user_id)
        if stored is not None:
            return tuple(float(value) for value in stored)
        # Cold-start fold-in: the protocol allows support interactions for
        # per-user inference, so a rating-weighted mean of item factors is a
        # legitimate online adaptation of the frozen item space.
        assert self._factors_by_movie is not None
        weighted: list[tuple[float, tuple[float, ...]]] = []
        for feedback in history.ratings:
            factor = self._factors_by_movie.get(str(feedback.movie_id))
            if factor is None:
                continue
            weighted.append((max(float(feedback.rating) - 3.0, FOLD_IN_FLOOR), factor))
        if not weighted:
            return None
        total = sum(weight for weight, _ in weighted)
        return tuple(
            sum(weight * factor[position] for weight, factor in weighted) / total
            for position in range(len(weighted[0][1]))
        )

    def recommend(self, request: RecommendationRequest) -> RecommendationResult:
        artifact = self._get_artifact()
        if not artifact:
            return _empty(request, self._model_version)
        self._prepare(artifact)
        reader = self._history_reader
        if reader is None:
            from recommender.business import read_history
            reader = read_history
        history = reader(request)
        seen = {str(feedback.movie_id) for feedback in history.ratings} | {
            str(movie_id) for movie_id in history.favorite_movie_ids}
        vector = self._user_vector(artifact, str(request.subject_id), history)
        if vector is None:
            return _empty(request, self._model_version)

        scored: list[tuple[str, int, float]] = []
        for movie_id, numeric_id, factor in self._prepared:
            if movie_id in seen:
                continue
            score = 0.0
            for left, right in zip(vector, factor):
                score += left * right
            scored.append((movie_id, numeric_id, round(score, 6)))
        scored.sort(key=lambda entry: (-entry[2], entry[1]))

        limit = min(request.limit, MAX_RECOMMENDATIONS)
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
            for rank, (movie_id, _, score) in enumerate(scored[:limit], 1)
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
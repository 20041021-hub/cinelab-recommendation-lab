"""Lab 3: the evaluation-mode provider entry.

The course guide requires offline evaluation to be a *separate process*: the
API keeps serving the business models, while the evaluation process imports
this module, which re-points the same ``PROVIDERS`` slots at the frozen
``models/evaluation`` manifests with the evaluation history adapter.

    python -m recommender.check selfcheck \
        --module recommender.student.evaluation_entry \
        --algorithm als --mode evaluation --limit 10

The module is never imported by ``backend``/``recommender.contracts`` itself.
"""
from __future__ import annotations

from pathlib import Path

from recommender.contracts import Algorithm, PROVIDERS

EVAL_ROOT = Path(__file__).resolve().parents[2] / "models" / "evaluation"

EVALUATION_MODEL_VERSIONS = {
    Algorithm.content: "content-genre-tfidf-eval-v1",
    Algorithm.popular: "popular-support-eval-v1",
    Algorithm.itemcf: "itemcf-cooccurrence-eval-v1",
    Algorithm.als: "als-mllib-eval-v1",
    Algorithm.cold_start: "coldstart-popular-content-eval-v1",
}


def register_evaluation_providers() -> dict:
    """Bind every algorithm slot to its evaluation manifest; return PROVIDERS."""
    from .als import AlsProvider
    from .cold_start import ColdStartProvider
    from .content import ContentProvider
    from .evaluation_data import read_evaluation_history
    from .itemcf import ItemCFProvider
    from .popular import PopularProvider

    shared = {"history_reader": read_evaluation_history}
    PROVIDERS[Algorithm.content] = ContentProvider(
        EVAL_ROOT / "content-v1" / "manifest.json",
        model_version=EVALUATION_MODEL_VERSIONS[Algorithm.content], **shared)
    PROVIDERS[Algorithm.popular] = PopularProvider(
        EVAL_ROOT / "popular-v1" / "manifest.json",
        model_version=EVALUATION_MODEL_VERSIONS[Algorithm.popular], **shared)
    PROVIDERS[Algorithm.itemcf] = ItemCFProvider(
        EVAL_ROOT / "itemcf-v1" / "manifest.json",
        model_version=EVALUATION_MODEL_VERSIONS[Algorithm.itemcf], **shared)
    PROVIDERS[Algorithm.als] = AlsProvider(
        EVAL_ROOT / "als-v1" / "manifest.json",
        model_version=EVALUATION_MODEL_VERSIONS[Algorithm.als], **shared)
    PROVIDERS[Algorithm.cold_start] = ColdStartProvider(
        EVAL_ROOT / "cold-start-v1" / "manifest.json",
        popular_manifest_path=EVAL_ROOT / "popular-v1" / "manifest.json",
        content_manifest_path=EVAL_ROOT / "content-v1" / "manifest.json",
        model_version=EVALUATION_MODEL_VERSIONS[Algorithm.cold_start],
        popular_model_version=EVALUATION_MODEL_VERSIONS[Algorithm.popular],
        content_model_version=EVALUATION_MODEL_VERSIONS[Algorithm.content],
        **shared)
    return PROVIDERS


register_evaluation_providers()
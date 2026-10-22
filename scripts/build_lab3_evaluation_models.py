"""Build the Lab 3 evaluation artifacts for content, popular, ItemCF and cold-start.

Every artifact here is fitted on ``evaluation/train.csv`` only (583,502 rows),
never on validation/test/cold query labels.  The content artifact is a copy of
the business genre/TF-IDF artifact because it depends on ``movies.csv`` genres
alone; popular and ItemCF are recomputed from train positives because their
business counterparts saw the full business history.

Run from the repository root:

    python scripts/build_lab3_evaluation_models.py

The ALS evaluation artifact is produced by ``scripts/build_lab3_als.py``.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recommender.data import DATASET, FrozenData, MANIFEST_SHA, SCHEMA, SPLIT  # noqa: E402

POSITIVE_RATING = 4.0
MIN_POSITIVE_SUPPORT = 20
MIN_ITEM_SUPPORT = 20
MIN_COOCCURRENCE = 5
MAX_NEIGHBORS = 50
MAX_RECOMMENDATIONS = 10

CONTENT_VERSION = "content-genre-tfidf-eval-v1"
POPULAR_VERSION = "popular-support-eval-v1"
ITEMCF_VERSION = "itemcf-cooccurrence-eval-v1"
COLD_START_VERSION = "coldstart-popular-content-eval-v1"

EVAL_ROOT = ROOT / "models" / "evaluation"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value, *, compact: bool = False) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":") if compact else None,
                   indent=None if compact else 2) + "\n",
        encoding="utf-8",
    )


def manifest(algorithm: str, version: str, artifact: str, artifact_sha: str,
             parameters: dict, files: dict) -> dict:
    return {
        "algorithm": algorithm,
        "model_version": version,
        "runtime_mode": "evaluation",
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "schema_version": SCHEMA,
        "manifest_sha256": MANIFEST_SHA,
        "training_files": {
            "evaluation/train.csv": files["evaluation/train.csv"]["sha256"],
        },
        "feature_files": {
            "movies.csv": files["movies.csv"]["sha256"],
            "training-catalog.csv": files["training-catalog.csv"]["sha256"],
        },
        "artifacts": {artifact: artifact_sha},
        "parameters": parameters,
        "tool_versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": __import__("scipy").__version__,
            "generator": "scripts/build_lab3_evaluation_models.py",
        },
    }


def train_frame(data: FrozenData) -> pd.DataFrame:
    """Train rows, verified against the frozen manifest, restricted to catalog."""
    path = data.checked("evaluation/train.csv", evaluation=True)
    frame = pd.read_csv(path, dtype={"userId": str, "movieId": str, "rating": float})
    catalog = data.catalog()
    frame = frame[frame["movieId"].isin(catalog)].copy()
    return frame


def build_content(files: dict) -> None:
    source = ROOT / "models" / "business" / "content-v1" / "movie-genres.json"
    target_dir = EVAL_ROOT / "content-v1"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "movie-genres.json"
    artifact = json.loads(source.read_text(encoding="utf-8"))
    artifact["model_version"] = CONTENT_VERSION
    artifact["fit_note"] = ("genre vocabulary/IDF depend on movies.csv only; copied from "
                            "models/business/content-v1, no rating file was used")
    write_json(target, artifact, compact=True)
    write_json(target_dir / "manifest.json", manifest(
        "content", CONTENT_VERSION, "movie-genres.json", sha256(target),
        {
            "features": "genres Multi-hot with IDF weighting",
            "similarity": "cosine on unit genre vectors",
            "positive_rating_threshold": POSITIVE_RATING,
            "profile_weights": "max(rating - 3.0, 0.1)",
            "max_recommendations": MAX_RECOMMENDATIONS,
            "rating_file_used": False,
        }, files))
    print(f"content: {target} ({target.stat().st_size} bytes)")


def build_popular(data: FrozenData, train: pd.DataFrame, files: dict) -> None:
    catalog = sorted(data.catalog(), key=int)
    all_support = train.groupby("movieId").size().to_dict()
    mean_rating = train.groupby("movieId")["rating"].mean().to_dict()
    positive = train[train["rating"] >= POSITIVE_RATING]
    positive_support = positive.groupby("movieId").size().to_dict()
    rows = [
        {
            "movie_id": movie_id,
            "positive_support": int(positive_support.get(movie_id, 0)),
            "all_support": int(all_support.get(movie_id, 0)),
            "mean_rating": round(float(mean_rating.get(movie_id, 0.0)), 6),
        }
        for movie_id in catalog
    ]
    rows.sort(key=lambda row: (-row["positive_support"], -row["mean_rating"], int(row["movie_id"])))
    top_support = max((row["positive_support"] for row in rows), default=0)
    for row in rows:
        row["score"] = round(row["positive_support"] / top_support, 6) if top_support else 0.0

    target_dir = EVAL_ROOT / "popular-v1"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "popular.json"
    write_json(target, {
        "model_version": POPULAR_VERSION,
        "positive_rating_threshold": POSITIVE_RATING,
        "min_positive_support": MIN_POSITIVE_SUPPORT,
        "scoring": "positive_support / top_positive_support",
        "items": rows,
    }, compact=True)
    write_json(target_dir / "manifest.json", manifest(
        "popular", POPULAR_VERSION, "popular.json", sha256(target),
        {
            "fit_file": "evaluation/train.csv",
            "positive_rating_threshold": POSITIVE_RATING,
            "min_positive_support": MIN_POSITIVE_SUPPORT,
            "max_recommendations": MAX_RECOMMENDATIONS,
            "ordering": "positive_support desc, mean_rating desc, movie_id asc",
        }, files))
    print(f"popular: {target} ({target.stat().st_size} bytes)")


def build_itemcf(data: FrozenData, train: pd.DataFrame, files: dict) -> None:
    catalog = sorted(data.catalog(), key=int)
    catalog_index = {movie_id: position for position, movie_id in enumerate(catalog)}
    positive = train[train["rating"] >= POSITIVE_RATING]
    users = sorted(positive["userId"].unique().tolist())
    user_index = {user: position for position, user in enumerate(users)}
    rows = np.fromiter((user_index[user] for user in positive["userId"]), dtype=np.int32)
    cols = np.fromiter((catalog_index[movie] for movie in positive["movieId"]), dtype=np.int32)
    matrix = csr_matrix((np.ones(len(positive), dtype=np.float32), (rows, cols)),
                        shape=(len(users), len(catalog)))
    cooccurrence = (matrix.T @ matrix).tocsr()
    support = cooccurrence.diagonal()

    items: dict[str, dict] = {}
    for item_position, movie_id in enumerate(catalog):
        item_support = int(support[item_position])
        if item_support < MIN_ITEM_SUPPORT:
            continue
        row = cooccurrence.getrow(item_position)
        indices, counts = row.indices, row.data
        mask = (indices != item_position) & (counts >= MIN_COOCCURRENCE)
        if not mask.any():
            continue
        indices, counts = indices[mask], counts[mask]
        similarity = counts / np.sqrt(support[item_position] * support[indices])
        if len(similarity) > MAX_NEIGHBORS:
            keep = np.argpartition(-similarity, MAX_NEIGHBORS - 1)[:MAX_NEIGHBORS]
            indices, counts, similarity = indices[keep], counts[keep], similarity[keep]
        neighbors = [
            [catalog[int(index)], round(float(score), 6), int(count)]
            for index, score, count in zip(indices, similarity, counts)
        ]
        neighbors.sort(key=lambda row: (-row[1], int(row[0])))
        items[movie_id] = {"support": item_support, "neighbors": neighbors}

    target_dir = EVAL_ROOT / "itemcf-v1"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "item-similarities.json"
    write_json(target, {
        "model_version": ITEMCF_VERSION,
        "positive_rating_threshold": POSITIVE_RATING,
        "min_item_support": MIN_ITEM_SUPPORT,
        "min_cooccurrence": MIN_COOCCURRENCE,
        "max_neighbors": MAX_NEIGHBORS,
        "items": items,
    }, compact=True)
    write_json(target_dir / "manifest.json", manifest(
        "itemcf", ITEMCF_VERSION, "item-similarities.json", sha256(target),
        {
            "fit_file": "evaluation/train.csv",
            "similarity": "cosine over binary positive interactions",
            "positive_rating_threshold": POSITIVE_RATING,
            "min_item_support": MIN_ITEM_SUPPORT,
            "min_cooccurrence": MIN_COOCCURRENCE,
            "max_neighbors": MAX_NEIGHBORS,
            "max_recommendations": MAX_RECOMMENDATIONS,
        }, files))
    print(f"itemcf: {target} ({target.stat().st_size} bytes, {len(items)} items with neighbours)")


def build_cold_start(files: dict) -> None:
    target_dir = EVAL_ROOT / "cold-start-v1"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "cold-start.json"
    write_json(target, {
        "model_version": COLD_START_VERSION,
        "strategy": "popular_without_positive_history_then_content",
        "positive_rating_threshold": POSITIVE_RATING,
        "max_recommendations": MAX_RECOMMENDATIONS,
        "popular_model_version": POPULAR_VERSION,
        "content_model_version": CONTENT_VERSION,
        "strategy_note": ("No positive support -> non-personalised popular ranking; "
                          "otherwise the genre content profile built from support interactions."),
    }, compact=True)
    write_json(target_dir / "manifest.json", manifest(
        "cold_start", COLD_START_VERSION, "cold-start.json", sha256(target),
        {
            "fit_file": "evaluation/train.csv",
            "strategy": "popular_without_positive_history_then_content",
            "positive_rating_threshold": POSITIVE_RATING,
            "max_recommendations": MAX_RECOMMENDATIONS,
        }, files))
    print(f"cold-start: {target} ({target.stat().st_size} bytes)")


def main() -> None:
    data = FrozenData()
    files = data.manifest["files"]
    EVAL_ROOT.mkdir(parents=True, exist_ok=True)
    train = train_frame(data)
    print(f"train rows in catalog: {len(train)} (users={train['userId'].nunique()})")
    build_content(files)
    build_popular(data, train, files)
    build_itemcf(data, train, files)
    build_cold_start(files)


if __name__ == "__main__":
    main()
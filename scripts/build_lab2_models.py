"""Build the Lab 2 popular, ItemCF and cold-start artifacts.

The script reads the approved business history once and writes three
self-contained model directories under ``models/business``:

- ``popular-v1``: positive-interaction popularity with a support floor;
- ``itemcf-v1``: item-item cosine similarity from user co-occurrence;
- ``cold-start-v1``: a strategy descriptor used by the cold-start provider.

Run from the repository root:

    python scripts/build_lab2_models.py

The build needs numpy/scipy (the runtime providers do not).
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

POPULAR_VERSION = "popular-business-v1"
ITEMCF_VERSION = "itemcf-cooccurrence-v1"
COLD_START_VERSION = "coldstart-popular-content-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value, *, compact: bool = False) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        ) + "\n",
        encoding="utf-8",
    )


def manifest(algorithm: str, version: str, artifact: str, artifact_sha: str,
             parameters: dict, files: dict) -> dict:
    return {
        "algorithm": algorithm,
        "model_version": version,
        "runtime_mode": "business",
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "schema_version": SCHEMA,
        "manifest_sha256": MANIFEST_SHA,
        "training_files": {
            "business/history_ratings.csv": files["business/history_ratings.csv"]["sha256"],
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
            "generator": "scripts/build_lab2_models.py",
        },
    }


def main() -> None:
    data = FrozenData()
    package = data.package
    files = data.manifest["files"]
    catalog = sorted(data.catalog(), key=int)
    catalog_set = set(catalog)
    catalog_index = {movie_id: position for position, movie_id in enumerate(catalog)}

    history = pd.read_csv(package / "business" / "history_ratings.csv")
    history = history[history["movieId"].astype(str).isin(catalog_set)].copy()
    history["movieId"] = history["movieId"].astype(str)
    history["userId"] = history["userId"].astype(str)
    history["rating"] = history["rating"].astype(float)

    all_support = history.groupby("movieId").size().to_dict()
    mean_rating = history.groupby("movieId")["rating"].mean().to_dict()
    positive = history[history["rating"] >= POSITIVE_RATING]
    positive_support = positive.groupby("movieId").size().to_dict()

    # Popularity is ordered by breadth of positive feedback, then by mean
    # rating.  A few 5-star ratings can never outrank a broadly liked film.
    popular_rows = [
        {
            "movie_id": movie_id,
            "positive_support": int(positive_support.get(movie_id, 0)),
            "all_support": int(all_support.get(movie_id, 0)),
            "mean_rating": round(float(mean_rating.get(movie_id, 0.0)), 6),
        }
        for movie_id in catalog
    ]
    popular_rows.sort(key=lambda row: (-row["positive_support"], -row["mean_rating"], int(row["movie_id"])))
    top_support = max((row["positive_support"] for row in popular_rows), default=0)
    for row in popular_rows:
        row["score"] = round(row["positive_support"] / top_support, 6) if top_support else 0.0

    popular_dir = ROOT / "models" / "business" / "popular-v1"
    popular_dir.mkdir(parents=True, exist_ok=True)
    popular_path = popular_dir / "popular.json"
    write_json(popular_path, {
        "model_version": POPULAR_VERSION,
        "positive_rating_threshold": POSITIVE_RATING,
        "min_positive_support": MIN_POSITIVE_SUPPORT,
        "scoring": "positive_support / top_positive_support",
        "items": popular_rows,
    }, compact=True)
    write_json(popular_dir / "manifest.json", manifest(
        "popular", POPULAR_VERSION, "popular.json", sha256(popular_path),
        {
            "positive_rating_threshold": POSITIVE_RATING,
            "min_positive_support": MIN_POSITIVE_SUPPORT,
            "max_recommendations": MAX_RECOMMENDATIONS,
            "ordering": "positive_support desc, mean_rating desc, movie_id asc",
        }, files))

    # Binary user-item matrix over positive feedback.  Item-item co-occurrence
    # is X.T @ X; cosine normalises by both item supports.
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
        indices = row.indices
        counts = row.data
        mask = (indices != item_position) & (counts >= MIN_COOCCURRENCE)
        if not mask.any():
            continue
        indices = indices[mask]
        counts = counts[mask]
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

    itemcf_dir = ROOT / "models" / "business" / "itemcf-v1"
    itemcf_dir.mkdir(parents=True, exist_ok=True)
    itemcf_path = itemcf_dir / "item-similarities.json"
    write_json(itemcf_path, {
        "model_version": ITEMCF_VERSION,
        "positive_rating_threshold": POSITIVE_RATING,
        "min_item_support": MIN_ITEM_SUPPORT,
        "min_cooccurrence": MIN_COOCCURRENCE,
        "max_neighbors": MAX_NEIGHBORS,
        "items": items,
    }, compact=True)
    write_json(itemcf_dir / "manifest.json", manifest(
        "itemcf", ITEMCF_VERSION, "item-similarities.json", sha256(itemcf_path),
        {
            "similarity": "cosine over binary positive interactions",
            "positive_rating_threshold": POSITIVE_RATING,
            "min_item_support": MIN_ITEM_SUPPORT,
            "min_cooccurrence": MIN_COOCCURRENCE,
            "max_neighbors": MAX_NEIGHBORS,
            "max_recommendations": MAX_RECOMMENDATIONS,
        }, files))

    cold_dir = ROOT / "models" / "business" / "cold-start-v1"
    cold_dir.mkdir(parents=True, exist_ok=True)
    cold_path = cold_dir / "cold-start.json"
    write_json(cold_path, {
        "model_version": COLD_START_VERSION,
        "strategy": "popular_without_positive_history_then_content",
        "positive_rating_threshold": POSITIVE_RATING,
        "max_recommendations": MAX_RECOMMENDATIONS,
        "popular_model_version": POPULAR_VERSION,
        "content_model_version": "content-genre-tfidf-v1",
        "strategy_note": "No positive history -> popular; otherwise genre content profile.",
    }, compact=True)
    write_json(cold_dir / "manifest.json", manifest(
        "cold_start", COLD_START_VERSION, "cold-start.json", sha256(cold_path),
        {
            "strategy": "popular_without_positive_history_then_content",
            "positive_rating_threshold": POSITIVE_RATING,
            "max_recommendations": MAX_RECOMMENDATIONS,
        }, files))

    print(f"catalog: {len(catalog)}")
    print(f"positive interactions: {len(positive)}")
    print(f"popular: {popular_path} ({popular_path.stat().st_size} bytes)")
    print(f"itemcf: {itemcf_path} ({itemcf_path.stat().st_size} bytes, {len(items)} items with neighbours)")
    print(f"cold-start: {cold_path} ({cold_path.stat().st_size} bytes)")
    print(f"top popular: {[row['movie_id'] for row in popular_rows[:10]]}")


if __name__ == "__main__":
    main()

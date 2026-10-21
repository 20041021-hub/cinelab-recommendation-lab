"""Build the Lab 1 content model artifact and its sidecar manifest.

The model is intentionally small and deterministic:
- each movie is represented by its genres;
- genre weights are IDF values fitted on the training catalogue;
- the provider normalises each movie vector and uses cosine similarity.

Run from the repository root:
    python scripts/build_content_model.py
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recommender.data import DATASET, FrozenData, MANIFEST_SHA, SCHEMA, SPLIT  # noqa: E402

MODEL_VERSION = "content-genre-tfidf-v1"
OUTPUT_DIR = ROOT / "models" / "business" / "content-v1"
ARTIFACT_NAME = "movie-genres.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    data = FrozenData()
    catalog = data.catalog()
    rows = list(data.rows("movies.csv"))
    if len(rows) != 8459:
        raise SystemExit(f"unexpected movies.csv row count: {len(rows)}")

    genre_sets: dict[str, list[str]] = {}
    document_frequency: Counter[str] = Counter()
    for row in rows:
        movie_id = row["movie_id"]
        if movie_id not in catalog:
            continue
        genres = [
            genre.strip()
            for genre in row["genres"].split("|")
            if genre.strip() and genre.strip() != "(no genres listed)"
        ]
        # Keep one copy of each genre per movie; the source data is already clean,
        # but set() makes the feature representation robust to duplicate tags.
        unique = sorted(set(genres))
        genre_sets[movie_id] = unique
        document_frequency.update(unique)

    vocabulary = sorted(document_frequency)
    if not vocabulary:
        raise SystemExit("no genres found in movies.csv")

    movie_count = len(genre_sets)
    idf = {
        genre: round(math.log((1 + movie_count) / (1 + document_frequency[genre])) + 1.0, 8)
        for genre in vocabulary
    }

    artifact = {
        "model_version": MODEL_VERSION,
        "representation": "genre_tfidf",
        "similarity": "cosine",
        "genre_vocabulary": vocabulary,
        "idf": idf,
        "movie_genres": {movie_id: genre_sets[movie_id] for movie_id in sorted(genre_sets, key=int)},
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = OUTPUT_DIR / ARTIFACT_NAME
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    files = data.manifest["files"]
    manifest = {
        "algorithm": "content",
        "model_version": MODEL_VERSION,
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
        "artifacts": {ARTIFACT_NAME: sha256(artifact_path)},
        "parameters": {
            "representation": "genre_tfidf",
            "similarity": "cosine",
            "positive_rating_threshold": 4.0,
            "rating_weight": "max(rating - 3.0, 0.1)",
            "max_recommendations": 10,
            "exclude_rated": True,
            "exclude_favorites": True,
        },
        "tool_versions": {
            "python": platform.python_version(),
            "generator": "scripts/build_content_model.py",
        },
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"movies: {movie_count}")
    print(f"genres: {len(vocabulary)} -> {', '.join(vocabulary)}")
    print(f"artifact: {artifact_path} ({artifact_path.stat().st_size} bytes)")
    print(f"manifest: {manifest_path}")
    print(f"artifact sha256: {manifest['artifacts'][ARTIFACT_NAME]}")


if __name__ == "__main__":
    main()

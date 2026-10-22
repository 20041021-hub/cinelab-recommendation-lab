"""Train the Lab 3 ALS factors with Spark MLlib (explicit ratings).

- ``--mode evaluation``: fits on ``evaluation/train.csv`` only, tunes
  ``rank``/``regParam`` on the frozen validation partition (Precision@10 macro,
  Recall@10 as tie-break) and writes ``models/evaluation/als-v1``.
- ``--mode business``: fits the same architecture on the sealed
  ``business/history_ratings.csv`` snapshot and writes ``models/business/als-v1``
  for the FastAPI process.  Reuse the tuned parameters from the evaluation
  manifest with ``--reuse-tuned``.

Both splits produce ``als-factors.json`` (user factors keyed by canonical ml
user id, item factors keyed by movie id) and a real ``manifest.json`` whose
artifact SHA-256 is computed from the written file.

Run from the repository root, for example:

    python scripts/build_lab3_als.py --mode evaluation
    python scripts/build_lab3_als.py --mode business --reuse-tuned

Spark needs a JDK 11/17; the script picks ``JAVA_HOME`` or the Microsoft JDK
11 install found on this machine.  Nothing is written into the package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recommender.data import DATASET, FrozenData, MANIFEST_SHA, SCHEMA, SPLIT, require  # noqa: E402

POSITIVE_RATING = 4.0
SEED = 20261022
TUNE_MAX_ITER = 12
FINAL_MAX_ITER = 15
TUNE_RANKS = (8, 16, 32)
TUNE_REGS = (0.05, 0.1, 0.2)
# Candidate support floors tried after rank/regParam; 0 keeps the whole catalog.
TUNE_SUPPORT_FLOORS = (0, 100, 200, 300, 500)
BUSINESS_VERSION = "als-mllib-v1"
EVALUATION_VERSION = "als-mllib-eval-v1"
DEFAULT_JAVA_HOME = r"C:\Program Files\Microsoft\jdk-11.0.16.101-hotspot"


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


def configure_environment(java_home: str | None) -> str:
    # Prefer the documented JDK 11/17 over whatever JAVA_HOME the machine
    # happens to export (this host carries an unrelated JDK 8 in %JAVA_HOME%).
    if java_home is None:
        java_home = DEFAULT_JAVA_HOME if Path(DEFAULT_JAVA_HOME).is_dir() else os.environ.get("JAVA_HOME")
    require(java_home and Path(java_home).is_dir(), f"JAVA_HOME不存在：{java_home}", "ENVIRONMENT_INVALID")
    os.environ["JAVA_HOME"] = java_home
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    spark_tmp = ROOT / "artifacts" / "spark-local"
    spark_tmp.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SPARK_LOCAL_DIRS", str(spark_tmp))
    return java_home


def java_version(java_home: str) -> str:
    try:
        completed = subprocess.run([str(Path(java_home) / "bin" / "java"), "-version"],
                                   capture_output=True, text=True, timeout=60)
        first = (completed.stderr or completed.stdout).strip().splitlines()[0]
        return first.strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def load_ratings(mode: str) -> tuple[pd.DataFrame, FrozenData, dict[str, dict]]:
    data = FrozenData()
    name = "evaluation/train.csv" if mode == "evaluation" else "business/history_ratings.csv"
    path = data.package / name
    expected = data.manifest["files"][name]
    require(path.is_file(), f"评分文件缺失：{name}", "DATA_IDENTITY_INVALID")
    require(path.stat().st_size == expected["bytes"] and sha256(path) == expected["sha256"],
            f"评分文件摘要不符：{name}", "DATA_IDENTITY_INVALID")
    frame = pd.read_csv(path, dtype={"userId": str, "movieId": str, "rating": float})
    catalog = data.catalog()
    frame = frame[frame["movieId"].isin(catalog)].copy()
    users = sorted(frame["userId"].unique().tolist(), key=int)
    items = sorted(catalog, key=int)
    user_index = {user: position for position, user in enumerate(users)}
    item_index = {movie: position for position, movie in enumerate(items)}
    frame["user_index"] = frame["userId"].map(user_index).astype("int32")
    frame["item_index"] = frame["movieId"].map(item_index).astype("int32")
    index = {"users": users, "items": items, "user_index": user_index, "item_index": item_index}
    return frame, data, index


def start_spark(app_name: str):
    from pyspark.sql import SparkSession
    artifacts = ROOT / "artifacts"
    return (SparkSession.builder
            .appName(app_name)
            .master("local[4]")
            .config("spark.driver.memory", "3g")
            .config("spark.driver.host", "127.0.0.1")
            .config("spark.sql.shuffle.partitions", "8")
            .config("spark.default.parallelism", "8")
            .config("spark.ui.enabled", "false")
            .config("spark.local.dir", str(artifacts / "spark-local"))
            .config("spark.sql.warehouse.dir", str(artifacts / "spark-warehouse"))
            .getOrCreate())


def train_once(spark, frame: pd.DataFrame, rank: int, reg: float, max_iter: int) -> tuple[dict, dict]:
    from pyspark.ml.recommendation import ALS
    from pyspark.sql.types import FloatType, IntegerType, StructField, StructType
    schema = StructType([
        StructField("user_index", IntegerType(), False),
        StructField("item_index", IntegerType(), False),
        StructField("rating", FloatType(), False),
    ])
    rows = list(zip(frame["user_index"].tolist(), frame["item_index"].tolist(),
                    frame["rating"].astype("float32").tolist()))
    sdf = spark.createDataFrame(rows, schema=schema).repartition(8)
    als = ALS(rank=rank, regParam=reg, maxIter=max_iter, seed=SEED,
              implicitPrefs=False, coldStartStrategy="drop", nonnegative=False,
              userCol="user_index", itemCol="item_index", ratingCol="rating")
    model = als.fit(sdf)
    users = {int(row["id"]): [round(float(value), 6) for value in row["features"]]
             for row in model.userFactors.collect()}
    items = {int(row["id"]): [round(float(value), 6) for value in row["features"]]
             for row in model.itemFactors.collect()}
    return users, items


def validation_partition(data: FrozenData) -> tuple[list[str], dict[str, set[str]], dict[str, set[str]]]:
    """Eligible validation users, per-user train exclusions and relevant labels."""
    from recommender.evaluation import eligible_users
    users = sorted(eligible_users(data, "validation"), key=int)
    train_rows = list(data.training())
    exclusions: dict[str, set[str]] = {user: set() for user in users}
    for row in train_rows:
        if row["userId"] in exclusions:
            exclusions[row["userId"]].add(row["movieId"])
    relevant: dict[str, set[str]] = {user: set() for user in users}
    for row in data.rows("evaluation/validation.csv", evaluation=True):
        if row["userId"] in relevant and float(row["rating"]) >= POSITIVE_RATING:
            relevant[row["userId"]].add(row["movieId"])
    # Officially only labels inside the candidate set count (catalog minus train).
    catalog = data.catalog()
    for user in users:
        relevant[user] = {movie for movie in relevant[user]
                          if movie in catalog and movie not in exclusions[user]}
        require(relevant[user], f"有效validation用户缺少候选相关目标：{user}", "EVALUATION_INPUT_INVALID")
    return users, exclusions, relevant


def macro_metrics(user_factors: np.ndarray, item_factors: np.ndarray, users: list[str],
                  index: dict, exclusions: dict[str, set[str]],
                  relevant: dict[str, set[str]], movie_ids: list[int],
                  movie_positions: dict[str, int], support: np.ndarray | None = None,
                  floor: int = 0) -> tuple[float, float]:
    """Precision@10 / Recall@10 macro-averaged exactly like the offline runner."""
    user_position = index["user_index"]
    precision_total = recall_total = 0.0
    order = np.arange(len(movie_ids))
    for user in users:
        position = user_position.get(user)
        if position is None:
            continue
        scores = np.round(item_factors @ user_factors[position], 6)
        if support is not None and floor > 0:
            # Long-tail guardrail: items without enough fit-file support cannot
            # be ranked, so their unstable scores never reach the Top-10.
            scores[support < floor] = -np.inf
        for movie in exclusions[user]:
            scores[movie_positions[movie]] = -np.inf
        ranked = order[np.lexsort((np.asarray(movie_ids), -scores))][:10]
        hits = sum(1 for position in ranked if index["items"][int(position)] in relevant[user])
        # relevant labels are already candidates (catalog minus train) by construction
        precision_total += hits / 10
        recall_total += hits / len(relevant[user]) if relevant[user] else 0.0
    return precision_total / len(users), recall_total / len(users)


def build_manifest(mode: str, version: str, artifact: Path, parameters: dict,
                   tool_versions: dict, files: dict) -> dict:
    training = "evaluation/train.csv" if mode == "evaluation" else "business/history_ratings.csv"
    return {
        "algorithm": "als",
        "model_version": version,
        "runtime_mode": mode,
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "schema_version": SCHEMA,
        "manifest_sha256": MANIFEST_SHA,
        "training_files": {training: files[training]["sha256"]},
        "feature_files": {
            "movies.csv": files["movies.csv"]["sha256"],
            "training-catalog.csv": files["training-catalog.csv"]["sha256"],
        },
        "artifacts": {"als-factors.json": sha256(artifact)},
        "parameters": parameters,
        "tool_versions": tool_versions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["business", "evaluation"], required=True)
    parser.add_argument("--java-home", default=None)
    parser.add_argument("--rank", type=int, default=None)
    parser.add_argument("--reg", type=float, default=None)
    parser.add_argument("--max-iter", type=int, default=FINAL_MAX_ITER)
    parser.add_argument("--reuse-tuned", action="store_true",
                        help="reuse rank/regParam recorded in the evaluation manifest")
    parser.add_argument("--skip-tune", action="store_true")
    args = parser.parse_args()

    java_home = configure_environment(args.java_home)
    frame, data, index = load_ratings(args.mode)
    support_counts = frame["movieId"].value_counts()
    support_array = np.array([int(support_counts.get(movie, 0)) for movie in index["items"]])
    print(f"mode={args.mode} rows={len(frame)} users={len(index['users'])} items={len(index['items'])}")
    print(f"JAVA_HOME={java_home} ({java_version(java_home)})")

    evaluation_manifest = ROOT / "models" / "evaluation" / "als-v1" / "manifest.json"
    reuse = args.reuse_tuned or (args.mode == "business" and args.rank is None
                                and evaluation_manifest.is_file())
    support_floor = 0
    if reuse:
        tuned = json.loads(evaluation_manifest.read_text(encoding="utf-8"))["parameters"]
        rank, reg = int(tuned["rank"]), float(tuned["regParam"])
        support_floor = int(tuned.get("candidate_support_floor", 0))
        print(f"reusing tuned parameters: rank={rank} regParam={reg} support_floor={support_floor}")
    else:
        rank = args.rank or 16
        reg = args.reg if args.reg is not None else 0.1

    spark = start_spark(f"cinelab-als-{args.mode}")
    tuning_report: list[dict] = []
    floor_report: list[dict] = []
    started = time.time()
    try:
        if args.mode == "evaluation" and not args.skip_tune:
            users, exclusions, relevant = validation_partition(data)
            movie_ids = [int(movie) for movie in index["items"]]
            movie_positions = {movie: position for position, movie in enumerate(index["items"])}
            for candidate_rank in TUNE_RANKS:
                for candidate_reg in TUNE_REGS:
                    comb_started = time.time()
                    raw_users, raw_items = train_once(spark, frame, candidate_rank, candidate_reg, TUNE_MAX_ITER)
                    user_factors = np.zeros((len(index["users"]), candidate_rank))
                    item_factors = np.zeros((len(index["items"]), candidate_rank))
                    for position, factor in raw_users.items():
                        user_factors[position] = factor
                    for position, factor in raw_items.items():
                        item_factors[position] = factor
                    require(len(raw_users) == len(index["users"]) and len(raw_items) == len(index["items"]),
                            "ALS因子缺失：拟合结果没有覆盖全部用户/电影", "MODEL_BINDING_INVALID")
                    precision, recall = macro_metrics(user_factors, item_factors, users, index,
                                                      exclusions, relevant, movie_ids, movie_positions,
                                                      support_array, 0)
                    tuning_report.append({
                        "rank": candidate_rank, "regParam": candidate_reg, "maxIter": TUNE_MAX_ITER,
                        "precision_at_10": round(precision, 6), "recall_at_10": round(recall, 6),
                        "seconds": round(time.time() - comb_started, 1),
                    })
                    print(f"tune rank={candidate_rank} reg={candidate_reg} "
                          f"P@10={precision:.6f} R@10={recall:.6f} ({tuning_report[-1]['seconds']}s)")
            best = max(tuning_report, key=lambda row: (row["precision_at_10"], row["recall_at_10"],
                                                       -row["regParam"], -row["rank"]))
            rank, reg = best["rank"], best["regParam"]
            print(f"selected rank={rank} regParam={reg}")

            # Second stage: the candidate support floor, with rank/regParam fixed.
            floor_started = time.time()
            raw_users, raw_items = train_once(spark, frame, rank, reg, TUNE_MAX_ITER)
            user_factors = np.zeros((len(index["users"]), rank))
            item_factors = np.zeros((len(index["items"]), rank))
            for position, factor in raw_users.items():
                user_factors[position] = factor
            for position, factor in raw_items.items():
                item_factors[position] = factor
            require(len(raw_users) == len(index["users"]) and len(raw_items) == len(index["items"]),
                    "ALS因子缺失：拟合结果没有覆盖全部用户/电影", "MODEL_BINDING_INVALID")
            for candidate_floor in TUNE_SUPPORT_FLOORS:
                precision, recall = macro_metrics(user_factors, item_factors, users, index,
                                                  exclusions, relevant, movie_ids, movie_positions,
                                                  support_array, candidate_floor)
                floor_report.append({
                    "support_floor": candidate_floor, "maxIter": TUNE_MAX_ITER,
                    "candidates": int((support_array >= candidate_floor).sum()),
                    "precision_at_10": round(precision, 6), "recall_at_10": round(recall, 6),
                })
                print(f"tune floor={candidate_floor} candidates={floor_report[-1]['candidates']} "
                      f"P@10={precision:.6f} R@10={recall:.6f}")
            best_floor = max(floor_report, key=lambda row: (row["precision_at_10"], row["recall_at_10"],
                                                            -row["support_floor"]))
            support_floor = int(best_floor["support_floor"])
            print(f"selected support floor={support_floor} "
                  f"(stage {round(time.time() - floor_started, 1)}s)")

        raw_users, raw_items = train_once(spark, frame, rank, reg, args.max_iter)
        users_out = {index["users"][position]: factor for position, factor in raw_users.items()}
        items_out = {index["items"][position]: factor for position, factor in raw_items.items()}
        final_metrics: dict | None = None
        if args.mode == "evaluation":
            users, exclusions, relevant = validation_partition(data)
            user_factors = np.zeros((len(index["users"]), rank))
            item_factors = np.zeros((len(index["items"]), rank))
            for position, factor in raw_users.items():
                user_factors[position] = factor
            for position, factor in raw_items.items():
                item_factors[position] = factor
            precision, recall = macro_metrics(
                user_factors, item_factors, users, index, exclusions, relevant,
                [int(movie) for movie in index["items"]],
                {movie: position for position, movie in enumerate(index["items"])},
                support_array, support_floor)
            final_metrics = {"partition": "validation", "maxIter": args.max_iter,
                             "support_floor": support_floor,
                             "precision_at_10": round(precision, 6), "recall_at_10": round(recall, 6)}
            print(f"final validation P@10={precision:.6f} R@10={recall:.6f}")
    finally:
        spark.stop()

    version = EVALUATION_VERSION if args.mode == "evaluation" else BUSINESS_VERSION
    target_dir = ROOT / "models" / ("evaluation" if args.mode == "evaluation" else "business") / "als-v1"
    target_dir.mkdir(parents=True, exist_ok=True)
    artifact = target_dir / "als-factors.json"
    write_json(artifact, {
        "model_version": version,
        "rank": rank,
        "positive_rating_threshold": POSITIVE_RATING,
        "fold_in_floor": 0.5,
        "fit": {
            "file": "evaluation/train.csv" if args.mode == "evaluation" else "business/history_ratings.csv",
            "rows": int(len(frame)),
            "users": len(users_out),
            "items": len(items_out),
        },
        "users": users_out,
        "items": items_out,
        "candidate_support_floor": support_floor,
        "item_support": {movie: int(support_counts.get(movie, 0)) for movie in index["items"]},
    }, compact=True)

    parameters = {
        "rank": rank,
        "regParam": reg,
        "maxIter": args.max_iter,
        "seed": SEED,
        "implicitPrefs": False,
        "coldStartStrategy": "drop",
        "positive_rating_threshold": POSITIVE_RATING,
        "candidate_support_floor": support_floor,
        "candidate_support_rule": (
            "item_support counted inside the fit file itself; items below the floor "
            "never enter the Top-10 shortlist"
        ),
        "fold_in": "rating-weighted mean of item factors for users absent from the fit",
        "tuning": {
            "grid": [{"rank": r, "regParam": g, "maxIter": TUNE_MAX_ITER}
                     for r in TUNE_RANKS for g in TUNE_REGS],
            "partition": "validation",
            "selection": "macro Precision@10, tie-break Recall@10, then smaller regParam/rank",
            "report": tuning_report,
            "selected_rank": rank,
            "selected_regParam": reg,
            "support_floor_grid": list(TUNE_SUPPORT_FLOORS),
            "support_floor_report": floor_report,
            "selected_support_floor": support_floor,
        } if tuning_report else {"note": "tuning performed in evaluation mode; parameters reused"},
    }
    if final_metrics:
        parameters["final_model_validation_metrics"] = final_metrics
    tool_versions = {
        "python": platform.python_version(),
        "pyspark": __import__("pyspark").__version__,
        "java": java_version(java_home),
        "spark_master": "local[4]",
        "driver_memory": "3g",
        "generator": "scripts/build_lab3_als.py",
    }
    manifest_path = target_dir / "manifest.json"
    manifest = build_manifest(args.mode, version, artifact, parameters, tool_versions,
                              data.manifest["files"])
    require(json.dumps(manifest, ensure_ascii=False).isascii(),
            "模型清单只能包含ASCII字符：评审端按系统locale读取清单，非ASCII会解码失败",
            "MODEL_BINDING_INVALID")
    write_json(manifest_path, manifest)
    print(f"wrote {artifact} ({artifact.stat().st_size} bytes, users={len(users_out)}, items={len(items_out)})")
    print(f"wrote {manifest_path}")
    print(f"total seconds: {round(time.time() - started, 1)}")


if __name__ == "__main__":
    main()
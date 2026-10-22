"""Run the frozen Lab 3 offline evaluation and export real predictions/metrics.

For every requested algorithm and partition this script

1. binds the evaluation manifests through ``recommender.student.evaluation_entry``;
2. asks each provider for the Top-10 of every fixed eligible user, passing the
   partition in ``batch_id`` so the evaluation history adapter knows which
   frozen file is the user's legitimate history;
3. validates every DTO against the partition's candidate set with the existing
   ``check_result`` contract;
4. writes ``<out>/<algorithm>/<partition>.predictions.jsonl`` in the documented
   ``{"ml_user_id", "result"}`` format;
5. computes Precision@10 / Recall@10 (positive: rating >= 4.0, denominator 10 /
   number of candidate-relevant labels) macro-averaged over the fixed users and
   writes ``<out>/<algorithm>/<partition>.evaluation-result.json``;
6. re-validates both files through ``check_predictions`` and
   ``check_evaluation_result``.

Run from the repository root:

    python scripts/run_lab3_evaluation.py --algorithm all --partition all

The raw JSONL lives under ``reports/`` (git-ignored); committing it is neither
required nor useful, the command regenerates it deterministically.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recommender.contracts import Algorithm, RecommendationRequest  # noqa: E402
from recommender.data import DATASET, SCHEMA, SPLIT, FrozenData, require, sha256  # noqa: E402
from recommender.evaluation import check_evaluation_result, check_predictions, eligible_users, task as evaluation_task  # noqa: E402
from recommender.validation import check_model, check_result  # noqa: E402
from recommender.student import evaluation_entry  # noqa: E402,F401  (registers the evaluation PROVIDERS)
from recommender.contracts import PROVIDERS  # noqa: E402
from recommender.student.evaluation_data import exclusion_file_names, query_file_name, use_partition  # noqa: E402

POSITIVE_RATING = 4.0
TOP_K = 10
PARTITIONS = ("validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10")
ALGORITHMS = (Algorithm.popular, Algorithm.content, Algorithm.itemcf, Algorithm.als, Algorithm.cold_start)


def partition_inputs(data: FrozenData, partition: str):
    """Fixed users, per-user candidate exclusions and candidate-relevant labels."""
    users = sorted(eligible_users(data, partition), key=int)
    catalog = data.catalog()
    exclusions = {user: set() for user in users}
    for name in exclusion_file_names(partition):
        for row in data.rows(name, evaluation=True):
            if row["userId"] in exclusions:
                exclusions[row["userId"]].add(row["movieId"])
    relevant = {user: set() for user in users}
    for row in data.rows(query_file_name(partition), evaluation=True):
        if row["userId"] in relevant and float(row["rating"]) >= POSITIVE_RATING:
            relevant[row["userId"]].add(row["movieId"])
    for user in users:
        labels = relevant[user] & (catalog - exclusions[user])
        require(labels, f"有效用户在候选集内没有相关目标：{user}", "EVALUATION_INPUT_INVALID")
        relevant[user] = labels
    return users, exclusions, relevant


def run_partition(algorithm: Algorithm, partition: str, out_dir: Path) -> dict:
    data = FrozenData()
    provider = PROVIDERS[algorithm]
    model_path = Path(provider.model_manifest_path)
    model = check_model(model_path, "evaluation", algorithm.value, data)
    users, exclusions, relevant = partition_inputs(data, partition)

    algorithm_dir = out_dir / algorithm.value
    algorithm_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = algorithm_dir / f"{partition}.predictions.jsonl"
    result_path = algorithm_dir / f"{partition}.evaluation-result.json"

    precision_total = recall_total = 0.0
    empty_users = 0
    started = time.time()
    with use_partition(partition), predictions_path.open("w", encoding="utf-8", newline="\n") as stream:
        for index, user in enumerate(users, 1):
            request = RecommendationRequest("ml", int(user), TOP_K, "evaluation", DATASET, SPLIT)
            checked = check_result(provider.recommend(request), request, algorithm,
                                   data.catalog() - exclusions[user], model.model_version)
            ranked = {item.movie_id for item in checked.items}
            hits = len(ranked & relevant[user])
            precision_total += hits / TOP_K
            recall_total += hits / len(relevant[user])
            empty_users += 1 if not checked.items else 0
            stream.write(json.dumps({"ml_user_id": user, "result": checked.model_dump(mode="json")},
                                    ensure_ascii=False) + "\n")
            if index % 500 == 0:
                print(f"  {algorithm.value}/{partition}: {index}/{len(users)} users "
                      f"({round(time.time() - started, 1)}s)", flush=True)

    task_data = evaluation_task(data, partition)
    precision = round(precision_total / len(users), 6)
    recall = round(recall_total / len(users), 6)
    result_path.write_text(json.dumps({
        "status": "completed",
        "runtime_mode": "evaluation",
        "algorithm": algorithm.value,
        "model_version": model.model_version,
        "task_sha256": task_data["task_sha256"],
        "model_manifest_sha256": sha256(model_path),
        "dataset_version": DATASET,
        "split_version": SPLIT,
        "schema_version": SCHEMA,
        "partition": partition,
        "user_count": len(users),
        "precision_at_10": precision,
        "recall_at_10": recall,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # The course contract re-validates both artefacts; nothing is trusted blindly.
    check_predictions(predictions_path, model_path, partition, data)
    check_evaluation_result(result_path, model_path, data)
    return {
        "algorithm": algorithm.value, "partition": partition, "users": len(users),
        "precision_at_10": precision, "recall_at_10": recall,
        "empty_users": empty_users, "seconds": round(time.time() - started, 1),
        "model_version": model.model_version,
        "predictions": str(predictions_path.relative_to(ROOT)),
        "result": str(result_path.relative_to(ROOT)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", action="append", default=None,
                        choices=[algorithm.value for algorithm in ALGORITHMS] + ["all"])
    parser.add_argument("--partition", action="append", default=None, choices=list(PARTITIONS) + ["all"])
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports" / "lab3")
    args = parser.parse_args()

    algorithms = list(ALGORITHMS) if not args.algorithm or "all" in args.algorithm else \
        [Algorithm(name) for name in args.algorithm]
    partitions = list(PARTITIONS) if not args.partition or "all" in args.partition else args.partition

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for algorithm in algorithms:
        for partition in partitions:
            print(f"running {algorithm.value}/{partition} ...", flush=True)
            row = run_partition(algorithm, partition, out_dir)
            summary.append(row)
            print(f"  P@10={row['precision_at_10']:.6f} R@10={row['recall_at_10']:.6f} "
                  f"users={row['users']} empty={row['empty_users']} ({row['seconds']}s)", flush=True)

    summary_path = out_dir / "summary.json"
    merged: dict[tuple[str, str], dict] = {}
    if summary_path.exists():
        for row in json.loads(summary_path.read_text(encoding="utf-8")):
            merged[(row["algorithm"], row["partition"])] = row
    for row in summary:
        merged[(row["algorithm"], row["partition"])] = row
    order = {partition: index for index, partition in enumerate(PARTITIONS)}
    summary = sorted(merged.values(), key=lambda row: (order[row["partition"]], row["algorithm"]))
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {summary_path}")
    header = f"| {'algorithm':<10} | {'partition':<9} | {'users':>5} | {'P@10':>8} | {'R@10':>8} |"
    print(header)
    print("|" + "-" * 12 + "|" + "-" * 11 + "|" + "-" * 7 + "|" + "-" * 10 + "|" + "-" * 10 + "|")
    for row in summary:
        print(f"| {row['algorithm']:<10} | {row['partition']:<9} | {row['users']:>5} | "
              f"{row['precision_at_10']:>8.6f} | {row['recall_at_10']:>8.6f} |")


if __name__ == "__main__":
    main()
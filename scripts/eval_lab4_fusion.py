"""实验四 S1：加权 RRF 的权重对照实验（扩展分析，不改变课程冻结的四算法评价表）。

做法与课程离线评价完全同口径：

* 只使用冻结的 evaluation/train 拟合出来的评价模型（models/evaluation/*）；
* 用户集合、候选排除、相关标签、P@10/R@10 分母都复用
  ``scripts/run_lab3_evaluation.py`` 的 ``partition_inputs``；
* 每一路候选都先过 ``check_result`` 契约校验，证明融合用的是真实合法候选；
* 同一用户的各路名次只算一次，随后在内存里对不同权重组合做融合，
  因此比较的是"同一批候选 + 不同权重"，而不是不同候选池。

输出 reports/lab4/fusion-tuning.json，并打印一张对照表。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from recommender.contracts import Algorithm, RecommendationItem, RecommendationRequest, RecommendationResult, PROVIDERS  # noqa: E402
from recommender.data import DATASET, SPLIT, FrozenData  # noqa: E402
from recommender.student import evaluation_entry  # noqa: E402,F401  (registers evaluation PROVIDERS)
from recommender.student import rrf  # noqa: E402
from recommender.student.evaluation_data import use_partition  # noqa: E402
from recommender.validation import check_model, check_result  # noqa: E402
import run_lab3_evaluation as lab3  # noqa: E402

LIMIT = 10

# 权重网格：以行为共现（itemcf）与隐向量（als）两路协同为主，另留内容/热门的对照。
# 单路基线只用于核对，不参与“多路融合”的选优（见 main 里的选优规则）。
GRID: "dict[str, dict[str, float]]" = {
    "itemcf1-als1": {"itemcf": 1.0, "als": 1.0},
    "itemcf2-als1": {"itemcf": 2.0, "als": 1.0},
    "itemcf3-als1": {"itemcf": 3.0, "als": 1.0},
    "itemcf4-als1": {"itemcf": 4.0, "als": 1.0},
    "itemcf6-als1": {"itemcf": 6.0, "als": 1.0},
    "itemcf8-als1": {"itemcf": 8.0, "als": 1.0},
    "itemcf2-als2": {"itemcf": 2.0, "als": 2.0},
    "itemcf4-als2": {"itemcf": 4.0, "als": 2.0},
    "itemcf6-als2": {"itemcf": 6.0, "als": 2.0},
    "itemcf4-als1-popular1": {"itemcf": 4.0, "als": 1.0, "popular": 1.0},
    "itemcf4-als1-content1": {"itemcf": 4.0, "als": 1.0, "content": 1.0},
    "itemcf4-als2-content1": {"itemcf": 4.0, "als": 2.0, "content": 1.0},
    "itemcf4-als2-popular1": {"itemcf": 4.0, "als": 2.0, "popular": 1.0},
    "itemcf6-als2-popular1": {"itemcf": 6.0, "als": 2.0, "popular": 1.0},
    "content1-itemcf2-als2": {"content": 1.0, "itemcf": 2.0, "als": 2.0},
    "content1-itemcf4": {"content": 1.0, "itemcf": 4.0},
    "content1-itemcf1-popular1": {"content": 1.0, "itemcf": 1.0, "popular": 1.0},
    "popular1-content1-itemcf1-als1": {"content": 1.0, "itemcf": 1.0, "popular": 1.0, "als": 1.0},
    "itemcf4": {"itemcf": 4.0},  # 单路基线（不参与选优）
}


def load_baselines(partition: str) -> dict[str, dict]:
    path = ROOT / "reports" / "lab3" / "summary.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["algorithm"]: row for row in rows if row["partition"] == partition}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition", default="validation", choices=["validation", "test"])
    parser.add_argument("--depth", type=int, default=30, help="每路候选召回深度（1—50）")
    parser.add_argument("--users", type=int, default=0, help="只跑前 N 个用户（0 表示全部）")
    parser.add_argument("--configs", default="", help="只评估这些权重配置（逗号分隔，默认全部）")
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "lab4" / "fusion-tuning.json")
    args = parser.parse_args()

    grid = GRID
    if args.configs.strip():
        wanted = [name.strip() for name in args.configs.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in GRID]
        if unknown:
            raise SystemExit(f"未知配置：{unknown}")
        grid = {name: GRID[name] for name in wanted}
    algorithms = sorted({name for weights in grid.values() for name in weights},
                        key=lambda name: [algorithm.value for algorithm in Algorithm].index(name))
    data = FrozenData()
    models = {name: check_model(Path(PROVIDERS[Algorithm(name)].model_manifest_path), "evaluation", name, data)
              for name in algorithms}
    users, exclusions, relevant = lab3.partition_inputs(data, args.partition)
    if args.users:
        users = users[:args.users]
    catalog = data.catalog()
    print(f"partition={args.partition} users={len(users)} sources={algorithms} depth={args.depth}", flush=True)

    # 一次遍历：取出每路契约校验后的真实候选（名次就是 provider 给出的 rank）。
    rankings: dict[str, dict[str, object]] = {}

    def new_bucket() -> dict[str, object]:
        return {name: None for name in algorithms}

    started = time.time()
    with use_partition(args.partition):
        for index, user in enumerate(users, 1):
            bucket = rankings.setdefault(user, new_bucket())
            request = RecommendationRequest("ml", int(user), args.depth, "evaluation", DATASET, SPLIT)
            for name in algorithms:
                checked = check_result(PROVIDERS[Algorithm(name)].recommend(request), request,
                                       Algorithm(name), catalog - exclusions[user], models[name].model_version)
                bucket[name] = [(item.movie_id, item.rank) for item in checked.items]
                if index == 1:
                    print(f"  sample {name}: depth={len(checked.items)} top={bucket[name][:3]}", flush=True)
            if index % 250 == 0:
                print(f"  candidates {index}/{len(users)} users ({round(time.time() - started, 1)}s)", flush=True)
    print(f"candidate pass finished in {round(time.time() - started, 1)}s", flush=True)

    # 在内存里对不同权重做融合；分母与课程一致（P@10 用 10，R@10 用该用户候选内相关数）。
    rows = []
    for config, weights in grid.items():
        precision_total = recall_total = 0.0
        empty_users = 0
        multi_source = 0
        delivered_total = 0
        coverage: set[str] = set()
        for user in users:
            bucket = rankings[user]
            results = {}
            for name in weights:
                items = bucket.get(name) or []
                results[name] = RecommendationResult(
                    status="ready", algorithm=Algorithm(name), model_version=models[name].model_version,
                    dataset_version=DATASET, split_version=SPLIT, runtime_mode="evaluation",
                    batch_id=args.partition, generated_at="1970-01-01T00:00:00+00:00",
                    items=[RecommendationItem(movie_id=movie_id, rank=rank, algorithm=Algorithm(name),
                                              model_version=models[name].model_version, score=None)
                           for movie_id, rank in items])
            outcome = rrf.fuse(results, weights, LIMIT, exclusions[user], catalog)
            delivered = {item.movie_id for item in outcome.items}
            hits = len(delivered & relevant[user])
            precision_total += hits / LIMIT
            recall_total += hits / len(relevant[user])
            empty_users += 1 if not delivered else 0
            delivered_total += len(delivered)
            coverage |= delivered
            multi_source += sum(1 for item in outcome.items if len(item.sources) > 1)
        rows.append({
            "config": config,
            "weights": weights,
            "users": len(users),
            "precision_at_10": round(precision_total / len(users), 6),
            "recall_at_10": round(recall_total / len(users), 6),
            "empty_users": empty_users,
            "multi_source_items": multi_source,
            "delivered_items": delivered_total,
            "catalog_coverage": len(coverage),
        })
        row = rows[-1]
        print(f"  {config:<34} P@10={row['precision_at_10']:.6f} R@10={row['recall_at_10']:.6f} "
              f"empty={row['empty_users']} multi={row['multi_source_items']}", flush=True)

    multi_rows = [row for row in rows if len(row["weights"]) > 1]
    best = max(multi_rows, key=lambda row: (row["precision_at_10"], row["recall_at_10"]))
    best_single = max((row for row in rows if len(row["weights"]) == 1),
                      key=lambda row: (row["precision_at_10"], row["recall_at_10"]), default=None)
    payload = {
        "task": "lab4-weighted-rrf-weight-study",
        "note": "扩展分析：权重在 validation 上比较，只为选择实验四五的默认权重；课程冻结的四算法评价表不变",
        "partition": args.partition,
        "requested_configs": args.configs or None,
        "user_count": len(users),
        "depth": args.depth,
        "limit": LIMIT,
        "rrf_k": rrf.RRF_K,
        "positive_threshold": "4.0",
        "candidate_rule": "catalog-minus-train（与课程 check_predictions 的排除规则一致）",
        "selection_rule": "在 validation 上按 P@10（并列看 R@10）选多路配置；单路基线只作参照",
        "best_config": best["config"],
        "best_multi_source": best,
        "best_single_source": best_single,
        "baselines_single_algorithm": load_baselines(args.partition),
        "grid": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nbest_multi={best['config']} P@10={best['precision_at_10']:.6f} R@10={best['recall_at_10']:.6f}")
    if best_single:
        print(f"best_single={best_single['config']} P@10={best_single['precision_at_10']:.6f} "
              f"R@10={best_single['recall_at_10']:.6f}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
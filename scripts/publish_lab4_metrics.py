"""把实验四 S1 的权重对照实验结果发布成前端数据与文档表格。

输入：
* ``reports/lab4/fusion-tuning.json``：validation 上的权重对照实验（选优依据）。
* ``reports/lab4/fusion-test.json``：方案冻结后在 test 上的单次报告（可选）。

输出：
* ``frontend/src/data/lab4-fusion.json``：融合面板与实验页读取的构建期数据。
* ``docs/lab4-fusion-table.md``：可直接贴进实验报告的对照表。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TUNING = ROOT / "reports" / "lab4" / "fusion-tuning.json"
TEST = ROOT / "reports" / "lab4" / "fusion-test.json"
FRONTEND = ROOT / "frontend" / "src" / "data" / "lab4-fusion.json"
DOC = ROOT / "docs" / "lab4-fusion-table.md"

BASELINE_ORDER = ("popular", "content", "itemcf", "als", "cold_start")
BASELINE_LABEL = {"popular": "Popular（热门）", "content": "Content（内容）", "itemcf": "ItemCF",
                  "als": "Spark ALS", "cold_start": "ColdStart（冷启动）"}


def config_label(weights: dict[str, float]) -> str:
    names = {"popular": "Popular", "content": "Content", "itemcf": "ItemCF", "als": "ALS", "cold_start": "ColdStart"}
    return " + ".join(f"{names.get(name, name)} ×{weight:g}" for name, weight in weights.items())


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"缺少 {path}；请先运行 scripts/eval_lab4_fusion.py")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    tuning = load(TUNING)
    best = tuning["best_multi_source"]
    rows = sorted(tuning["grid"], key=lambda row: (-row["precision_at_10"], -row["recall_at_10"]))
    baselines = {name: tuning["baselines_single_algorithm"].get(name) for name in BASELINE_ORDER}
    baselines = {name: row for name, row in baselines.items() if row}
    test = None
    if TEST.exists():
        payload = load(TEST)
        target = tuning["best_config"]
        match = next((row for row in payload["grid"] if row["config"] == target), None)
        if match:
            test = {**match, "partition": payload["partition"], "label": config_label(match["weights"]),
                    "user_count": payload["user_count"]}

    selected = {
        "config": best["config"],
        "weight": best["config"],
        "label": config_label(best["weights"]),
        "sources": list(best["weights"]),
        "k": tuning["rrf_k"] if "rrf_k" in tuning else 60,
        "precision_at_10": best["precision_at_10"],
        "recall_at_10": best["recall_at_10"],
    }
    frontend = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "partition": tuning["partition"],
        "user_count": tuning["user_count"],
        "depth": tuning["depth"],
        "k": 60,
        "selection_rule": tuning["selection_rule"],
        "selected": selected,
        "baselines": {name: {"algorithm": name, "precision_at_10": row["precision_at_10"],
                             "recall_at_10": row["recall_at_10"]} for name, row in baselines.items()},
        "grid": [{"config": row["config"], "label": config_label(row["weights"]), "weights": row["weights"],
                  "precision_at_10": row["precision_at_10"], "recall_at_10": row["recall_at_10"],
                  "empty_users": row["empty_users"], "multi_source_items": row["multi_source_items"]}
                 for row in rows],
        "test": test,
    }
    FRONTEND.parent.mkdir(parents=True, exist_ok=True)
    FRONTEND.write_text(json.dumps(frontend, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 实验四 S1：加权 RRF 权重对照（验证集）",
        "",
        f"- 评价口径：与课程离线评价完全一致（正反馈 ≥ 4.0、Top-10、候选排除该用户在 train 中的评分）。",
        f"- 用户集合：validation 固定评价用户 {tuning['user_count']} 位；每路候选深度 {tuning['depth']}，融合后取 Top-10。",
        f"- 选优规则：{tuning['selection_rule']}",
        f"- 选定配置：**{selected['label']}**（{selected['config']}），RRF k = {frontend['k']}，"
        f"P@10 = {selected['precision_at_10']:.6f}，R@10 = {selected['recall_at_10']:.6f}。",
        "",
        "## 1. 单路基线（来自实验三评价表）",
        "",
        "| 算法 | 模型版本 | Precision@10 | Recall@10 |",
        "| --- | --- | ---: | ---: |",
    ]
    for name, row in baselines.items():
        lines.append(f"| {BASELINE_LABEL.get(name, name)} | `{row.get('model_version') or '—'}` | "
                     f"{row['precision_at_10']:.6f} | {row['recall_at_10']:.6f} |")
    lines += [
        "",
        "## 2. 权重对照（同一批候选，只改权重）",
        "",
        "| # | 权重配置 | 来源数 | Precision@10 | Recall@10 | 多路命中条目 | 空结果用户 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, row in enumerate(rows, 1):
        mark = " ✅" if row["config"] == best["config"] else ""
        lines.append(f"| {index} | {config_label(row['weights'])}{mark} | {len(row['weights'])} | "
                     f"{row['precision_at_10']:.6f} | {row['recall_at_10']:.6f} | "
                     f"{row['multi_source_items']} | {row['empty_users']} |")
    lines += ["", "✅ = 验证集上选定的多路配置（单路配置只作参照，不参与选优）。"]
    if test:
        lines += [
            "",
            "## 3. 测试集单次报告（方案冻结后）",
            "",
            "| 配置 | 分区 | 用户数 | Precision@10 | Recall@10 | 空结果用户 |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
            f"| {test['label']} | {test['partition']} | {test['user_count']} | "
            f"{test['precision_at_10']:.6f} | {test['recall_at_10']:.6f} | {test['empty_users']} |",
            "",
            "> 权重在 validation 上选定并冻结，test 只报告这一次。",
        ]
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {FRONTEND}")
    print(f"wrote {DOC}")


if __name__ == "__main__":
    main()
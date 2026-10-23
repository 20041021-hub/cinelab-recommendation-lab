"""Turn the offline evaluation summary into the artefacts the submission ships.

``scripts/run_lab3_evaluation.py`` writes ``reports/lab3/summary.json`` (ignored
by git).  This script republishes exactly the same numbers as:

- ``frontend/src/data/lab3-metrics.json`` — bundled into the web build so the
  experiment page can render the table without the business API ever touching
  evaluation data;
- ``backend/app/lab3_metrics.json`` — a read-only snapshot the API ships, so the
  lab-6 natural-language assistant can report "which algorithm + how accurate"
  when it calls a recommender.  It is metrics, not a model: no evaluation
  provider or evaluation fit file is ever loaded by the API;
- ``docs/lab3-evaluation-table.md`` — the table quoted in the lab report.

Usage::

    D:\\Python311\\python.exe scripts/publish_lab3_metrics.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "reports" / "lab3" / "summary.json"
METRICS = ROOT / "frontend" / "src" / "data" / "lab3-metrics.json"
API_METRICS = ROOT / "backend" / "app" / "lab3_metrics.json"
DOC_TABLE = ROOT / "docs" / "lab3-evaluation-table.md"

ALGORITHM_ORDER = ("popular", "content", "itemcf", "als", "cold_start")
PARTITIONS = ("validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10")
PARTITION_LABELS = {
    "validation": "验证集",
    "test": "测试集",
    "cold-k0": "冷启动 k=0",
    "cold-k1": "冷启动 k=1",
    "cold-k5": "冷启动 k=5",
    "cold-k10": "冷启动 k=10",
}


def load_rows() -> list[dict]:
    if not SUMMARY.is_file():
        sys.exit(f"missing {SUMMARY}; run scripts/run_lab3_evaluation.py first")
    rows = json.loads(SUMMARY.read_text(encoding="utf-8"))
    if not rows:
        sys.exit(f"{SUMMARY} is empty")
    return rows


def normalize(rows: list[dict]) -> dict[str, list[dict]]:
    # summary.json accumulates across runs: the newest row for a
    # (algorithm, partition) pair wins, so a re-run replaces a stale number.
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        latest[(row["algorithm"], row["partition"])] = row
    grouped: dict[str, list[dict]] = {partition: [] for partition in PARTITIONS}
    for (algorithm, partition), row in latest.items():
        if partition not in grouped:
            continue
        grouped[partition].append({
            "algorithm": algorithm,
            "partition": partition,
            "model_version": row.get("model_version"),
            "users": row["users"],
            "empty": row.get("empty_users", 0),
            "precision_at_10": row["precision_at_10"],
            "recall_at_10": row["recall_at_10"],
        })
    for partition, items in grouped.items():
        items.sort(key=lambda item: ALGORITHM_ORDER.index(item["algorithm"])
                   if item["algorithm"] in ALGORITHM_ORDER else len(ALGORITHM_ORDER))
    return grouped


def markdown_table(rows: list[dict], title: str) -> str:
    lines = [f"### {title}", "",
             "| 算法 | 有效用户 | 空结果 | Precision@10 | Recall@10 | 模型版本 |",
             "| --- | ---: | ---: | ---: | ---: | --- |"]
    for row in rows:
        lines.append(f"| {row['algorithm']} | {row['users']} | {row['empty']} | "
                     f"{row['precision_at_10']:.6f} | {row['recall_at_10']:.6f} | "
                     f"{row['model_version'] or '—'} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = load_rows()
    grouped = normalize(rows)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    payload = {
        "dataset_version": rows[0].get("dataset_version", "rec-course-v3-train5-1.0.0"),
        "split_version": rows[0].get("split_version", "temporal-v1-cold500-train5-v3"),
        "generated_at": generated_at,
        "source": "scripts/run_lab3_evaluation.py",
        "note": ("由 scripts/publish_lab3_metrics.py 从 reports/lab3/summary.json 生成；"
                 "评价在独立进程执行，业务 API 不连接 evaluation 数据库。"),
        "validation": grouped["validation"],
        "test": grouped["test"],
        "cold": [row for partition in ("cold-k0", "cold-k1", "cold-k5", "cold-k10")
                 for row in grouped[partition]],
    }
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {METRICS.relative_to(ROOT)} "
          f"(validation={len(payload['validation'])} test={len(payload['test'])} cold={len(payload['cold'])})")

    # 同一份数字同时给后端一份：实验六助手在调用推荐工具时要回答"用了哪个算法、
    # 准确率多少"。这只是指标快照，API 依旧不加载任何 evaluation 模型或评价 Provider。
    API_METRICS.parent.mkdir(parents=True, exist_ok=True)
    API_METRICS.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {API_METRICS.relative_to(ROOT)}")

    doc = ["# 实验三 · 统一离线评价结果", "",
           f"- 数据版本：`{payload['dataset_version']}`，划分：`{payload['split_version']}`",
           "- 正反馈阈值 4.0（评分 ≥ 4.0），Top-10；宏平均（无结果的用户保留在分母中）",
           "- Precision@10 分母恒为 10；Recall@10 分母为该用户候选集内的相关目标数",
           f"- 生成时间：{generated_at}",
           "- 复现命令：`D:\\Python311\\python.exe scripts\\run_lab3_evaluation.py` "
           "然后 `D:\\Python311\\python.exe scripts\\publish_lab3_metrics.py`", ""]
    if payload["validation"]:
        doc.append(markdown_table(payload["validation"], "验证集 · 调参与方案选择（4,209 位用户）"))
        doc.append("")
    if payload["test"]:
        doc.append(markdown_table(payload["test"], "测试集 · 方案冻结后只报一次（4,198 位用户）"))
        doc.append("")
    cold = payload["cold"]
    if cold:
        lines = ["### 冷启动分区 · 每位用户 499 人", "",
                 "| 分区 | 算法 | 有效用户 | 空结果 | Precision@10 | Recall@10 |",
                 "| --- | --- | ---: | ---: | ---: | ---: |"]
        for row in cold:
            lines.append(f"| {PARTITION_LABELS.get(row['partition'], row['partition'])} | {row['algorithm']} | "
                         f"{row['users']} | {row['empty']} | {row['precision_at_10']:.6f} | "
                         f"{row['recall_at_10']:.6f} |")
        doc.append("\n".join(lines) + "\n")
    DOC_TABLE.write_text("\n".join(doc), encoding="utf-8")
    print(f"wrote {DOC_TABLE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
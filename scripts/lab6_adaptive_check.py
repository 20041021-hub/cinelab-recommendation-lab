"""实验六创新点 A：兴趣自适应融合权重的真实对照实验。

在同一账号、同一时刻、同一批候选上只切换 ``adaptive=0/1``：

    w' = w × (1 + β × affinity)，affinity = 该路候选与短期兴趣类型的平均命中强度

做法：真实评分触发真实 Kafka 事件 → 真实消费端写出短期兴趣状态 → 立刻对比两次融合。
状态是刚写的，所以不会遇到"状态过期"分支；过期分支由 lab6_integration_check / 助手的
场景测试覆盖（接口会返回明确原因）。

    python scripts/lab6_adaptive_check.py

报告写到 reports/lab6/adaptive-weights.json（含两路的权重、匹配度、Top-10 与是否变序）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab5_scenarios as lab5  # noqa: E402

BASE = "http://127.0.0.1:8080"
REPORT = REPO / "reports/lab6/adaptive-weights.json"
NAME = "lab6adapt1022"
PASSWORD = "Lab6Adapt!2026"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fusion(client: lab5.Client, adaptive: int, suffix: str = "", limit: int = 10) -> dict:
    status, payload = client.request(
        "GET", f"/api/v1/me/recommendations/fusion?limit={limit}&interest=1&adaptive={adaptive}{suffix}")
    if status != 200 or not payload:
        raise RuntimeError(f"融合请求失败：{status} {payload}")
    return payload


def state_version(client: lab5.Client) -> int | None:
    status, payload = client.request("GET", "/api/v1/me/behavior/interest?events=1")
    return ((payload or {}).get("interest") or {}).get("state_version")


def event_count(client: lab5.Client) -> int:
    status, payload = client.request("GET", "/api/v1/me/behavior/interest?events=1")
    return int(((payload or {}).get("interest") or {}).get("events") or 0)


def rating(client: lab5.Client, movie_id: str, value: str) -> dict:
    status, payload = client.request("PUT", f"/api/v1/me/ratings/{movie_id}", {"rating": value}, csrf=True)
    return {"movie_id": movie_id, "rating": value, "status": status, "response": payload}


def main() -> int:
    parser = argparse.ArgumentParser(description="创新点 A：自适应融合权重对照")
    parser.add_argument("--fresh-account", action="store_true",
                        help="换一个带时间戳的新账号（默认复用固定账号，重复运行会覆盖它的评分）")
    parser.add_argument("--consumer-idle", type=float, default=6.0)
    parser.add_argument("--half-life", type=float, default=30.0)
    args = parser.parse_args()

    name = f"{NAME}{datetime.now().strftime('%m%d%H%M%S')}" if args.fresh_account else NAME
    client = lab5.Client(BASE)
    account = client.login_or_register(name, PASSWORD)
    events_before = event_count(client)

    # 真实写入：三部动画正反馈（含收藏）＋ 一部恐怖片差评，全部落库并发布事件
    animation = lab5.pick_movies(client, "Animation", 3)
    horror = lab5.pick_movies(client, "Horror", 1)
    writes = [rating(client, animation[0]["movie_id"], "5.0"),
              rating(client, animation[1]["movie_id"], "5.0"),
              rating(client, animation[2]["movie_id"], "4.5"),
              rating(client, horror[0]["movie_id"], "1.0")]
    favorite_status, favorite_payload = client.request(
        "PUT", f"/api/v1/me/favorites/{animation[0]['movie_id']}", None, csrf=True)
    writes.append({"movie_id": animation[0]["movie_id"], "kind": "favorite",
                   "status": favorite_status, "response": favorite_payload})

    producer = lab5.wait_for_sent(client, len(writes), timeout=30.0)
    before = state_version(client)
    consumer = lab5.run_consumer(len(writes) + 20, args.consumer_idle, args.half_life)

    status, interest = client.request("GET", "/api/v1/me/behavior/interest?events=10")
    interest_block = (interest or {}).get("interest") or {}
    after = interest_block.get("state_version")
    events_after = int(interest_block.get("events") or 0)

    # 两组基准权重各做一次 adaptive=0/1 对照：默认（itemcf=6, als=1）与等权（1:1）。
    # 等权那一组更能说明机制：谁与短期兴趣更亲近，谁就在融合里拿到更大话语权。
    cases = []
    presets = (
        ("", "默认权重 itemcf=6 als=1"),
        ("&weights=1,1", "等权 1:1"),
        # 机制对照：两路候选差别大（popular 类型杂、itemcf 贴近动画兴趣），权重重排后 Top-10 会变序
        ("&algorithms=popular,itemcf&weights=1,1", "popular+itemcf 等权（预期变序）"),
    )
    for suffix, label in presets:
        fixed = fusion(client, 0, suffix)
        adapted = fusion(client, 1, suffix)
        fixed_ids = [item["movie_id"] for item in fixed["items"]]
        adapted_ids = [item["movie_id"] for item in adapted["items"]]
        block = adapted.get("adaptive") or {}
        fixed_block = fixed.get("adaptive") or {}
        cases.append({
            "label": label,
            "weights_query": suffix or None,
            "adaptive_off": {"requested": fixed_block.get("requested"), "enabled": fixed_block.get("enabled"),
                             "reason": fixed_block.get("reason"), "weights": fixed.get("weights") or {},
                             "ranking": fixed_ids,
                             "boosts": [item.get("interest_boost") for item in fixed["items"]]},
            "adaptive_on": {"requested": block.get("requested"), "enabled": block.get("enabled"),
                            "beta": block.get("beta"), "state_version": block.get("state_version"),
                            "weights": adapted.get("weights") or {}, "ranking": adapted_ids,
                            "boosts": [item.get("interest_boost") for item in adapted["items"]],
                            "sources": {row["algorithm"]: {"base_weight": row["base_weight"],
                                                           "affinity": row["affinity"],
                                                           "adaptive_weight": row["adaptive_weight"],
                                                           "candidates": row["candidates"]}
                                        for row in (block.get("sources") or [])},
                            "rule": block.get("rule")},
            "ranking_changed": fixed_ids != adapted_ids,
        })

    def weights_changed(case: dict) -> bool:
        off = case["adaptive_off"]["weights"] or {}
        on = case["adaptive_on"]["weights"] or {}
        return any(abs(float(off.get(name, 0)) - float(on.get(name, 0))) > 1e-9 for name in on)

    def affinity_differs(case: dict) -> bool:
        rows = (case["adaptive_on"]["sources"] or {}).values()
        return len({round(float(row["affinity"]), 6) for row in rows}) > 1

    checks = {
        "events_recorded": bool(events_after - events_before == len(writes)),
        "consumer_advanced_state": bool(
            after is not None and (before is None or int(after) > int(before))),
        "state_not_stale": bool((interest_block.get("stale") is False) and interest_block.get("updated_at")),
        "adaptive_enabled": all(case["adaptive_on"]["enabled"] for case in cases),
        "adaptive_disabled_when_flag_0": all(
            case["adaptive_off"]["requested"] is False and case["adaptive_off"]["enabled"] is False for case in cases),
        "guardrail_disabled_path_explained": all(
            (case["adaptive_off"]["reason"] or "").strip() for case in cases),
        "weights_changed": all(weights_changed(case) for case in cases),
        "affinity_differs_across_sources": all(affinity_differs(case) for case in cases),
        "ranking_reacts_to_weights": any(case["ranking_changed"] for case in cases),
    }
    report = {
        "status": "passed" if all(checks.values()) else "failed",
        "passed": all(checks.values()),
        "generated_at": stamp(),
        "rule": cases[0]["adaptive_on"]["rule"] or "w' = w × (1 + β × affinity)",
        "beta": cases[0]["adaptive_on"]["beta"],
        "account": {"login_name": name,
                    "subject_key": interest_block.get("subject_key") or f"app:{account['account'].get('account_id')}",
                    "action": account["action"]},
        "writes": writes,
        "producer": {"sent": producer.get("sent"), "failed": producer.get("failed"), "skipped": producer.get("skipped")},
        "consumer": {"exit_code": consumer.get("exit_code"), "state_version_before": before,
                     "state_version_after": after, "events_before": events_before,
                     "events_after": events_after, "expected_events": len(writes),
                     "last_applied": consumer.get("last_applied")},
        "interest_top_genres": interest_block.get("top_genres"),
        "cases": cases,
        "checks": checks,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"status": report["status"], "report": str(REPORT.relative_to(REPO)),
               "cases": [{"label": case["label"],
                          "weights_off": case["adaptive_off"]["weights"],
                          "weights_on": case["adaptive_on"]["weights"],
                          "ranking_changed": case["ranking_changed"]} for case in cases],
               "failed_checks": [key for key, value in checks.items() if not value]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
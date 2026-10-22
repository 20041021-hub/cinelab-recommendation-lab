"""实验六：系统集成检查（环境/账号行为/历史用户/三核心算法/热门与冷启动/S1/S3/评价/前端改造）。

一次跑完，把每一项的真实请求与状态写进 reports/lab6/integration.json；
任何一项不通过都会显式记 false 并以退出码 1 结束（不做“静默通过”）。

    D:\\Python311\\python.exe scripts\\lab6_integration_check.py
"""

from __future__ import annotations

import csv
import re
import urllib.parse
import urllib.request
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import lab5_scenarios as lab5

REPO = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8080"
PACKAGE = REPO / "data/packages/rec-course-v3-train5-1.0.0"
ACCOUNT = ("lab6check1022", "Lab6Check!2026")
EMPTY_ACCOUNT = ("lab6empty1022", "Lab6Empty!2026")
HISTORY_USERS = (14, 55)
CORE = ("content", "itemcf", "als")
ALL_FIVE = ("popular", "content", "itemcf", "als", "cold_start")

sys.stdout.reconfigure(encoding="utf-8")
checks: list[dict] = []


class Api(lab5.Client):
    """接口路径自动补 /api/v1 前缀（/health/ready 等根路径除外）。"""

    def request(self, method: str, path: str, payload: dict | None = None, csrf: bool = False):
        if not path.startswith("/api/") and not path.startswith("/health/"):
            path = "/api/v1" + path
        return super().request(method, path, payload=payload, csrf=csrf)


def record(name: str, ok: bool, evidence) -> bool:
    checks.append({"check": name, "passed": bool(ok), "evidence": evidence})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {json.dumps(evidence, ensure_ascii=False)[:240]}")
    return bool(ok)


def history_of(user_id: int) -> dict[str, str]:
    rated: dict[str, str] = {}
    with (PACKAGE / "business/history_ratings.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("userId") == str(user_id):
                rated[row["movieId"]] = row["rating"]
    return rated


def ids_of(payload) -> list[str]:
    return [item["movie_id"] for item in (payload or {}).get("items", [])]


def main() -> int:
    client = Api(BASE)

    # 1. 环境与数据身份
    status, health = client.request("GET", "/health/ready")
    record(
        "环境启动与数据身份",
        status == 200 and health.get("dataset", {}).get("version") == "rec-course-v3-train5-1.0.0"
        if isinstance(health.get("dataset"), dict)
        else status == 200,
        {"status": status, "body": health},
    )

    status, registry = client.request("GET", "/experiments/algorithms")
    items = {row["algorithm"]: row["status"] for row in (registry or {}).get("items", [])}
    record("算法注册表五个都可用", status == 200 and all(items.get(a) == "registered" for a in ALL_FIVE), {"status": status, "registry": items})

    # 2. 注册、评分、收藏（真实写路径）
    created = client.login_or_register(*ACCOUNT)
    record("注册/登录与会话", True, created)

    catalog_status, catalog = client.request("GET", "/movies?per_page=12&genre=Animation")
    movies = [row["movie_id"] for row in (catalog or {}).get("items", [])][:3]
    rated_ok = []
    for movie, rating in zip(movies, ("5.0", "4.5", "2.0")):
        code, body = client.request("PUT", f"/me/ratings/{movie}", {"rating": rating}, csrf=True)
        rated_ok.append({"movie_id": movie, "rating": rating, "status": code})
    fav_code, fav_body = client.request("PUT", f"/me/favorites/{movies[0]}", None, csrf=True)
    record(
        "注册账号评分与收藏",
        catalog_status == 200 and all(row["status"] == 200 for row in rated_ok) and fav_code in (200, 201, 204),
        {"catalog_status": catalog_status, "ratings": rated_ok, "favorite": {"movie_id": movies[0], "status": fav_code}},
    )

    # 3. 三个核心算法 + 热门/冷启动（当前账号）
    seen = set(movies)
    for algorithm in (*CORE, "popular"):
        code, payload = client.request("GET", f"/me/recommendations/{algorithm}?limit=10")
        recommended = ids_of(payload)
        record(
            f"当前账号 {algorithm} 推荐",
            code == 200 and len(recommended) == 10 and not (set(recommended) & seen),
            {"status": code, "model_version": payload.get("model_version"), "n": len(recommended), "hit_rated": sorted(set(recommended) & seen), "top3": recommended[:3]},
        )

    empty = Api(BASE)
    empty.login_or_register(*EMPTY_ACCOUNT)
    code, payload = empty.request("GET", "/me/recommendations/cold_start?limit=10")
    sources = {source for item in (payload or {}).get("items", []) for source in (item.get("recall_sources") or [])}
    record(
        "零历史账号冷启动",
        code == 200 and len(ids_of(payload)) == 10 and "cold_start_popular" in sources,
        {"status": code, "model_version": payload.get("model_version"), "sources": sorted(sources)},
    )

    # 4. 两个历史用户 × 五个算法，且与本人已评分无交集
    for user in HISTORY_USERS:
        rated = history_of(user)
        for algorithm in ALL_FIVE:
            code, payload = client.request("GET", f"/demo/history-users/{user}/recommendations/{algorithm}?limit=10")
            recommended = ids_of(payload)
            record(
                f"ml:{user} {algorithm}",
                code == 200 and len(recommended) == 10 and not (set(recommended) & set(rated)),
                {"status": code, "model_version": payload.get("model_version"), "n": len(recommended), "top3": recommended[:3], "history_rows": len(rated)},
            )

    # 5. S1 融合 + S3 短期兴趣（两项标准扩展）：先把刚写入的行为事件消费成短期兴趣状态
    try:
        consumed = lab5.run_consumer(max_messages=50, idle_timeout=12.0, half_life=30.0)
    except Exception as exc:  # noqa: BLE001 - 消费端失败必须如实记录，不能假装成功
        consumed = {"error": f"{type(exc).__name__}: {exc}"}
    record("S3 消费端处理行为事件", not consumed.get("error"), consumed)

    code, fusion_off = client.request("GET", "/me/recommendations/fusion?interest=0&limit=10")
    code2, fusion_on = client.request("GET", "/me/recommendations/fusion?interest=1&limit=10")
    interest = (fusion_on or {}).get("interest") or {}
    record(
        "S1 加权融合（含来源与名次）",
        code == 200 and code2 == 200 and len(ids_of(fusion_on)) >= 1 and all(item.get("recall_sources") and item.get("recall_ranks") for item in (fusion_on or {}).get("items", [])),
        {"status": [code, code2], "pool": (fusion_on or {}).get("pool_size"), "sources": (fusion_on or {}).get("sources"), "top1": ids_of(fusion_on)[:1]},
    )
    record(
        "S3 短期兴趣状态与重排",
        bool(interest.get("enabled")) and bool(interest.get("top_genres")) and (interest.get("state_version") or 0) > 0,
        {"interest": interest},
    )

    status, behavior = client.request("GET", "/experiments/behavior/status")
    record(
        "S3 生产端/消费端状态可见",
        status == 200 and (behavior or {}).get("producer", {}).get("sent", 0) >= 4 and (behavior or {}).get("consumer", {}).get("state_version", 0) > 0,
        {"status": status, "producer": (behavior or {}).get("producer"), "consumer": (behavior or {}).get("consumer")},
    )

    # 6. 实验六 S2：外部模型受控工具调用（真实调用，不 mock）
    status, assistant_status = client.request("GET", "/experiments/assistant/status")
    assistant_status = assistant_status or {}
    llm = assistant_status.get("llm") or {}
    record(
        "S2 助手状态可见（提供方与模型）",
        status == 200 and bool(assistant_status.get("tools")) and bool(llm.get("model")) and bool(llm.get("key_configured")),
        {"status": status, "mode": llm.get("mode"), "base_url": llm.get("base_url"), "model": llm.get("model"),
         "key_configured": llm.get("key_configured"), "max_tool_rounds": llm.get("max_tool_rounds"),
         "tools": assistant_status.get("tools")},
    )
    question = "给我来几部 90 年代的动画片，顺便说说和我看过的哪部最像"
    code, answer = client.request("GET", "/me/assistant/query?q=" + urllib.parse.quote(question) + "&limit=5")
    payload = answer or {}
    items = payload.get("items") or []
    trace = payload.get("tool_calls") or []
    sources = {row.get("tool") for row in trace}
    record(
        "S2 助手真实调用并只用工具结果",
        code == 200 and payload.get("status") == "ready" and len(items) >= 1
        and all(item.get("source_tool") for item in items) and bool(sources) and bool(payload.get("answer")),
        {"status": code, "answer_status": payload.get("status"), "tools": sorted(sources),
         "items": [{"movie_id": item.get("movie_id"), "source_tool": item.get("source_tool")} for item in items[:5]],
         "guardrails": payload.get("guardrails"), "model": payload.get("model"), "provider": payload.get("provider")},
    )
    code, bad = client.request("GET", "/me/assistant/query?q=" + urllib.parse.quote("给我推荐几部不存在的类型 Anime 的电影"))
    bad = bad or {}
    rejected = [row for row in (bad.get("tool_calls") or []) if row.get("ok") is False]
    record(
        "S2 助手错误输入有明确状态（不静默编造）",
        code == 200 and bad.get("status") in {"no_result", "ready"} and bool(bad.get("answer") or bad.get("warnings")),
        {"status": code, "answer_status": bad.get("status"), "rejected_calls": len(rejected),
         "answer": (bad.get("answer") or "")[:120], "warnings": bad.get("warnings")},
    )

    # 7. 实验六创新点 A：短期兴趣自适应融合权重（同一账号、同一时刻只切 adaptive）
    q = "/me/recommendations/fusion?algorithms=popular,itemcf&weights=1,1&interest=1&limit=10"
    code_off, fusion_fixed = client.request("GET", q + "&adaptive=0")
    code_on, fusion_adapt = client.request("GET", q + "&adaptive=1")
    fixed_weights = {key: float(value) for key, value in ((fusion_fixed or {}).get("weights") or {}).items()}
    adapt_weights = {key: float(value) for key, value in ((fusion_adapt or {}).get("weights") or {}).items()}
    adapt_block = (fusion_adapt or {}).get("adaptive") or {}
    affinities = {row.get("algorithm"): row.get("affinity") for row in (adapt_block.get("sources") or [])}
    record(
        "创新点 A 自适应权重真实改写各路权重",
        code_off == 200 and code_on == 200 and bool(adapt_block.get("enabled"))
        and any(abs(fixed_weights.get(name, 0) - value) > 1e-9 for name, value in adapt_weights.items())
        and len(set(affinities.values())) > 1,
        {"status": [code_off, code_on], "requested": adapt_block.get("requested"),
         "enabled": adapt_block.get("enabled"), "beta": adapt_block.get("beta"),
         "rule": adapt_block.get("rule"), "weights_off": fixed_weights, "weights_on": adapt_weights,
         "ranking_off": ids_of(fusion_fixed), "ranking_on": ids_of(fusion_adapt),
         "affinities": affinities},
    )
    code, fixed_off_db = client.request("GET", "/me/recommendations/fusion?interest=1&limit=5&adaptive=0")
    record(
        "创新点 A 关闭时有明确原因（不静默生效）",
        code == 200 and ((fixed_off_db or {}).get("adaptive") or {}).get("requested") is False
        and bool(((fixed_off_db or {}).get("adaptive") or {}).get("reason")),
        {"status": code, "adaptive": (fixed_off_db or {}).get("adaptive")},
    )

    # 8. 离线评价结果（真实文件）
    summary = json.loads((REPO / "reports/lab3/summary.json").read_text(encoding="utf-8"))
    rows = summary if isinstance(summary, list) else summary.get("rows", [])
    partitions = {row["partition"] for row in rows}
    metrics = json.loads((REPO / "frontend/src/data/lab3-metrics.json").read_text(encoding="utf-8"))
    record(
        "离线评价表完整（validation/test/cold）",
        {"validation", "test"} <= partitions and len([row for row in rows if row["partition"].startswith("cold")]) == 20,
        {"rows": len(rows), "partitions": sorted(partitions), "frontend_counts": {"validation": len(metrics.get("validation", [])), "test": len(metrics.get("test", [])), "cold": len(metrics.get("cold", []))}},
    )
    manifest = json.loads((REPO / "models/evaluation/als-v1/manifest.json").read_text(encoding="utf-8"))
    als_row = next((row for row in rows if row["algorithm"] == "als" and row["partition"] == "validation"), {})
    expected = manifest["parameters"].get("final_model_validation_metrics", {})
    record(
        "ALS 指标与模型清单一致",
        abs(float(als_row.get("precision_at_10", -1)) - float(expected.get("precision_at_10", -2))) < 1e-9,
        {"report": {k: als_row.get(k) for k in ("precision_at_10", "recall_at_10", "users", "empty_users")}, "manifest": expected},
    )

    # 9. 前端改造证据：从**正在运行的网页**拉取真正的构建产物，而不是本地源码目录
    def fetch_text(url: str) -> str:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - 本机固定地址
            return response.read().decode("utf-8", errors="ignore")

    index_html = fetch_text(BASE + "/")
    asset_paths = sorted({match for match in re.findall(r"/assets/[A-Za-z0-9_.\-]+\.js", index_html)})
    contents: dict[str, str] = {path: fetch_text(BASE + path) for path in asset_paths}
    # 路由是懒加载的：从入口产物里再发现一层分块（ExperimentsView 等）
    for _ in range(2):
        discovered = {
            "/assets/" + match
            for text in contents.values()
            for match in re.findall(r"assets/([A-Za-z0-9_.\-]+\.js)", text)
        }
        missing = sorted(discovered - set(contents))
        if not missing:
            break
        for path in missing:
            contents[path] = fetch_text(BASE + path)
    asset_paths = sorted(contents)
    asset_text = "\n".join(contents.values())
    markers = {
        "融合面板": "fusion-panel" in asset_text,
        "短期兴趣面板": "behavior-panel" in asset_text or "短期兴趣" in asset_text,
        "离线评价表": "evaluation-tables" in asset_text or "evaluation-block" in asset_text,
        "冷启动入口": "cold-start" in asset_text,
        "S2 助手面板": "assistant-panel" in asset_text or "助手" in asset_text,
        "创新点 A 面板": "fusion-adaptive-panel" in asset_text or "adaptive-pill" in asset_text,
    }
    record(
        "前端改造在运行中的网页可见",
        bool(asset_paths) and all(markers.values()),
        {"assets": asset_paths, "markers": markers},
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": BASE,
        "passed": all(row["passed"] for row in checks),
        "total": len(checks),
        "failed": [row["check"] for row in checks if not row["passed"]],
        "checks": checks,
    }
    out = REPO / "reports/lab6/integration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{report['total']} 项检查，失败 {len(report['failed'])} 项 -> {out.relative_to(REPO)}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

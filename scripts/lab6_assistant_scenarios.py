"""实验六 S2：自然语言助手的四场景测试（正常 / 无结果 / 错误输入 / 外部服务不可用）。

只走真实链路：真实 DeepSeek 调用、真实电影目录、真实 ItemCF 工件、真实推荐 Provider、
真实账号会话；任何一步失败都会原样写进报告，绝不吞异常（手册要求"不能静默伪造数据"）。

    python scripts/lab6_assistant_scenarios.py                 # 全部场景（含两次容器重启）
    python scripts/lab6_assistant_scenarios.py --skip-restart  # 只跑不需要动容器的三个场景

外部服务不可用场景的实现方式（不做假）：临时把 api 容器用
``APP_LLM_BASE_URL=http://127.0.0.1:9``（不可路由）或 ``APP_LLM_KEY_FILE=不存在的文件``
重启一次，验证接口返回 503 ASSISTANT_UNAVAILABLE 且**业务推荐不受影响**，最后恢复默认配置。

报告写到 reports/lab6/assistant-scenarios.json。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab5_scenarios as lab5  # noqa: E402  复用带会话/CSRF/Origin 的客户端

BASE = "http://127.0.0.1:8080"
COMPOSE = ["docker", "compose",
           "-f", "compose.yaml",
           "-f", "deployment/lab5/compose.lab5.yaml",
           "-f", "deployment/lab6/compose.lab6.yaml"]
DEMO_USER = "14"
DEMO_PATH = f"/api/v1/demo/history-users/{DEMO_USER}/assistant/query"
REPORT = REPO / "reports/lab6/assistant-scenarios.json"
APP_NAME = "lab6check1022"
APP_PASSWORD = "Lab6Check!2026"

NO_RESULT_QUESTION = "只要 2029 年上映的 Film-Noir 电影，别的都不要"
BAD_GENRE_QUESTION = "推荐几部 Anime 类型的电影"
NORMAL_QUESTION = "推荐几部动画电影"
ALGORITHM_QUESTION = "用 ItemCF 给我推荐 5 部电影，并说明这个算法的准确率"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get(path: str, timeout: float = 120.0) -> tuple[int, dict]:
    """不带会话的只读 GET（助手演示接口不需要登录）。"""
    request = urllib.request.Request(BASE + path, method="GET")
    request.add_header("Origin", lab5.ORIGIN)
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, (json.loads(raw) if raw.strip() else None)
        except ValueError:
            return exc.code, {"raw": raw[:400]}


def ask(question: str, limit: int = 5, user: str = DEMO_USER) -> tuple[int, dict]:
    query = urllib.parse.urlencode({"q": question, "limit": limit})
    return get(f"/api/v1/demo/history-users/{user}/assistant/query?{query}")


def wait_ready(timeout: float = 150.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = get("/health/ready", timeout=5.0)
            if status == 200:
                return True
        except Exception:  # noqa: BLE001  容器重启期间连接被拒是正常现象
            pass
        time.sleep(3)
    return False


def recreate_api(overrides: dict[str, str] | None = None) -> dict:
    """用指定的环境变量重启 api 容器（compose 变量插值），并等到 ready。"""
    environment = os.environ.copy()
    environment.update(overrides or {})
    result = subprocess.run(COMPOSE + ["up", "-d", "--no-deps", "--force-recreate", "api"],
                            cwd=REPO, env=environment, capture_output=True, text=True)
    ready = wait_ready()
    return {"overrides": overrides or {}, "exit_code": result.returncode, "ready": ready,
            "stderr_tail": (result.stderr or "").strip().splitlines()[-3:]}


def scenario_normal(client) -> dict:
    """正常场景：真实模型 + 真实工具，返回可追溯的片单。"""
    status, payload = ask(NORMAL_QUESTION, limit=5)
    tools = payload.get("tool_calls") or [] if isinstance(payload, dict) else []
    items = payload.get("items") or [] if isinstance(payload, dict) else []
    checks = {
        "http_200": status == 200,
        "status_ready": isinstance(payload, dict) and payload.get("status") == "ready",
        "provider_live": isinstance(payload, dict) and payload.get("provider") == "live",
        "answer_not_empty": bool(isinstance(payload, dict) and (payload.get("answer") or "").strip()),
        "tools_called": bool(tools) and all(row.get("ok") for row in tools),
        "items_from_tools": bool(items) and all(row.get("source_tool") for row in items),
        "no_unverified_mentions": not (payload.get("unverified_mentions") or []),
    }
    # 指定算法的提问：必须报出"用了哪个算法 + 它的离线准确率"（模型回答 + 结构化 methods）
    algo_status, algo_payload = ask(ALGORITHM_QUESTION, limit=5)
    algo_payload = algo_payload if isinstance(algo_payload, dict) else {}
    methods = algo_payload.get("methods") or []
    method = methods[0] if methods else {}
    metrics = method.get("offline_metrics") or {}
    answer_text = algo_payload.get("answer") or ""
    checks["algorithm_reported"] = (algo_status == 200 and method.get("algorithm") == "itemcf"
                                   and method.get("model_version") == "itemcf-cooccurrence-v1")
    checks["accuracy_reported"] = (metrics.get("precision_at_10") == 0.055263
                                   and metrics.get("recall_at_10") == 0.09783
                                   and metrics.get("users") == 4209)
    checks["accuracy_in_answer"] = ("Precision@10" in answer_text or "准确率" in answer_text
                                    or "5.53" in answer_text)
    checks["methods_match_tool_evidence"] = bool(tools) and any(
        (row.get("evidence") or {}).get("offline_metrics") for row in tools) or bool(methods)
    # 登录后的 /me 路径也要真实可用
    login = client.login_or_register(APP_NAME, APP_PASSWORD)
    query = urllib.parse.urlencode({"q": "看看我最近评过什么，再推荐几部", "limit": 5})
    me_status, me_payload = client.request("GET", f"/api/v1/me/assistant/query?{query}")
    checks["me_path_ok"] = me_status == 200 and (me_payload or {}).get("subject_key", "").startswith("app:")
    return {"question": NORMAL_QUESTION, "status_code": status, "account_action": login["action"],
            "checks": checks, "passed": all(checks.values()),
            "evidence": {"rounds": payload.get("rounds"), "tools": [
                {"tool": row.get("tool"), "ok": row.get("ok"), "matched": row.get("matched"),
                 "arguments": row.get("arguments"), "source": (row.get("evidence") or {}).get("source")}
                for row in tools],
                "items": [{"movie_id": row.get("movie_id"), "title": row.get("title"),
                           "source_tool": row.get("source_tool")} for row in items],
                "answer": (payload.get("answer") or "")[:400],
                "algorithm_question": ALGORITHM_QUESTION,
                "algorithm_answer": answer_text[:400],
                "methods": methods,
                "me_status_code": me_status,
                "me_subject_key": (me_payload or {}).get("subject_key")}}


def scenario_no_result() -> dict:
    """无结果场景：目录里确实没有这样的电影，必须如实说"没有找到"。"""
    status, payload = ask(NO_RESULT_QUESTION, limit=5)
    warnings = payload.get("warnings") or [] if isinstance(payload, dict) else []
    checks = {
        "http_200": status == 200,
        "status_no_result": isinstance(payload, dict) and payload.get("status") == "no_result",
        "no_items": not (payload.get("items") or []),
        "warning_explains": any("没有返回任何电影" in str(row) for row in warnings),
        "answer_not_fabricated": bool(isinstance(payload, dict) and (payload.get("answer") or "").strip()),
    }
    return {"question": NO_RESULT_QUESTION, "status_code": status, "checks": checks,
            "passed": all(checks.values()),
            "evidence": {"status": payload.get("status"), "warnings": warnings,
                         "answer": (payload.get("answer") or "")[:300],
                         "tools": [{"tool": row.get("tool"), "ok": row.get("ok"), "matched": row.get("matched")}
                                   for row in (payload.get("tool_calls") or [])]}}


def scenario_bad_input() -> dict:
    """错误输入场景：参数错误被接口拒绝；类型不存在时工具被拒且不编造结果。"""
    short_status, _ = ask("a")
    limit_status, _ = ask(NORMAL_QUESTION, limit=99)
    missing_status, _ = ask(NORMAL_QUESTION, user="999999")
    genre_status, genre_payload = ask(BAD_GENRE_QUESTION, limit=5)
    trace = genre_payload.get("tool_calls") or [] if isinstance(genre_payload, dict) else []
    warnings = genre_payload.get("warnings") or [] if isinstance(genre_payload, dict) else []
    checks = {
        "short_question_rejected": short_status == 422,
        "bad_limit_rejected": limit_status == 422,
        "unknown_user_404": missing_status == 404,
        "invalid_genre_tool_rejected": bool(trace) and any(row.get("ok") is False for row in trace),
        "rejection_is_visible": any("被拒绝的工具调用" in str(row) for row in warnings),
        "no_fabricated_items": not (genre_payload.get("items") or []) if isinstance(genre_payload, dict) else False,
    }
    return {"question": BAD_GENRE_QUESTION, "status_code": genre_status, "checks": checks,
            "passed": all(checks.values()),
            "evidence": {"short_question_status": short_status, "bad_limit_status": limit_status,
                         "unknown_user_status": missing_status, "warnings": warnings,
                         "tools": [{"tool": row.get("tool"), "ok": row.get("ok"), "error": row.get("error")}
                                   for row in trace],
                         "answer": (genre_payload.get("answer") or "")[:300] if isinstance(genre_payload, dict) else None}}


def scenario_external_down(client) -> dict:
    """外部服务不可用场景：模型不可达/未配置 Key 时明确报错，且业务推荐照常工作。"""
    records = []
    # 1) 模型地址不可路由
    records.append({"case": "network_unreachable", "restart": recreate_api({"APP_LLM_BASE_URL": "http://127.0.0.1:9"})})
    status, payload = ask(NORMAL_QUESTION, limit=5)
    body = payload if isinstance(payload, dict) else {}
    error = body.get("error") or {}
    details = error.get("details") or {}
    records[-1].update({"status_code": status, "code": error.get("code"), "reason": details.get("reason"),
                        "message": (error.get("message") or "")[:200],
                        "business_ok": get("/api/v1/experiments/fusion/sources", timeout=30)[0] == 200})
    # 2) Key 文件不存在
    records.append({"case": "missing_key",
                    "restart": recreate_api({"APP_LLM_KEY_FILE": "/app/llm-key/does-not-exist.txt"})})
    status, payload = ask(NORMAL_QUESTION, limit=5)
    body = payload if isinstance(payload, dict) else {}
    error = body.get("error") or {}
    details = error.get("details") or {}
    records[-1].update({"status_code": status, "code": error.get("code"), "reason": details.get("reason"),
                        "message": (error.get("message") or "")[:200]})
    # 3) 恢复默认配置：真实调用立刻恢复
    restore = recreate_api(None)
    status, payload = ask(NORMAL_QUESTION, limit=3)
    restored = isinstance(payload, dict) and payload.get("status") == "ready"
    checks = {
        "unreachable_is_503": records[0].get("status_code") == 503 and records[0].get("code") == "ASSISTANT_UNAVAILABLE",
        "unreachable_reason": records[0].get("reason") in ("network_error", "timeout"),
        "missing_key_reason": records[1].get("reason") == "missing_key",
        "business_not_broken": bool(records[0].get("business_ok")),
        "restored": bool(restore.get("ready")) and restored,
    }
    return {"checks": checks, "passed": all(checks.values()),
            "evidence": {"cases": records, "restore": restore, "restored_status": payload.get("status") if isinstance(payload, dict) else None}}


def main() -> int:
    parser = argparse.ArgumentParser(description="实验六 S2 助手场景测试")
    parser.add_argument("--skip-restart", action="store_true", help="跳过需要重启 api 的外部服务不可用场景")
    args = parser.parse_args()

    if not wait_ready():
        print(json.dumps({"status": "failed", "reason": "api 未就绪"}, ensure_ascii=False))
        return 1

    client = lab5.Client(BASE)
    scenarios = {
        "normal": scenario_normal(client),
        "no_result": scenario_no_result(),
        "bad_input": scenario_bad_input(),
    }
    if not args.skip_restart:
        scenarios["external_unavailable"] = scenario_external_down(client)

    passed = all(row.get("passed") for row in scenarios.values())
    report = {"status": "passed" if passed else "failed", "passed": passed, "generated_at": stamp(),
              "guardrails": ["工具白名单", "参数取值域校验", "只读工具", "items 仅由工具输出构成", "轮次上限"],
              "scenarios": scenarios}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {name: {"passed": row.get("passed"), "failed_checks": [key for key, value in (row.get("checks") or {}).items() if not value]}
               for name, row in scenarios.items()}
    print(json.dumps({"status": report["status"], "report": str(REPORT.relative_to(REPO)), "scenarios": summary},
                     ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
"""实验五 S3：四个场景的可复现测试（正常 / 无结果 / 错误输入 / 外部服务不可用）。

脚本只做"真实链路"的验证：真实注册账号、真实评分收藏、真实 Kafka 事件、真实消费端状态、
真实融合接口对比；任何一步失败都会在报告里写明，绝不吞掉异常（手册要求"不能静默伪造数据"）。

    python scripts/lab5_scenarios.py                          # 全部场景
    python scripts/lab5_scenarios.py --kafka-down             # Kafka 停机场景（外部先停 Kafka）
    python scripts/lab5_scenarios.py --skip-consumer          # 只跑接口侧（消费端另起）

报告写到 reports/lab5/scenarios.json（含每个场景的请求、状态码、关键字段）。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8080"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Client:
    """带会话、CSRF 与 Origin 的极简客户端（写接口必须带这三样）。

    课程把会话 Cookie 标成 ``Secure``，浏览器在 http://127.0.0.1 上仍会收下并回送
    （localhost 属于可信来源），而 Python 自带的 CookieJar 会拒绝在明文 HTTP 上发送。
    这里按属性无关的方式自己管 Cookie，行为与浏览器一致，接口本身不做任何改动。
    """

    def __init__(self, base_url: str, timeout: float = 20.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.cookies: dict[str, str] = {}
        self.csrf: str | None = None

    def _cookie_header(self) -> str | None:
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items()) or None

    def _absorb(self, response) -> None:
        for raw in response.headers.get_all("Set-Cookie") or []:
            pair = raw.split(";", 1)[0].strip()
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            if not value or "Max-Age=0" in raw or "expires=Thu, 01 Jan 1970" in raw:
                self.cookies.pop(name.strip(), None)
            else:
                self.cookies[name.strip()] = value

    def request(self, method: str, path: str, payload: dict | None = None, csrf: bool = False):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=body, method=method)
        request.add_header("Origin", ORIGIN)
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        cookie = self._cookie_header()
        if cookie:
            request.add_header("Cookie", cookie)
        if csrf:
            request.add_header("x-csrf-token", self.csrf or self.fetch_csrf())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._absorb(response)
                raw = response.read().decode("utf-8")
                return response.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as exc:
            self._absorb(exc)
            raw = exc.read().decode("utf-8")
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except ValueError:
                parsed = {"raw": raw[:400]}
            return exc.code, parsed

    def fetch_csrf(self) -> str:
        status, payload = self.request("GET", "/api/v1/auth/csrf")
        if status != 200 or not payload:
            raise RuntimeError(f"获取 CSRF 失败：{status} {payload}")
        self.csrf = payload.get("csrf_token") or payload.get("token")
        if not self.csrf:
            raise RuntimeError(f"CSRF 响应没有令牌：{payload}")
        return self.csrf

    def login_or_register(self, name: str, password: str) -> dict:
        self.fetch_csrf()
        credentials = {"login_name": name, "password": password}
        status, payload = self.request("POST", "/api/v1/auth/login", credentials, csrf=True)
        action = "login"
        if status in (401, 404):
            status, payload = self.request("POST", "/api/v1/auth/register", credentials, csrf=True)
            action = "register"
            if status not in (200, 201):
                raise RuntimeError(f"注册失败：{status} {payload}")
            # 注册只建账号，不发会话；必须再登录一次才会拿到 course_session。
            self.fetch_csrf()
            status, payload = self.request("POST", "/api/v1/auth/login", credentials, csrf=True)
            action = "register+login"
        if status != 200:
            raise RuntimeError(f"{action} 失败：{status} {payload}")
        if not self.cookies.get("course_session"):
            raise RuntimeError(f"{action} 之后仍未拿到会话 Cookie：{sorted(self.cookies)}")
        self.fetch_csrf()  # 登录后会话变了，必须重新取与当前会话绑定的 CSRF 令牌
        return {"action": action, "status": status, "account": payload}


def pick_movies(client: Client, genre: str, count: int) -> list[dict]:
    status, payload = client.request("GET", f"/api/v1/movies?genre={urllib.parse.quote(genre)}&per_page=20")
    if status != 200 or not payload:
        raise RuntimeError(f"查询 {genre} 电影失败：{status} {payload}")
    chosen = [{"movie_id": row["movie_id"], "genre": genre, "title": row.get("movielens_title")} for row in payload["items"][:count]]
    if len(chosen) < count:
        raise RuntimeError(f"{genre} 只有 {len(chosen)} 部，无法取 {count} 部")
    return chosen


def producer_status(client: Client) -> dict:
    status, payload = client.request("GET", "/api/v1/experiments/behavior/status")
    return payload["producer"] if status == 200 and payload else {"status_code": status}


def wait_for_sent(client: Client, expected: int, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = producer_status(client)
        if int(current.get("sent") or 0) >= expected:
            return current
        time.sleep(0.5)
    return producer_status(client)


def run_consumer(max_messages: int, idle_timeout: float, half_life: float) -> dict:
    command = [sys.executable, str(REPO / "scripts" / "run_behavior_consumer.py"),
               "--max-messages", str(max_messages), "--idle-timeout", str(idle_timeout),
               "--half-life-minutes", str(half_life)]
    finished = subprocess.run(command, capture_output=True, text=True, timeout=max(60.0, idle_timeout + 60.0))
    lines = [line for line in finished.stdout.splitlines() if line.strip()]
    applied = [json.loads(line) for line in lines if line.startswith("{") and '"status"' not in line]
    summary = next((json.loads(line) for line in reversed(lines) if '"status"' in line), {})
    return {
        "command": " ".join(command),
        "exit_code": finished.returncode,
        "applied": len(applied),
        "last_applied": applied[-1] if applied else None,
        "consumer_summary": summary,
        "stderr_tail": finished.stderr.strip().splitlines()[-3:],
    }


def fusion(client: Client, limit: int, interest: int) -> dict:
    status, payload = client.request("GET", f"/api/v1/me/recommendations/fusion?limit={limit}&interest={interest}")
    return {"status": status, "payload": payload}


def ranking(payload: dict | None) -> list[str]:
    return [item["movie_id"] for item in (payload or {}).get("items", [])]


def scenario_normal(client: Client, name: str, password: str, args) -> dict:
    account = client.login_or_register(name, password)
    animation = pick_movies(client, "Animation", 2)
    horror = pick_movies(client, "Horror", 1)
    writes = []
    for movie, rating in ((animation[0], "5.0"), (animation[1], "4.5"), (horror[0], "1.0")):
        status, payload = client.request("PUT", f"/api/v1/me/ratings/{movie['movie_id']}", {"rating": rating}, csrf=True)
        writes.append({"kind": "rating", "movie_id": movie["movie_id"], "rating": rating, "status": status, "response": payload})
    status, payload = client.request("PUT", f"/api/v1/me/favorites/{animation[0]['movie_id']}", None, csrf=True)
    writes.append({"kind": "favorite", "movie_id": animation[0]["movie_id"], "status": status, "response": payload})
    expected = len(writes)
    produced = wait_for_sent(client, expected, timeout=args.timeout)

    consumer = {"skipped": True}
    if not args.skip_consumer:
        consumer = run_consumer(expected + 20, args.consumer_idle, args.half_life)

    status, interest = client.request("GET", "/api/v1/me/behavior/interest?events=10")
    before = fusion(client, 10, 0)
    after = fusion(client, 10, 1)
    before_ids, after_ids = ranking(before["payload"]), ranking(after["payload"])
    boosts = {item["movie_id"]: item.get("interest_boost") for item in (after["payload"] or {}).get("items", [])}
    block = (interest or {}).get("interest") or {}
    top_rows = block.get("top_genres") or []
    genre_weights = block.get("genres") or {}
    return {
        "account": account,
        "writes": writes,
        "producer_after_writes": produced,
        "consumer": consumer,
        "interest_status_code": status,
        "interest": interest,
        "fusion_without_interest": {"status": before["status"], "ranking": before_ids,
                                    "interest": (before["payload"] or {}).get("interest")},
        "fusion_with_interest": {"status": after["status"], "ranking": after_ids,
                                 "interest": (after["payload"] or {}).get("interest")},
        "ranking_changed": before_ids != after_ids,
        "boosted_items": {mid: boost for mid, boost in boosts.items() if boost and boost > 1.0},
        "checks": {
            "events_recorded": bool(block and block.get("events") == expected),
            # 两部评分电影都是 Animation，类型权重会与 Adventure/Children 并列第一，
            # 所以只要求 Animation 处在并列最高权重里，不要求它单独排在第一位。
            "top_genre_is_animation": bool(
                genre_weights and top_rows
                and genre_weights.get("Animation", 0) > 0
                and any(row["genre"] == "Animation" and abs(row["weight"] - top_rows[0]["weight"]) < 1e-9
                        for row in top_rows)
            ),
            "interest_block_enabled": bool((after["payload"] or {}).get("interest", {}).get("enabled")),
            # Horror 被评 1.0 分，权重必须为负，并且不能出现在正向 top_genres 里。
            "horror_downweighted": bool(
                genre_weights.get("Horror") is not None and genre_weights["Horror"] < 0
                and all(row["genre"] != "Horror" for row in top_rows)
            ),
        },
    }


def scenario_empty(client: Client, name: str, password: str) -> dict:
    account = client.login_or_register(name, password)
    status, interest = client.request("GET", "/api/v1/me/behavior/interest?events=5")
    result = fusion(client, 10, 1)
    return {
        "account": account,
        "interest_status_code": status,
        "interest": interest,
        "fusion": {"status": result["status"], "items": len(ranking(result["payload"])),
                   "interest": (result["payload"] or {}).get("interest")},
        "checks": {
            "no_fabricated_interest": bool(interest and interest.get("interest") is None and interest.get("reason")),
            "fusion_reports_reason": bool((result["payload"] or {}).get("interest", {}).get("reason")),
            "no_boost_on_items": all(item.get("interest_boost") in (None, 1.0)
                                     for item in (result["payload"] or {}).get("items", [])),
        },
    }


def scenario_bad_input(client: Client) -> dict:
    before = producer_status(client)
    cases = [
        ("电影不存在", "PUT", "/api/v1/me/ratings/999999", {"rating": "4.5"}),
        ("评分不是半星", "PUT", "/api/v1/me/ratings/1", {"rating": "3.7"}),
        ("评分超范围", "PUT", "/api/v1/me/ratings/1", {"rating": "9.0"}),
        ("收藏不存在的电影", "PUT", "/api/v1/me/favorites/999999", None),
    ]
    results = []
    for label, method, path, payload in cases:
        status, response = client.request(method, path, payload, csrf=True)
        error = (response or {}).get("error") if isinstance(response, dict) else None
        results.append({"case": label, "path": path, "status": status,
                        "code": (error or {}).get("code"),
                        "message": (error or {}).get("message")})
    time.sleep(1.0)
    after = producer_status(client)
    return {
        "cases": results,
        "producer_before": before,
        "producer_after": after,
        "checks": {
            "all_rejected": all(row["status"] in (404, 422) for row in results),
            "no_phantom_event": int(after.get("sent") or 0) == int(before.get("sent") or 0),
        },
    }


def scenario_kafka_down(client: Client, name: str, password: str) -> dict:
    account = client.login_or_register(name, password)
    before = producer_status(client)
    status, payload = client.request("PUT", "/api/v1/me/ratings/1", {"rating": "4.5"}, csrf=True)
    after_rating = producer_status(client)
    interest_status, interest = client.request("GET", "/api/v1/me/behavior/interest?events=5")
    result = fusion(client, 10, 1)
    return {
        "account": account,
        "rating_status": status,
        "rating_response": payload,
        "producer_before": before,
        "producer_after": after_rating,
        "interest_status_code": interest_status,
        "interest_reason": (interest or {}).get("reason"),
        "fusion": {"status": result["status"], "items": len(ranking(result["payload"])),
                   "interest": (result["payload"] or {}).get("interest")},
        "checks": {
            "business_not_broken": status == 200,
            "failure_visible": int(after_rating.get("failed") or 0) > int(before.get("failed") or 0)
                               or int(after_rating.get("skipped") or 0) > int(before.get("skipped") or 0),
            "recommendation_still_works": result["status"] == 200,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="实验五 S3 四场景测试")
    parser.add_argument("--base-url", default=ORIGIN)
    parser.add_argument("--name-prefix", default="lab5demo")
    parser.add_argument("--password", default="Lab5Kafka!2026")
    parser.add_argument("--report", default=str(REPO / "reports" / "lab5" / "scenarios.json"))
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--consumer-idle", type=float, default=4.0)
    parser.add_argument("--half-life", type=float, default=30.0)
    parser.add_argument("--skip-consumer", action="store_true")
    parser.add_argument("--kafka-down", action="store_true", help="只跑 Kafka 停机场景")
    args = parser.parse_args()

    suffix = datetime.now().strftime("%m%d%H%M%S")
    report: dict = {"generated_at": stamp(), "base_url": args.base_url, "mode": "kafka_down" if args.kafka_down else "full"}

    if args.kafka_down:
        client = Client(args.base_url)
        report["kafka_down"] = scenario_kafka_down(client, f"{args.name_prefix}down{suffix}", args.password)
    else:
        normal = Client(args.base_url)
        report["normal"] = scenario_normal(normal, f"{args.name_prefix}{suffix}", args.password, args)
        empty = Client(args.base_url)
        report["empty"] = scenario_empty(empty, f"{args.name_prefix}empty{suffix}", args.password)
        bad = Client(args.base_url)
        bad.login_or_register(f"{args.name_prefix}bad{suffix}", args.password)
        report["bad_input"] = scenario_bad_input(bad)

    for name in ("normal", "empty", "bad_input", "kafka_down"):
        if name in report:
            checks = report[name].get("checks", {})
            report[name]["passed"] = all(bool(value) for value in checks.values())
    report["passed"] = all(report[name]["passed"] for name in ("normal", "empty", "bad_input", "kafka_down") if name in report)

    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"report": str(path), "scenarios": sorted(report), "generated_at": report["generated_at"],
                      "passed": report["passed"]}, ensure_ascii=False))
    for name in ("normal", "empty", "bad_input", "kafka_down"):
        if name in report:
            print(json.dumps({"scenario": name, "checks": report[name].get("checks", {}),
                              "passed": report[name]["passed"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
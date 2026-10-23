"""抓取真实电影海报并写入服务端缓存（实验六扩展 · 演示用）。

原理：调用后端 `GET /api/v1/movies/{movie_id}/poster/status?fetch=1`，由服务端按 `tmdb_id`
到公开电影资料页取回真实海报（600×900 JPEG）并缓存到 `CINELAB_POSTER_DIR`；本脚本只负责
“把全部电影过一遍”，不自己下载图片，因此不会把图片流量重复拉一遍到宿主。

用法（宿主，可随时中断续跑；已缓存的电影会被服务端直接跳过）：

    D:\\Python311\\python.exe scripts\\fetch_posters.py                 # 全部电影
    D:\\Python311\\python.exe scripts\\fetch_posters.py --limit 200     # 只跑前 200 部
    D:\\Python311\\python.exe scripts\\fetch_posters.py --ids 1,2,3     # 指定 movie_id
    D:\\Python311\\python.exe scripts\\fetch_posters.py --workers 2 --delay 0.6

报告写入 `reports/lab6/posters.json`（缓存数量、失败原因分布、耗时与速率）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8080"
API = BASE_URL + "/api/v1"
REPORT = REPO / "reports" / "lab6" / "posters.json"
PER_PAGE = 100
RETRY_SLEEPS = (5.0, 15.0, 40.0)

_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def get_json(url: str, timeout: float = 30.0) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def catalog_ids(base: str) -> list[int]:
    """从目录分页接口取出全部 movie_id（升序，即先热后冷）。"""
    ids: list[int] = []
    page = 1
    while True:
        payload = get_json(f"{base}/movies?page={page}&per_page={PER_PAGE}")
        items = payload.get("items") or []
        if not items:
            break
        for item in items:
            value = item.get("id", item.get("movie_id"))
            if value is not None:
                ids.append(int(value))
        total = int(payload.get("total") or 0)
        if len(ids) >= total or len(items) < PER_PAGE:
            break
        page += 1
    return sorted(set(ids))


def warm(base: str, movie_id: int, delay: float) -> dict:
    """预热一部电影的海报缓存；返回服务端状态（含 cached / reason）。"""
    url = f"{base}/movies/{movie_id}/poster/status?fetch=1"
    last_error = ""
    for attempt, pause in enumerate((0.0, *RETRY_SLEEPS)):
        if pause:
            time.sleep(pause)
        try:
            payload = get_json(url, timeout=90.0)
            payload["movie_id"] = movie_id
            time.sleep(delay)
            return payload
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = f"{type(error).__name__}"
            if attempt >= len(RETRY_SLEEPS):
                break
    return {"movie_id": movie_id, "cached": False, "reason": last_error or "请求失败", "error": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取真实电影海报并写入服务端缓存")
    parser.add_argument("--base-url", default=BASE_URL, help=f"站点地址（默认 {BASE_URL}）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 部（默认全部）")
    parser.add_argument("--ids", default="", help="只处理这些 movie_id（逗号分隔）")
    parser.add_argument("--start", type=int, default=0, help="从该 movie_id 起（含）")
    parser.add_argument("--end", type=int, default=0, help="到该 movie_id 止（含）")
    parser.add_argument("--workers", type=int, default=3, help="并发线程数（默认 3）")
    parser.add_argument("--delay", type=float, default=0.4, help="每个请求后的等待秒数（默认 0.4）")
    parser.add_argument("--report", default=str(REPORT), help="报告输出路径")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"

    started = time.time()
    try:
        health = get_json(base.replace("/api/v1", "") + "/health/ready", timeout=20.0)
    except Exception as error:  # noqa: BLE001 - 只用于给出可读提示
        log(f"无法访问 {base}：{type(error).__name__} {error}")
        return 2
    log(f"服务端就绪：dataset={health.get('dataset_version')}")

    if args.ids:
        movie_ids = [int(part) for part in args.ids.replace("，", ",").split(",") if part.strip()]
    else:
        movie_ids = catalog_ids(base)
        if args.start:
            movie_ids = [mid for mid in movie_ids if mid >= args.start]
        if args.end:
            movie_ids = [mid for mid in movie_ids if mid <= args.end]
        if args.limit:
            movie_ids = movie_ids[: args.limit]
    if not movie_ids:
        log("没有需要处理的电影")
        return 1

    log(f"待处理 {len(movie_ids)} 部（movie_id {movie_ids[0]} … {movie_ids[-1]}），并发 {args.workers}，间隔 {args.delay}s")
    counters: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    done = 0

    def task(movie_id: int) -> dict:
        return warm(base, movie_id, args.delay)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for result in pool.map(task, movie_ids):
            done += 1
            if result.get("cached"):
                counters["cached"] += 1
            elif result.get("error"):
                counters["error"] += 1
                reasons[str(result.get("reason"))] += 1
            else:
                counters["fallback"] += 1
                reasons[str(result.get("reason"))] += 1
            if done % 50 == 0 or done == len(movie_ids):
                elapsed = time.time() - started
                rate = done / elapsed if elapsed else 0.0
                remain = (len(movie_ids) - done) / rate if rate else 0.0
                log(
                    f"进度 {done}/{len(movie_ids)} · 命中 {counters['cached']} · 回退 {counters['fallback']}"
                    f" · 失败 {counters['error']} · {rate:.2f} 部/秒 · 约剩 {remain / 60:.1f} 分钟"
                )

    elapsed = time.time() - started
    try:
        summary = get_json(f"{base}/experiments/poster-cache", timeout=20.0)
    except Exception:  # noqa: BLE001 - 概览失败不影响结论
        summary = {}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "processed": len(movie_ids),
        "cached": counters["cached"],
        "fallback": counters["fallback"],
        "errors": counters["error"],
        "seconds": round(elapsed, 1),
        "requests_per_second": round(len(movie_ids) / elapsed, 2) if elapsed else None,
        "reasons": dict(reasons.most_common()),
        "cache": summary,
        "workers": args.workers,
        "delay_seconds": args.delay,
    }
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    log(
        f"完成：命中 {counters['cached']} / 回退 {counters['fallback']} / 失败 {counters['error']}"
        f"，缓存共 {summary.get('cached')} 张（{summary.get('bytes', 0) / 1048576:.1f} MiB），报告 {path}"
    )
    return 0 if counters["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
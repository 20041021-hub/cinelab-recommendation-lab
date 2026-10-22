"""实验五 S3：行为事件消费端 —— Kafka 事件 → 可追溯的短期兴趣状态。

真实链路（没有任何 Mock / 随机结果）：

    前端评分或收藏 → MySQL 落库 → Kafka ``cinelab.behavior.business``
        → 本消费端按分区/位移消费 → ``reports/lab5/state/events.jsonl``（逐条证据）
        → ``reports/lab5/state/interest.json``（按类型加权 + 时间衰减的短期兴趣）
        → API 读取状态文件，在融合推荐上做**规则明确**的重排（见 backend/app/interest.py）

状态文件是"可追溯"的关键：每条事件都记录 topic/partition/offset/event_id/occurred_at/consumed_at，
状态里保存 offsets 与已应用事件数量，随时能回答"这条兴趣是哪几条事件推出来的"。

用法（宿主侧运行，Kafka 走 127.0.0.1:29092）：

    python scripts/run_behavior_consumer.py --max-messages 20          # 消费 20 条就退出（取证用）
    python scripts/run_behavior_consumer.py --idle-timeout 120         # 空闲 120 秒后退出
    python scripts/run_behavior_consumer.py                            # 常驻（Ctrl-C 停止）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = REPO / "reports" / "lab5" / "state"
DEFAULT_TOPIC = "cinelab.behavior.business"
SCHEMA = "cinelab-interest-v1"
POSITIVE_RATING = 4.0
NEUTRAL_LOW = 3.0  # 2.5/3.0/3.5 记为中性：只记录事件，不改变兴趣权重


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def decay(seconds: float, half_life_minutes: float) -> float:
    if half_life_minutes <= 0 or seconds <= 0:
        return 1.0
    return 0.5 ** (seconds / (half_life_minutes * 60.0))


def rating_weight(rating: float) -> float:
    """正反馈加分、负反馈减分、中性不加不减——规则写在这里，别处不再假设。"""
    if rating >= POSITIVE_RATING:
        return (rating - NEUTRAL_LOW) / 2.0
    if rating <= NEUTRAL_LOW - 1.0:  # ≤2.0
        return -(NEUTRAL_LOW - rating) / 2.0
    return 0.0


def empty_state(topic: str, group: str, half_life_minutes: float) -> dict:
    return {
        "schema": SCHEMA,
        "topic": topic,
        "group": group,
        "half_life_minutes": half_life_minutes,
        "state_version": 0,
        "events_applied": 0,
        "last_event_at": None,
        "generated_at": stamp(),
        "offsets": {},
        "subjects": {},
    }


def load_state(path: Path, topic: str, group: str, half_life_minutes: float) -> dict:
    if not path.exists():
        return empty_state(topic, group, half_life_minutes)
    state = json.loads(path.read_text(encoding="utf-8"))
    state["half_life_minutes"] = half_life_minutes
    state.setdefault("subjects", {})
    state.setdefault("offsets", {})
    return state


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def subject_of(state: dict, key: str) -> dict:
    return state["subjects"].setdefault(key, {
        "genres": {},
        "positive_movies": [],
        "negative_movies": [],
        "counts": {},
        "events": 0,
        "first_event_at": None,
        "last_event_at": None,
        "updated_at": None,
    })


def apply_event(state: dict, event: dict, half_life_minutes: float) -> dict:
    """把一条事件并入短期兴趣，返回状态里的呈现记录。"""
    key = str(event.get("subject_key") or "")
    if not key:
        raise ValueError("事件缺少 subject_key")
    occurred = parse_time(str(event.get("occurred_at")))
    subject = subject_of(state, key)
    previous = subject.get("updated_at")
    factor = decay((occurred - parse_time(previous)).total_seconds(), half_life_minutes) if previous else 1.0

    kind = str(event.get("type") or "")
    rating = event.get("rating")
    increment = 0.0
    if kind == "rating" and rating is not None:
        increment = rating_weight(float(rating))
    elif kind == "favorite":
        increment = 1.5
    elif kind == "unfavorite":
        increment = -1.5

    genres = [g for g in str(event.get("genres") or "").split("|") if g and g != "(no genres listed)"]
    for genre in genres:
        subject["genres"][genre] = round(subject["genres"].get(genre, 0.0) * factor + increment, 6)

    movie = str(event.get("movie_id") or "")
    if increment > 0 and movie:
        if movie in subject["negative_movies"]:
            subject["negative_movies"].remove(movie)
        if movie not in subject["positive_movies"]:
            subject["positive_movies"].append(movie)
    elif increment < 0 and movie:
        if movie in subject["positive_movies"]:
            subject["positive_movies"].remove(movie)
        if movie not in subject["negative_movies"]:
            subject["negative_movies"].append(movie)

    subject["counts"][kind] = subject["counts"].get(kind, 0) + 1
    subject["events"] += 1
    subject["first_event_at"] = subject.get("first_event_at") or occurred.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    subject["last_event_at"] = occurred.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    subject["updated_at"] = max(previous or "", subject["last_event_at"]) or subject["last_event_at"]
    return {"subject_key": key, "genres": genres, "increment": round(increment, 6), "decay_factor": round(factor, 6)}


def main() -> int:
    parser = argparse.ArgumentParser(description="实验五 S3 行为事件消费端")
    parser.add_argument("--bootstrap", default=os.environ.get("CINELAB_KAFKA_BOOTSTRAP", "127.0.0.1:29092"))
    parser.add_argument("--topic", default=os.environ.get("CINELAB_KAFKA_TOPIC", DEFAULT_TOPIC))
    parser.add_argument("--group", default="lab5-short-term-interest")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--half-life-minutes", type=float, default=30.0)
    parser.add_argument("--max-messages", type=int, default=0, help="消费多少条后退出，0 表示不限制")
    parser.add_argument("--idle-timeout", type=float, default=0.0, help="空闲多少秒后退出，0 表示不限制")
    parser.add_argument("--latest", action="store_true", help="只消费启动之后的事件（默认从头消费）")
    args = parser.parse_args()

    from kafka import KafkaConsumer, TopicPartition

    state_dir = Path(args.state_dir)
    state_path = state_dir / "interest.json"
    events_path = state_dir / "events.jsonl"
    state_dir.mkdir(parents=True, exist_ok=True)

    consumer = KafkaConsumer(
        bootstrap_servers=args.bootstrap,
        group_id=args.group,
        enable_auto_commit=False,
        auto_offset_reset="latest" if args.latest else "earliest",
        consumer_timeout_ms=1000,
        value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
        key_deserializer=lambda raw: raw.decode("utf-8") if raw else None,
    )
    consumer.subscribe([args.topic])
    state = load_state(state_path, args.topic, args.group, args.half_life_minutes)
    applied_ids = set(state.get("recent_event_ids", []))
    print(json.dumps({
        "status": "consumer_started",
        "bootstrap": args.bootstrap,
        "topic": args.topic,
        "group": args.group,
        "state_dir": str(state_dir),
        "half_life_minutes": args.half_life_minutes,
        "resuming_state_version": state.get("state_version", 0),
    }, ensure_ascii=False), flush=True)

    consumed = 0
    idle_since = time.monotonic()
    try:
        while True:
            batch = consumer.poll(timeout_ms=500)
            if not batch:
                if args.idle_timeout and time.monotonic() - idle_since > args.idle_timeout:
                    print(json.dumps({"status": "consumer_idle_exit", "consumed": consumed}, ensure_ascii=False), flush=True)
                    break
                continue
            idle_since = time.monotonic()
            for partition, records in batch.items():
                for record in records:
                    event = record.value
                    event_id = str(event.get("event_id") or "")
                    duplicate = bool(event_id) and event_id in applied_ids
                    if duplicate:
                        record_summary = {"status": "duplicate_skipped", "event_id": event_id, "offset": record.offset}
                    else:
                        try:
                            effect = apply_event(state, event, args.half_life_minutes)
                        except Exception as exc:  # 坏事件不能让消费端崩掉：记证据、继续消费
                            effect = {"error": f"{type(exc).__name__}: {exc}"}
                        line = {
                            "topic": record.topic,
                            "partition": record.partition,
                            "offset": record.offset,
                            "consumed_at": stamp(),
                            **{k: v for k, v in event.items() if k != "schema"},
                            "effect": effect,
                        }
                        with events_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
                        if event_id:
                            applied_ids.add(event_id)
                        state["events_applied"] = int(state.get("events_applied", 0)) + 1
                        state["last_event_at"] = event.get("occurred_at") or state.get("last_event_at")
                        record_summary = {"status": "applied", "event_id": event_id, "subject_key": event.get("subject_key"), "effect": effect}
                    state["state_version"] = int(state.get("state_version", 0)) + 1
                    state["generated_at"] = stamp()
                    state["offsets"][f"{record.topic}:{record.partition}"] = record.offset + 1
                    state["recent_event_ids"] = list(applied_ids)[-200:]
                    write_json_atomic(state_path, state)
                    consumed += 1
                    print(json.dumps({
                        "consumed": consumed,
                        "partition": record.partition,
                        "offset": record.offset,
                        "state_version": state["state_version"],
                        **record_summary,
                    }, ensure_ascii=False), flush=True)
                    if args.max_messages and consumed >= args.max_messages:
                        print(json.dumps({"status": "consumer_max_messages_exit", "consumed": consumed}, ensure_ascii=False), flush=True)
                        return 0
                consumer.commit()
    except KeyboardInterrupt:
        print(json.dumps({"status": "consumer_interrupted", "consumed": consumed}, ensure_ascii=False), flush=True)
    finally:
        try:
            consumer.commit()
        except Exception:
            pass
        consumer.close()
    print(json.dumps({
        "status": "consumer_stopped",
        "consumed": consumed,
        "state_version": state.get("state_version", 0),
        "subjects": sorted(state.get("subjects", {})),
        "state_path": str(state_path),
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""实验五 S3：真实业务行为事件的生产端（Kafka）。

设计要点（对应手册对 S3 的要求与"必须真实运行、不能静默伪造数据"）：

* 事件只在数据库事务提交成功之后发送：Kafka 里存在的事件，一定对应库里真实的一行评分/收藏。
* 生产端不参与业务成败：Kafka 不可用时接口照常 200，只把失败记进状态里（异常场景测试要求）。
  连续失败会进入短暂冷却，避免每个请求都等满超时。
* 事件信封 ``cinelab-behavior-v1`` 以 ``subject_key`` 作为消息键，同一账号的事件进同一分区，保序。
* 未配置 ``CINELAB_KAFKA_BOOTSTRAP`` 时整个模块处于"未启用"状态，接口不受任何影响；
  镜像里没有 kafka-python 时同样降级（status 里会写明原因）。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA = "cinelab-behavior-v1"
DEFAULT_TOPIC = "cinelab.behavior.business"
POSITIVE_RATING = 4.0
PRODUCER = "backend/app/activity.py"

logger = logging.getLogger("cinelab.behavior")

_TRUTHY = {"1", "true", "yes", "on"}


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def bootstrap() -> str:
    return (os.environ.get("CINELAB_KAFKA_BOOTSTRAP") or "").strip()


def topic() -> str:
    return (os.environ.get("CINELAB_KAFKA_TOPIC") or "").strip() or DEFAULT_TOPIC


def send_timeout() -> float:
    return _number("CINELAB_KAFKA_TIMEOUT", 2.0)


def cooldown_seconds() -> float:
    return _number("CINELAB_KAFKA_COOLDOWN", 5.0)


def enabled() -> bool:
    """是否启用事件投递：必须先显式配置 broker 地址。"""
    if (os.environ.get("CINELAB_KAFKA_ENABLED") or "1").strip().lower() not in _TRUTHY:
        return False
    return bool(bootstrap())


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BehaviorPublisher:
    """线程安全的 Kafka 生产端；懒连接 + 失败冷却 + 内存计数。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._producer: Any = None
        self._cooldown_until = 0.0
        self._import_error: str | None = None
        self.sent = 0
        self.failed = 0
        self.skipped = 0
        self.last_error: str | None = None
        self.last_sent_at: str | None = None
        self.last_skipped_reason: str | None = None
        self.topic = topic()
        try:  # 镜像里可能没有 kafka-python（课程离线镜像就没有），此时整体降级
            import kafka  # noqa: F401
        except Exception as exc:  # pragma: no cover - 取决于镜像内容
            self._import_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Kafka 生产端不可用，行为事件将只记录状态：%s", self._import_error)
        atexit.register(self.close)

    # ---- 状态 -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._import_error:
                state = "client_missing"
            elif not enabled():
                state = "disabled"
            elif self._producer is not None:
                state = "connected"
            elif self._cooldown_until > time.monotonic():
                state = "cooldown"
            else:
                state = "idle"
            return {
                "enabled": enabled() and self._import_error is None,
                "state": state,
                "bootstrap": bootstrap(),
                "topic": self.topic,
                "timeout_seconds": send_timeout(),
                "sent": self.sent,
                "failed": self.failed,
                "skipped": self.skipped,
                "last_error": self.last_error,
                "last_sent_at": self.last_sent_at,
                "last_skipped_reason": self.last_skipped_reason,
            }

    def close(self) -> None:
        with self._lock:
            if self._producer is not None:
                try:
                    self._producer.close(timeout=2)
                except Exception:  # pragma: no cover - 关闭失败不影响进程退出
                    pass
                self._producer = None

    # ---- 发送 -------------------------------------------------------------

    def _client(self) -> Any:
        if self._producer is not None:
            return self._producer
        from kafka import KafkaProducer  # 延迟导入，未启用时不需要这个依赖

        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap(),
            key_serializer=lambda value: value.encode("utf-8"),
            value_serializer=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            acks=1,
            retries=1,
            linger_ms=0,
            request_timeout_ms=int(send_timeout() * 1000),
            max_block_ms=int(send_timeout() * 1000),
        )
        return self._producer

    def publish(self, event: dict[str, Any]) -> bool:
        """投递一条事件；返回是否成功。任何失败都只记状态，不抛给调用方。"""
        if not enabled():
            with self._lock:
                self.skipped += 1
                self.last_skipped_reason = "CINELAB_KAFKA_BOOTSTRAP 未配置"
            return False
        if self._import_error is not None:
            with self._lock:
                self.skipped += 1
                self.last_skipped_reason = f"kafka-python 不可用：{self._import_error}"
            return False
        now = time.monotonic()
        if now < self._cooldown_until:
            with self._lock:
                self.skipped += 1
                self.last_skipped_reason = "Kafka 发送失败后的冷却期内"
            return False

        envelope = {
            "event_id": uuid.uuid4().hex,
            "schema": SCHEMA,
            "producer": PRODUCER,
            "occurred_at": _stamp(),
            "runtime_mode": "business",
            **event,
        }
        try:
            with self._lock:
                producer = self._client()
            producer.send(self.topic, key=str(envelope["subject_key"]), value=envelope).get(timeout=send_timeout())
        except Exception as exc:
            with self._lock:
                self.failed += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._producer = None
                self._cooldown_until = time.monotonic() + cooldown_seconds()
            logger.warning("行为事件投递失败（业务不受影响）：%s", self.last_error)
            return False
        with self._lock:
            self.sent += 1
            self.last_sent_at = envelope["occurred_at"]
            self.last_error = None
            self._cooldown_until = 0.0
        return True


def build_event(
    kind: str,
    account_id: int,
    movie_id: int,
    *,
    rating: str | None = None,
    genres: str | None = None,
) -> dict[str, Any]:
    """组装行为事件；字段与消费端约定一致（消费端按 genres 累积短期兴趣）。"""
    event: dict[str, Any] = {
        "type": kind,
        "subject_key": f"app:{account_id}",
        "account_id": str(account_id),
        "movie_id": str(movie_id),
    }
    if rating is not None:
        event["rating"] = rating
        event["positive"] = float(rating) >= POSITIVE_RATING
    if genres:
        event["genres"] = genres
    return event


publisher = BehaviorPublisher()


def publish_event(kind: str, account_id: int, movie_id: int, *, rating: str | None = None, genres: str | None = None) -> bool:
    return publisher.publish(build_event(kind, account_id, movie_id, rating=rating, genres=genres))
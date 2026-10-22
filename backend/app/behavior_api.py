"""实验五 S3：行为事件与短期兴趣的只读接口（前端状态反馈与取证用）。

* ``GET /me/behavior/interest``：当前登录账号的短期兴趣——来自哪条 Kafka 事件、
  衰减后最强的类型、消费位移、状态是否过期；同时给出生产端（Kafka 投递）的实时状态。
* ``GET /experiments/behavior/status``：不带账号信息的整体状态，用于排查"Kafka 挂了/消费端没跑"。

这两个接口只读状态，不改任何推荐结果；融合推荐里的重排发生在 ``backend/app/fusion.py``。
"""
from typing import Annotated

from fastapi import APIRouter, Query, Request

from . import interest
from .behavior import publisher
from .security import ActorDependency

router = APIRouter(tags=["实验五扩展：行为事件与短期兴趣"])


@router.get("/me/behavior/interest")
def my_interest(request: Request, actor: ActorDependency, events: Annotated[int, Query(ge=0, le=50)] = 10):
    subject_key = f"app:{actor.account_id}"
    state = interest.snapshot(subject_key)
    if state is None:
        reason = ("短期兴趣功能未启用（CINELAB_INTEREST_ENABLED=0）" if not interest.wanted()
                  else "消费端还没有写出本账号的短期兴趣（先评分或收藏，再运行消费端）")
    else:
        reason = "状态已过期，融合推荐不会按它加分" if state.get("stale") else None
    return {
        "subject_key": subject_key,
        "interest": state,
        "reason": reason,
        "producer": publisher.status(),
        "consumer": {key: value for key, value in interest.status().items() if key != "subjects"},
        "events": interest.recent_events(subject_key, events),
    }


@router.get("/experiments/behavior/status")
def behavior_status():
    return {"producer": publisher.status(), "consumer": interest.status()}
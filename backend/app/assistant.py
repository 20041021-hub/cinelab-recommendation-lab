"""实验六 S2：自然语言 → 受控工具调用 → 真实电影数据。

设计要点（对应实验手册"电影数据必须来自真实业务接口"）：

* 模型**不能**直接访问数据库或推荐器：它只能调用这里白名单里的四个工具；
* 四个工具都是只读的，数据来自真实来源——电影目录（MySQL ``movies`` 表）、
  ItemCF 共现工件、已注册的推荐 Provider（``recommender.contracts.recommend``）、
  用户真实评分与收藏；
* 工具参数逐个校验（类型、取值域、存在性、条数上限），不合法的调用被拒绝并
  记进 ``tool_calls`` 证据里，**不会**用编造的数据兜底；
* 返回给前端的 ``items`` 只由工具输出构成（去重、上限截断），模型的话术无法
  凭空插入电影；回答文本里出现但工具没返回过的片名会被记成 ``unverified_mentions``；
* 调用推荐类工具时，工具证据里带上算法名与该算法实验三的离线评价指标（precision_at_10 /
  recall_at_10，快照见 ``backend/app/lab3_metrics.json``），模型被要求在回答里如实说明，
  响应里另有结构化的 ``methods`` 字段，前端不依赖模型措辞也能展示"算法 + 准确率"；
* 外部模型不可用时返回 503 ``ASSISTANT_UNAVAILABLE``，并给出原因（缺 Key/超时/
  网络/HTTP 错误），绝不静默降级成"假推荐"。

路由（挂 /api/v1）：

* ``GET /me/assistant/query?q=...&limit=5``            当前登录账号
* ``GET /demo/history-users/{id}/assistant/query?q=...``  历史匿名用户（演示用）
* ``GET /experiments/assistant/status``                 模型配置与工具清单（不回显 Key）
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import bindparam, text

from recommender.business import read_history
from recommender.contracts import Algorithm, AlgorithmNotImplemented, RecommendationRequest, recommend
from recommender.data import ContractError
from .catalog import get_genres
from .config import DATASET, ROOT, SPLIT
from .errors import APIError
from .llm import MAX_TOOL_ROUNDS, LLMUnavailable, chat, configured, mode, model as llm_model, status as llm_status
from .schemas import IdPath, identifier
from .security import ActorDependency

router = APIRouter(tags=["实验六扩展：自然语言助手（受控工具调用）"])

MAX_QUESTION = 300
MIN_QUESTION = 2
MAX_LIMIT = 10
ITEM_LIMIT = 20
EARLIEST_YEAR, LATEST_YEAR = 1888, 2030
TOOL_NAMES = ("search_movies", "similar_movies", "recommend_movies", "my_history")
# 卡片排序：用户问的通常是"推荐"，所以推荐结果优先；同类按调用时间倒序（越近的越相关），
# my_history 只作为解释材料排在最后。
TOOL_PRIORITY = {"recommend_movies": 0, "similar_movies": 1, "search_movies": 2, "my_history": 3}

# 实验三的离线评价指标（只读快照，由 scripts/publish_lab3_metrics.py 生成）。
# 它是"数字"不是"模型"：API 不会因此加载任何 evaluation 工件或评价 Provider。
METRICS_PATH = Path(__file__).resolve().parent / "lab3_metrics.json"
ALGORITHM_LABELS = {
    "popular": "热门榜 Popular",
    "content": "内容推荐 Content",
    "itemcf": "ItemCF 协同过滤",
    "als": "Spark ALS 矩阵分解",
    "cold_start": "冷启动 Cold Start",
}
METRICS_PROTOCOL = "冻结验证集 · 4,209 位固定用户 · 正反馈≥4.0 · Top-10 · 宏平均"

SYSTEM_PROMPT = """你是课程系统"CineLab"里的电影推荐助手。
规则（必须遵守）：
1. 关于电影的任何事实（片名、类型、年份、评分）只能来自你调用的工具返回值；不得凭记忆作答。
2. 需要片单时先调用工具，再用中文简要说明推荐理由（每条 1 句话），并明确说出推荐了哪些电影。
3. 工具返回空结果时，直接说明"没有找到符合条件的电影"，不要用别的电影凑数。
4. 不要在回答里编造工具没有返回过的电影或年份；不要输出代码块或 JSON。
5. 当前用户的评分/收藏只能通过 my_history 工具查看。
6. 只要这次用了 recommend_movies 或 similar_movies，回答里就要写清楚①用了哪个算法（用工具证据里的 algorithm_label）、②它在这批固定用户上的准确率（工具证据里 offline_metrics 的 precision_at_10 与 recall_at_10，按百分比说），并注明这是固定验证集上的算法级离线评价，不是对本次提问的准确率承诺。
"""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "search_movies",
        "description": "按类型/年份在电影目录里检索电影（真实目录，8459 部）。",
        "parameters": {"type": "object", "properties": {
            "genres": {"type": "array", "items": {"type": "string"}, "description": "类型名，如 Animation、Comedy；必须来自目录真实类型"},
            "year_min": {"type": "integer", "description": "最早年份（可省略）"},
            "year_max": {"type": "integer", "description": "最晚年份（可省略）"},
            "limit": {"type": "integer", "description": "返回条数，1-20，默认 5"}},
            "required": ["genres"]}}},
    {"type": "function", "function": {
        "name": "similar_movies",
        "description": "给出与某部电影最相似的电影（基于真实共现相似度模型）。",
        "parameters": {"type": "object", "properties": {
            "movie_id": {"type": "string", "description": "电影 ID（目录里的数字 ID）"},
            "limit": {"type": "integer", "description": "返回条数，1-20，默认 5"}},
            "required": ["movie_id"]}}},
    {"type": "function", "function": {
        "name": "recommend_movies",
        "description": "调用系统里已注册的推荐算法，为当前用户生成真实推荐。",
        "parameters": {"type": "object", "properties": {
            "algorithm": {"type": "string", "enum": ["popular", "content", "itemcf", "als", "cold_start"]},
            "limit": {"type": "integer", "description": "返回条数，1-10，默认 5"}},
            "required": ["algorithm"]}}},
    {"type": "function", "function": {
        "name": "my_history",
        "description": "查看当前用户自己的评分和收藏（真实业务数据）。",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数，1-20，默认 5"}},
            "required": []}}},
]


# ---- 工具实现（全部只读，全部真实数据） --------------------------------------

@lru_cache(maxsize=1)
def _neighbours() -> dict[str, Any]:
    path = ROOT / "models/business/itemcf-v1/item-similarities.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    return artifact.get("items", {})


@lru_cache(maxsize=1)
def _offline_metrics() -> dict[str, dict[str, Any]]:
    """实验三离线评价（验证集）的只读快照，按算法索引。

    文件缺失时返回空字典：指标只用于"说明清楚"，绝不能因为展示数据而让问答失败。
    """
    if not METRICS_PATH.is_file():
        return {}
    payload = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    return {row["algorithm"]: row for row in payload.get("validation", []) if row.get("algorithm")}


def _metrics_for(algorithm: str) -> dict[str, Any] | None:
    row = _offline_metrics().get(algorithm)
    if not row:
        return None
    return {
        "partition": row.get("partition", "validation"),
        "users": row.get("users"),
        "empty_users": row.get("empty", 0),
        "precision_at_10": row.get("precision_at_10"),
        "recall_at_10": row.get("recall_at_10"),
        "model_version": row.get("model_version"),
        "protocol": METRICS_PROTOCOL,
    }


def _movie_rows(conn, movie_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not movie_ids:
        return {}
    ids = sorted({int(mid) for mid in movie_ids})
    statement = text("SELECT movie_id, movielens_title, title_zh, genres, release_year, poster_path "
                     "FROM movies WHERE movie_id IN :ids").bindparams(bindparam("ids", expanding=True))
    rows = conn.execute(statement, {"ids": ids}).mappings().all()
    return {str(row["movie_id"]): {
        "movie_id": str(row["movie_id"]),
        "title": row["title_zh"] or row["movielens_title"],
        "original_title": row["movielens_title"],
        "genres": [] if row["genres"] == "(no genres listed)" else row["genres"].split("|"),
        "year": row["release_year"],
        "poster_path": row["poster_path"],
    } for row in rows}


def _tool_search_movies(conn, subject, arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    genres = arguments.get("genres")
    if not isinstance(genres, list) or not genres or not all(isinstance(g, str) for g in genres):
        raise ValueError("genres 必须是至少一个类型名的数组")
    vocabulary = set(get_genres(conn))
    unknown = [g for g in genres if g not in vocabulary]
    if unknown:
        raise ValueError(f"目录里没有这些类型：{'、'.join(unknown)}（可用类型见 /api/v1/genres）")
    year_min = arguments.get("year_min")
    year_max = arguments.get("year_max")
    for value, label in ((year_min, "year_min"), (year_max, "year_max")):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not EARLIEST_YEAR <= value <= LATEST_YEAR):
            raise ValueError(f"{label} 必须是 {EARLIEST_YEAR}-{LATEST_YEAR} 的整数")
    if year_min is not None and year_max is not None and year_min > year_max:
        raise ValueError("year_min 不能大于 year_max")
    limit = arguments.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= ITEM_LIMIT:
        raise ValueError(f"limit 必须是 1-{ITEM_LIMIT} 的整数")

    where, values = [], {"limit": limit}
    for index, genre in enumerate(genres):
        where.append(f"genres LIKE :genre{index}")
        values[f"genre{index}"] = f"%{genre}%"
    if year_min is not None:
        where.append("release_year >= :year_min")
        values["year_min"] = year_min
    if year_max is not None:
        where.append("release_year <= :year_max")
        values["year_max"] = year_max
    clause = " AND ".join(where)
    rows = conn.execute(
        text(f"SELECT movie_id FROM movies WHERE {clause} "
             "ORDER BY (SELECT COUNT(*) FROM history_ratings r WHERE r.movie_id=movies.movie_id) DESC, movie_id "
             "LIMIT :limit"), values).scalars().all()
    details = _movie_rows(conn, [str(row) for row in rows])
    items = [details[str(mid)] for mid in rows if str(mid) in details]
    # LIKE 可能命中子串（如 "Comedy" 命中 "Comedy"），这里按真实类型列表复核
    items = [item for item in items if set(genres) <= set(item["genres"])]
    evidence = {"source": "business.movies（真实目录）", "genres": genres, "year_min": year_min, "year_max": year_max,
                "matched": len(items)}
    return items, evidence


def _tool_similar_movies(conn, subject, arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    movie_id = arguments.get("movie_id")
    if isinstance(movie_id, bool) or not isinstance(movie_id, (str, int)) or not re.fullmatch(r"[1-9][0-9]*", str(movie_id)):
        raise ValueError("movie_id 必须是目录里的数字 ID")
    movie_id = str(movie_id)
    limit = arguments.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= ITEM_LIMIT:
        raise ValueError(f"limit 必须是 1-{ITEM_LIMIT} 的整数")
    if not _movie_rows(conn, [movie_id]):
        raise ValueError(f"电影 {movie_id} 不在目录里")
    entry = _neighbours().get(movie_id)
    if not entry:
        raise ValueError(f"电影 {movie_id} 的共现样本不足，模型里没有它的相似邻居")
    neighbours = [row[0] for row in entry.get("neighbors", [])[:limit]]
    details = _movie_rows(conn, neighbours)
    items = []
    for row in entry.get("neighbors", [])[:limit]:
        detail = details.get(str(row[0]))
        if detail:
            items.append({**detail, "algorithm": "itemcf", "similarity": round(float(row[1]), 6),
                          "cooccurrence": int(row[2])})
    evidence = {"source": "models/business/itemcf-v1（真实共现模型）",
                "algorithm": "itemcf", "algorithm_label": ALGORITHM_LABELS["itemcf"],
                "model_version": "itemcf-cooccurrence-v1", "offline_metrics": _metrics_for("itemcf"),
                "movie_id": movie_id, "matched": len(items)}
    return items, evidence


def _tool_recommend_movies(conn, subject, arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    algorithm = arguments.get("algorithm")
    allowed = {member.value for member in Algorithm}
    if algorithm not in allowed:
        raise ValueError(f"algorithm 必须是 {'、'.join(sorted(allowed))} 之一")
    limit = arguments.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit 必须是 1-{MAX_LIMIT} 的整数")
    kind, uid = subject
    request = RecommendationRequest(subject_kind=kind, subject_id=uid, limit=limit,
                                    runtime_mode="business", dataset_version=DATASET, split_version=SPLIT)
    try:
        result = recommend(Algorithm(algorithm), request)
    except AlgorithmNotImplemented as exc:
        raise ValueError(f"算法 {algorithm} 还没有实现：{exc}") from None
    except ContractError as exc:
        raise ValueError(f"算法 {algorithm} 的模型没通过契约校验：{exc.code}") from None
    details = _movie_rows(conn, [item.movie_id for item in result.items])
    items = []
    for item in result.items:
        detail = details.get(item.movie_id)
        if detail:
            items.append({**detail, "algorithm": algorithm, "rank": item.rank, "score": item.score,
                          "model_version": result.model_version})
    evidence = {"source": f"recommender.contracts.{algorithm}（已注册 Provider）",
                "algorithm": algorithm, "algorithm_label": ALGORITHM_LABELS.get(algorithm, algorithm),
                "model_version": result.model_version, "offline_metrics": _metrics_for(algorithm),
                "matched": len(items)}
    return items, evidence


def _tool_my_history(conn, subject, arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    limit = arguments.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= ITEM_LIMIT:
        raise ValueError(f"limit 必须是 1-{ITEM_LIMIT} 的整数")
    kind, uid = subject
    request = RecommendationRequest(subject_kind=kind, subject_id=uid, limit=1,
                                    runtime_mode="business", dataset_version=DATASET, split_version=SPLIT)
    history = read_history(request, database=None)
    rated = list(history.ratings)[-limit:]
    favourites = list(history.favorite_movie_ids)[:limit]
    details = _movie_rows(conn, [item.movie_id for item in rated] + list(favourites))
    items = []
    for feedback in rated:
        detail = details.get(feedback.movie_id)
        if detail:
            items.append({**detail, "my_rating": feedback.rating, "kind": "rating"})
    for movie_id in favourites:
        detail = details.get(movie_id)
        if detail:
            items.append({**detail, "kind": "favorite"})
    evidence = {"source": "业务库 account_ratings/account_favorites 或 history_ratings（真实数据）",
                "ratings": len(rated), "favorites": len(favourites)}
    return items, evidence


HANDLERS = {"search_movies": _tool_search_movies, "similar_movies": _tool_similar_movies,
            "recommend_movies": _tool_recommend_movies, "my_history": _tool_my_history}


# ---- 编排 --------------------------------------------------------------------

def _run_tools(conn, subject, tool_calls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """执行模型请求的工具调用，返回（工具输出条目, 调用证据）。"""
    items: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw = function.get("arguments") or "{}"
        record: dict[str, Any] = {"tool": name, "arguments": None, "ok": False, "matched": 0, "error": None}
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(arguments, dict):
                raise ValueError("arguments 必须是 JSON 对象")
            record["arguments"] = arguments
        except ValueError as exc:
            record["error"] = f"参数不是合法 JSON：{exc}"
            trace.append(record)
            continue
        handler = HANDLERS.get(name)
        if handler is None:
            record["error"] = f"工具 {name or '(空)'} 不在白名单里"
            trace.append(record)
            continue
        try:
            produced, evidence = handler(conn, subject, arguments)
        except ValueError as exc:
            record["error"] = str(exc)
            trace.append(record)
            continue
        record.update(ok=True, matched=len(produced), evidence=evidence)
        # 每条结果都记住是哪个工具给的：前端能按来源分组，也让"数据不是编的"可追溯
        record["items"] = [{**item, "source_tool": name} for item in produced]
        items.extend(produced)
        trace.append(record)
    return items, trace


def _dedupe(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in items:
        movie_id = str(item.get("movie_id"))
        if movie_id in seen:
            continue
        seen.add(movie_id)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _normalise_title(value: str) -> str:
    """片名归一化：去掉结尾的年份括号、压缩空白、统一小写。

    模型在回答里通常写《Toy Story》，而目录里的标题是 ``Toy Story (1995)``——
    不归一化就会把真实数据误判成"编造"。
    """
    text = re.sub(r"\s*\(\d{4}\)\s*$", "", str(value or "")).strip()
    return re.sub(r"\s+", " ", text).casefold()


def _unverified_mentions(answer: str, items: list[dict[str, Any]]) -> list[str]:
    """回答里用《》提到的片名，如果不在工具结果里，就是一次"疑似编造"。"""
    allowed = set()
    for item in items:
        for key in ("title", "original_title"):
            value = str(item.get(key) or "")
            if value:
                allowed.add(_normalise_title(value))
    mentioned = set(re.findall(r"《([^》]{1,80})》", answer or ""))
    return sorted(name for name in mentioned if name and _normalise_title(name) not in allowed)


def _messages(question: str) -> list[dict[str, Any]]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]


def answer_question(subject: tuple[str, int], question: str, limit: int, database) -> dict[str, Any]:
    if not configured():
        raise APIError(503, "ASSISTANT_UNAVAILABLE", "自然语言助手未配置外部模型 API Key",
                       {"reason": "missing_key", "llm": llm_status()})
    asked: tuple[str, int] = subject
    with database.connection() as conn:
        messages = _messages(question)
        trace: list[dict[str, Any]] = []
        collected: list[dict[str, Any]] = []
        answer_text = ""
        rounds = 0
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        try:
            while rounds < MAX_TOOL_ROUNDS:
                rounds += 1
                message, usage = chat(messages, TOOLS)
                for name in usage_total:
                    usage_total[name] += int(usage.get(name) or 0)
                calls = message.get("tool_calls") or []
                if not calls:
                    answer_text = str(message.get("content") or "").strip()
                    break
                messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
                produced, batch = _run_tools(conn, asked, calls)
                collected.extend(produced)
                trace.extend(batch)
                # OpenAI 兼容协议：每条 tool 消息必须用 tool_call_id 指回请求它的那次调用
                # （DeepSeek 会严格校验；少这个字段会直接 422）。
                for call, record in zip(calls, batch):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": record["tool"],
                        # evidence 里带着"用了哪个算法、离线准确率多少"，模型才可能
                        # 在回答里如实说明（规则 6），而不是自己编一个准确率。
                        "content": json.dumps({"ok": record["ok"], "matched": record["matched"],
                                               "error": record["error"], "evidence": record.get("evidence"),
                                               "items": record.get("items", [])},
                                              ensure_ascii=False),
                    })
        except LLMUnavailable as exc:
            raise APIError(503, "ASSISTANT_UNAVAILABLE", f"外部模型不可用：{exc.reason}",
                           {"reason": exc.reason, "detail": exc.detail, "llm": llm_status()}) from None
    return _assemble(asked, question, limit, answer_text, rounds, trace, collected, usage_total)


def _assemble(subject, question, limit, answer_text, rounds, trace, produced, usage=None) -> dict[str, Any]:
    kind, uid = subject
    # 展示顺序：推荐类工具的输出排在前面（回答通常就是在解释这批结果），同类里
    # 最近一次调用优先，my_history 只作为解释材料排最后；否则"先推荐、再查历史"
    # 的提问会把用户自己的旧评分当成推荐结果展示出来。
    ranked = sorted(((TOOL_PRIORITY.get(row.get("tool"), 9), -index, row)
                     for index, row in enumerate(trace)), key=lambda entry: (entry[0], entry[1]))
    ordered: list[dict[str, Any]] = []
    for _, _, record in ranked:
        if record.get("ok"):
            ordered.extend(record.get("items") or [])
    items = _dedupe(ordered or produced, limit)
    # 证据里不回传每条工具输出的明细（items 已单独给出），只留调用摘要
    trace = [{key: value for key, value in row.items() if key != "items"} for row in trace]
    warnings = []
    if not answer_text:
        warnings.append("模型在允许的轮次内没有给出最终文字回答")
    # 校验口径是"工具返回过的全部电影"，不是只交付的那几条：
    # 模型可能引用另一轮工具（例如 my_history）里的片子来解释回答，那也是真实数据，
    # 不能因为没进 Top-N 卡片就被误判成编造。
    unverified = _unverified_mentions(answer_text, produced or items)
    if unverified:
        warnings.append("回答里提到的片名不在工具结果中，已标记为不可信")
    failed = [row["tool"] for row in trace if not row["ok"]]
    if failed:
        warnings.append(f"有被拒绝的工具调用：{'、'.join(failed)}")
    if not items:
        warnings.append("工具没有返回任何电影（无结果状态）")
    # 结构化地给出"本次用了哪些推荐算法、它在这批固定用户上的准确率"：
    # 不依赖模型是否照规则 6 写进回答，前端也能稳定展示。
    methods: list[dict[str, Any]] = []
    for row in trace:
        evidence = row.get("evidence") or {}
        metrics = evidence.get("offline_metrics")
        if not row.get("ok") or not metrics:
            continue
        methods.append({
            "tool": row["tool"],
            "algorithm": evidence.get("algorithm"),
            "algorithm_label": evidence.get("algorithm_label") or evidence.get("algorithm"),
            "model_version": evidence.get("model_version"),
            "offline_metrics": metrics,
        })
    return {
        "status": "ready" if items else "no_result",
        "subject_key": f"{kind}:{uid}",
        "question": question,
        "answer": answer_text,
        "provider": mode(),
        "model": llm_model(),
        "rounds": rounds,
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "tool_calls": trace,
        "methods": methods,
        "items": items,
        "unverified_mentions": unverified,
        "warnings": warnings,
        "guardrails": ["工具白名单", "参数取值域校验", "只读工具", "items 仅由工具输出构成", f"轮次上限 {MAX_TOOL_ROUNDS}"],
        "generated_at": datetime.now(timezone.utc),
    }


# ---- 路由 --------------------------------------------------------------------

def _query(request: Request, subject: tuple[str, int], q: str, limit: int) -> dict[str, Any]:
    return answer_question(subject, q, limit, request.app.state.db)


@router.get("/me/assistant/query")
def my_assistant(request: Request, actor: ActorDependency,
                 q: Annotated[str, Query(min_length=MIN_QUESTION, max_length=MAX_QUESTION)],
                 limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 5):
    return _query(request, ("app", actor.account_id), q, limit)


@router.get("/demo/history-users/{ml_user_id}/assistant/query")
def history_assistant(ml_user_id: IdPath, request: Request,
                      q: Annotated[str, Query(min_length=MIN_QUESTION, max_length=MAX_QUESTION)],
                      limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 5):
    uid = identifier(ml_user_id)
    with request.app.state.db.connection() as conn:
        if conn.execute(text("SELECT ml_user_id FROM history_users WHERE ml_user_id=:id"), {"id": uid}).first() is None:
            raise APIError(404, "HISTORY_USER_NOT_FOUND", "历史匿名用户不存在")
    return _query(request, ("ml", uid), q, limit)


@router.get("/experiments/assistant/status")
def assistant_status():
    return {"llm": llm_status(), "tools": [tool["function"]["name"] for tool in TOOLS],
            "tool_schemas": TOOLS, "max_tool_rounds": MAX_TOOL_ROUNDS,
            "offline_metrics": {"source": "backend/app/lab3_metrics.json",
                                "partition": "validation",
                                "protocol": METRICS_PROTOCOL,
                                "algorithms": sorted(_offline_metrics().keys())},
            "guardrails": ["工具白名单", "参数取值域校验", "只读工具", "items 仅由工具输出构成"]}
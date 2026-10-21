from datetime import date, datetime
from decimal import Decimal
import json
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import text

from .errors import APIError
from .schemas import IdPath, MovieQuery, Page, identifier

router = APIRouter(tags=["电影与只读历史演示"])


def public_row(row) -> dict:
    result = dict(row)
    for key, value in result.items():
        if key in {"movie_id", "account_id", "ml_user_id", "tmdb_id"} and value is not None:
            result[key] = str(value)
        elif isinstance(value, Decimal): result[key] = f"{value:.1f}"
        elif isinstance(value, datetime): result[key] = value.isoformat(timespec="microseconds") + "Z"
        elif isinstance(value, date): result[key] = value.isoformat()
    if "genres" in result:
        raw = result["genres"]
        result["genres"] = [] if raw == "(no genres listed)" else raw.split("|")
    for key in ["directors", "cast_top10"]:
        if isinstance(result.get(key), str): result[key] = json.loads(result[key])
    return result


def get_genres(conn) -> list[str]:
    raw = conn.execute(text("SELECT DISTINCT genres FROM movies")).scalars()
    return sorted({g for value in raw for g in value.split("|") if g != "(no genres listed)"})


def require_movie(conn, movie_id: int) -> None:
    if conn.execute(text("SELECT movie_id FROM movies WHERE movie_id=:id"), {"id": movie_id}).first() is None:
        raise APIError(404, "MOVIE_NOT_FOUND", "电影不存在")


def page_result(rows, total: int, query: Page) -> dict:
    return {"items": [public_row(row) for row in rows], "total": total, "page": query.page, "per_page": query.per_page}


@router.get("/genres")
def genres(request: Request):
    with request.app.state.db.connection() as conn:
        return {"items": get_genres(conn)}


@router.get("/movies")
def movies(request: Request, query: Annotated[MovieQuery, Query()]):
    clauses, values = [], {"limit": query.per_page, "offset": (query.page - 1) * query.per_page}
    with request.app.state.db.connection() as conn:
        if query.genre:
            if query.genre not in get_genres(conn):
                raise APIError(422, "INVALID_GENRE", "类型不在本数据包电影分类中")
            clauses.append("CONCAT('|',genres,'|') LIKE :genre")
            values["genre"] = "%|" + query.genre + "|%"
        if query.q and query.q.strip():
            escaped = query.q.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
            clauses.append("(LOWER(movielens_title) LIKE LOWER(:term) ESCAPE '!' OR LOWER(COALESCE(title_zh,'')) LIKE LOWER(:term) ESCAPE '!')")
            values["term"] = "%" + escaped + "%"
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = conn.execute(text("SELECT COUNT(*) FROM movies" + where), values).scalar_one()
        selected = conn.execute(text("SELECT movie_id,movielens_title,title_zh,genres,release_year,poster_path "
            "FROM movies" + where + " ORDER BY movie_id LIMIT :limit OFFSET :offset"), values).mappings().all()
    return page_result(selected, total, query)


@router.get("/movies/{movie_id}")
def movie_detail(movie_id: IdPath, request: Request):
    mid = identifier(movie_id)
    with request.app.state.db.connection() as conn:
        row = conn.execute(text("SELECT * FROM movies WHERE movie_id=:id"), {"id": mid}).mappings().first()
    if row is None: raise APIError(404, "MOVIE_NOT_FOUND", "电影不存在")
    return public_row(row)


@router.get("/demo/history-users")
def history_users(request: Request, query: Annotated[Page, Query()]):
    with request.app.state.db.connection() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM history_users")).scalar_one()
        rows = conn.execute(text("SELECT ml_user_id,evaluation_role FROM history_users ORDER BY ml_user_id LIMIT :limit OFFSET :offset"),
            {"limit": query.per_page, "offset": (query.page - 1) * query.per_page}).mappings().all()
    result = page_result(rows, total, query)
    for row in result["items"]: row["subject_key"] = "ml:" + row["ml_user_id"]
    return result


@router.get("/demo/history-users/{ml_user_id}/ratings")
def history_ratings(ml_user_id: IdPath, request: Request, query: Annotated[Page, Query()]):
    uid = identifier(ml_user_id)
    with request.app.state.db.connection() as conn:
        if conn.execute(text("SELECT ml_user_id FROM history_users WHERE ml_user_id=:id"), {"id": uid}).first() is None:
            raise APIError(404, "HISTORY_USER_NOT_FOUND", "历史匿名用户不存在")
        values = {"id": uid, "limit": query.per_page, "offset": (query.page - 1) * query.per_page}
        total = conn.execute(text("SELECT COUNT(*) FROM history_ratings WHERE ml_user_id=:id"), values).scalar_one()
        selected = conn.execute(text("SELECT r.movie_id,r.rating,r.timestamp_utc,m.movielens_title,m.genres,m.poster_path "
            "FROM history_ratings r JOIN movies m ON m.movie_id=r.movie_id WHERE r.ml_user_id=:id "
            "ORDER BY r.movie_id LIMIT :limit OFFSET :offset"), values).mappings().all()
    return {**page_result(selected, total, query), "subject_key": "ml:" + str(uid), "read_only": True}

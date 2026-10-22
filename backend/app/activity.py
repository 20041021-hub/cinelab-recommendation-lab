from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import text

from .behavior import publish_event
from .catalog import page_result, public_row, require_movie
from .schemas import IdPath, Input, Page, RatingInput, identifier
from .security import ActorDependency, authenticated_write

router = APIRouter(prefix="/me", tags=["当前账号评分与收藏"])


@router.put("/ratings/{movie_id}")
def rate_movie(movie_id: IdPath, body: RatingInput, request: Request, actor: ActorDependency):
    mid = identifier(movie_id)
    with authenticated_write(request, actor) as conn:
        require_movie(conn, mid)
        genres = conn.execute(text("SELECT genres FROM movies WHERE movie_id=:id"), {"id": mid}).scalar_one()
        conn.execute(text("INSERT INTO account_ratings(account_id,movie_id,rating) VALUES(:uid,:mid,:rating) "
            "ON DUPLICATE KEY UPDATE rating=:rating,updated_at=UTC_TIMESTAMP(6)"),
            {"uid": actor.account_id, "mid": mid, "rating": body.rating})
        row = conn.execute(text("SELECT movie_id,rating,created_at,updated_at FROM account_ratings WHERE account_id=:uid AND movie_id=:mid"),
            {"uid": actor.account_id, "mid": mid}).mappings().one()
    # 事务提交成功后才投递事件（实验五 S3）：Kafka 里的评分事件一定对应库里真实的一行。
    publish_event("rating", actor.account_id, mid, rating=f"{body.rating:.1f}", genres=genres)
    return {**public_row(row), "subject_key": "app:" + str(actor.account_id)}


@router.get("/ratings")
def ratings(request: Request, actor: ActorDependency, query: Annotated[Page, Query()]):
    with request.app.state.db.connection() as conn:
        values = {"uid": actor.account_id, "limit": query.per_page, "offset": (query.page - 1) * query.per_page}
        total = conn.execute(text("SELECT COUNT(*) FROM account_ratings WHERE account_id=:uid"), values).scalar_one()
        rows = conn.execute(text("SELECT r.movie_id,r.rating,r.created_at,r.updated_at,m.movielens_title,m.genres,m.poster_path "
            "FROM account_ratings r JOIN movies m ON m.movie_id=r.movie_id WHERE r.account_id=:uid "
            "ORDER BY r.updated_at DESC,r.movie_id LIMIT :limit OFFSET :offset"), values).mappings().all()
    return {**page_result(rows, total, query), "subject_key": "app:" + str(actor.account_id)}


@router.get("/ratings/{movie_id}")
def my_rating(movie_id: IdPath, request: Request, actor: ActorDependency):
    mid = identifier(movie_id)
    with request.app.state.db.connection() as conn:
        require_movie(conn, mid)
        row = conn.execute(text("SELECT movie_id,rating,created_at,updated_at FROM account_ratings WHERE account_id=:uid AND movie_id=:mid"),
            {"uid": actor.account_id, "mid": mid}).mappings().first()
    return public_row(row) if row else {"movie_id": str(mid), "rating": None}


@router.put("/favorites/{movie_id}")
def favorite(movie_id: IdPath, request: Request, actor: ActorDependency, body: Input | None = None):
    mid = identifier(movie_id)
    with authenticated_write(request, actor) as conn:
        require_movie(conn, mid)
        values = {"uid": actor.account_id, "mid": mid}
        genres = conn.execute(text("SELECT genres FROM movies WHERE movie_id=:id"), {"id": mid}).scalar_one()
        # Current reads avoid an earlier REPEATABLE READ snapshot hiding a concurrent insert.
        row = conn.execute(text("SELECT created_at FROM account_favorites WHERE account_id=:uid AND movie_id=:mid FOR UPDATE"), values).first()
        if row is None:
            conn.execute(text("INSERT INTO account_favorites(account_id,movie_id) VALUES(:uid,:mid)"), values)
        when = conn.execute(text("SELECT created_at FROM account_favorites WHERE account_id=:uid AND movie_id=:mid FOR UPDATE"), values).scalar_one()
    publish_event("favorite", actor.account_id, mid, genres=genres)
    return {"movie_id": str(mid), "favorited": True, "created_at": when.isoformat(timespec="microseconds") + "Z"}


@router.delete("/favorites/{movie_id}", status_code=204, response_class=Response)
def unfavorite(movie_id: IdPath, request: Request, actor: ActorDependency, body: Input | None = None):
    mid = identifier(movie_id)
    with authenticated_write(request, actor) as conn:
        require_movie(conn, mid)
        genres = conn.execute(text("SELECT genres FROM movies WHERE movie_id=:id"), {"id": mid}).scalar_one()
        conn.execute(text("DELETE FROM account_favorites WHERE account_id=:uid AND movie_id=:mid"), {"uid": actor.account_id, "mid": mid})
    publish_event("unfavorite", actor.account_id, mid, genres=genres)
    return Response(status_code=204)


@router.get("/favorites")
def favorites(request: Request, actor: ActorDependency, query: Annotated[Page, Query()]):
    with request.app.state.db.connection() as conn:
        values = {"uid": actor.account_id, "limit": query.per_page, "offset": (query.page - 1) * query.per_page}
        total = conn.execute(text("SELECT COUNT(*) FROM account_favorites WHERE account_id=:uid"), values).scalar_one()
        rows = conn.execute(text("SELECT m.movie_id,m.movielens_title,m.genres,m.poster_path,f.created_at "
            "FROM account_favorites f JOIN movies m ON m.movie_id=f.movie_id WHERE f.account_id=:uid "
            "ORDER BY f.created_at DESC,f.movie_id LIMIT :limit OFFSET :offset"), values).mappings().all()
    return {**page_result(rows, total, query), "subject_key": "app:" + str(actor.account_id)}

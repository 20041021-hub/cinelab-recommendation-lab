"""Read-only user feedback adapter using the existing business connection configuration."""
from dataclasses import dataclass
from functools import lru_cache
import os

from sqlalchemy import text

from .data import require
from .validation import check_request


@lru_cache(maxsize=1)
def _database():
    # Deferred imports: offline evaluation never imports/opens this adapter.
    from backend.app.config import ROOT, Settings
    from backend.app.db import Database
    db = Database(Settings(_env_file=os.getenv("APP_ENV_FILE", str(ROOT / "backend/.env"))))
    db.validate()
    return db


@dataclass(frozen=True)
class Feedback:
    movie_id: str
    rating: str


@dataclass(frozen=True)
class UserHistory:
    subject_key: str
    ratings: tuple[Feedback, ...]
    favorite_movie_ids: tuple[str, ...]


def read_history(request, database=None) -> UserHistory:
    check_request(request)
    require(request.runtime_mode == "business", "业务历史适配器不允许评价模式", "DATA_MODE_INVALID")
    db = database or _database()
    # Validate even supplied test connections; no evaluation fallback or root configuration.
    db.check_configuration()
    kind, uid = request.subject_kind, request.subject_id
    with db.connection() as conn:
        table, key = ("history_users", "ml_user_id") if kind == "ml" else ("accounts", "account_id")
        require(conn.execute(text(f"SELECT {key} FROM {table} WHERE {key}=:id"), {"id": uid}).first() is not None,
                "用户不存在", "USER_NOT_FOUND")
        table = "history_ratings" if kind == "ml" else "account_ratings"
        rows = conn.execute(text(f"SELECT movie_id,rating FROM {table} WHERE {key}=:id ORDER BY movie_id"), {"id": uid}).all()
        favorites = () if kind == "ml" else tuple(str(mid) for mid in conn.execute(text(
            "SELECT movie_id FROM account_favorites WHERE account_id=:id ORDER BY movie_id"), {"id": uid}).scalars())
    return UserHistory(f"{kind}:{uid}", tuple(Feedback(str(mid), f"{rating:.1f}") for mid, rating in rows), favorites)

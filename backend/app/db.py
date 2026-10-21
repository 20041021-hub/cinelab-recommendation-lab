from contextlib import contextmanager
import hashlib
import json
import re
from typing import Iterator

from sqlalchemy import Connection, create_engine, event, text
from sqlalchemy.engine import URL

from .config import DATASET, SPLIT, SCHEMA, MANIFEST_SHA, Settings
from .errors import APIError

TABLES = {"movies", "history_users", "history_ratings", "accounts", "account_ratings",
          "account_favorites", "recommendation_results", "evaluation_results"}


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.receipt = "course-v3:ready:business:" + MANIFEST_SHA
        options: dict = {"connect_timeout": 5, "read_timeout": 15, "write_timeout": 15, "local_infile": False}
        if settings.db_socket:
            options["unix_socket"] = settings.db_socket
        if settings.db_ssl_ca:
            options.update(ssl_ca=settings.db_ssl_ca, ssl_verify_cert=True, ssl_verify_identity=True)
        url = URL.create("mysql+pymysql", username=settings.db_user, password=settings.db_password.get_secret_value(),
            host=settings.db_host, port=settings.db_port, database=settings.db_name, query={"charset": "utf8mb4"})
        self.engine = create_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True, connect_args=options)

        @event.listens_for(self.engine, "connect")
        def configure_connection(raw, _record):
            with raw.cursor() as cur:
                cur.execute("SET SESSION time_zone='+00:00'")
                cur.execute("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")

    def check_configuration(self) -> dict:
        if not re.fullmatch(r"rec_course_[a-z0-9_]+_business", self.settings.db_name):
            raise APIError(503, "DATABASE_MODE_MISMATCH", "业务API仅允许连接独立business数据库")
        path = self.settings.manifest_path
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != MANIFEST_SHA:
            raise APIError(503, "PACKAGE_IDENTITY_MISMATCH", "正式数据包manifest身份不匹配")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (manifest["dataset_version"], manifest["split_version"], manifest["schema_version"]) != (DATASET, SPLIT, SCHEMA):
            raise APIError(503, "PACKAGE_VERSION_MISMATCH", "数据版本不匹配")
        return manifest

    def check_receipt(self, conn: Connection) -> None:
        row = conn.execute(text("SELECT TABLE_COMMENT FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='history_ratings'")).scalar_one_or_none()
        if row != self.receipt:
            raise APIError(503, "DATABASE_IDENTITY_MISMATCH", "数据库导入标识不匹配或导入尚未完成")

    def validate(self) -> dict:
        manifest = self.check_configuration()  # Reject evaluation before opening a DB connection.
        with self.engine.connect() as conn:
            version, principal = conn.execute(text("SELECT VERSION(), CURRENT_USER()")).one()
            if not re.match(r"8\.(?:[1-9]\d*\.\d+|0\.(?:1[6-9]|[2-9]\d|\d{3,}))", version) or "mariadb" in version.lower():
                raise APIError(503, "DATABASE_ENGINE_MISMATCH", "需要MySQL 8.0.16及以上的MySQL 8实例")
            if principal.split("@")[0].lower() == "root":
                raise APIError(503, "DATABASE_PRIVILEGE_MISMATCH", "业务API不得使用root账户")
            self.check_receipt(conn)
            tables = set(conn.execute(text("SHOW TABLES")).scalars())
            if tables != TABLES:
                raise APIError(503, "DATABASE_SCHEMA_MISMATCH", "需要固定八表结构")
            column = conn.execute(text("SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='accounts' AND COLUMN_NAME='auth_version'")).one_or_none()
            if column is None or tuple(column) != ("bigint unsigned", "NO", "0"):
                raise APIError(503, "AUTH_MIGRATION_REQUIRED", "需要显式执行business认证迁移")
            check = conn.execute(text("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='accounts_business_only'")).scalar_one_or_none()
            if check is None or "business" not in check or "evaluation" in check:
                raise APIError(503, "DATABASE_SCHEMA_MISMATCH", "账号表不是business约束")
            counts = {name: conn.execute(text("SELECT COUNT(*) FROM " + name)).scalar_one() for name in ("movies", "history_users", "history_ratings")}
            if counts != {"movies": manifest["movies"], "history_users": manifest["history_users"], "history_ratings": manifest["modes"]["business"]["ratings"]}:
                raise APIError(503, "DATABASE_COUNTS_MISMATCH", "业务基础数据数量不匹配")
        return {"status": "ready", "dataset_version": DATASET, "split_version": SPLIT,
                "schema_version": SCHEMA, "app_schema_version": "starter-auth-v1", "manifest_sha256": MANIFEST_SHA}

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[Connection]:
        with (self.engine.begin() if write else self.engine.connect()) as conn:
            self.check_receipt(conn)
            yield conn

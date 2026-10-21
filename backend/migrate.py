"""Explicit, business-only auth extension; never creates/imports business tables."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import pymysql

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SHA = "fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e"


def migrate(conn, database: str) -> dict:
    if not re.fullmatch(r"rec_course_[a-z0-9_]+_business", database):
        raise ValueError("Migration only accepts an isolated business database")
    manifest = ROOT / "data/packages/rec-course-v3-train5-1.0.0/manifest.json"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_SHA:
        raise ValueError("Formal package identity mismatch")
    with conn.cursor() as cur:
        cur.execute("SELECT TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='history_ratings'", (database,))
        receipt = cur.fetchone()
        if receipt != ("course-v3:ready:business:" + MANIFEST_SHA,):
            raise ValueError("Migration refuses unrecognized or unfinished database")
        conn.select_db(database)
        cur.execute("SELECT COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='accounts' AND COLUMN_NAME='auth_version'", (database,))
        existing = cur.fetchone()
        if existing is not None:
            if existing != ("bigint unsigned", "NO", "0"):
                raise ValueError("Existing auth_version has unexpected definition")
            return {"status": "already_applied", "app_schema_version": "starter-auth-v1"}
        sql = (Path(__file__).parent / "migrations/001_auth_version.sql").read_text()
        cur.execute("\n".join(line for line in sql.splitlines() if not line.startswith("--")))
        return {"status": "applied", "app_schema_version": "starter-auth-v1"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    options = dict(host=os.getenv("MYSQL_HOST", "127.0.0.1"), port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"], charset="utf8mb4", autocommit=True)
    if os.getenv("MYSQL_UNIX_SOCKET"):
        options["unix_socket"] = os.environ["MYSQL_UNIX_SOCKET"]
    with pymysql.connect(**options) as connection:
        print(json.dumps(migrate(connection, args.database)))

"""Explicit teacher action: identify business DB, migrate auth and create a new restricted user.

No table creation/import, no password reset of existing MySQL users, no API startup hook.
"""
import argparse
import json
import os
import re
import pymysql

from .migrate import migrate


def grant_application(conn, database: str, username: str, host: str = "localhost") -> None:
    if not re.fullmatch(r"rec_course_[a-z0-9_]+_business", database):
        raise ValueError("Only business targets are allowed")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username) or username.lower() == "root":
        raise ValueError("Use a dedicated non-root application user")
    # Database identifier is validated above; user/host literals are parameterized.
    with conn.cursor() as cur:
        who = (username, host)
        cur.execute("GRANT SELECT ON `" + database + "`.* TO %s@%s", who)
        cur.execute("GRANT INSERT(login_name,password_hash),UPDATE(auth_version) ON `" + database + "`.accounts TO %s@%s", who)
        cur.execute("GRANT INSERT(account_id,movie_id,rating),UPDATE(rating,updated_at) ON `" + database + "`.account_ratings TO %s@%s", who)
        cur.execute("GRANT INSERT(account_id,movie_id),DELETE ON `" + database + "`.account_favorites TO %s@%s", who)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", required=True)
    p.add_argument("--app-user", required=True)
    p.add_argument("--app-host", default="localhost")
    args = p.parse_args()
    password = os.environ["BOOTSTRAP_APP_PASSWORD"]
    if len(password.encode()) < 24:
        raise ValueError("Generate an independent application password of at least 24 bytes")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", args.app_user) or args.app_user.lower() == "root":
        raise ValueError("Invalid independent application user")
    options = dict(host=os.getenv("MYSQL_HOST", "127.0.0.1"), port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"], charset="utf8mb4", autocommit=True)
    if os.getenv("MYSQL_UNIX_SOCKET"): options["unix_socket"] = os.environ["MYSQL_UNIX_SOCKET"]
    if os.getenv("MYSQL_SSL_CA"): options.update(ssl_ca=os.environ["MYSQL_SSL_CA"], ssl_verify_cert=True, ssl_verify_identity=True)
    with pymysql.connect(**options) as conn:
        result = migrate(conn, args.database)
        with conn.cursor() as cur:
            # Existing users are deliberately not modified or silently reused.
            cur.execute("CREATE USER %s@%s IDENTIFIED BY %s", (args.app_user, args.app_host, password))
        grant_application(conn, args.database, args.app_user, args.app_host)
    print(json.dumps({"migration": result, "application_user_created": True, "database": args.database}))


if __name__ == "__main__": main()

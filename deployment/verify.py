"""Read-only deployment inspection, also used to compare persistence snapshots."""
import hashlib
import json
import os

from sqlalchemy import text

from backend.app.config import Settings
from backend.app.db import Database
from deployment.serve import secret


def main() -> None:
    os.environ['APP_DB_PASSWORD'] = secret('app_password')
    os.environ['APP_JWT_SECRET'] = secret('jwt_secret')
    db = Database(Settings(_env_file='/dev/null'))
    try:
        ready = db.validate()
        with db.connection() as conn:
            counts = {name: conn.execute(text('SELECT COUNT(*) FROM ' + name)).scalar_one() for name in (
                'movies', 'history_users', 'history_ratings', 'accounts', 'account_ratings',
                'account_favorites', 'recommendation_results', 'evaluation_results')}
            digests = {}
            for table, order in [('accounts', 'account_id'), ('account_ratings', 'account_id,movie_id'),
                                 ('account_favorites', 'account_id,movie_id')]:
                digest = hashlib.sha256()
                for row in conn.execute(text('SELECT * FROM ' + table + ' ORDER BY ' + order)):
                    digest.update(json.dumps(list(row), default=str, ensure_ascii=False, separators=(',', ':')).encode())
                    digest.update(b'\n')
                digests[table] = digest.hexdigest()
            engine, principal = conn.execute(text('SELECT VERSION(),CURRENT_USER()')).one()
        print(json.dumps({'ready': ready, 'mysql': engine, 'principal': principal,
                          'counts': counts, 'mutable_sha256': digests}, indent=2))
    finally:
        db.engine.dispose()


if __name__ == '__main__':
    main()

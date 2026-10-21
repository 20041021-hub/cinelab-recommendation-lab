"""Explicit frozen business import + approved auth migration. Never erase existing data."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pymysql

from backend.bootstrap import grant_application
from backend.migrate import MANIFEST_SHA, migrate
from deployment.serve import secret
from scripts.student_package import connect, import_package, offline

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'data/packages/rec-course-v3-train5-1.0.0'
DATABASE = 'rec_course_v3_fcfb9a195a_business'
APP_USER = 'course_app'


def verify_grants(admin) -> None:
    """Compare exact approved schema/table/column rights, including role inheritance."""
    principal = "'course_app'@'%'"
    with admin.cursor() as cur:
        cur.execute("SELECT PRIVILEGE_TYPE FROM information_schema.USER_PRIVILEGES WHERE GRANTEE=%s AND PRIVILEGE_TYPE<>'USAGE'", (principal,))
        if cur.fetchall():
            raise ValueError('APPLICATION_GRANTS_UNSAFE: global privileges')
        cur.execute('SELECT TABLE_SCHEMA,PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.SCHEMA_PRIVILEGES WHERE GRANTEE=%s', (principal,))
        if set(cur.fetchall()) != {(DATABASE, 'SELECT', 'NO')}:
            raise ValueError('APPLICATION_GRANTS_UNSAFE: schema privileges')
        cur.execute('SELECT TABLE_SCHEMA,TABLE_NAME,PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.TABLE_PRIVILEGES WHERE GRANTEE=%s', (principal,))
        if set(cur.fetchall()) != {(DATABASE, 'account_favorites', 'DELETE', 'NO')}:
            raise ValueError('APPLICATION_GRANTS_UNSAFE: table privileges')
        expected = set()
        for table, privilege, columns in [
            ('accounts', 'INSERT', ['login_name', 'password_hash']),
            ('accounts', 'UPDATE', ['auth_version']),
            ('account_ratings', 'INSERT', ['account_id', 'movie_id', 'rating']),
            ('account_ratings', 'UPDATE', ['rating', 'updated_at']),
            ('account_favorites', 'INSERT', ['account_id', 'movie_id']),
        ]:
            expected.update((DATABASE, table, col, privilege, 'NO') for col in columns)
        cur.execute('SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.COLUMN_PRIVILEGES WHERE GRANTEE=%s', (principal,))
        if set(cur.fetchall()) != expected:
            raise ValueError('APPLICATION_GRANTS_UNSAFE: column privileges')
        cur.execute('SELECT COUNT(*) FROM mysql.role_edges WHERE TO_USER=%s', (APP_USER,))
        if cur.fetchone()[0]:
            raise ValueError('APPLICATION_GRANTS_UNSAFE: inherited roles')


def application_connection(password: str):
    return pymysql.connect(host=os.environ['MYSQL_HOST'], user=APP_USER, password=password,
                           database=DATABASE, charset='utf8mb4', connect_timeout=10, autocommit=True)


def initialize() -> dict:
    if hashlib.sha256((PACKAGE / 'manifest.json').read_bytes()).hexdigest() != MANIFEST_SHA:
        raise ValueError('PACKAGE_IDENTITY_MISMATCH: expected the approved stage 3 manifest')
    print('Checking frozen package hashes and split integrity...', file=sys.stderr, flush=True)
    checked, manifest = offline(PACKAGE)  # Original importer verifies all file hashes and split integrity.
    password = secret('app_password')
    os.environ['MYSQL_PASSWORD'] = secret('mysql_root_password')
    args = SimpleNamespace(package=PACKAGE, mode='business', database=DATABASE)
    admin, _ = connect(args)
    # Import runs on the original tool's separate connection. Do not retain an
    # earlier MySQL RR snapshot of dictionary metadata before migration checks.
    admin.autocommit(True)
    try:
        with admin.cursor() as cur:
            cur.execute('SELECT GET_LOCK(%s,10)', ('cinelab-explicit-initialize',))
            if cur.fetchone()[0] != 1:
                raise ValueError('INITIALIZATION_BUSY')
            cur.execute('SELECT COUNT(*) FROM mysql.user WHERE User=%s', (APP_USER,))
            exists = cur.fetchone()[0]
            if exists:
                cur.execute('SELECT Host FROM mysql.user WHERE User=%s', (APP_USER,))
                if cur.fetchall() != (('%',),):
                    raise ValueError('EXISTING_APPLICATION_USER_CONFLICT: no users or passwords were reset')
                # Refuse mismatched credentials BEFORE importing or migrating anything.
                with application_connection(password) as app:
                    with app.cursor() as ac:
                        ac.execute('SELECT CURRENT_USER()')
                        if ac.fetchone()[0] != APP_USER + '@%':
                            raise ValueError('APPLICATION_PRINCIPAL_MISMATCH')
                verify_grants(admin)
        print('Importing or verifying the recognized business database...', file=sys.stderr, flush=True)
        imported = import_package(args, checked, manifest)  # No acceptance() call: mutable rows must survive.
        print('Applying/checking the explicit auth migration and application grants...', file=sys.stderr, flush=True)
        migration = migrate(admin, DATABASE)
        with admin.cursor() as cur:
            if not exists:
                cur.execute('CREATE USER %s@%s IDENTIFIED BY %s', (APP_USER, '%', password))
                grant_application(admin, DATABASE, APP_USER, '%')
        verify_grants(admin)
        with application_connection(password) as app:
            with app.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM history_ratings')
                if cur.fetchone()[0] != 798775:
                    raise ValueError('APPLICATION_READ_CHECK_FAILED')
        return {'status': 'passed', 'import': imported, 'migration': migration,
                'application_user': 'verified_existing' if exists else 'created_least_privilege',
                'manifest_sha256': MANIFEST_SHA, 'evaluation_database_touched': False}
    finally:
        try:
            with admin.cursor() as cur:
                cur.execute('SELECT RELEASE_LOCK(%s)', ('cinelab-explicit-initialize',))
        finally:
            admin.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-business-initialize', action='store_true', required=True)
    parser.parse_args()
    try:
        print(json.dumps(initialize(), ensure_ascii=False, indent=2))
    except Exception as exc:
        # SQL errors can contain credentials; only emit safe categories.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__,
                          'message': str(exc) if type(exc) is ValueError else 'Initialization refused; check package, credentials, database identity and permissions. No automatic reset.'}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()

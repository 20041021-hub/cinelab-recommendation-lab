import secrets

from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .errors import APIError
from .schemas import Credentials, Input
from .security import (Actor, ActorDependency, DUMMY_HASH, PASSWORDS, SESSION_COOKIE, COOKIE_PATH,
    authenticated_write, encode, issue_csrf, public_account)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/csrf")
def csrf(request: Request, response: Response):
    return {"csrf_token": issue_csrf(request, response, request.cookies.get(SESSION_COOKIE))}


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request):
    hashed = PASSWORDS.hash(body.password)
    try:
        with request.app.state.db.connection(write=True) as conn:
            result = conn.execute(text("INSERT INTO accounts(login_name,password_hash) VALUES(:name,:hash)"),
                {"name": body.login_name, "hash": hashed})
            return {"account_id": str(result.lastrowid), "subject_key": "app:" + str(result.lastrowid), "login_name": body.login_name}
    except IntegrityError as exc:
        if exc.orig.args[0] == 1062:
            raise APIError(409, "LOGIN_NAME_TAKEN", "登录名已被使用") from None
        raise


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    limiter = request.app.state.login_limiter
    limiter.enter(request.client.host if request.client else "unknown", body.login_name)
    with request.app.state.db.connection() as conn:
        row = conn.execute(text("SELECT account_id,login_name,password_hash,auth_version FROM accounts WHERE login_name=:name AND runtime_mode='business'"), {"name": body.login_name}).mappings().first()
    valid = PASSWORDS.verify(body.password, row["password_hash"] if row else DUMMY_HASH)
    if row is None or not valid:
        raise APIError(401, "INVALID_CREDENTIALS", "登录名或密码错误")
    limiter.success(body.login_name)
    actor = Actor(row["account_id"], row["login_name"], row["auth_version"])
    token = encode(request, {"sub": "app:" + str(actor.account_id), "ver": actor.auth_version, "jti": secrets.token_urlsafe(24)}, "course-api", "session")
    cfg = request.app.state.settings
    response.set_cookie(SESSION_COOKIE, token, max_age=cfg.jwt_minutes * 60, httponly=True,
        secure=cfg.cookie_secure, samesite="strict", path=COOKIE_PATH)
    return {"account": public_account(actor), "csrf_token": issue_csrf(request, response, token)}


@router.get("/me")
def me(actor: ActorDependency):
    return public_account(actor)


@router.post("/logout")
def logout(request: Request, response: Response, actor: ActorDependency, body: Input | None = None):
    with authenticated_write(request, actor) as conn:
        conn.execute(text("UPDATE accounts SET auth_version=auth_version+1 WHERE account_id=:id"), {"id": actor.account_id})
    cfg = request.app.state.settings
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH, secure=cfg.cookie_secure, httponly=True, samesite="strict")
    return {"status": "logged_out", "scope": "all_account_sessions", "csrf_token": issue_csrf(request, response, None)}

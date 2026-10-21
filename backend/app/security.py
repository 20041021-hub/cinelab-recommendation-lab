from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import secrets
from threading import Lock
import time
from typing import Annotated, Iterator

from fastapi import Depends, Request, Response
import jwt
from pwdlib import PasswordHash
from sqlalchemy import Connection, text

from .errors import APIError

SESSION_COOKIE = "course_session"
CSRF_COOKIE = "course_csrf"
COOKIE_PATH = "/api/v1"
PASSWORDS = PasswordHash.recommended()
# Avoid a fast nonexistent-user path without keeping any default user/password.
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def ready_dependency(request: Request) -> None:
    if not request.app.state.ready:
        raise APIError(503, "SERVICE_NOT_READY", "数据库配置或数据身份检查未通过")


def encode(request: Request, claims: dict, audience: str, kind: str) -> str:
    cfg = request.app.state.settings
    now = int(time.time())
    return jwt.encode({**claims, "iss": "course-starter", "aud": audience, "kind": kind,
        "iat": now, "exp": now + cfg.jwt_minutes * 60}, cfg.jwt_secret.get_secret_value(), algorithm="HS256")


def decode(request: Request, token: str, audience: str, kind: str) -> dict:
    claims = jwt.decode(token, request.app.state.settings.jwt_secret.get_secret_value(), algorithms=["HS256"],
        audience=audience, issuer="course-starter", options={"require": ["exp", "iat", "iss", "aud", "kind"]})
    if claims["kind"] != kind:
        raise jwt.InvalidTokenError("Wrong token purpose")
    return claims


def issue_csrf(request: Request, response: Response, session: str | None) -> str:
    nonce = secrets.token_urlsafe(32)
    signed = encode(request, {"nonce": nonce, "sid": hashlib.sha256((session or "").encode()).hexdigest()}, "course-csrf", "csrf")
    cfg = request.app.state.settings
    response.set_cookie(CSRF_COOKIE, signed, max_age=cfg.jwt_minutes * 60, httponly=True,
        secure=cfg.cookie_secure, samesite="strict", path=COOKIE_PATH)
    return nonce


def verify_csrf(request: Request) -> None:
    if request.headers.get("origin") != request.app.state.settings.origin:
        raise APIError(403, "ORIGIN_REJECTED", "写请求必须来自配置的同源地址")
    try:
        claims = decode(request, request.cookies.get(CSRF_COOKIE, ""), "course-csrf", "csrf")
        header = request.headers.get("x-csrf-token", "")
        session_hash = hashlib.sha256(request.cookies.get(SESSION_COOKIE, "").encode()).hexdigest()
        if not header or not secrets.compare_digest(header, claims["nonce"]) or not secrets.compare_digest(session_hash, claims["sid"]):
            raise jwt.InvalidTokenError("CSRF mismatch")
    except (jwt.InvalidTokenError, KeyError, TypeError):
        raise APIError(403, "CSRF_REJECTED", "CSRF令牌缺失、过期或与当前会话不匹配") from None


@dataclass(frozen=True)
class Actor:
    account_id: int
    login_name: str
    auth_version: int


def public_account(actor: Actor) -> dict:
    return {"account_id": str(actor.account_id), "subject_key": "app:" + str(actor.account_id), "login_name": actor.login_name}


def current_actor(request: Request) -> Actor:
    ready_dependency(request)
    try:
        claims = decode(request, request.cookies.get(SESSION_COOKIE, ""), "course-api", "session")
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject.startswith("app:") or not subject[4:].isascii() or not subject[4:].isdigit():
            raise jwt.InvalidTokenError("Wrong identity namespace")
        aid = int(subject[4:])
        if aid <= 0 or aid > 2**64 - 1 or type(claims["ver"]) is not int or not isinstance(claims["jti"], str):
            raise jwt.InvalidTokenError("Invalid claims")
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise APIError(401, "AUTHENTICATION_REQUIRED", "请先登录或重新登录") from None
    with request.app.state.db.connection() as conn:
        row = conn.execute(text("SELECT account_id,login_name,auth_version FROM accounts WHERE account_id=:id AND runtime_mode='business'"), {"id": aid}).mappings().first()
    if row is None or row["auth_version"] != claims["ver"]:
        raise APIError(401, "SESSION_REVOKED", "登录已退出或失效，请重新登录")
    return Actor(row["account_id"], row["login_name"], row["auth_version"])


ActorDependency = Annotated[Actor, Depends(current_actor)]


@contextmanager
def authenticated_write(request: Request, actor: Actor) -> Iterator[Connection]:
    # Lock identity and recheck inside the write transaction to order concurrent logout/writes.
    with request.app.state.db.connection(write=True) as conn:
        version = conn.execute(text("SELECT auth_version FROM accounts WHERE account_id=:id FOR UPDATE"), {"id": actor.account_id}).scalar_one_or_none()
        if version != actor.auth_version:
            raise APIError(401, "SESSION_REVOKED", "登录已失效")
        yield conn


class LoginLimiter:
    """Bounded, process-local basic limiter. Authentication revocation lives in MySQL."""
    def __init__(self):
        self.events: dict[tuple, deque] = {}
        self.lock = Lock()

    def enter(self, ip: str, name: str) -> None:
        now = time.monotonic()
        with self.lock:
            for key in list(self.events):
                while self.events[key] and self.events[key][0] <= now - 300:
                    self.events[key].popleft()
                if not self.events[key]: del self.events[key]
            for key, maximum in [(('ip', ip), 100), (('account', name), 5)]:
                values = self.events.get(key, deque())
                if len(values) >= maximum:
                    delay = max(1, int(300 - (now - values[0])) + 1)
                    raise APIError(429, "LOGIN_RATE_LIMITED", "登录尝试过于频繁，请稍后再试", headers={"Retry-After": str(delay)})
            if len(self.events) >= 10000:
                raise APIError(429, "LOGIN_RATE_LIMITED", "登录请求繁忙，请稍后再试", headers={"Retry-After": "60"})
            self.events.setdefault(('ip', ip), deque()).append(now)
            self.events.setdefault(('account', name), deque()).append(now)

    def success(self, name: str) -> None:
        with self.lock:
            self.events.pop(('account', name), None)

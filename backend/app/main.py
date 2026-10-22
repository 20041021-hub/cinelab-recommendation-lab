from contextlib import asynccontextmanager
import logging
import os
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from .config import Settings, ROOT
from .db import Database
from .errors import APIError, error_response
from .security import LoginLimiter, ready_dependency, verify_csrf
from .auth import router as auth_router
from .catalog import router as catalog_router
from .activity import router as activity_router
from .experiments import router as experiments_router
from .fusion import router as fusion_router

log = logging.getLogger("course_api")


def require_ready(request: Request) -> None:
    if not request.app.state.ready:
        raise APIError(503, "SERVICE_NOT_READY", "数据库配置或数据身份检查未通过")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings(_env_file=os.getenv("APP_ENV_FILE", str(ROOT / "backend/.env")))
    db = Database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = False
        try:
            db.validate()
            app.state.ready = True
        except APIError as exc:
            log.error("Startup readiness rejected: %s", exc.code)
        except SQLAlchemyError:
            log.error("Startup database unavailable")
        yield
        db.engine.dispose()

    app = FastAPI(title="推荐算法课程基础业务API", version="4B", lifespan=lifespan)
    app.state.settings, app.state.db, app.state.ready = settings, db, False
    app.state.login_limiter = LoginLimiter()

    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        request.state.request_id = uuid4().hex
        try:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
                verify_csrf(request)
                if request.query_params:
                    raise APIError(422, "UNEXPECTED_QUERY_PARAMETERS", "写接口不接受身份或其他查询参数")
            response = await call_next(request)
        except APIError as exc:
            response = error_response(request, exc)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo passwords, cookies or original input values from Pydantic errors.
        details = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
        return error_response(request, APIError(422, "VALIDATION_ERROR", "请求参数不合法", details))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(request, APIError(exc.status_code, "HTTP_" + str(exc.status_code), "请求路径或方法不可用"))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        original = getattr(exc, "orig", None)
        code = original.args[0] if original is not None and original.args else "unknown"
        log.error("Database request failed; request_id=%s code=%s", request.state.request_id, code)
        return error_response(request, APIError(503, "DATABASE_UNAVAILABLE", "数据库暂不可用"))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        log.error("Unhandled request error; request_id=%s type=%s", request.state.request_id, type(exc).__name__)
        return error_response(request, APIError(500, "INTERNAL_ERROR", "请求处理失败"))

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        try:
            result = db.validate()
            app.state.ready = True
            return result
        except (APIError, SQLAlchemyError):
            app.state.ready = False
            raise

    app.include_router(auth_router, prefix="/api/v1", dependencies=[Depends(ready_dependency)])
    app.include_router(catalog_router, prefix="/api/v1", dependencies=[Depends(ready_dependency)])
    app.include_router(activity_router, prefix="/api/v1", dependencies=[Depends(ready_dependency)])
    # Fusion is our own extension; register before experiments so "fusion" in
    # /me/recommendations/{algorithm} is not claimed by the frozen contract route.
    app.include_router(fusion_router, prefix="/api/v1", dependencies=[Depends(ready_dependency)])
    app.include_router(experiments_router, prefix="/api/v1", dependencies=[Depends(ready_dependency)])
    return app

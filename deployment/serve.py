"""Daily API start: read secrets, validate configuration, exec one worker. No DDL."""
import os
from pathlib import Path


def secret(name: str, directory: Path = Path('/run/secrets')) -> str:
    value = (directory / name).read_text(encoding='utf-8').strip()
    if len(value.encode()) < 32:
        raise ValueError(f'Secret file {name} requires at least 32 bytes')
    return value


def main() -> None:
    try:
        os.environ['APP_DB_PASSWORD'] = secret('app_password')
        os.environ['APP_JWT_SECRET'] = secret('jwt_secret')
        from backend.app.config import Settings
        Settings(_env_file='/dev/null')
    except Exception as exc:
        # Never serialize Settings, passwords, or validation input.
        raise SystemExit(f'CONFIGURATION_INVALID ({type(exc).__name__}): check secret files, Origin and business settings') from None
    os.execvp('python', ['python', '-m', 'uvicorn', 'backend.app.main:create_app', '--factory',
                       '--host', '0.0.0.0', '--port', '8000', '--workers', '1', '--no-proxy-headers', '--no-access-log'])


if __name__ == '__main__':
    main()

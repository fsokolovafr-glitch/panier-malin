import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_env_file():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _resolve_path(value):
    path = Path(value)
    return str(path if path.is_absolute() else ROOT / path)


def load_config():
    _load_env_file()
    return {
        "APP_HOST": os.getenv("APP_HOST", "127.0.0.1"),
        "APP_PORT": int(os.getenv("APP_PORT", "8000")),
        "DATABASE_PATH": _resolve_path(
            os.getenv("DATABASE_PATH", "./var/data/panier_malin.sqlite3")
        ),
        "DEFAULT_CURRENCY": os.getenv("DEFAULT_CURRENCY", "EUR").upper(),
        "SECRET_KEY": os.getenv("APP_SECRET_KEY", "local-development-only"),
    }

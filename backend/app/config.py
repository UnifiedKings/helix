import os

def _env(name: str, default: str | None = None) -> str:
    val = os.getenv(name, default)
    if val is None:
        raise RuntimeError(f"Missing required env var: {name}")
    return val

SLSKD_BASE_URL = _env("SLSKD_BASE_URL")
SLSKD_API_KEY  = _env("SLSKD_API_KEY")

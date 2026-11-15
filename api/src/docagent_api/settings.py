"""API settings: environment variables (secrets, DSNs, flags) + config/limits.yaml (limits)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ApiSettings:
    app_dsn: str
    """docagent_app login: every query runs under RLS with app.session_id set."""
    checkpoint_dsn: str
    upload_dir: Path
    allowed_origins: list[str]
    replay_only: bool
    replay_speed: float
    cookie_secure: bool
    cookie_domain: str | None
    """e.g. .meetmoeed.com so demo.* (web) and api.* share the session; None = host-only."""
    ip_hash_salt: str
    trust_proxy: bool
    daily_budget_usd: Decimal
    max_upload_bytes: int
    max_pages: int
    run_limit: str
    question_limit: str
    session_ttl_hours: int
    samples_dir: Path
    fixtures_dir: Path
    evals_report: Path
    mcp_urls: dict[str, str]
    mcp_secret: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ApiSettings":
        env = os.environ if env is None else env
        config_dir = Path(env.get("DOCAGENT_CONFIG_DIR", ROOT / "config"))
        limits: dict[str, Any] = yaml.safe_load((config_dir / "limits.yaml").read_text())
        missing = [
            k
            for k in ("DATABASE_APP_URL", "DATABASE_URL", "UPLOAD_DIR", "DAILY_BUDGET_USD")
            if not env.get(k)
        ]
        if missing:
            raise LookupError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            app_dsn=env["DATABASE_APP_URL"],
            checkpoint_dsn=env["DATABASE_URL"],
            upload_dir=Path(env["UPLOAD_DIR"]),
            allowed_origins=[
                o.strip() for o in env.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
            ],
            replay_only=_flag(env.get("REPLAY_ONLY")),
            replay_speed=float(env.get("REPLAY_SPEED", "1.0")),
            cookie_secure=_flag(env.get("COOKIE_SECURE")),
            cookie_domain=env.get("COOKIE_DOMAIN") or None,
            ip_hash_salt=env.get("IP_HASH_SALT") or os.urandom(16).hex(),
            trust_proxy=_flag(env.get("TRUST_PROXY")),
            daily_budget_usd=Decimal(env["DAILY_BUDGET_USD"]),
            max_upload_bytes=int(limits["uploads"]["max_bytes"]),
            max_pages=int(limits["uploads"]["max_pages"]),
            run_limit=str(limits["rate_limits"]["runs"]),
            question_limit=str(limits["rate_limits"]["questions"]),
            session_ttl_hours=int(limits["sessions"]["ttl_hours"]),
            samples_dir=ROOT / "evals" / "dataset" / "samples",
            fixtures_dir=ROOT / "api" / "fixtures" / "replay",
            evals_report=ROOT / "evals" / "reports" / "latest.json",
            mcp_urls={
                s: env.get(f"MCP_{s.upper()}_URL", "")
                for s in ("extraction", "validation", "sqlstore", "reference")
            },
            mcp_secret=env.get("MCP_SHARED_SECRET", ""),
        )

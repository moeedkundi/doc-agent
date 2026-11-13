"""Importing the gateway must not load a .env file into the environment (LiteLLM would)."""

import os
import subprocess
import sys
from pathlib import Path

CHECK = "import docagent_gateway, os; print(os.environ.get('DOCAGENT_DOTENV_CANARY', 'absent'))"


def test_importing_gateway_does_not_read_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DOCAGENT_DOTENV_CANARY=leaked\n", encoding="utf-8")
    env = {
        k: v for k, v in os.environ.items() if k not in ("LITELLM_MODE", "DOCAGENT_DOTENV_CANARY")
    }
    out = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", CHECK],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert out.stdout.strip() == "absent"

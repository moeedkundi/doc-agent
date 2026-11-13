"""LLM gateway: tier routing, cost ledger, budget cap. All LLM calls go through here."""

import os

# LiteLLM calls dotenv.load_dotenv() on import unless LITELLM_MODE != "DEV", which would pull
# a local .env (API keys included) into os.environ behind our back. Config comes from the
# real environment only, so pin production mode before any submodule imports litellm.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")


from docagent_gateway.config import GatewayConfig, TierConfig, load_config
from docagent_gateway.errors import (
    BudgetExceeded,
    GatewayError,
    OutputParseError,
    ProviderError,
    redact,
)
from docagent_gateway.gateway import Completion, Gateway, ImageInput, compute_cost
from docagent_gateway.ledger import Ledger, LedgerEntry, MemoryLedger, PostgresLedger

__version__ = "0.1.0"

__all__ = [
    "BudgetExceeded",
    "Completion",
    "Gateway",
    "GatewayConfig",
    "GatewayError",
    "ImageInput",
    "Ledger",
    "LedgerEntry",
    "MemoryLedger",
    "OutputParseError",
    "PostgresLedger",
    "ProviderError",
    "TierConfig",
    "compute_cost",
    "load_config",
    "redact",
]

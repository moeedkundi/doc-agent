"""JSON Schema export for the shared models.

Run `uv run python -m docagent_schemas.json_schema` to regenerate `packages/schemas/json/`.
"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from docagent_schemas.extracted import Invoice, PurchaseOrder
from docagent_schemas.ground_truth import GroundTruth
from docagent_schemas.matching import MatchResult
from docagent_schemas.records import InvoiceRecord, PurchaseOrderRecord

EXPORTED: dict[str, type[BaseModel]] = {
    "invoice.extracted": Invoice,
    "purchase_order.extracted": PurchaseOrder,
    "invoice.record": InvoiceRecord,
    "purchase_order.record": PurchaseOrderRecord,
    "match_result": MatchResult,
    "ground_truth": GroundTruth,
}

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "json"


def render_all() -> dict[str, str]:
    """File name -> pretty JSON text for every exported model."""
    return {
        f"{name}.schema.json": json.dumps(model.model_json_schema(), indent=2, sort_keys=True)
        + "\n"
        for name, model in EXPORTED.items()
    }


def write_all(out_dir: Path = DEFAULT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, text in render_all().items():
        path = out_dir / filename
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in write_all(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR):
        print(p)

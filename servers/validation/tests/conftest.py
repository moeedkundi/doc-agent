import json
from pathlib import Path

import pytest

from docagent_mcp_validation.settings import ValidationSettings, load_settings
from docagent_schemas import GroundTruth, InvoiceRecord, PurchaseOrderRecord

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "evals" / "dataset"


@pytest.fixture(scope="session")
def settings() -> ValidationSettings:
    return load_settings(ROOT / "config")


@pytest.fixture(scope="session")
def truths() -> list[GroundTruth]:
    """Every ground-truth document, in ingest (manifest) order."""
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    return [
        GroundTruth.model_validate_json((DATASET / d["ground_truth"]).read_text(encoding="utf-8"))
        for d in manifest["documents"]
    ]


@pytest.fixture(scope="session")
def by_id(truths: list[GroundTruth]) -> dict[str, GroundTruth]:
    return {t.doc_id: t for t in truths}


def _inv(gt: GroundTruth) -> InvoiceRecord:
    assert isinstance(gt.record, InvoiceRecord)
    return gt.record


def _po(gt: GroundTruth) -> PurchaseOrderRecord:
    assert isinstance(gt.record, PurchaseOrderRecord)
    return gt.record


@pytest.fixture(scope="session")
def drift_pair(by_id: dict[str, GroundTruth]) -> tuple[PurchaseOrderRecord, InvoiceRecord]:
    """Public sample: PO-85044 and its invoice with a +5% unit-price drift."""
    return _po(by_id["po_003"]), _inv(by_id["inv_003"])


@pytest.fixture(scope="session")
def partial_pair(by_id: dict[str, GroundTruth]) -> tuple[PurchaseOrderRecord, InvoiceRecord]:
    """Public sample: PO-1042 and its invoice for part of the order."""
    return _po(by_id["po_002"]), _inv(by_id["inv_002"])


@pytest.fixture(scope="session")
def full_pair(by_id: dict[str, GroundTruth]) -> tuple[PurchaseOrderRecord, InvoiceRecord]:
    """Public sample: a PO and the invoice that bills all of it."""
    return _po(by_id["po_001"]), _inv(by_id["inv_001"])

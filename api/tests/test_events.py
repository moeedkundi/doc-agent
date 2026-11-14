from typing import Any, cast
from uuid import UUID, uuid4

from docagent_api.db import Db
from docagent_api.events import stream_run


class FakeDb:
    """Run status steps through `statuses`, one per poll; no trace events."""

    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.polls = 0

    def events_after(self, sid: UUID, run_id: UUID, seq: int) -> list[dict[str, Any]]:
        return []

    def get_run(self, sid: UUID, run_id: UUID) -> dict[str, Any]:
        status = self.statuses[min(self.polls, len(self.statuses) - 1)]
        self.polls += 1
        return {"status": status, "error_code": None, "result_json": None}


async def test_review_is_announced_only_once_its_payload_exists() -> None:
    db = FakeDb(["awaiting_review", "awaiting_review", "awaiting_review", "completed"])
    payloads: list[dict[str, Any] | None] = [None, {"run_id": "r", "issues": []}]

    async def review_payload(_: UUID) -> dict[str, Any] | None:
        return payloads.pop(0) if payloads else {"run_id": "r", "issues": []}

    events = [
        e.event
        async for e in stream_run(cast(Db, db), uuid4(), uuid4(), 0, review_payload, poll_seconds=0)
    ]
    # The first poll sees the status before the interrupt is checkpointed: no empty review.
    assert events == ["status", "review_required", "status", "end"]

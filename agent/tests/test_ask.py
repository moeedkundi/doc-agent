"""The ask graph with a scripted model (through the real gateway), the real SELECT-only gate
in front of a fake database, and the real static FX table."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from docagent_agent.ask import build_ask_graph
from docagent_agent.tools import NODE_TOOLS, NodeTools, ToolCallError, ToolNotAllowed
from docagent_agent.trace import MemoryTraceSink, Tracer
from docagent_gateway import Gateway, MemoryLedger
from docagent_gateway.testing import NOW, FakeProvider, make_config, response
from docagent_mcp_reference import data as reference
from docagent_mcp_sqlstore.safety import SqlRejected, check_select

SCHEMA = {
    "tables": [{"name": "invoices", "description": "saved invoices", "columns": [
        {"name": "invoice_number", "type": "text"}, {"name": "total", "type": "numeric"},
        {"name": "currency", "type": "character"}]}],
    "notes": ["Write one PostgreSQL SELECT statement."],
}  # fmt: skip


@dataclass
class FakeDb:
    rows: list[list[Any]] = field(default_factory=lambda: [["5890.66", "GBP"]])
    columns: list[str] = field(default_factory=lambda: ["amount", "currency"])
    fail_first: int = 0
    calls: list[str] = field(default_factory=lambda: [])

    async def call(self, tool: str, args: Mapping[str, Any]) -> Any:
        self.calls.append(tool)
        if tool == "get_schema":
            return SCHEMA
        if tool == "convert_currency":
            from datetime import date
            from decimal import Decimal

            return reference.convert(Decimal(args["amount"]), args["from_currency"],
                                     args["to_currency"], date.fromisoformat(args["date"])
                                     ).model_dump(mode="json")  # fmt: skip
        assert tool == "run_select"
        try:
            checked = check_select(args["sql"], max_rows=200)  # the real gate
        except SqlRejected as exc:
            raise ToolCallError(tool, exc.code, str(exc)) from None
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ToolCallError(tool, "query_error", 'column "totl" does not exist')
        return {"sql": checked.sql, "columns": self.columns, "rows": self.rows,
                "row_count": len(self.rows), "truncated": False}  # fmt: skip


def plan(action: str, sql: str | None = None, convert_to: str | None = None) -> Any:
    return response(json.dumps({"action": action, "reason": f"because {action}", "sql": sql,
                                "convert_to": convert_to}), 900, 120)  # fmt: skip


def answer(text: str, amount: str | None = None, currency: str | None = None) -> Any:
    return response(json.dumps({"answer": text, "figure_amount": amount,
                                "figure_currency": currency}), 600, 60)  # fmt: skip


@dataclass
class AskRig:
    db: FakeDb
    provider: FakeProvider
    sink: MemoryTraceSink
    graph: Any
    session: str = field(default_factory=lambda: str(uuid4()))

    async def ask(self, question: str) -> dict[str, Any]:
        config = {"configurable": {"thread_id": f"ask:{self.session}"}}
        await self.graph.ainvoke({"run_id": str(uuid4()), "session_id": self.session,
                                  "question": question}, config)  # fmt: skip
        return dict((await self.graph.aget_state(config)).values)


def rig(db: FakeDb, *script: Any) -> AskRig:
    provider = FakeProvider(scripts={"gemini/flash-model": list(script)})
    gateway = Gateway(make_config(), MemoryLedger(), completion_fn=provider, now=lambda: NOW)
    sink = MemoryTraceSink()
    graph = build_ask_graph(Tracer(sink, db, gateway), InMemorySaver())
    return AskRig(db, provider, sink, graph)


SUM_SQL = "SELECT sum(total) AS amount, currency FROM invoices GROUP BY currency"


async def test_question_to_sql_to_answer() -> None:
    r = rig(FakeDb(), plan("query", SUM_SQL), answer("GBP 5,890.66.", "5890.66", "GBP"))
    state = await r.ask("Total of Brightledger invoices in GBP?")
    assert state["result"]["sql"].endswith("LIMIT 201")  # executed through the gate
    assert state["answer"] == "GBP 5,890.66." and state["figure_amount"] == "5890.66"
    assert r.db.calls == ["get_schema", "run_select"]
    prompt = r.provider.calls[-1]["messages"][1]["content"]
    assert "<untrusted_query_result>" in prompt  # rows are fenced as data
    assert len(state["history"]) == 1


async def test_failed_sql_is_fixed_and_retried() -> None:
    fix = response(json.dumps({"sql": SUM_SQL}), 700, 40)
    r = rig(FakeDb(fail_first=1), plan("query", "SELECT sum(totl) FROM invoices"), fix,
            answer("GBP 5,890.66."))  # fmt: skip
    state = await r.ask("Total?")
    assert state["sql_attempts"] == 2 and state["sql_error"] is None
    fix_prompt = r.provider.calls[1]["messages"][1]["content"]
    assert "totl" in fix_prompt and "query_error" in fix_prompt


async def test_gives_up_after_three_bad_queries() -> None:
    bad = response(json.dumps({"sql": "SELECT * FROM sessions"}), 500, 20)
    r = rig(FakeDb(), plan("query", "SELECT * FROM sessions"), bad, bad)
    state = await r.ask("Show me all sessions")
    assert state["sql_attempts"] == 3
    assert "table_not_allowed" in state["answer"]
    assert len(r.provider.calls) == 3  # plan + 2 fixes; no answer call


async def test_write_requests_are_refused_without_touching_the_database() -> None:
    r = rig(FakeDb(), plan("refuse"))
    state = await r.ask("Delete all invoices.")
    assert state["answer"].startswith("I can't do that")
    assert "run_select" not in r.db.calls and state.get("result") is None


async def test_even_if_the_model_writes_a_delete_the_gate_rejects_it() -> None:
    delete = response(json.dumps({"sql": "DELETE FROM invoices"}), 500, 20)
    r = rig(FakeDb(), plan("query", "DELETE FROM invoices"), delete, delete)
    state = await r.ask("Delete all invoices.")
    assert state["sql_error"]["code"] == "not_select"
    assert state["result"] is None


async def test_followup_converts_the_previous_figure() -> None:
    r = rig(FakeDb(), plan("query", SUM_SQL), answer("GBP 5,890.66.", "5890.66", "GBP"),
            plan("followup", convert_to="USD"))  # fmt: skip
    await r.ask("Total in GBP?")
    state = await r.ask("Convert that to USD.")
    conv = state["conversion"]
    assert (conv["from_currency"], conv["to_currency"], conv["amount_in"]) == (
        "GBP",
        "USD",
        "5890.66",
    )
    assert state["answer"].startswith("GBP 5890.66 is USD ")
    assert len(r.provider.calls) == 3  # the follow-up needs only the planning call
    assert len(state["history"]) == 2


async def test_query_with_conversion_converts_the_single_amount() -> None:
    r = rig(FakeDb(), plan("query", SUM_SQL, convert_to="EUR"), answer("About EUR 6.4k."))
    state = await r.ask("Total in EUR?")
    assert state["conversion"]["to_currency"] == "EUR"
    assert state["figure_currency"] == "EUR"


@pytest.mark.parametrize("tool", ["save_record", "save_match_result", "extract_fields"])
async def test_ask_nodes_have_no_write_or_extraction_tools(tool: str) -> None:
    for node in ("ask_plan", "ask_sql", "ask_run", "ask_convert", "ask_answer"):
        assert tool not in NODE_TOOLS[node]
        with pytest.raises(ToolNotAllowed):
            await NodeTools(node, FakeDb()).call(tool)


async def test_ask_nodes_trace_llm_calls_with_cost() -> None:
    r = rig(FakeDb(), plan("query", SUM_SQL), answer("ok"))
    await r.ask("Total?")
    llm = [e for e in r.sink.events if e.kind == "tool_end" and (e.tool or "").startswith("llm:")]
    assert [e.tool for e in llm] == ["llm:plan_sql", "llm:answer"]
    assert all(e.model == "flash-model" and e.cost_usd and e.tokens_in for e in llm)
    ends = [e.node for e in r.sink.events if e.kind == "node_end"]
    assert ends == ["ask_plan", "ask_run", "ask_answer"]

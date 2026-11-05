"""Telemetry: counters, span attributes and the JSON audit lines (telemetry.py).

One in-memory SDK for the whole module: OTel lets a process set its
providers once, and the instruments created at import bind to it.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import access
import server
import telemetry

READER = InMemoryMetricReader()
SPANS = InMemorySpanExporter()
metrics.set_meter_provider(MeterProvider(metric_readers=[READER]))
_tp = TracerProvider()
_tp.add_span_processor(SimpleSpanProcessor(SPANS))
trace.set_tracer_provider(_tp)


def points(name: str) -> dict[tuple, float]:
    """Current value per attribute set of one instrument."""
    out = {}
    data = READER.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    for p in m.data.data_points:
                        out[tuple(sorted(p.attributes.items()))] = p.value
    return out


def value(name: str, **attrs) -> float:
    want = set(attrs.items())
    return sum(v for k, v in points(name).items() if want <= set(k))


def lines(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]


@pytest.fixture
def vault(tmp_path: Path) -> server.Vault:
    root = tmp_path / "repo"
    for d in ("agents", "inbox"):
        (root / d).mkdir(parents=True)
    (root / "agents" / "digest.md").write_text("# Digest\nsecret body text\n")
    return server.Vault(root=root, tmp=tmp_path / "tmp")


@pytest.fixture
def restricted(monkeypatch):
    who = {"session": "login-1"}

    async def fake(auth, upstream):
        return access.Caller(client="claude-ai", loopback=False, session=who["session"])

    monkeypatch.setattr(access, "identify", fake)
    return who


def call(mcp, tool, **args):
    async def run():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args)).data

    return asyncio.run(run())


def test_calls_counted_by_result(vault, restricted, caplog):
    mcp = server.build_server(vault)
    before_ok = value("agent_notes.calls", client="claude-ai", result="ok")
    refused_write = {"client": "claude-ai", "result": "refused", "action": "write", "tier": "restricted"}
    before_refused = value("agent_notes.calls", **refused_write)
    with caplog.at_level("INFO", logger="agent-notes"):
        call(mcp, "read_note", path="agents/digest.md")
        with pytest.raises(ToolError):
            call(mcp, "write_note", path="agents/x.md", content="body that must not be logged")
    assert value("agent_notes.calls", client="claude-ai", result="ok") == before_ok + 1
    assert value("agent_notes.calls", **refused_write) == before_refused + 1
    events = [e for e in lines(caplog) if e["event"] == "call"]
    assert events[-2]["result"] == "ok" and events[-2]["action"] == "read" and events[-2]["level"] == "info"
    refused = events[-1]
    assert refused["result"] == "refused" and refused["path"] == "agents/x.md" and refused["level"] == "warning"
    assert refused["tier"] == "restricted" and refused["client"] == "claude-ai"
    # Content never reaches the log.
    assert not any("must not be logged" in r.getMessage() or "secret body" in r.getMessage() for r in caplog.records)


def test_invalid_calls_counted_once(vault, restricted):
    mcp = server.build_server(vault)
    before_invalid = value("agent_notes.calls", result="invalid", action="read_note")
    before_refused = value("agent_notes.calls", result="refused")
    with pytest.raises(ToolError, match="no such note"):
        call(mcp, "read_note", path="inbox/missing.md")
    assert value("agent_notes.calls", result="invalid", action="read_note") == before_invalid + 1
    assert value("agent_notes.calls", result="refused") == before_refused  # not a refusal
    # A refusal isn't also counted as invalid.
    before_invalid = value("agent_notes.calls", result="invalid")
    with pytest.raises(ToolError):
        call(mcp, "write_note", path="agents/y.md", content="x")
    assert value("agent_notes.calls", result="invalid") == before_invalid


def test_span_carries_the_decision(vault, restricted):
    SPANS.clear()
    with pytest.raises(ToolError):
        call(server.build_server(vault), "write_note", path="agents/z.md", content="x")
    attrs = [dict(s.attributes) for s in SPANS.get_finished_spans()]
    decided = [a for a in attrs if "agent_notes.result" in a]
    assert decided and decided[-1]["agent_notes.result"] == "refused" and decided[-1]["agent_notes.tier"] == "restricted"


def test_audit_line_has_the_trace_id(vault, restricted, caplog):
    with caplog.at_level("INFO", logger="agent-notes"):
        call(server.build_server(vault), "list_notes")
    line = [e for e in lines(caplog) if e["event"] == "call"][-1]
    assert re.fullmatch(r"[0-9a-f]{32}", line.get("trace_id", "")), line


def test_elevation_events(vault, restricted, caplog):
    clock_now = [1_000_000.0]
    grants = access.Grants(lambda: clock_now[0])
    approver = access.Approver(
        discovery=access.Discovery("https://idp.example", fetch=lambda u: {}), client_id="app", client_secret="s", base_url="https://n", allowed_subs=frozenset({"o"}),
        exchange=lambda code: {},
    )
    mcp = server.build_server(vault, grants=grants, approver=approver)
    before = {e: value("agent_notes.elevations", event=e) for e in ("requested", "approved", "expired", "ended")}
    with caplog.at_level("INFO", logger="agent-notes"):
        text = call(mcp, "request_elevation", reason="r")
    grant_id = re.search(r'grant="([^"]+)"', text).group(1)
    assert value("agent_notes.elevations", event="requested") == before["requested"] + 1
    # Neither the id nor the ticket reaches the log; the ref does.
    logged = "\n".join(r.getMessage() for r in caplog.records)
    g = grants.get(grant_id)
    assert grant_id not in logged and g.ticket not in logged and g.ref in logged
    assert grants.counts()["pending"] == 1

    grants.decide(grant_id, True)
    clock_now[0] += access.GRANT_TTL + 1
    assert grants.counts() == {"pending": 0, "approved": 0, "denied": 0}
    assert value("agent_notes.elevations", event="ended") == before["ended"] + 1

    g2 = grants.request("claude-ai", "r")
    clock_now[0] += access.PENDING_TTL + 1
    grants.get(g2.id)
    assert value("agent_notes.elevations", event="expired") == before["expired"] + 1


def test_a_grant_used_by_another_session_is_refused_and_logged(vault, restricted, caplog):
    grants = access.Grants()
    approver = access.Approver(
        discovery=access.Discovery("https://idp.example", fetch=lambda u: {}), client_id="app", client_secret="s",
        base_url="https://n", allowed_subs=frozenset({"o"}), exchange=lambda code: {},
    )
    mcp = server.build_server(vault, grants=grants, approver=approver)
    grant_id = re.search(r'grant="([^"]+)"', call(mcp, "request_elevation", reason="r")).group(1)
    grants.decide(grant_id, True)
    before = value("agent_notes.elevations", event="refused")
    restricted["session"] = "login-2"  # the same claude.ai client, another conversation
    with caplog.at_level("INFO", logger="agent-notes"), pytest.raises(ToolError, match="another session's login"):
        call(mcp, "read_note", path="agents/digest.md", grant=grant_id)
    assert value("agent_notes.elevations", event="refused") == before + 1
    line = [e for e in lines(caplog) if e["event"] == "elevation"][-1]
    assert (line["result"], line["level"], line["grant"]) == ("refused", "warning", grants.get(grant_id).ref)
    assert "another session" in line["why"] and line["caller_client"] == "claude-ai"
    assert grant_id not in "\n".join(r.getMessage() for r in caplog.records)
    # A made-up id is the caller's mistake, not a leak: refused, not counted as one.
    with pytest.raises(ToolError, match="not valid"):
        call(mcp, "read_note", path="agents/digest.md", grant="made-up")
    assert value("agent_notes.elevations", event="refused") == before + 1


def test_grants_gauge():
    grants = access.Grants()
    grants.request("c", "r")
    telemetry.TELEMETRY.watch_grants(grants.counts)
    assert points("agent_notes.grants")[(("status", "pending"),)] == 1


def test_foreign_login_counted(caplog):
    from fastmcp.server.auth import AccessToken
    from fastmcp.utilities.authorization import AuthContext

    check = server.allowed_subs_check(frozenset({"owner-sub"}))
    before = value("agent_notes.auth", event="denied")
    token = AccessToken(token="t", client_id="c", scopes=[], claims={"sub": "someone-else"})
    with caplog.at_level("INFO", logger="agent-notes"):
        assert not check(AuthContext(token=token, component=None))
    assert value("agent_notes.auth", event="denied") == before + 1
    line = lines(caplog)[-1]
    assert (line["event"], line["result"], line["sub"], line["level"]) == ("auth", "denied", "someone-else", "warning")


def test_client_registration_logged(caplog):
    class Info:
        client_id = "abc"
        client_name = "Claude Code"
        redirect_uris = ["http://localhost:33418/callback", "http://127.0.0.1:1/cb"]

    before = value("agent_notes.auth", event="client_registered")
    with caplog.at_level("INFO", logger="agent-notes"):
        server.note_registration(Info())
    line = lines(caplog)[-1]
    assert line["result"] == "client_registered" and line["redirect_hosts"] == ["127.0.0.1", "localhost"]
    assert value("agent_notes.auth", event="client_registered") == before + 1


def test_setup_without_endpoint_is_a_no_op():
    # No OTEL_EXPORTER_OTLP_ENDPOINT (tests, local runs): the providers stay
    # whatever was there, here the in-memory ones.
    provider = metrics.get_meter_provider()
    telemetry.setup({})
    assert metrics.get_meter_provider() is provider

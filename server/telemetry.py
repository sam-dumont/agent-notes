"""OpenTelemetry and the audit log for agent-notes.

Three outputs, one call site (`emit`, `annotate` and the counters):

- **Audit lines**: one JSON object per line on stdout, for whatever ships your
  logs (Vector, Promtail, Fluent Bit, `docker logs`). Paths and ids only,
  never note content or a grant id. `trace_id` links a line to its trace.
- **Traces**: FastMCP opens a span per MCP request (it ships only the OTel
  API: a no-op until an SDK is installed). With OTEL_EXPORTER_OTLP_ENDPOINT
  set, they go to your collector. We add the access decision to the span.
- **Metrics**: counters for what spans can't say (tier, client, refusal,
  elevation events), over OTLP to the same collector.

Labels stay bounded: `client` is an OAuth client id, and there are as many
as registered clients (a handful); never a path, a sub or a grant.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable

from opentelemetry import metrics, trace

log = logging.getLogger("agent-notes")

METER = "agent-notes"
ATTR = "agent_notes."  # span attribute prefix


class Telemetry:
    def __init__(self, meter: metrics.Meter | None = None):
        m = meter or metrics.get_meter(METER)
        self.calls = m.create_counter(
            "agent_notes.calls", unit="{call}",
            description="Tool calls by action, tier, client and result (ok, refused, error)",
        )
        self.elevations = m.create_counter(
            "agent_notes.elevations", unit="{event}",
            description="Elevation events: requested, approved, denied, refused, login_refused, login_failed",
        )
        self.auth = m.create_counter(
            "agent_notes.auth", unit="{event}",
            description="Auth events outside tool calls: denied (a sub not allowed), client_registered",
        )
        self._grants: Callable[[], dict[str, int]] = dict
        m.create_observable_gauge(
            "agent_notes.grants", unit="{grant}",
            description="Grants held in memory, by status (pending, approved, denied)",
            callbacks=[self._observe_grants],
        )

    def watch_grants(self, counts: Callable[[], dict[str, int]]) -> None:
        self._grants = counts

    def _observe_grants(self, _options):
        return [metrics.Observation(n, {"status": s}) for s, n in self._grants().items()]


# One set of instruments for the process. Created at import: the API hands
# out proxy instruments that bind to the SDK once setup() installs it.
TELEMETRY = Telemetry()


def _trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def emit(event: str, level: int = logging.INFO, **fields: object) -> None:
    """One audit line: JSON with `event`, a short `msg` for the Logs page,
    the fields (None dropped) and the current trace id."""
    body = {k: v for k, v in fields.items() if v is not None}
    line = {
        "ts": round(time.time(), 3),
        "level": logging.getLevelName(level).lower(),
        "event": event,
        "msg": "agent-notes: " + event + "".join(f" {k}={v}" for k, v in body.items() if k in ("action", "tier", "result", "path")),
        **body,
    }
    tid = _trace_id()
    if tid:
        line["trace_id"] = tid
    log.log(level, json.dumps(line, separators=(",", ":"), default=str))


def annotate(**attrs: object) -> None:
    """The access decision on the current (FastMCP) span."""
    span = trace.get_current_span()
    for k, v in attrs.items():
        if v is not None:
            span.set_attribute(ATTR + k, str(v) if not isinstance(v, (bool, int, float)) else v)


def setup(env: dict[str, str]) -> None:
    """Install the SDK when an OTLP endpoint is configured; otherwise the
    API stays a no-op (tests, local runs). Service name and resource
    attributes come from the standard OTEL_* variables."""
    if not env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create()  # OTEL_SERVICE_NAME, OTEL_RESOURCE_ATTRIBUTES
    tracer = TracerProvider(resource=resource)
    tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer)
    interval = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "30000"))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

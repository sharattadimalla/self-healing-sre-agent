"""OpenTelemetry wiring. Best-effort: the app must start even if this fails."""
from __future__ import annotations

import logging

logger = logging.getLogger("app.telemetry")


def setup_telemetry(app, engine, *, endpoint: str, service_name: str) -> bool:
    """Instrument FastAPI + SQLAlchemy and export spans via OTLP.

    Returns True if tracing export was configured, False otherwise. Never raises.
    """
    if not endpoint:
        logger.info("OTEL endpoint unset; tracing export disabled")
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
        logger.info("tracing export configured -> %s", endpoint)
        return True
    except Exception:  # pragma: no cover - defensive, exercised only on misconfig
        logger.exception("telemetry setup failed; continuing without tracing")
        return False

"""Optional OpenTelemetry tracing with a no-op local fallback."""

import os
from contextlib import contextmanager
from typing import Any, Iterator


class Telemetry:
    def __init__(self) -> None:
        self._configured = False
        self._trace: Any | None = None
        self._propagate: Any | None = None

    def configure(self) -> None:
        if self._configured:
            return
        self._configured = True
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if not endpoint:
            return
        try:
            from opentelemetry import propagate, trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": os.getenv("OTEL_SERVICE_NAME", "data-agent"),
                        "service.version": os.getenv("APP_VERSION", "2.1"),
                        "deployment.environment": os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "development"),
                    }
                )
            )
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            trace.set_tracer_provider(provider)
            self._trace = trace
            self._propagate = propagate
        except Exception as exc:
            print(f"[Telemetry] OpenTelemetry disabled: {exc}")

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        carrier: dict[str, str] | None = None,
    ) -> Iterator[Any | None]:
        self.configure()
        if self._trace is None:
            yield None
            return
        tracer = self._trace.get_tracer("data-agent")
        context = self._propagate.extract(carrier) if carrier and self._propagate is not None else None
        with tracer.start_as_current_span(name, context=context) as span:
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, str(value)[:2_000])
            yield span

    def inject(self, carrier: dict[str, str]) -> None:
        self.configure()
        if self._propagate is not None:
            self._propagate.inject(carrier)

    def current_trace_id(self) -> str | None:
        if self._trace is None:
            return None
        context = self._trace.get_current_span().get_span_context()
        return f"{context.trace_id:032x}" if context and context.is_valid else None

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        if self._trace is None:
            return
        span = self._trace.get_current_span()
        if span and span.is_recording():
            span.add_event(name, {key: str(value)[:2_000] for key, value in (attributes or {}).items()})


telemetry = Telemetry()

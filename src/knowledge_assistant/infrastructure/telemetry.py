"""OpenTelemetry OTLP/HTTP adapter."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from knowledge_assistant.ports.telemetry import Attributes


class OpenTelemetryAdapter:
    """Export application-owned spans and metrics through OTLP/HTTP."""

    def __init__(self, *, service_name: str, endpoint: str) -> None:
        base_endpoint = endpoint.rstrip("/")
        resource = Resource.create({SERVICE_NAME: service_name})

        trace_provider = TracerProvider(resource=resource)
        trace_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base_endpoint}/v1/traces"))
        )
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{base_endpoint}/v1/metrics")
        )
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=(metric_reader,),
        )

        self._tracer = trace_provider.get_tracer("knowledge-assistant")
        self._meter = meter_provider.get_meter("knowledge-assistant")
        self._trace_provider = trace_provider
        self._meter_provider = meter_provider
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Attributes | None = None,
    ) -> Iterator[object]:
        with self._tracer.start_as_current_span(
            name,
            attributes=dict(attributes or {}),
        ) as current:
            yield current

    def count(
        self,
        name: str,
        value: int = 1,
        attributes: Attributes | None = None,
    ) -> None:
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(name)
            self._counters[name] = counter
        counter.add(value, dict(attributes or {}))

    def observe(
        self,
        name: str,
        value: float,
        attributes: Attributes | None = None,
    ) -> None:
        histogram = self._histograms.get(name)
        if histogram is None:
            histogram = self._meter.create_histogram(name)
            self._histograms[name] = histogram
        histogram.record(value, dict(attributes or {}))

    def close(self) -> None:
        self._trace_provider.shutdown()
        self._meter_provider.shutdown()


def current_trace_context() -> tuple[str | None, str | None]:
    """Return IDs safe for restricted structured logs."""

    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"

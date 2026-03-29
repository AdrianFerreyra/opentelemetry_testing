import logging

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.settings import settings


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": settings.app_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.environment,
        }
    )


def _setup_traces(resource: Resource) -> None:
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def _setup_metrics(resource: Resource) -> None:
    exporter = OTLPMetricExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=10_000)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)


def _setup_logs(resource: Resource) -> None:
    exporter = OTLPLogExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    # Bridge Python's standard logging into OTel logs
    handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
    logging.getLogger().addHandler(handler)


def setup_telemetry() -> None:
    resource = _build_resource()
    if settings.otel_traces_enabled:
        _setup_traces(resource)
    if settings.otel_metrics_enabled:
        _setup_metrics(resource)
    if settings.otel_logs_enabled:
        _setup_logs(resource)

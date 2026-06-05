import logging

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
  BatchLogRecordProcessor,
  LogRecordExporter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
  MetricExporter,
  PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from settings import Exporter, get_settings
from starlette.applications import Starlette

_resource = Resource.create({"service.name": "service-oa-records"})


def _get_providers() -> tuple[LoggerProvider | None, TracerProvider | None]:
  settings = get_settings()

  if settings.otel_sdk_disabled:
    return None, None

  log_provider = LoggerProvider(resource=_resource)
  set_logger_provider(log_provider)

  trace_provider = TracerProvider(resource=_resource)
  trace.set_tracer_provider(trace_provider)

  return log_provider, trace_provider


def _get_exporters() -> tuple[
  list[LogRecordExporter],
  list[SpanExporter],
  list[MetricExporter],
]:
  settings = get_settings()

  if settings.otel_sdk_disabled:
    return [], [], []

  metric_exporters: list[MetricExporter] = []
  logs_exporters: list[LogRecordExporter] = []
  span_exporters: list[SpanExporter] = []

  if settings.otel_enable_otlp_exporter:
    if Exporter.OTLP in settings.otel_trace_exporters:
      span_exporters.append(
        OTLPSpanExporter(
          endpoint=settings.otel_exporter_otlp_endpoint,
          headers=settings.otel_exporter_otlp_headers,
          insecure=settings.otel_exporter_otlp_insecure,
        )
      )

    if Exporter.OTLP in settings.otel_metrics_exporters:
      metric_exporters.append(
        OTLPMetricExporter(
          endpoint=settings.otel_exporter_otlp_endpoint,
          headers=settings.otel_exporter_otlp_headers,
          insecure=settings.otel_exporter_otlp_insecure,
        )
      )

    if Exporter.OTLP in settings.otel_logging_exporters:
      logs_exporters.append(
        OTLPLogExporter(
          endpoint=settings.otel_exporter_otlp_endpoint,
          headers=settings.otel_exporter_otlp_headers,
          insecure=settings.otel_exporter_otlp_insecure,
        )
      )

  return logs_exporters, span_exporters, metric_exporters


def _setup_log_processors(
  provider: LoggerProvider | None,
  exporters: list[LogRecordExporter],
) -> None:
  if provider is None:
    return

  for exporter in exporters:
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))


def _setup_span_processors(
  provider: TracerProvider | None,
  exporters: list[SpanExporter],
) -> None:
  if provider is None:
    return

  for exporter in exporters:
    provider.add_span_processor(BatchSpanProcessor(exporter))


def _setup_metrics(exporters: list[MetricExporter]) -> MeterProvider | None:
  settings = get_settings()

  if settings.otel_sdk_disabled or not settings.otel_enable_metrics:
    return None

  metric_readers = [PeriodicExportingMetricReader(exporter) for exporter in exporters]

  meter_provider = MeterProvider(
    metric_readers=metric_readers,
    resource=_resource,
  )
  metrics.set_meter_provider(meter_provider)

  return meter_provider


# ------------------------------------------------------------------------------
# NOTE: Import-time setup is intentional.
#
# This allows uvicorn's logging.dictConfig() to resolve:
#
#   handlers:
#     otel:
#       (): otel.get_otel_handler
#
# At that point, get_otel_handler() must be importable and must already have access
# to an initialized LoggerProvider.

log_provider, trace_provider = _get_providers()

log_exporters, span_exporters, metric_exporters = _get_exporters()

_setup_log_processors(log_provider, log_exporters)
_setup_span_processors(trace_provider, span_exporters)

meter_provider = _setup_metrics(metric_exporters)


def get_otel_handler() -> logging.Handler:
  """Get the OTEL logging Handler"""
  settings = get_settings()

  if settings.otel_sdk_disabled:
    raise ValueError("Cannot use OTEL handler in logging configuration when OTEL_SDK_DISABLE is true")
  if log_provider is None:
    raise ValueError("OTEL log provider is not available")

  return LoggingHandler(logger_provider=log_provider)


def initialize_instrumentation(app: Starlette) -> None:
  """Initialize OTEL instrumentation for the Starlette app."""
  settings = get_settings()

  if settings.otel_sdk_disabled:
    return

  if settings.otel_enable_asgi:
    app.add_middleware(OpenTelemetryMiddleware)
  if settings.otel_enable_opensearch:
    URLLib3Instrumentor().instrument()


def shutdown_otel() -> None:
  """Flush and shutdown OTEL providers/processors on application shutdown."""
  if trace_provider is not None:
    trace_provider.shutdown()

  if log_provider is not None:
    log_provider.shutdown()

  if meter_provider is not None:
    meter_provider.shutdown()

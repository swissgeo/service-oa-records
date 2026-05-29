from enum import StrEnum

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Exporter(StrEnum):
  OTLP = "otlp"
  CONSOLE = "console"


class Settings(BaseSettings):
  model_config = SettingsConfigDict(
    env_file=(".env", ".env.default"),
    env_file_encoding="utf-8",
    enable_decoding=False,
    extra="ignore",
  )

  # OTEL configuration
  otel_sdk_disabled: bool = False
  # Instrumentation
  otel_enable_asgi: bool = True
  otel_enable_opensearch: bool = True
  # OTLP exporter
  otel_enable_otlp_exporter: bool = True
  otel_exporter_otlp_endpoint: str = "http://localhost:4317"
  otel_exporter_otlp_headers: str = ""
  otel_exporter_otlp_insecure: bool = False
  # Console exporter
  otel_enable_console_exporter: bool = False
  # Metrics
  otel_enable_metrics: bool = False

  # Configure exporters
  otel_trace_exporters: list[Exporter] = [Exporter.OTLP]
  otel_metrics_exporters: list[Exporter] = [Exporter.OTLP]
  otel_logging_exporters: list[Exporter] = [Exporter.OTLP]

  @field_validator(
    "otel_trace_exporters",
    "otel_metrics_exporters",
    "otel_logging_exporters",
    mode="before",
  )
  @classmethod
  def parse_list(cls, v: str | list[str]) -> list[str]:
    if isinstance(v, list):
      return v
    return v.split(",")


_settings: Settings | None = None


def get_settings() -> Settings:
  global _settings  # noqa: PLW0603
  if _settings is None:
    _settings = Settings()
  return _settings

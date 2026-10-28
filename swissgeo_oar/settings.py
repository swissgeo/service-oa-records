from functools import lru_cache
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SERVICE_NAME: ClassVar[str] = "service-oa-records"

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.default"),
        env_file_encoding="utf-8",
        enable_decoding=False,
        extra="ignore",
    )

    # URL prefix under which the pygeoapi API is mounted, also used to build the links.
    # NOTE: server.url in pygeoapi-config.yml repeats this default, as pygeoapi reads
    # that file itself (see tests/test_settings.py).
    api_prefix: str = "/api/oar/rc1"

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
    # Metrics
    otel_enable_metrics: bool = False

    # Size of the thread pool running the pygeoapi API calls. Also used as the
    # OpenSearch connection pool size, so every worker thread can hold a connection.
    threadpool_max_workers: int = 16

    # OpenSearch client
    opensearch_timeout: int = 30
    opensearch_max_retries: int = 3

    # Cache-Control header added to GET/HEAD responses. Empty disables it.
    cache_control_header: str = "public, max-age=300"

    # Serve pygeoapi's HTML output. When disabled, f=html is rejected and browsers get JSON.
    html_enabled: bool = False

    @property
    def otlp_kwargs(self) -> dict:
        return {
            "endpoint": self.otel_exporter_otlp_endpoint,
            "headers": self.otel_exporter_otlp_headers,
            "insecure": self.otel_exporter_otlp_insecure,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

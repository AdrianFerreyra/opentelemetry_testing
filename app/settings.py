from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # App
    app_name: str = "fastapi-otel-demo"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"

    # OTel Collector endpoint (gRPC)
    otel_exporter_otlp_endpoint: str = "http://otelcollector:4317"
    otel_traces_enabled: bool = True
    otel_metrics_enabled: bool = True
    otel_logs_enabled: bool = True


settings = Settings()

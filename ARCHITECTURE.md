# Architecture & OpenTelemetry Guide

This document explains how the solution is structured, how OpenTelemetry concepts are applied, and how telemetry data flows from the application to Grafana.

---

## Table of Contents

1. [Solution Overview](#1-solution-overview)
2. [OpenTelemetry Concepts](#2-opentelemetry-concepts)
3. [Application Instrumentation](#3-application-instrumentation)
4. [Data Flow](#4-data-flow)
5. [Infrastructure Components](#5-infrastructure-components)
6. [Port Reference](#6-port-reference)

---

## 1. Solution Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Docker network                             │
│                                                                     │
│  ┌──────────┐  OTLP/gRPC  ┌───────────────┐  scrape  ┌──────────┐ │
│  │ FastAPI  │────────────▶│ OTel Collector│─────────▶│Prometheus│ │
│  │  :8000   │             │   :4317/:4318  │          │  :9090   │ │
│  └──────────┘             │               │  push    └──────────┘ │
│                           │               │──────────▶┌──────────┐ │
│                           └───────────────┘    OTLP   │  Tempo   │ │
│                                                        │  :3200   │ │
│                                                        └──────────┘ │
│                                              ┌─────────────────────┐│
│                                              │       Grafana       ││
│                                              │  queries Prometheus ││
│                                              │  + Tempo → :3000    ││
│                                              └─────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

The FastAPI application never talks directly to Prometheus, Tempo, or Grafana. All telemetry is sent to the **OTel Collector**, which acts as the single routing layer between the app and every backend. This is the standard production pattern: changing backends requires only a collector config change, not a code change.

---

## 2. OpenTelemetry Concepts

OpenTelemetry defines three **signals** — independent types of telemetry data — plus a set of shared building blocks used by all three.

### 2.1 The Three Signals

| Signal | What it captures | Backend used here |
|--------|-----------------|-------------------|
| **Traces** | The lifecycle of a single request across one or more services | Tempo |
| **Metrics** | Numeric measurements aggregated over time | Prometheus |
| **Logs** | Discrete timestamped text events | Collector stdout (debug) |

### 2.2 Shared Building Blocks

**Resource**

A `Resource` is a set of key-value attributes that describe the *source* of all telemetry emitted by a process. Every span, metric, and log record produced by this app carries the same resource:

```python
# app/telemetry.py
Resource.create({
    "service.name": "fastapi-otel-demo",
    "service.version": "0.1.0",
    "deployment.environment": "development",
})
```

The `service.name` attribute is especially important — Tempo uses it to group traces under a service name, and Grafana's service map is built from it.

**Provider**

Each signal has its own provider (`TracerProvider`, `MeterProvider`, `LoggerProvider`). A provider is the factory and configuration root for that signal. You register one globally, and every piece of code that asks for a tracer or meter gets one backed by that provider:

```python
trace.set_tracer_provider(provider)   # global registration
tracer = trace.get_tracer(...)        # resolved from the global provider
```

**Exporter**

An exporter is the transport layer — it knows how to serialize and send data to a specific backend. All three signals here use the **OTLP gRPC exporter**, pointing at the OTel Collector:

```
OTLPSpanExporter   → traces   → otelcollector:4317
OTLPMetricExporter → metrics  → otelcollector:4317
OTLPLogExporter    → logs     → otelcollector:4317
```

The app itself is backend-agnostic. It only knows about OTLP, not Prometheus or Tempo.

**Processor / Reader**

Between the provider and the exporter sits a processor (traces/logs) or reader (metrics) that controls *when* and *how* data is sent:

- `BatchSpanProcessor` — collects spans in memory and flushes them in batches, reducing network overhead vs. sending each span immediately.
- `BatchLogRecordProcessor` — same pattern for log records.
- `PeriodicExportingMetricReader` — exports accumulated metric data every 10 seconds.

### 2.3 Traces in Detail

A **trace** represents the end-to-end journey of a single request. It is composed of **spans** — individual units of work with a start time, end time, and attributes.

**Automatic instrumentation** via `FastAPIInstrumentor` creates a root span for every HTTP request automatically. It captures:
- `http.method`, `http.route`, `http.status_code`
- Request duration
- Exceptions (marked as span errors)

**Manual instrumentation** adds a child span inside the handler:

```python
# app/main.py
with tracer.start_as_current_span("root-handler") as span:
    span.set_attribute("handler.name", "root")
    span.set_attribute("app.version", settings.app_version)
```

The result is a trace with two spans: the outer HTTP span created automatically, and the inner `root-handler` span created manually. In Tempo/Grafana, this appears as a nested timeline.

**Span context propagation** — when `FastAPIInstrumentor` creates the root span, it stores the active span in a context object. `tracer.start_as_current_span(...)` reads that context, automatically making the new span a child of the HTTP span. No parent IDs need to be passed manually.

### 2.4 Metrics in Detail

A **metric** is a named measurement that gets aggregated before export. Two instruments are used here:

**Counter** — a value that only goes up. Used to count requests:

```python
request_counter = meter.create_counter(
    name="api.requests.total",
    unit="1",
)
request_counter.add(1, {"endpoint": "/", "method": "GET", "status": "200"})
```

Each call to `.add()` increments the counter. The attributes (`endpoint`, `method`, `status`) become Prometheus labels, allowing slicing in PromQL.

**Histogram** — records the distribution of a value. Used to measure latency:

```python
request_duration = meter.create_histogram(
    name="api.request.duration",
    unit="ms",
)
request_duration.record(duration_ms, {"endpoint": "/"})
```

A histogram does not export raw values — it exports bucket counts, a sum, and a count. This is what makes PromQL percentile queries like `histogram_quantile(0.95, ...)` possible.

The `FastAPIInstrumentor` also automatically creates its own histogram for `http.server.request.duration`, which is why the Grafana dashboard queries `otel_http_server_request_duration_milliseconds_*` in addition to the custom metrics.

### 2.5 Logs in Detail

Python's standard `logging` module is bridged into OTel via a `LoggingHandler`:

```python
handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
logging.getLogger().addHandler(handler)
```

Any call to `logger.info(...)`, `logger.error(...)`, etc. throughout the app is automatically captured as an OTel log record and exported via OTLP alongside traces and metrics. The log records carry the same `Resource` attributes, so they're correlated to the same service.

In this setup, logs are exported to the collector and printed to its stdout via the `debug` exporter. Adding a Loki exporter to `otelcol-config.yaml` would route them to Grafana Loki with no app changes required.

---

## 3. Application Instrumentation

### 3.1 Initialization Order

The order in `app/main.py` is not arbitrary — it is a hard requirement:

```python
setup_telemetry()                    # 1. register providers globally
app = FastAPI(...)                   # 2. create the ASGI app
FastAPIInstrumentor.instrument_app(app)  # 3. patch ASGI middleware
```

`FastAPIInstrumentor` works by wrapping the app's ASGI middleware stack. If telemetry were initialized after the instrumentor ran, it would find no registered providers and emit no-op spans instead of real ones.

### 3.2 Module Responsibilities

| File | Responsibility |
|------|---------------|
| `app/settings.py` | All configuration via environment variables. The OTel endpoint, feature flags, and service identity live here — no hardcoded values in telemetry or app code. |
| `app/telemetry.py` | Builds the `Resource`, creates and registers the three providers, wires up processors/readers and exporters. Called once at startup. |
| `app/main.py` | Defines the FastAPI app and endpoints. Calls `setup_telemetry()` at module load, attaches the instrumentor, and adds manual instrumentation inside the `GET /` handler. |

---

## 4. Data Flow

### 4.1 Traces

```
GET / arrives
    │
    ▼
FastAPIInstrumentor (ASGI middleware)
    ├─ creates root span: "GET /"
    ├─ starts timer
    │
    ▼
root() handler
    ├─ tracer.start_as_current_span("root-handler")  ← child span
    │       span.set_attribute(...)
    │       logger.info(...)  ← also captured as OTel log record
    │
    ▼
handler returns
    │
    ├─ child span ends, queued in BatchSpanProcessor
    ├─ root span ends, queued in BatchSpanProcessor
    │
    ▼
BatchSpanProcessor (background thread)
    ├─ accumulates spans in memory
    ├─ flushes when batch is full OR 5s timeout
    │
    ▼
OTLPSpanExporter  ──gRPC──▶  OTel Collector :4317
    │
    ▼
Collector traces pipeline
    ├─ memory_limiter processor
    ├─ batch processor
    │
    ▼
otlp/tempo exporter  ──gRPC──▶  Tempo :4317
    │
    ▼
Stored in Tempo (local file backend)
    │
    ▼
Grafana queries Tempo HTTP API :3200
    └─ displayed in "Traces" panel and Explore view
```

### 4.2 Metrics

```
request_counter.add(1, {...})
request_duration.record(ms, {...})
    │
    ▼
MeterProvider accumulates in memory
(FastAPIInstrumentor also records http.server.request.duration)
    │
    ▼
PeriodicExportingMetricReader (every 10 seconds)
    │
    ▼
OTLPMetricExporter  ──gRPC──▶  OTel Collector :4317
    │
    ▼
Collector metrics pipeline
    ├─ memory_limiter processor
    ├─ batch processor
    │
    ▼
Prometheus exporter
    └─ exposes /metrics endpoint on Collector :8889
       (Prometheus text format, metrics prefixed with "otel_")
    │
    ▼
Prometheus scrapes Collector :8889 every 15s
    └─ stored in Prometheus TSDB
    │
    ▼
Grafana queries Prometheus HTTP API :9090
    └─ displayed in request rate, latency, and counter panels
```

### 4.3 Logs

```
logger.info("Handling GET /")
    │
    ▼
Python logging module
    │
    ▼
LoggingHandler (OTel bridge)
    ├─ converts LogRecord → OTel LogRecord
    ├─ attaches active trace/span context (correlates log to current trace)
    │
    ▼
BatchLogRecordProcessor
    │
    ▼
OTLPLogExporter  ──gRPC──▶  OTel Collector :4317
    │
    ▼
Collector logs pipeline
    │
    ▼
debug exporter → Collector stdout
```

### 4.4 Metric Naming Convention

The OTel Collector's Prometheus exporter applies a namespace prefix (`otel`) and converts the OTel semantic naming (dots, slashes) into Prometheus-compatible names (underscores). Examples:

| OTel metric name | Prometheus metric name |
|-----------------|----------------------|
| `api.requests.total` (Counter) | `otel_api_requests_total_total` |
| `api.request.duration` (Histogram) | `otel_api_request_duration_milliseconds_bucket/count/sum` |
| `http.server.request.duration` (auto, Histogram) | `otel_http_server_request_duration_milliseconds_bucket/count/sum` |

---

## 5. Infrastructure Components

### OTel Collector (`otel/opentelemetry-collector-contrib`)

The collector is the central routing hub. It is configured as a pipeline with three stages:

```
receivers → processors → exporters
```

The `contrib` distribution is used (not `core`) because it includes the Prometheus exporter and the OTLP/Tempo exporter, which are not in the core distribution.

Each signal (traces, metrics, logs) has its own independent pipeline. A single receiver (`otlp`) feeds all three pipelines — the collector demultiplexes by signal type automatically.

**Processors applied to every pipeline:**
- `memory_limiter` — enforces a memory cap on the collector process, preventing OOM under load spikes. Runs before the batch processor so data can be dropped before it accumulates.
- `batch` — groups telemetry records before forwarding. Reduces the number of outbound connections and improves throughput.

### Tempo (`grafana/tempo`)

Tempo is Grafana's distributed tracing backend. Key properties relevant to this setup:

- Uses a **local file backend** (`/var/tempo/blocks`) — no external object storage needed for local development.
- Accepts traces via **OTLP gRPC** on port `4317` inside the container.
- Exposes an **HTTP query API** on port `3200` that Grafana uses.
- Block retention is set to `1h` — traces older than one hour are deleted automatically.

### Prometheus (`prom/prometheus`)

Prometheus uses a **pull model**: it scrapes targets on a schedule rather than receiving pushed data. The scrape target here is the OTel Collector's Prometheus exporter endpoint (`:8889`), not the FastAPI app directly.

The `--enable-feature=exemplar-storage` flag enables Prometheus to store **exemplars** — trace ID pointers embedded in metric samples by the OTel SDK. This is what allows Grafana to render a "Go to Trace" link directly from a metric spike on a dashboard panel.

### Grafana (`grafana/grafana`)

Grafana is configured entirely via provisioning files mounted at startup — no manual UI configuration is needed:

- `provisioning/datasources/datasources.yaml` — registers Prometheus and Tempo as datasources, and configures the `exemplarTraceIdDestinations` linkage so metric panels can link to traces.
- `provisioning/dashboards/` — loads the pre-built dashboard JSON on startup.
- `grafana.ini` — enables anonymous access so no login is required in local development.

---

## 6. Port Reference

| Host port | Service | Purpose |
|-----------|---------|---------|
| `8000` | FastAPI | API (`GET /`, `GET /health`) |
| `4317` | OTel Collector | OTLP gRPC receiver — app sends telemetry here |
| `4318` | OTel Collector | OTLP HTTP receiver — useful for `curl` debugging |
| `8889` | OTel Collector | Prometheus scrape endpoint |
| `13133` | OTel Collector | Health check (`wget` liveness probe) |
| `55679` | OTel Collector | zPages debug UI (`/debug/tracez`, `/debug/pipelinez`) |
| `4319` | Tempo | OTLP gRPC ingestion (mapped from container `4317`, avoids collision with collector) |
| `3200` | Tempo | HTTP query API (Grafana reads from here) |
| `9090` | Prometheus | Web UI and query API |
| `3000` | Grafana | Dashboard UI |

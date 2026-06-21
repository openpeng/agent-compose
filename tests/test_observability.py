"""
Observability 单元测试
"""

import json
import time

import pytest

from agent_compose.observability import (
    StructuredLogger,
    LogLevel,
    Tracer,
    Span,
    MetricsCollector,
    PerformanceProfiler,
    Observability,
    get_trace_context,
    set_trace_context,
    TraceContext,
)


# ============ StructuredLogger Tests ============


class TestStructuredLogger:
    def test_log_output(self):
        lines = []
        logger = StructuredLogger("test", LogLevel.DEBUG, output=lambda line: lines.append(line))
        logger.info("Test message", extra={"key": "value"})

        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["message"] == "Test message"
        assert record["level"] == "INFO"
        assert record["logger"] == "test"
        assert record["extra"]["key"] == "value"
        assert "timestamp" in record

    def test_log_level_filtering(self):
        lines = []
        logger = StructuredLogger("test", LogLevel.WARNING, output=lambda line: lines.append(line))
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warning("warning msg")
        logger.error("error msg")

        assert len(lines) == 2
        assert json.loads(lines[0])["level"] == "WARNING"
        assert json.loads(lines[1])["level"] == "ERROR"

    def test_log_with_exception(self):
        lines = []
        logger = StructuredLogger("test", LogLevel.DEBUG, output=lambda line: lines.append(line))
        try:
            raise ValueError("test error")
        except Exception as e:
            logger.error("Error occurred", exc_info=e)

        record = json.loads(lines[0])
        assert record["exception"]["type"] == "ValueError"
        assert record["exception"]["message"] == "test error"


# ============ TraceContext Tests ============


class TestTraceContext:
    def test_default_context(self):
        ctx = get_trace_context()
        assert ctx.trace_id == ""
        assert ctx.span_id == ""

    def test_set_context(self):
        new_ctx = TraceContext(trace_id="abc", span_id="def")
        set_trace_context(new_ctx)
        ctx = get_trace_context()
        assert ctx.trace_id == "abc"
        assert ctx.span_id == "def"


# ============ Tracer Tests ============


class TestTracer:
    def test_start_span(self):
        tracer = Tracer()
        span = tracer.start_span("test_span")
        assert span.name == "test_span"
        assert span.trace_id != ""
        assert span.span_id != ""
        assert span.end_time == 0.0

    def test_end_span(self):
        tracer = Tracer()
        span = tracer.start_span("test_span")
        tracer.end_span(span)
        assert span.end_time > 0
        assert span.status == "ok"

    def test_span_with_error(self):
        tracer = Tracer()
        span = tracer.start_span("test_span")
        tracer.end_span(span, status="error")
        assert span.status == "error"

    def test_span_events(self):
        span = Span("test", "trace-1", "span-1")
        span.add_event("event1", {"key": "value"})
        assert len(span.events) == 1
        assert span.events[0]["name"] == "event1"

    def test_span_to_dict(self):
        tracer = Tracer()
        span = tracer.start_span("test", attributes={"key": "value"})
        tracer.end_span(span)
        d = span.to_dict()
        assert d["name"] == "test"
        assert d["status"] == "ok"
        assert d["attributes"]["key"] == "value"
        assert "duration_ms" in d

    def test_get_spans(self):
        tracer = Tracer()
        tracer.start_span("span1")
        tracer.start_span("span2")
        spans = tracer.get_spans()
        assert len(spans) == 2


# ============ MetricsCollector Tests ============


class TestMetricsCollector:
    def test_counter(self):
        metrics = MetricsCollector()
        metrics.counter("requests", 1)
        metrics.counter("requests", 1)
        result = metrics.get_metrics()
        assert result["counters"]["agent_compose_requests"] == 2

    def test_counter_with_labels(self):
        metrics = MetricsCollector()
        metrics.counter("requests", 1, {"method": "GET"})
        metrics.counter("requests", 1, {"method": "POST"})
        result = metrics.get_metrics()
        assert len(result["counters"]) == 2

    def test_gauge(self):
        metrics = MetricsCollector()
        metrics.gauge("active_sessions", 5)
        metrics.gauge("active_sessions", 3)
        result = metrics.get_metrics()
        assert result["gauges"]["agent_compose_active_sessions"] == 3

    def test_histogram(self):
        metrics = MetricsCollector()
        metrics.histogram("latency", 100)
        metrics.histogram("latency", 200)
        result = metrics.get_metrics()
        hist = result["histograms"]["agent_compose_latency"]
        assert hist["count"] == 2
        assert hist["sum"] == 300

    def test_prometheus_export(self):
        metrics = MetricsCollector()
        metrics.counter("requests", 5)
        metrics.gauge("active", 3)
        output = metrics.export_prometheus()
        assert "agent_compose_requests" in output
        assert "agent_compose_active" in output

    def test_clear(self):
        metrics = MetricsCollector()
        metrics.counter("test", 1)
        metrics.clear()
        result = metrics.get_metrics()
        assert result["counters"] == {}


# ============ PerformanceProfiler Tests ============


class TestPerformanceProfiler:
    def test_record(self):
        profiler = PerformanceProfiler()
        profiler.record("op1", 100)
        profiler.record("op1", 200)
        stats = profiler.get_stats()
        assert stats["op1"]["count"] == 2
        assert stats["op1"]["avg_ms"] == 150
        assert stats["op1"]["min_ms"] == 100
        assert stats["op1"]["max_ms"] == 200

    def test_percentiles(self):
        profiler = PerformanceProfiler()
        for i in range(1, 101):
            profiler.record("op", i * 10)
        stats = profiler.get_stats()
        # p50 of 10,20,30...1000 = 500 (50th value)
        assert stats["op"]["p50_ms"] == 500
        # p95 of 10,20...1000 = 950 (95th value)
        assert stats["op"]["p95_ms"] == 950

    def test_clear(self):
        profiler = PerformanceProfiler()
        profiler.record("op", 100)
        profiler.clear()
        assert profiler.get_stats() == {}


# ============ Observability Facade Tests ============


class TestObservability:
    def test_init(self):
        obs = Observability("test-service")
        assert obs.logger is not None
        assert obs.tracer is not None
        assert obs.metrics is not None
        assert obs.profiler is not None

    def test_trace_span_context_manager(self):
        obs = Observability()
        with obs.trace_span("test_operation") as span:
            assert span.name == "test_operation"
            span.add_event("midpoint")
        # Span should be ended after context manager exits
        assert span.end_time > 0

    def test_log_pipeline_start(self):
        lines = []
        obs = Observability()
        obs.logger = StructuredLogger("test", LogLevel.DEBUG, output=lambda line: lines.append(line))
        obs.log_pipeline_start("agent-1", "test-pipeline")

        record = json.loads(lines[0])
        assert record["message"] == "Pipeline execution started"
        assert record["extra"]["agent_id"] == "agent-1"

    def test_log_pipeline_end(self):
        lines = []
        obs = Observability()
        obs.logger = StructuredLogger("test", LogLevel.DEBUG, output=lambda line: lines.append(line))
        obs.log_pipeline_end("agent-1", True, 1500, step_count=5)

        record = json.loads(lines[0])
        assert record["extra"]["status"] == "success"
        assert record["extra"]["duration_ms"] == 1500

    def test_log_step_execution(self):
        lines = []
        obs = Observability()
        obs.logger = StructuredLogger("test", LogLevel.DEBUG, output=lambda line: lines.append(line))
        obs.log_step_execution("step1", "echo", True, 100)

        record = json.loads(lines[0])
        assert record["extra"]["tool_name"] == "echo"
        assert record["extra"]["status"] == "success"

    def test_log_tool_call(self):
        lines = []
        obs = Observability()
        obs.logger = StructuredLogger("test", LogLevel.DEBUG, output=lambda line: lines.append(line))
        obs.log_tool_call("bash", False, 500, error="command not found")

        record = json.loads(lines[0])
        assert record["level"] == "ERROR"
        assert record["extra"]["error"] == "command not found"

    def test_log_session_event(self):
        lines = []
        obs = Observability()
        obs.logger = StructuredLogger("test", LogLevel.DEBUG, output=lambda line: lines.append(line))
        obs.log_session_event("session-1", "created", {"user": "test"})

        record = json.loads(lines[0])
        assert record["extra"]["event"] == "created"
        assert record["extra"]["user"] == "test"

    def test_get_health_report(self):
        obs = Observability()
        obs.metrics.counter("test", 1)
        obs.profiler.record("op", 100)
        report = obs.get_health_report()
        assert report["service"] == "agent-compose"
        assert "metrics" in report
        assert "performance" in report

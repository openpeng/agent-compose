"""
Observability - 可观测性模块

提供:
- 结构化日志 (JSON 格式)
- 指标收集 (Prometheus 兼容)
- 执行追踪 (OpenTelemetry 风格)
- 性能分析

设计原则:
- 零依赖核心 (纯 Python)
- 可选增强 (OpenTelemetry, Prometheus 客户端)
- 低开销 (异步采样)
"""

import json
import os
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

# ============ 结构化日志 ============


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class StructuredLogger:
    """结构化日志记录器

    输出 JSON 格式日志，包含标准字段:
    - timestamp: ISO 格式时间戳
    - level: 日志级别
    - message: 日志消息
    - logger: 记录器名称
    - trace_id: 追踪 ID
    - span_id: 跨度 ID
    - extra: 额外字段
    """

    def __init__(
        self,
        name: str = "agent-compose",
        level: LogLevel = LogLevel.INFO,
        output: Optional[Callable[[str], None]] = None,
    ):
        self.name = name
        self.level = level
        self.output = output or self._default_output
        self._lock = threading.Lock()

    def _default_output(self, line: str) -> None:
        print(line, flush=True)

    def _should_log(self, level: LogLevel) -> bool:
        levels = list(LogLevel)
        return levels.index(level) >= levels.index(self.level)

    def _log(
        self,
        level: LogLevel,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        exc_info: Optional[Exception] = None,
    ) -> None:
        if not self._should_log(level):
            return

        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z",
            "level": level.value,
            "message": message,
            "logger": self.name,
        }

        # 追踪上下文
        trace_ctx = get_trace_context()
        if trace_ctx.trace_id:
            record["trace_id"] = trace_ctx.trace_id
        if trace_ctx.span_id:
            record["span_id"] = trace_ctx.span_id

        # 额外字段
        if extra:
            record["extra"] = extra

        # 异常信息
        if exc_info:
            record["exception"] = {
                "type": type(exc_info).__name__,
                "message": str(exc_info),
            }

        with self._lock:
            self.output(json.dumps(record, ensure_ascii=False, default=str))

    def debug(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(LogLevel.DEBUG, message, extra)

    def info(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(LogLevel.INFO, message, extra)

    def warning(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(LogLevel.WARNING, message, extra)

    def error(
        self,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        exc_info: Optional[Exception] = None,
    ) -> None:
        self._log(LogLevel.ERROR, message, extra, exc_info)

    def critical(
        self,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        exc_info: Optional[Exception] = None,
    ) -> None:
        self._log(LogLevel.CRITICAL, message, extra, exc_info)


# 全局日志记录器
_default_logger: Optional[StructuredLogger] = None


def get_logger(name: str = "agent-compose") -> StructuredLogger:
    """获取全局日志记录器"""
    global _default_logger
    if _default_logger is None:
        # 从环境变量读取日志级别
        level_str = os.environ.get("AGENT_COMPOSE_LOG_LEVEL", "INFO").upper()
        try:
            level = LogLevel[level_str]
        except KeyError:
            level = LogLevel.INFO
        _default_logger = StructuredLogger(name=name, level=level)
    return _default_logger


def set_logger(logger: StructuredLogger) -> None:
    """设置全局日志记录器"""
    global _default_logger
    _default_logger = logger


# ============ 执行追踪 ============


@dataclass
class TraceContext:
    """追踪上下文"""

    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    agent_id: str = ""
    session_id: str = ""


# 上下文变量 (async-safe)
_current_trace: ContextVar[TraceContext] = ContextVar("trace_context", default=TraceContext())


def get_trace_context() -> TraceContext:
    """获取当前追踪上下文"""
    return _current_trace.get()


def set_trace_context(ctx: TraceContext) -> None:
    """设置当前追踪上下文"""
    _current_trace.set(ctx)


@dataclass
class Span:
    """追踪跨度"""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    status: str = "ok"  # ok / error
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def end(self, status: str = "ok") -> None:
        self.end_time = time.time()
        self.status = status

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round((self.end_time - self.start_time) * 1000, 2) if self.end_time else None,
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


class Tracer:
    """追踪器"""

    def __init__(self, service_name: str = "agent-compose"):
        self.service_name = service_name
        self._spans: List[Span] = []
        self._lock = threading.Lock()

    def start_span(
        self,
        name: str,
        parent_span_id: str = "",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        trace_ctx = get_trace_context()
        trace_id = trace_ctx.trace_id or str(uuid.uuid4())
        span_id = str(uuid.uuid4())[:16]

        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id or trace_ctx.span_id,
            attributes=attributes or {},
        )

        # 更新上下文
        new_ctx = TraceContext(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id or trace_ctx.span_id,
            agent_id=trace_ctx.agent_id,
            session_id=trace_ctx.session_id,
        )
        set_trace_context(new_ctx)

        with self._lock:
            self._spans.append(span)

        return span

    def end_span(self, span: Span, status: str = "ok") -> None:
        span.end(status)
        # 恢复父级上下文
        trace_ctx = get_trace_context()
        if trace_ctx.parent_span_id:
            set_trace_context(
                TraceContext(
                    trace_id=trace_ctx.trace_id,
                    span_id=trace_ctx.parent_span_id,
                    parent_span_id="",
                    agent_id=trace_ctx.agent_id,
                    session_id=trace_ctx.session_id,
                )
            )

    def get_spans(self) -> List[Span]:
        with self._lock:
            return list(self._spans)

    def export_spans(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self._spans]

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()


# ============ 指标收集 ============


class MetricsCollector:
    """指标收集器 (Prometheus 兼容格式)

    支持指标类型:
    - counter: 单调递增计数器
    - gauge: 可增可减的度量值
    - histogram: 直方图 (分桶统计)
    """

    def __init__(self, prefix: str = "agent_compose"):
        self.prefix = prefix
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def counter(self, name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        """增加计数器"""
        key = self._format_key(name, labels)
        with self._lock:
            self._counters[key] += value

    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """设置 gauge 值"""
        key = self._format_key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """记录直方图值"""
        key = self._format_key(name, labels)
        with self._lock:
            self._histograms[key].append(value)

    def _format_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        key = f"{self.prefix}_{name}"
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
            key = f'{key}{{{label_str}}}'
        return key

    def get_metrics(self) -> Dict[str, Any]:
        """获取所有指标"""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: {"count": len(v), "sum": sum(v), "values": v} for k, v in self._histograms.items()},
            }

    def export_prometheus(self) -> str:
        """导出 Prometheus 格式文本"""
        lines = []

        with self._lock:
            # Counters
            for key, value in self._counters.items():
                lines.append(f"# TYPE {key.split('{')[0]} counter")
                lines.append(f"{key} {value}")

            # Gauges
            for key, value in self._gauges.items():
                lines.append(f"# TYPE {key.split('{')[0]} gauge")
                lines.append(f"{key} {value}")

            # Histograms (简化输出: count + sum)
            for key, values in self._histograms.items():
                base = key.split("{")[0]
                lines.append(f"# TYPE {base} histogram")
                lines.append(f"{key}_count {len(values)}")
                lines.append(f"{key}_sum {sum(values)}")

        return "\n".join(lines)

    def clear(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


# ============ 性能分析 ============


class PerformanceProfiler:
    """性能分析器"""

    def __init__(self):
        self._timings: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, name: str, duration_ms: float) -> None:
        with self._lock:
            self._timings[name].append(duration_ms)

    def get_stats(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            stats = {}
            for name, values in self._timings.items():
                if not values:
                    continue
                sorted_values = sorted(values)
                n = len(sorted_values)
                stats[name] = {
                    "count": n,
                    "total_ms": sum(values),
                    "avg_ms": sum(values) / n,
                    "min_ms": sorted_values[0],
                    "max_ms": sorted_values[-1],
                    "p50_ms": sorted_values[(n - 1) // 2],
                    "p95_ms": sorted_values[int((n - 1) * 0.95)] if n >= 20 else sorted_values[-1],
                    "p99_ms": sorted_values[int((n - 1) * 0.99)] if n >= 100 else sorted_values[-1],
                }
            return stats

    def clear(self) -> None:
        with self._lock:
            self._timings.clear()


# ============ 可观测性门面 ============


class Observability:
    """可观测性门面 - 统一入口

    集成日志、追踪、指标、性能分析于一体。
    """

    def __init__(self, service_name: str = "agent-compose"):
        self.logger = get_logger(service_name)
        self.tracer = Tracer(service_name)
        self.metrics = MetricsCollector(prefix=service_name.replace("-", "_"))
        self.profiler = PerformanceProfiler()

    def trace_span(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """上下文管理器风格的 span 追踪"""
        return _TraceSpanContext(self.tracer, name, attributes)

    def log_pipeline_start(self, agent_id: str, pipeline_name: str = "") -> None:
        trace_id = str(uuid.uuid4())
        set_trace_context(
            TraceContext(trace_id=trace_id, agent_id=agent_id)
        )
        self.logger.info(
            "Pipeline execution started",
            extra={"agent_id": agent_id, "pipeline_name": pipeline_name, "trace_id": trace_id},
        )
        self.metrics.counter("pipeline_executions_total", 1, {"agent_id": agent_id})

    def log_pipeline_end(
        self,
        agent_id: str,
        success: bool,
        duration_ms: float,
        step_count: int = 0,
    ) -> None:
        status = "success" if success else "failure"
        self.logger.info(
            "Pipeline execution completed",
            extra={
                "agent_id": agent_id,
                "status": status,
                "duration_ms": duration_ms,
                "step_count": step_count,
            },
        )
        self.metrics.counter("pipeline_completed_total", 1, {"agent_id": agent_id, "status": status})
        self.metrics.histogram("pipeline_duration_ms", duration_ms, {"agent_id": agent_id})
        self.profiler.record("pipeline_execution", duration_ms)

    def log_step_execution(
        self,
        step_name: str,
        tool_name: str,
        success: bool,
        duration_ms: float,
    ) -> None:
        status = "success" if success else "failure"
        self.logger.info(
            f"Step '{step_name}' executed",
            extra={
                "step_name": step_name,
                "tool_name": tool_name,
                "status": status,
                "duration_ms": duration_ms,
            },
        )
        self.metrics.counter("step_executions_total", 1, {"tool": tool_name, "status": status})
        self.metrics.histogram("step_duration_ms", duration_ms, {"tool": tool_name})
        self.profiler.record(f"step_{tool_name}", duration_ms)

    def log_tool_call(
        self,
        tool_name: str,
        success: bool,
        duration_ms: float,
        error: Optional[str] = None,
    ) -> None:
        status = "success" if success else "failure"
        extra = {"tool": tool_name, "status": status, "duration_ms": duration_ms}
        if error:
            extra["error"] = error
            self.logger.error(f"Tool '{tool_name}' failed", extra=extra)
        else:
            self.logger.info(f"Tool '{tool_name}' executed", extra=extra)
        self.metrics.counter("tool_calls_total", 1, {"tool": tool_name, "status": status})
        self.metrics.histogram("tool_duration_ms", duration_ms, {"tool": tool_name})

    def log_session_event(self, session_id: str, event: str, details: Optional[Dict[str, Any]] = None) -> None:
        extra = {"session_id": session_id, "event": event}
        if details:
            extra.update(details)
        self.logger.info(f"Session event: {event}", extra=extra)

    def get_health_report(self) -> Dict[str, Any]:
        """获取健康报告"""
        return {
            "service": "agent-compose",
            "timestamp": time.time(),
            "metrics": self.metrics.get_metrics(),
            "performance": self.profiler.get_stats(),
            "active_spans": len(self.tracer.get_spans()),
        }


class _TraceSpanContext:
    """追踪 Span 上下文管理器"""

    def __init__(self, tracer: Tracer, name: str, attributes: Optional[Dict[str, Any]]):
        self.tracer = tracer
        self.name = name
        self.attributes = attributes
        self.span: Optional[Span] = None

    def __enter__(self) -> Span:
        self.span = self.tracer.start_span(self.name, attributes=self.attributes)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.span:
            status = "error" if exc_type else "ok"
            if exc_val:
                self.span.add_event("exception", {"type": exc_type.__name__, "message": str(exc_val)})
            self.tracer.end_span(self.span, status)

    async def __aenter__(self) -> Span:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)

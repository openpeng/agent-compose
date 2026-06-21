"""
Resilience - 弹性与容错模块

提供:
- 熔断器 (Circuit Breaker)
- 重试策略 (Retry Policy with exponential backoff + jitter)
- 降级注册表 (Fallback Registry)
- 弹性调用装饰器 (resilient_call)

设计原则:
- 零外部依赖 (纯 Python)
- 线程安全
- 异步原生支持
"""

import asyncio
import functools
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, Union


# ============ 熔断器 ============


class CircuitState(Enum):
    """熔断器状态"""

    CLOSED = "closed"       # 正常状态，允许请求通过
    OPEN = "open"           # 熔断状态，拒绝请求
    HALF_OPEN = "half_open" # 半开状态，允许试探请求


class CircuitBreakerOpenError(Exception):
    """熔断器开启时抛出的异常"""

    def __init__(self, name: str, retry_after: float = 0.0):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker '{name}' is OPEN. Retry after {retry_after:.1f}s")


class CircuitBreaker:
    """熔断器

    当失败率达到阈值时自动熔断，防止级联故障。
    支持三种状态: closed -> open -> half_open -> closed

    Args:
        name: 熔断器名称
        failure_threshold: 触发熔断的失败次数阈值
        recovery_timeout: 从 open 到 half_open 的恢复等待时间（秒）
        half_open_max_calls: 半开状态下允许的最大试探请求数
        success_threshold: 半开状态下恢复 closed 所需的成功次数
        exception_types: 计入失败的异常类型列表（默认所有 Exception）
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        success_threshold: int = 2,
        exception_types: Optional[List[Type[Exception]]] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        self.exception_types = tuple(exception_types or [Exception])

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state
        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0
        elif new_state == CircuitState.OPEN:
            self._last_failure_time = time.time()
            self._half_open_calls = 0
            self._success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._success_count = 0

    def _should_allow(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
                return True
            return False
        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False
        return True

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """同步调用包装"""
        with self._lock:
            if not self._should_allow():
                retry_after = self.recovery_timeout - (time.time() - self._last_failure_time)
                raise CircuitBreakerOpenError(self.name, max(0, retry_after))

        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            if isinstance(e, self.exception_types):
                self.record_failure()
            raise

    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """异步调用包装"""
        with self._lock:
            if not self._should_allow():
                retry_after = self.recovery_timeout - (time.time() - self._last_failure_time)
                raise CircuitBreakerOpenError(self.name, max(0, retry_after))

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            if isinstance(e, self.exception_types):
                self.record_failure()
            raise

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = max(0, self._failure_count - 1)

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def get_stats(self) -> Dict[str, Any]:
        """获取熔断器统计信息"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "half_open_calls": self._half_open_calls,
                "last_failure_time": self._last_failure_time,
            }

    def reset(self) -> None:
        """手动重置熔断器到 closed 状态"""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)


# ============ 重试策略 ============


class RetryExhaustedError(Exception):
    """重试次数耗尽时抛出的异常"""

    def __init__(self, message: str, last_exception: Optional[Exception] = None):
        self.last_exception = last_exception
        super().__init__(message)


@dataclass
class RetryPolicy:
    """重试策略

    支持指数退避、最大重试次数、抖动等策略。

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_multiplier: 退避乘数
        jitter: 是否启用抖动（随机偏移）
        jitter_max: 最大抖动偏移量（秒）
        retry_on: 允许重试的异常类型列表
        on_retry: 每次重试时的回调函数 (attempt, exception, delay)
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    jitter_max: float = 0.5
    retry_on: List[Type[Exception]] = field(default_factory=lambda: [Exception])
    on_retry: Optional[Callable[[int, Exception, float], None]] = None

    def compute_delay(self, attempt: int) -> float:
        """计算第 attempt 次重试的延迟时间"""
        delay = self.base_delay * (self.backoff_multiplier ** (attempt - 1))
        delay = min(delay, self.max_delay)
        if self.jitter:
            delay += random.uniform(0, self.jitter_max)
        return delay

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """判断是否应该重试"""
        if attempt > self.max_retries:
            return False
        return isinstance(exception, tuple(self.retry_on))

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """同步执行带重试"""
        last_exception: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 2):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if not self.should_retry(e, attempt):
                    raise
                if attempt <= self.max_retries:
                    delay = self.compute_delay(attempt)
                    if self.on_retry:
                        self.on_retry(attempt, e, delay)
                    time.sleep(delay)
        raise RetryExhaustedError(
            f"Function failed after {self.max_retries + 1} attempts",
            last_exception,
        )

    async def execute_async(self, func: Callable, *args, **kwargs) -> Any:
        """异步执行带重试"""
        last_exception: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 2):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if not self.should_retry(e, attempt):
                    raise
                if attempt <= self.max_retries:
                    delay = self.compute_delay(attempt)
                    if self.on_retry:
                        if asyncio.iscoroutinefunction(self.on_retry):
                            await self.on_retry(attempt, e, delay)
                        else:
                            self.on_retry(attempt, e, delay)
                    await asyncio.sleep(delay)
        raise RetryExhaustedError(
            f"Function failed after {self.max_retries + 1} attempts",
            last_exception,
        )


# ============ 降级注册表 ============


class FallbackRegistry:
    """降级处理注册表

    为特定函数或操作注册降级处理器，当主逻辑失败时自动执行降级逻辑。
    """

    def __init__(self):
        self._fallbacks: Dict[str, Callable] = {}
        self._lock = threading.Lock()

    def register(self, name: str, handler: Callable) -> None:
        """注册降级处理器

        Args:
            name: 处理器名称（通常对应主函数名）
            handler: 降级处理函数，签名应与主函数一致
        """
        with self._lock:
            self._fallbacks[name] = handler

    def unregister(self, name: str) -> None:
        """注销降级处理器"""
        with self._lock:
            self._fallbacks.pop(name, None)

    def get(self, name: str) -> Optional[Callable]:
        """获取降级处理器"""
        with self._lock:
            return self._fallbacks.get(name)

    def has_fallback(self, name: str) -> bool:
        """检查是否有降级处理器"""
        with self._lock:
            return name in self._fallbacks

    def execute(self, name: str, *args, **kwargs) -> Any:
        """执行降级处理器"""
        handler = self.get(name)
        if handler is None:
            raise RuntimeError(f"No fallback registered for '{name}'")
        return handler(*args, **kwargs)

    async def execute_async(self, name: str, *args, **kwargs) -> Any:
        """异步执行降级处理器"""
        handler = self.get(name)
        if handler is None:
            raise RuntimeError(f"No fallback registered for '{name}'")
        if asyncio.iscoroutinefunction(handler):
            return await handler(*args, **kwargs)
        return handler(*args, **kwargs)

    def list_fallbacks(self) -> List[str]:
        """列出所有已注册的降级处理器名称"""
        with self._lock:
            return list(self._fallbacks.keys())


# ============ 弹性调用装饰器 ============


class ResilientConfig:
    """弹性调用配置

    组合熔断器、重试策略和降级处理。
    """

    def __init__(
        self,
        circuit_breaker: Optional[CircuitBreaker] = None,
        retry_policy: Optional[RetryPolicy] = None,
        fallback_registry: Optional[FallbackRegistry] = None,
        fallback_name: Optional[str] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        self.circuit_breaker = circuit_breaker
        self.retry_policy = retry_policy
        self.fallback_registry = fallback_registry
        self.fallback_name = fallback_name
        self.on_error = on_error


def resilient_call(
    circuit_breaker: Optional[CircuitBreaker] = None,
    retry_policy: Optional[RetryPolicy] = None,
    fallback_registry: Optional[FallbackRegistry] = None,
    fallback_name: Optional[str] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
):
    """弹性调用装饰器（支持同步和异步函数）

    组合熔断器、重试策略和降级处理，为函数提供全面的容错保护。

    Args:
        circuit_breaker: 熔断器实例
        retry_policy: 重试策略实例
        fallback_registry: 降级注册表实例
        fallback_name: 降级处理器名称（默认使用被装饰函数名）
        on_error: 错误回调函数

    Example:
        breaker = CircuitBreaker(name="api", failure_threshold=3)
        retry = RetryPolicy(max_retries=3, base_delay=1.0)
        fallback = FallbackRegistry()

        @resilient_call(circuit_breaker=breaker, retry_policy=retry, fallback_registry=fallback)
        async def call_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        nonlocal fallback_name
        if fallback_name is None:
            fallback_name = func.__name__

        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                try:
                    target = func
                    # 应用重试
                    if retry_policy is not None:
                        target = functools.partial(retry_policy.execute_async, func)
                    # 应用熔断器
                    if circuit_breaker is not None:
                        if retry_policy is not None:
                            target = functools.partial(circuit_breaker.call_async, retry_policy.execute_async, func)
                        else:
                            target = functools.partial(circuit_breaker.call_async, func)
                    # 执行
                    if retry_policy is not None and circuit_breaker is None:
                        return await target()
                    elif circuit_breaker is not None:
                        return await target()
                    return await func(*args, **kwargs)
                except Exception as e:
                    if on_error:
                        if asyncio.iscoroutinefunction(on_error):
                            await on_error(e)
                        else:
                            on_error(e)
                    # 尝试降级
                    if fallback_registry is not None and fallback_registry.has_fallback(fallback_name):
                        return await fallback_registry.execute_async(fallback_name, *args, **kwargs)
                    raise
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                try:
                    # 同步函数暂不支持重试+熔断器组合（需更复杂的实现）
                    if retry_policy is not None:
                        result = retry_policy.execute(func, *args, **kwargs)
                    elif circuit_breaker is not None:
                        result = circuit_breaker.call(func, *args, **kwargs)
                    else:
                        result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    if on_error:
                        on_error(e)
                    if fallback_registry is not None and fallback_registry.has_fallback(fallback_name):
                        return fallback_registry.execute(fallback_name, *args, **kwargs)
                    raise
            return sync_wrapper
    return decorator


# ============ 便捷工厂函数 ============


def create_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
) -> CircuitBreaker:
    """创建标准熔断器"""
    return CircuitBreaker(
        name=name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
    )


def create_retry_policy(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exponential: bool = True,
    jitter: bool = True,
) -> RetryPolicy:
    """创建标准重试策略"""
    return RetryPolicy(
        max_retries=max_retries,
        base_delay=base_delay,
        backoff_multiplier=2.0 if exponential else 1.0,
        jitter=jitter,
    )

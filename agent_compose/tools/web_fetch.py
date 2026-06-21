"""
web_fetch 工具 - HTTP 请求

支持:
- GET/POST/PUT/DELETE 等方法
- 自定义请求头
- 超时控制
- 重定向跟随
- SSRF 防护（内部 IP 拦截）
"""

import ipaddress
import re
import socket
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# 阻止的内部 IP 范围
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_internal_ip(hostname: str) -> bool:
    """检查主机名是否解析到内部 IP"""
    try:
        # 先检查是否是 IP 地址
        ip = ipaddress.ip_address(hostname)
        for network in BLOCKED_IP_RANGES:
            if ip in network:
                return True
        return False
    except ValueError:
        pass

    # 尝试解析主机名
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                for network in BLOCKED_IP_RANGES:
                    if ip in network:
                        return True
            except ValueError:
                continue
    except socket.gaierror:
        pass

    return False


def _is_host_allowed(hostname: str, whitelist: Optional[List[str]] = None) -> bool:
    """检查主机名是否在白名单中"""
    if not whitelist:
        return True

    hostname_lower = hostname.lower()
    for pattern in whitelist:
        pattern_lower = pattern.lower()
        if pattern_lower.startswith("*."):
            # 通配符匹配子域名
            suffix = pattern_lower[2:]
            if hostname_lower == suffix or hostname_lower.endswith("." + suffix):
                return True
        elif hostname_lower == pattern_lower:
            return True

    return False


def _validate_url(url: str, whitelist: Optional[List[str]] = None) -> None:
    """验证 URL 安全性"""
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"web_fetch: Invalid URL: {e}")

    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        raise ValueError(f"web_fetch: URL must use http or https scheme: {url}")

    if not parsed.hostname:
        raise ValueError(f"web_fetch: URL has no host: {url}")

    # 白名单检查
    if not _is_host_allowed(parsed.hostname, whitelist):
        raise PermissionError(f"web_fetch: Host '{parsed.hostname}' is not in the network whitelist")

    # 内部 IP 检查
    if _is_internal_ip(parsed.hostname):
        raise PermissionError(f"web_fetch: Access to internal IP range is blocked: {parsed.hostname}")


async def web_fetch_tool(args: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    HTTP 请求

    Args:
        args: {
            "url": str,                   # 必需: 目标 URL
            "method": str,                # 可选: HTTP 方法, 默认 "GET"
            "headers": Dict[str, str],    # 可选: 请求头
            "body": str,                  # 可选: 请求体
            "timeout": int,               # 可选: 超时(毫秒), 默认 30000
            "follow_redirects": bool,     # 可选: 跟随重定向, 默认 True
            "max_redirects": int,         # 可选: 最大重定向次数, 默认 10
        }

    Returns:
        {
            "status_code": int,
            "headers": Dict[str, str],
            "body": str,
            "duration_ms": float,
            "final_url": str,
        }
    """
    url = args.get("url")
    if not url or not isinstance(url, str):
        raise ValueError("web_fetch: 'url' parameter is required and must be a string")

    # URL 验证
    _validate_url(url)

    method = (args.get("method") or "GET").upper()
    headers = args.get("headers", {})
    body = args.get("body")
    timeout_ms = args.get("timeout", 30000)
    if timeout_ms <= 0:
        timeout_ms = 30000
    timeout_sec = timeout_ms / 1000.0
    follow_redirects = args.get("follow_redirects", True)
    max_redirects = args.get("max_redirects", 10)

    # 默认 User-Agent
    request_headers = dict(headers)
    if "user-agent" not in {k.lower() for k in request_headers}:
        request_headers["User-Agent"] = "agent-compose/1.0"

    import httpx

    start_time = time.time()
    redirect_count = 0
    current_url = url

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_sec),
            follow_redirects=False,  # 手动处理重定向以计数
        ) as client:
            while True:
                request_kwargs: Dict[str, Any] = {
                    "headers": request_headers,
                }
                if body and method in ("POST", "PUT", "PATCH"):
                    request_kwargs["content"] = body

                response = await client.request(method, current_url, **request_kwargs)

                # 处理重定向
                if follow_redirects and response.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > max_redirects:
                        raise RuntimeError(f"web_fetch: Too many redirects (max {max_redirects})")

                    location = response.headers.get("location")
                    if not location:
                        break

                    # 解析相对重定向
                    from urllib.parse import urljoin
                    current_url = urljoin(current_url, location)
                    _validate_url(current_url)

                    # 303 转换为 GET
                    if response.status_code == 303:
                        method = "GET"
                        body = None
                    continue

                break

        duration_ms = (time.time() - start_time) * 1000

        # 处理响应头
        response_headers = dict(response.headers)

        # 读取响应体
        response_body = response.text

        return {
            "status_code": response.status_code,
            "headers": response_headers,
            "body": response_body,
            "duration_ms": round(duration_ms, 2),
            "final_url": current_url,
        }

    except httpx.TimeoutException:
        raise TimeoutError(f"web_fetch: Request timed out after {timeout_ms}ms")
    except (ValueError, PermissionError):
        raise
    except Exception as e:
        raise RuntimeError(f"web_fetch: Request failed: {e}")

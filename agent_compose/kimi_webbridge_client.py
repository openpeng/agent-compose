"""
Kimi WebBridge Client - HTTP JSON API 客户端

Kimi WebBridge 通过浏览器扩展 + 本地 daemon 工作：
  - daemon 监听 http://127.0.0.1:10086
  - 单入口 POST /command
  - body: {"action": "<action_name>", "args": {...}}
  - response: {"ok": true/false, "data": {...}, "error": {...}}

工具名约定:
  - LLM 调用名: webbridge_<action>  (如 webbridge_navigate)
  - capability 兼容名: browser_<action>  (如 browser_navigate)
  - 底层 action: <action>            (如 navigate)
"""

import urllib.request
import urllib.error
import json
import os
from typing import Any, Dict, List, Optional, Callable


DEFAULT_BASE = "http://127.0.0.1:10086"

# ============================================================
# 工具注册表 — 用数据驱动替代 if-elif 链
# 格式: { llm_tool_name: {"action": "...", "arg_mapper": fn} }
# ============================================================

def _simple_mapper(**keymap) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """生成一个简单的参数映射函数。keymap: {arg_key: llm_param_key}"""
    def _mapper(args: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for action_key, llm_key in keymap.items():
            if llm_key in args and args[llm_key] is not None:
                out[action_key] = args[llm_key]
        return out
    return _mapper


def _screenshot_mapper(args: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    if "fullPage" in args:
        out["fullPage"] = bool(args["fullPage"])
    if args.get("selector"):
        out["selector"] = args["selector"]
    return out


def _pdf_mapper(args: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    if args.get("path"):
        out["path"] = args["path"]
    return out


# 工具定义: llm_tool_name -> {action, arg_mapper, tool_schema}
# schema 用于 LLM 工具调用时的描述生成
_WEBBRIDGE_TOOLS = [
    {
        "action": "navigate",
        "tool_name": "webbridge_navigate",
        "alt_name": "browser_navigate",
        "description": "在浏览器中打开一个 URL。这是所有网页操作的第一步。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要打开的完整 URL，例如 https://www.baidu.com"},
            },
            "required": ["url"],
        },
        "arg_mapper": _simple_mapper(url="url"),
    },
    {
        "action": "snapshot",
        "tool_name": "webbridge_snapshot",
        "alt_name": "browser_snapshot",
        "description": "获取当前页面的可访问性树快照，描述页面上的所有元素结构。用于了解页面布局和可操作的元素。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "arg_mapper": lambda args: {},
    },
    {
        "action": "click",
        "tool_name": "webbridge_click",
        "alt_name": "browser_click",
        "description": "点击页面上的一个元素。必须先用 snapshot 了解页面结构，再使用 CSS selector。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector，例如 'a#btn-1'、'button.submit'、'input[type=submit]'"},
            },
            "required": ["selector"],
        },
        "arg_mapper": _simple_mapper(selector="selector"),
    },
    {
        "action": "fill",
        "tool_name": "webbridge_fill",
        "alt_name": "browser_fill",
        "description": "在表单元素中填入一个值。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector，例如 'input#username'、'textarea.content'"},
                "value": {"type": "string", "description": "要填入的文本"},
            },
            "required": ["selector", "value"],
        },
        "arg_mapper": lambda args: {
            "selector": args.get("selector", ""),
            "value": str(args.get("value", "")),
        },
    },
    {
        "action": "key_type",
        "tool_name": "webbridge_type",
        "alt_name": "browser_type",
        "description": "在当前获得焦点的元素中输入文本。",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要输入的文本"}},
            "required": ["text"],
        },
        "arg_mapper": lambda args: {"text": str(args.get("text", ""))},
    },
    {
        "action": "send_keys",
        "tool_name": "webbridge_keys",
        "alt_name": "browser_keys",
        "description": "发送按键/特殊键。支持: enter, return, escape, esc, tab, backspace, delete, space, arrowup/down/left/right, home, end, pageup, pagedown, F1-F12, 单个字母或数字。",
        "parameters": {
            "type": "object",
            "properties": {"keys": {"type": "string", "description": "按键名，例如 'enter'、'tab'、'A'、'Hello World'"}},
            "required": ["keys"],
        },
        "arg_mapper": lambda args: {"keys": str(args.get("keys", ""))},
    },
    {
        "action": "evaluate",
        "tool_name": "webbridge_evaluate",
        "alt_name": "browser_evaluate",
        "description": "在当前页面执行一段 JavaScript 代码并返回值。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "JavaScript 表达式，例如 document.title、document.querySelectorAll('a').length"},
            },
            "required": ["code"],
        },
        "arg_mapper": _simple_mapper(code="code"),
    },
    {
        "action": "screenshot",
        "tool_name": "webbridge_screenshot",
        "alt_name": "browser_screenshot",
        "description": "对当前页面截图并返回文件路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "fullPage": {"type": "boolean", "description": "是否截取整页（需要滚动），默认 false"},
                "selector": {"type": "string", "description": "只截取某个元素（CSS selector），可选"},
            },
            "required": [],
        },
        "arg_mapper": _screenshot_mapper,
    },
    {
        "action": "save_as_pdf",
        "tool_name": "webbridge_pdf",
        "alt_name": "browser_pdf",
        "description": "将当前页面保存为 PDF 文件。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "PDF 保存路径，可选。默认自动命名到临时目录。"}},
            "required": [],
        },
        "arg_mapper": _pdf_mapper,
    },
    {
        "action": "list_tabs",
        "tool_name": "webbridge_list_tabs",
        "alt_name": "browser_list_tabs",
        "description": "列出当前浏览器所有打开的 tab。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "arg_mapper": lambda args: {},
    },
    {
        "action": "find_tab",
        "tool_name": "webbridge_find_tab",
        "alt_name": "browser_find_tab",
        "description": "查找并切换到匹配 URL 的 tab。",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "要匹配的 URL（部分匹配即可），例如 'baidu.com'"}},
            "required": ["url"],
        },
        "arg_mapper": _simple_mapper(url="url"),
    },
    {
        "action": "close_tab",
        "tool_name": "webbridge_close_tab",
        "alt_name": "browser_close_tab",
        "description": "关闭当前 tab。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "arg_mapper": lambda args: {},
    },
]

# 建立工具名 -> 条目 的快速查找表
_TOOL_BY_NAME = {t["tool_name"]: t for t in _WEBBRIDGE_TOOLS}
_TOOL_BY_ALT = {t["alt_name"]: t for t in _WEBBRIDGE_TOOLS}
# 所有已知工具名集合（含 capability 兼容名）
ALL_TOOL_NAMES = set(_TOOL_BY_NAME.keys()) | set(_TOOL_BY_ALT.keys())


def _resolve_tool_entry(name: str) -> Optional[Dict[str, Any]]:
    """通过工具名解析条目。支持 webbridge_* 和 browser_* 两套命名。"""
    return _TOOL_BY_NAME.get(name) or _TOOL_BY_ALT.get(name)


class KimiWebBridgeClient:
    """Kimi WebBridge HTTP 客户端。

    用例：
        client = KimiWebBridgeClient()
        health = client.health_check()
        result = client.run_tool("webbridge_navigate", {"url": "https://example.com"})
    """

    def __init__(self, base_url: str = DEFAULT_BASE, token: str = "", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.token = token or os.environ.get("WEBBRIDGE_TOKEN", "")
        self.timeout = timeout

    # ---------- 底层 HTTP ----------

    def _call(self, action: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """调用一个 action。返回 WebBridge 原生响应 dict (ok/data/error)。"""
        body = {"action": action, "args": args or {}}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/command",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.URLError as e:
            return {
                "ok": False,
                "error": {
                    "code": "connection_error",
                    "message": f"无法连接到 Kimi WebBridge ({self.base_url}): {e}. "
                    f"请确认浏览器中已启用 Kimi WebBridge 扩展。",
                },
            }
        except Exception as e:
            return {"ok": False, "error": {"code": "client_error", "message": str(e)}}

    def health_check(self) -> Dict[str, Any]:
        """检查 daemon 是否运行。

        返回:
            成功时返回 WebBridge 原生状态（含 running=True, extension_connected=True 等）
            失败时返回 {"running": False, "error": "..."}
        """
        try:
            req = urllib.request.Request(self.base_url + "/status", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except Exception as e:
            return {"running": False, "error": f"无法连接 {self.base_url}/status: {e}"}

    # ---------- 工具 schema ----------

    @staticmethod
    def get_tools(include_alt_names: bool = False) -> List[Dict[str, Any]]:
        """返回所有支持的工具 schema（OpenAI 格式）。

        Args:
            include_alt_names: 如果为 True，则额外注入 browser_* 别名工具，
                以兼容市场 agent.json 中的 capability 声明。
        """
        tools = []
        for entry in _WEBBRIDGE_TOOLS:
            tools.append({
                "type": "function",
                "function": {
                    "name": entry["tool_name"],
                    "description": entry["description"],
                    "parameters": entry["parameters"],
                },
            })
            if include_alt_names:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": entry["alt_name"],
                        "description": entry["description"],
                        "parameters": entry["parameters"],
                    },
                })
        return tools

    # ---------- 工具执行（注册表驱动） ----------

    def run_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """根据 LLM 工具调用名执行操作。

        支持 webbridge_* 和 browser_* 两套命名（自动归一化）。

        返回: 标准化的工具调用结果 {'content': '...'}
        """
        entry = _resolve_tool_entry(name)
        if entry is None:
            return {"content": f"[Error] 未知工具: {name} (支持: {', '.join(sorted(_TOOL_BY_NAME.keys()))})"}
        try:
            action_args = entry["arg_mapper"](arguments or {})
            raw = self._call(entry["action"], action_args)
            # snapshot 通常返回大文本，截断处理
            truncate = 3000 if entry["action"] == "snapshot" else 0
            return self._result(raw, truncate=truncate)
        except Exception as e:
            return {"content": f"[Error] 调用 {name} 失败: {e}"}

    def _result(self, raw: Dict[str, Any], truncate: int = 0) -> Dict[str, Any]:
        """将 WebBridge 原生响应转换为 LLM 友好的结果 {'content': ...}"""
        if not isinstance(raw, dict):
            return {"content": f"[ERROR] 无效响应: {raw!r}"}
        if not raw.get("ok", False):
            err = raw.get("error", {})
            if isinstance(err, dict):
                msg = err.get("message", str(err))
                code = err.get("code", "")
                return {"content": f"[ERROR {code}] {msg}"}
            return {"content": f"[ERROR] {err}"}
        data = raw.get("data", {})
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if truncate and isinstance(text, str) and len(text) > truncate:
            text = text[:truncate] + "\n...(已截断)...\n"
        return {"content": text}


if __name__ == "__main__":
    # 快速自检
    client = KimiWebBridgeClient()
    print("health:", json.dumps(client.health_check(), ensure_ascii=False))
    print("navigate:", json.dumps(client.run_tool("webbridge_navigate", {"url": "https://example.com"}), ensure_ascii=False))
    print("snapshot:", json.dumps(client.run_tool("webbridge_snapshot", {}), ensure_ascii=False)[:200])
    print("evaluate(title):", json.dumps(client.run_tool("webbridge_evaluate", {"code": "document.title"}), ensure_ascii=False))

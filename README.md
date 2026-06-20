# agent-compose

基于 YAML 配置的 Agent 编排器，支持 **Agent / Team / Workflow** 三层架构。
同时提供 `AgentRuntime`——直接从市场（market.aitboy.cn）拉取 Agent JSON v2 并运行的轻量执行引擎。

仓库: [github.com/openpeng/agent-compose](https://github.com/openpeng/agent-compose)

## 功能特性

- **YAML 声明式配置** — 定义 Agent、Team、Workflow，无需硬编码
- **可复用定义** — Skill、MCP、Agent 模板集中管理
- **引用机制** — `$ref`、`$file`、`${VAR}` 环境变量
- **完整打包** — Agent/Team/Workflow 均可打包为标准 JSON
- **Market 部署与下载** — 支持 `agent-compose market download <id>` 下载市场 Agent
- **AgentRuntime** — 标准 Agent JSON v2 直接运行，不依赖 YAML 定义
- **Kimi WebBridge 集成** — 通过 HTTP JSON (127.0.0.1:10086) 驱动浏览器，12 个浏览器操作工具
- **MCP 工具链** — stdio 子进程、SSE、Kimi WebBridge HTTP 三种模式
- **命令行工具** — `agent-compose` CLI

## 目录结构

```
agent-compose/
├── agent_compose/            # 核心代码
│   ├── agent_runtime.py      # ★ Agent JSON v2 执行引擎（含 WebBridge）
│   ├── kimi_webbridge_client.py  # ★ Kimi WebBridge HTTP 客户端
│   ├── market_client.py      # ★ market.aitboy.cn 客户端（下载&发布）
│   ├── agent_runner.py       # YAML Agent 执行
│   ├── agent_loader.py       # YAML → Agent
│   ├── team_loader.py
│   ├── workflow_loader.py
│   ├── definition_loader.py
│   ├── config_resolver.py
│   ├── mcp_builder.py
│   ├── mcp_stdio_client.py   # stdio MCP (子进程)
│   ├── mcp_sse_client.py     # SSE MCP
│   ├── llm_client.py
│   ├── orchestrator.py       # 统一入口
│   ├── remote_loader.py
│   ├── deployer.py
│   ├── deploy_cli.py
│   ├── cli.py                # ★ 命令行主入口
│   └── packagers/            # 打包器
├── examples/                 # 示例项目
├── tests/                    # 单元测试
├── requirements.txt
├── pyproject.toml
├── README.md
└── run_tests.py
```

## 安装

```bash
cd agent-compose
pip install -r requirements.txt
pip install -e .
```

## 快速开始

### 方式 A — 从市场下载并运行（最简单）

```bash
# 需要 LLM API Key（OpenRouter 免费）
$env:OPENROUTER_API_KEY='sk-or-v1-...'

# 下载 kimi-webbridge-operator 并直接对话
python -m agent_compose.cli market run kimi-webbridge-operator \
  -m "打开 https://example.com 并截图" \
  --model-provider openrouter --model-id openrouter/free \
  --base-url https://openrouter.ai/api/v1

# 或只下载到本地
python -m agent_compose.cli market download kimi-webbridge-operator -o ./downloads/
```

### 方式 B — 通过 YAML 定义运行

```bash
# 列出所有实体
agent-compose -d examples/workflow_example list

# 查看 Agent
agent-compose -d examples/workflow_example agent web_researcher

# 运行 Workflow
agent-compose -d examples/workflow_example run article_pipeline

# 打包
agent-compose -d examples/workflow_example package workflow article_pipeline

# 部署到 AgentOS
agent-compose -d examples/workflow_example deploy workflow article_pipeline
```

## Kimi WebBridge 浏览器自动化

Kimi WebBridge 通过浏览器扩展 + 本地 daemon，让 Agent 直接操作浏览器。

**先决条件**:

1. 安装 Kimi Desktop 或浏览器扩展（支持 Chrome/Edge）
2. 验证本地端口: `curl http://127.0.0.1:10086/status`
   应返回 `{ "running": true, "extension_connected": true, ... }`

**工具列表**（Agent JSON v2 中以 `browser_*` 命名，运行时同时注册 `webbridge_*` 别名，共 24 个 schema）:

| browser_* | 说明 |
|---|---|
| `browser_navigate` | 打开 URL |
| `browser_snapshot` | 获取页面结构快照 |
| `browser_click` | 点击元素（CSS selector） |
| `browser_fill` | 填写表单（selector + value） |
| `browser_type` | 在焦点元素输入文本 |
| `browser_keys` | 发送按键（enter/tab/escape/arrow 等） |
| `browser_evaluate` | 执行 JavaScript |
| `browser_screenshot` | 截图（可选 fullPage / selector） |
| `browser_pdf` | 保存 PDF |
| `browser_list_tabs` | 列出所有 tab |
| `browser_find_tab` | 查找并切换 tab |
| `browser_close_tab` | 关闭当前 tab |

## Agent JSON v2 规范（节选）

```json
{
  "schema_version": "2.0",
  "identity": { "name": "kimi-webbridge-operator", "version": "1.1.0", "display_name": "🌉 Kimi WebBridge", ... },
  "instructions": { "format": "markdown", "source": "inline", "content": "..." },
  "capabilities": [ { "type": "tool_call", "name": "browser_navigate", ... } ],
  "mcp_servers": [ { "name": "kimi-webbridge", "type": "kimi-webbridge", "base_url": "http://127.0.0.1:10086" } ],
  "metadata": { ... }
}
```

完整规范见: [agent-deploy/docs/specs/AGENT_JSON_SPEC_V2.md](../agent-deploy/docs/specs/AGENT_JSON_SPEC_V2.md)

## 运行测试

```bash
python run_tests.py
```

## 配置示例（YAML）

### Agent

```yaml
agents:
  researcher:
    role: "Web Researcher"
    description: "Research the web"
    instructions: ["Use web_search effectively"]
    tools: { builtin: [web_search] }
    model: { provider: openai, id: gpt-4o }
```

### Team

```yaml
teams:
  content_team:
    mode: coordinate
    agents: [researcher, writer]
    deploy: { version: "1.0.0", targets: [agentos, cursor] }
```

### Workflow

```yaml
workflows:
  article_pipeline:
    steps:
      - { name: research, type: agent, agent: researcher, output_key: notes }
      - { name: publish, type: function, function: handlers.publish:to_blog, output_key: published_url }
```

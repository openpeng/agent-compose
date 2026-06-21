"""
agent-compose - 统一 CLI 入口

功能分两大组:

  [1] 本地 YAML 编排 (基于 YamlOrchestrator)
      list, agent, team, workflow, run, package, deploy, load-all

  [2] 市场 Agent (基于 MarketClient + AgentRuntime)
      market health, market search, market download, market show, market run

用法:
    python -m agent_compose.cli --help

    # 本地
    python -m agent_compose.cli -d examples/kimi_webbridge list
    python -m agent_compose.cli -d examples/kimi_webbridge agent webbridge_agent
    python -m agent_compose.cli -d examples/kimi_webbridge package agent webbridge_agent

    # 市场
    python -m agent_compose.cli market health
    python -m agent_compose.cli market search kimi --limit 5
    python -m agent_compose.cli market show kimi-webbridge-operator
    python -m agent_compose.cli market download kimi-webbridge-operator -o ./agents

    # 运行市场 Agent（真实对话，需要 API Key）
    python -m agent_compose.cli market run kimi-webbridge-operator \
        --model-provider openrouter --model-id openrouter/free \
        --base-url https://openrouter.ai/api/v1 \
        --api-key sk-or-v1-xxxxxxxxxxxxxxxx \
        --message "介绍一下你自己" -y --allow-no-mcp

    python -m agent_compose.cli market run kimi-webbridge-operator -i \
        --model-provider openrouter --model-id openrouter/free \
        --base-url https://openrouter.ai/api/v1 \
        --api-key sk-or-v1-xxxxxxxxxxxxxxxx \
        --allow-no-mcp
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from agent_compose.orchestrator import YamlOrchestrator
from agent_compose.packagers.agent_packager import AgentPackager
from agent_compose.packagers.team_packager import TeamPackager
from agent_compose.packagers.workflow_packager import WorkflowPackager
from agent_compose.deployer import Deployer

# 市场相关模块（来自之前的 deploy_cli）
from agent_compose.market_client import MarketClient
from agent_compose.agent_runtime import AgentRuntime
from agent_compose.agent_runtime_server import AgentRuntimeServer
from agent_compose.session_store import create_session_store
from agent_compose.agentos_client import AgentOSClient


DEFAULT_MARKET_URL = "https://market.aitboy.cn"


# ============================================================
# 命令组 1: 本地 YAML 编排
# ============================================================

def _add_local_parsers(sub: argparse._SubParsersAction) -> None:
    list_p = sub.add_parser("list", help="List agents, teams, workflows")
    list_p.add_argument("kind", nargs="?", choices=["all", "agents", "teams", "workflows"], default="all")

    agent_p = sub.add_parser("agent", help="Show agent config")
    agent_p.add_argument("name", help="Agent name")
    agent_p.add_argument("--json", action="store_true", help="Output as JSON")

    team_p = sub.add_parser("team", help="Show team config")
    team_p.add_argument("name", help="Team name")
    team_p.add_argument("--json", action="store_true")

    wf_p = sub.add_parser("workflow", help="Show workflow config")
    wf_p.add_argument("name", help="Workflow name")
    wf_p.add_argument("--json", action="store_true")

    run_p = sub.add_parser("run", help="Run a local workflow")
    run_p.add_argument("name", help="Workflow name")

    package_p = sub.add_parser("package", help="Package entities")
    package_p.add_argument("kind", choices=["agent", "team", "workflow"])
    package_p.add_argument("name", help="Entity name")

    deploy_p = sub.add_parser("deploy", help="Deploy entity to AgentOS")
    deploy_p.add_argument("kind", choices=["agent", "team", "workflow"])
    deploy_p.add_argument("name", help="Entity name")

    sub.add_parser("load-all", help="Load and show all local entities")

    # serve — 启动运行时服务
    serve_p = sub.add_parser("serve", help="Start AgentRuntime HTTP server")
    serve_p.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    serve_p.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    serve_p.add_argument("--session-backend", default="memory", choices=["memory", "file", "redis"], help="Session storage backend")
    serve_p.add_argument("--session-dir", default="./.sessions", help="Session file directory (for file backend)")
    serve_p.add_argument("--session-ttl", type=int, default=3600, help="Session TTL in seconds (default: 3600)")
    serve_p.add_argument("--model-provider", default="kimi", help="Default model provider")
    serve_p.add_argument("--model-id", default="moonshot-v1-128k", help="Default model ID")
    serve_p.add_argument("--base-url", default=None, help="Default API base URL")
    serve_p.add_argument("--agentos-url", default=None, help="AgentOS URL for registration")


def _do_list(orch: YamlOrchestrator, kind: str) -> int:
    if kind in {"all", "agents"}:
        agents = orch.list_agents()
        print(f"Agents ({len(agents)}):")
        for a in agents:
            print(f"  - {a}")
    if kind in {"all", "teams"}:
        teams = orch.list_teams()
        print(f"Teams ({len(teams)}):")
        for t in teams:
            print(f"  - {t}")
    if kind in {"all", "workflows"}:
        wfs = orch.list_workflows()
        print(f"Workflows ({len(wfs)}):")
        for w in wfs:
            print(f"  - {w}")
    return 0


def _do_agent(orch: YamlOrchestrator, name: str, as_json: bool) -> int:
    agent = orch.get_agent(name)
    if agent is None:
        print(f"Agent '{name}' not found", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(agent, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Agent: {name}")
        print(f"  Role: {agent.get('role', '')}")
        print(f"  Description: {agent.get('description', '')}")
        print(f"  Tools: {len(agent.get('tools', []))} tools")
    return 0


def _do_team(orch: YamlOrchestrator, name: str, as_json: bool) -> int:
    team = orch.get_team(name)
    if team is None:
        print(f"Team '{name}' not found", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(team, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Team: {name}")
        print(f"  Mode: {team.get('mode', '')}")
        print(f"  Members: {len(team.get('members', []))}")
    return 0


def _do_workflow(orch: YamlOrchestrator, name: str, as_json: bool) -> int:
    wf = orch.get_workflow(name)
    if wf is None:
        print(f"Workflow '{name}' not found", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(wf, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Workflow: {name}")
        print(f"  Steps: {len(wf.get('steps', []))}")
    return 0


def _do_run_workflow(orch: YamlOrchestrator, name: str) -> int:
    result = orch.run_workflow(name)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("status") == "ok" else 1


def _do_package(orch: YamlOrchestrator, kind: str, name: str, output_dir: str) -> int:
    output_path = os.path.abspath(output_dir)
    if kind == "agent":
        agent = orch.get_agent(name)
        if agent is None:
            print(f"Agent '{name}' not found", file=sys.stderr)
            return 1
        packager = AgentPackager(output_dir=output_path)
        path = packager.package(name, agent)
        print(f"Packaged agent '{name}' to: {path}")
        return 0
    if kind == "team":
        team = orch.get_team(name)
        if team is None:
            print(f"Team '{name}' not found", file=sys.stderr)
            return 1
        packager = TeamPackager(output_dir=output_path)
        path = packager.package(name, team)
        print(f"Packaged team '{name}' to: {path}")
        return 0
    if kind == "workflow":
        wf = orch.get_workflow(name)
        if wf is None:
            print(f"Workflow '{name}' not found", file=sys.stderr)
            return 1
        packager = WorkflowPackager(output_dir=output_path)
        path = packager.package(name, wf)
        print(f"Packaged workflow '{name}' to: {path}")
        return 0
    return 1


def _do_deploy(orch: YamlOrchestrator, kind: str, name: str) -> int:
    deployer = Deployer()
    if kind == "agent":
        agent = orch.get_agent(name)
        if agent is None:
            print(f"Agent '{name}' not found", file=sys.stderr)
            return 1
        result = deployer.register_agent(name, agent)
    elif kind == "team":
        team = orch.get_team(name)
        if team is None:
            print(f"Team '{name}' not found", file=sys.stderr)
            return 1
        result = deployer.register_team(name, team)
    elif kind == "workflow":
        wf = orch.get_workflow(name)
        if wf is None:
            print(f"Workflow '{name}' not found", file=sys.stderr)
            return 1
        result = deployer.register_workflow(name, wf)
    else:
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("status") == "ok" else 1


def _do_load_all(orch: YamlOrchestrator) -> int:
    data = orch.load_all()
    total = (
        len(data.get("agents", {}))
        + len(data.get("teams", {}))
        + len(data.get("workflows", {}))
    )
    print(f"Loaded {total} entities")
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return 0


def _do_serve(args: argparse.Namespace) -> int:
    """启动 AgentRuntime HTTP 服务器"""
    # 创建会话存储
    store_kwargs = {}
    if args.session_backend == "file":
        store_kwargs["base_dir"] = args.session_dir
    elif args.session_backend == "redis":
        store_kwargs["redis_url"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    session_store = create_session_store(args.session_backend, **store_kwargs)

    # 创建服务器
    server = AgentRuntimeServer(
        host=args.host,
        port=args.port,
        session_store=session_store,
        session_ttl=args.session_ttl,
        default_model_provider=args.model_provider,
        default_model_id=args.model_id,
        default_base_url=args.base_url,
        agentos_url=args.agentos_url,
    )

    # AgentOS 注册
    if args.agentos_url:
        print(f"Registering with AgentOS at {args.agentos_url} ...")
        result = server.register_with_agentos(args.agentos_url)
        print(f"  {result}")

    # 启动服务
    server.serve()
    return 0


# ============================================================
# 命令组 2: 市场 Agent (Market)
# ============================================================

def _add_market_parsers(sub: argparse._SubParsersAction) -> None:
    """market 命令组: health / search / download / show / run"""
    market_parent = sub.add_parser(
        "market",
        help="Market operations (search, download, run agents from market.aitboy.cn)",
    )
    market_parent.add_argument(
        "--market-url",
        default=DEFAULT_MARKET_URL,
        help=f"Market API URL (default: {DEFAULT_MARKET_URL})",
    )
    market_parent.add_argument("--no-cache", action="store_true", help="Skip local cache")
    market_parent.add_argument("--refresh", action="store_true", help="Force refresh cache")

    market_sub = market_parent.add_subparsers(dest="market_command")

    # market health
    market_sub.add_parser("health", help="Check market service health")

    # market search
    search_p = market_sub.add_parser("search", help="Search agents/teams/workflows")
    search_p.add_argument("query", nargs="?", default="", help="Search keyword")
    search_p.add_argument(
        "--type", default="agents", choices=["agents", "teams", "workflows"], help="Entity type"
    )
    search_p.add_argument("--limit", type=int, default=20, help="Result count")

    # market download
    dl_p = market_sub.add_parser("download", help="Download agent JSON")
    dl_p.add_argument("agent_id", help="Agent ID (e.g. kimi-webbridge-operator)")
    dl_p.add_argument("-o", "--output", help="Output directory (prints to stdout if omitted)")

    # market show
    show_p = market_sub.add_parser("show", help="Show agent details from market")
    show_p.add_argument("agent_id", help="Agent ID")

    # market run
    run_p = market_sub.add_parser("run", help="Run an agent from the market with real LLM conversation")
    run_p.add_argument("agent_id", help="Agent ID")
    run_p.add_argument("-m", "--message", help="User message (skip for interactive mode)")
    run_p.add_argument("-i", "--interactive", action="store_true", help="Interactive chat mode")
    run_p.add_argument("--api-key", default=None, help="API Key (or set via env)")
    run_p.add_argument("--webbridge-token", default=None, help="Kimi WebBridge token (optional)")
    run_p.add_argument("--model-provider", default="openrouter", help="Model provider name")
    run_p.add_argument("--model-id", default="openrouter/free", help="Model ID")
    run_p.add_argument("--base-url", default="https://openrouter.ai/api/v1", help="API base URL")
    run_p.add_argument("--max-turns", type=int, default=15, help="Max tool-call turns per message")
    run_p.add_argument("--allow-no-mcp", action="store_true", help="Continue even without MCP servers connected")
    run_p.add_argument("-y", "--yes", action="store_true", help="Skip all confirmation prompts")


def _get_market_client(args: argparse.Namespace) -> MarketClient:
    return MarketClient(base_url=args.market_url)


def _print_market_row(item: dict, idx: int) -> None:
    name = item.get("id") or item.get("name") or "?"
    display = item.get("display_name") or name
    desc = (item.get("description") or "")[:60].replace("\n", " ")
    rating = item.get("rating", 0.0)
    downloads = item.get("download_count", 0)
    print(f"  [{idx:>2}] {name:<35s} ⭐{rating:>4.1f}  ⬇️{downloads:>5}  {desc}")


def _do_market_health(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    try:
        h = market.health()
        print(f"🌐 Market Service: {args.market_url}")
        print(f"  Status: {h.get('status', 'unknown')}")
        print(f"  Version: {h.get('version', 'n/a')}")
        print(f"  Agents: {h.get('agents_count', 0)}")
        print(f"  Teams: {h.get('teams_count', 0)}")
        print(f"  Workflows: {h.get('workflows_count', 0)}")
        return 0
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1


def _do_market_search(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    q = args.query or ""
    items = market.search_and_list(q=q, entity_type=args.type, page_size=args.limit)

    print(f"🔍 Searching '{q or '*'}' in {args.type} (limit={args.limit})\n")
    if not items:
        print("  (no results)")
        return 0

    for i, item in enumerate(items, 1):
        _print_market_row(item, i)
    print(f"\n  {len(items)} total")
    return 0


def _do_market_download(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    agent_id = args.agent_id

    print(f"⬇️  Downloading '{agent_id}' ...")
    try:
        agent_json, from_cache = market.fetch_agent_json(
            agent_id, use_cache=not args.no_cache, force_refresh=args.refresh
        )
    except Exception as e:
        print(f"❌ Download failed: {e}", file=sys.stderr)
        return 1

    print(f"  ✓ Got agent.json from {'cache' if from_cache else 'market'}")
    print(f"  ✓ schema_version: {agent_json.get('schema_version', '?')}")

    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{agent_id}.json"
        out_path.write_text(json.dumps(agent_json, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  ✓ Saved to {out_path}")
    else:
        print()
        print(json.dumps(agent_json, indent=2, ensure_ascii=False))
    return 0


def _do_market_show(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    agent_id = args.agent_id

    try:
        agent_json, _ = market.fetch_agent_json(
            agent_id, use_cache=not args.no_cache, force_refresh=args.refresh
        )
    except Exception as e:
        print(f"❌ Failed: {e}", file=sys.stderr)
        return 1

    identity = agent_json.get("identity", {}) or {}
    capabilities = agent_json.get("capabilities", []) or []
    mcps = agent_json.get("mcp_servers", []) or []
    instructions = agent_json.get("instructions", {}) or {}

    print(f"📋 Agent: {agent_id}")
    print(f"  Name: {identity.get('display_name', identity.get('name', agent_id))}")
    print(f"  Schema: v{agent_json.get('schema_version', '?')}")
    print(f"  Author: {identity.get('author', 'n/a')}")
    print(f"  Category: {identity.get('category', 'n/a')}")
    tags = identity.get("tags") or []
    if tags:
        print(f"  Tags: {', '.join(tags[:10])}")

    print(f"\n  🛠️  Capabilities ({len(capabilities)}):")
    for c in capabilities:
        ctype = c.get("type", "?")
        cname = c.get("name", "?")
        cdesc = (c.get("description") or "")[:60]
        print(f"    - [{ctype:<10s}] {cname:<25s} {cdesc}")

    if mcps:
        print(f"\n  🌐 MCP Servers ({len(mcps)}):")
        for s in mcps:
            sname = s.get("name", "?")
            sdesc = (s.get("description") or "")[:60]
            cmd = s.get("command", "")
            sargs = s.get("args", []) or []
            print(f"    - {sname:<20s} {sdesc}")
            if cmd:
                print(f"        startup: {cmd} {' '.join(sargs)}")

    prompt = instructions.get("content", "")
    if prompt:
        print(f"\n  📜 System Prompt (first 400 chars):")
        snippet = prompt[:400].replace("\n", "\n    ")
        print(f"    {snippet}...")
    return 0


def _do_market_run(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    agent_id = args.agent_id

    api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("KIMI_API_KEY") or ""
    webbridge_token = args.webbridge_token or os.environ.get("WEBBRIDGE_TOKEN", "")

    if not api_key:
        print(
            "❌ No API key provided. Use --api-key or set OPENROUTER_API_KEY / KIMI_API_KEY env variable.",
            file=sys.stderr,
        )
        return 1

    print(f"🤖 Preparing to run '{agent_id}' ...\n")

    try:
        agent_json, from_cache = market.fetch_agent_json(
            agent_id, use_cache=not args.no_cache, force_refresh=args.refresh
        )
    except Exception as e:
        print(f"❌ Download failed: {e}", file=sys.stderr)
        return 1

    print(f"  ✓ Agent: {agent_json.get('identity', {}).get('display_name', agent_id)}")
    print(f"  ✓ Schema: v{agent_json.get('schema_version', '?')}")
    print(f"  ✓ Source: {'cache' if from_cache else 'market'}")
    print(f"  ✓ Model: {args.model_provider}/{args.model_id}")
    print(f"  ✓ Base URL: {args.base_url}")
    print(f"  ✓ API Key: {'set' if api_key else '❌ missing'}")
    print(f"  ✓ WebBridge Token: {'set' if webbridge_token else 'not set (optional)'}")

    runtime = AgentRuntime(
        agent_id=agent_id,
        agent_json=agent_json,
        api_key=api_key,
        webbridge_token=webbridge_token,
        model_provider=args.model_provider,
        model_id=args.model_id,
        base_url=args.base_url,
    )

    # 初始化 MCP servers
    print(f"\n  Initializing MCP servers ...")
    connected = runtime.initialize_mcps()
    print(f"  Connected {len(connected)} MCP servers: {connected}")

    mcp_count = len(runtime.mcp_servers)
    if mcp_count > 0 and not connected and not args.allow_no_mcp:
        if not args.yes:
            try:
                resp = input("   ⚠️  This agent requires MCP servers but none connected. Continue? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 1
            if resp not in ("y", "yes"):
                print("Cancelled.")
                return 0

    # 确定运行模式
    interactive_mode = args.interactive or (not args.message)

    if interactive_mode:
        if args.message:
            print(f"\n  (starting interactive mode with first message: '{args.message}')")
            runtime.chat_interactive(first_message=args.message)
        else:
            print("\n  (interactive mode — type your message, 'quit' to exit)")
            runtime.chat_interactive()
    else:
        history = runtime.chat(user_message=args.message, max_turns=args.max_turns)
        print(f"\n  ✓ Chat complete, {len(history)} turns")

    return 0


# ============================================================
# 主入口
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-compose",
        description="Unified CLI: local YAML orchestration + market agent deployment & runtime",
    )
    parser.add_argument("--project-dir", "-d", default=".", help="Project directory (for local commands)")
    parser.add_argument("--output-dir", "-o", default="dist", help="Output directory (for package command)")
    parser.add_argument(
        "--market-url",
        default=DEFAULT_MARKET_URL,
        help=f"Market API URL (default: {DEFAULT_MARKET_URL})",
    )

    sub = parser.add_subparsers(dest="command")

    # --- 本地命令 ---
    _add_local_parsers(sub)

    # --- 市场命令 ---
    _add_market_parsers(sub)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cmd = args.command

    # --- 市场命令组 ---
    if cmd == "market":
        mcmd = args.market_command
        if mcmd is None or mcmd == "health":
            return _do_market_health(args)
        if mcmd == "search":
            return _do_market_search(args)
        if mcmd == "download":
            return _do_market_download(args)
        if mcmd == "show":
            return _do_market_show(args)
        if mcmd == "run":
            return _do_market_run(args)
        # 子命令未知时显示帮助
        parser.parse_args(["market", "--help"])
        return 1

    # --- 本地命令组 ---
    project_dir = os.path.abspath(args.project_dir)
    orch = YamlOrchestrator(project_dir=project_dir)

    if cmd is None or cmd == "list":
        kind = getattr(args, "kind", "all")
        return _do_list(orch, kind)

    if cmd == "agent":
        return _do_agent(orch, args.name, args.json)

    if cmd == "team":
        return _do_team(orch, args.name, args.json)

    if cmd == "workflow":
        return _do_workflow(orch, args.name, args.json)

    if cmd == "run":
        return _do_run_workflow(orch, args.name)

    if cmd == "package":
        return _do_package(orch, args.kind, args.name, args.output_dir)

    if cmd == "deploy":
        return _do_deploy(orch, args.kind, args.name)

    if cmd == "load-all":
        return _do_load_all(orch)

    if cmd == "serve":
        return _do_serve(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

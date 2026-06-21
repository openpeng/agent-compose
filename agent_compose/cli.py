"""
agent-compose - Unified CLI entry point

Two command groups:

  [1] Local YAML orchestration (based on YamlOrchestrator)
      list, agent, team, workflow, run, package, deploy, load-all

  [2] Market Agent (based on MarketClient + AgentRuntime)
      market health, market search, market download, market show, market run

Usage:
    python -m agent_compose.cli --help

    # Local
    python -m agent_compose.cli -d examples/kimi_webbridge list
    python -m agent_compose.cli -d examples/kimi_webbridge agent webbridge_agent
    python -m agent_compose.cli -d examples/kimi_webbridge package agent webbridge_agent

    # Market
    python -m agent_compose.cli market health
    python -m agent_compose.cli market search kimi --limit 5
    python -m agent_compose.cli market show kimi-webbridge-operator
    python -m agent_compose.cli market download kimi-webbridge-operator -o ./agents

    # Run market Agent (real conversation, needs API Key)
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
import itertools
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from agent_compose.orchestrator import YamlOrchestrator
from agent_compose.packagers.agent_packager import AgentPackager
from agent_compose.packagers.team_packager import TeamPackager
from agent_compose.packagers.workflow_packager import WorkflowPackager
from agent_compose.deployer import Deployer

# Market related modules (from previous deploy_cli)
from agent_compose.market_client import MarketClient
from agent_compose.agent_runtime import AgentRuntime
from agent_compose.agent_runtime_server import AgentRuntimeServer
from agent_compose.session_store import create_session_store
from agent_compose.agentos_client import AgentOSClient

# i18n support
from agent_compose.i18n import get_message as _

DEFAULT_MARKET_URL = "https://market.aitboy.cn"

# ============================================================
# Color / Style helpers (with Windows compatibility)
# ============================================================

# Detect Windows and enable ANSI if possible (Windows 10+ supports VT sequences)
_WINDOWS = platform.system() == "Windows"
if _WINDOWS:
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


class _Style:
    """ANSI escape codes for terminal styling."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"


def _colorize(text: str, color: str) -> str:
    """Wrap text with ANSI color codes."""
    return f"{color}{text}{_Style.RESET}"


def _success(text: str) -> str:
    return _colorize(text, _Style.GREEN)


def _error(text: str) -> str:
    return _colorize(text, _Style.RED)


def _warning(text: str) -> str:
    return _colorize(text, _Style.YELLOW)


def _info(text: str) -> str:
    return _colorize(text, _Style.CYAN)


def _bold(text: str) -> str:
    return _colorize(text, _Style.BOLD)


# ============================================================
# Progress Spinner
# ============================================================

class Spinner:
    """Terminal spinner for long-running operations."""

    _frames = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])

    def __init__(self, message: str = "", delay: float = 0.08):
        self.message = message or _("spinner_default")
        self.delay = delay
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _spin(self) -> None:
        while not self._stop_event.is_set():
            frame = next(self._frames)
            sys.stdout.write(f"\r  {frame} {self.message}")
            sys.stdout.flush()
            time.sleep(self.delay)
        # Clear line
        sys.stdout.write("\r" + " " * (len(self.message) + 10) + "\r")
        sys.stdout.flush()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final_message: Optional[str] = None) -> None:
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.delay * 2)
        self._running = False
        if final_message:
            print(f"  {_success('✓')} {final_message}")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


# ============================================================
# Confirmation Prompts
# ============================================================

def confirm(message: str, default: bool = False) -> bool:
    """
    Prompt user for y/n confirmation.

    Args:
        message: Prompt message (without brackets).
        default: Default value if user presses Enter.

    Returns:
        True if user confirmed, False otherwise.
    """
    if default:
        prompt = _("prompt_confirm_default_yes", message=message)
    else:
        prompt = _("prompt_confirm_default_no", message=message)

    try:
        resp = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if not resp:
        return default
    return resp in ("y", "yes")


# ============================================================
# Command Group 1: Local YAML Orchestration
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

    # init — interactive wizard
    init_p = sub.add_parser("init", help="Interactive setup wizard")
    init_p.add_argument("--template", "-t", help="Use a built-in template instead of wizard")

    # template — template management
    tpl_p = sub.add_parser("template", help="Template management")
    tpl_sub = tpl_p.add_subparsers(dest="template_command")
    tpl_sub.add_parser("list", help="List available templates")
    tpl_use_p = tpl_sub.add_parser("use", help="Generate from a named template")
    tpl_use_p.add_argument("name", help="Template name")
    tpl_use_p.add_argument("-o", "--output", default=".", help="Output directory")

    # serve — start runtime server
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
        print(_bold(_("cli_agents_count", count=len(agents))))
        for a in agents:
            print(f"  - {a}")
    if kind in {"all", "teams"}:
        teams = orch.list_teams()
        print(_bold(_("cli_teams_count", count=len(teams))))
        for t in teams:
            print(f"  - {t}")
    if kind in {"all", "workflows"}:
        wfs = orch.list_workflows()
        print(_bold(_("cli_workflows_count", count=len(wfs))))
        for w in wfs:
            print(f"  - {w}")
    return 0


def _do_agent(orch: YamlOrchestrator, name: str, as_json: bool) -> int:
    agent = orch.get_agent(name)
    if agent is None:
        print(_error(_("cli_agent_not_found", name=name)), file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(agent, indent=2, ensure_ascii=False, default=str))
    else:
        print(_bold(f"Agent: {name}"))
        print(f"  {_info('Role:')} {agent.get('role', '')}")
        print(f"  {_info('Description:')} {agent.get('description', '')}")
        print(f"  {_info('Tools:')} {len(agent.get('tools', []))} tools")
    return 0


def _do_team(orch: YamlOrchestrator, name: str, as_json: bool) -> int:
    team = orch.get_team(name)
    if team is None:
        print(_error(_("cli_team_not_found", name=name)), file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(team, indent=2, ensure_ascii=False, default=str))
    else:
        print(_bold(f"Team: {name}"))
        print(f"  {_info('Mode:')} {team.get('mode', '')}")
        print(f"  {_info('Members:')} {len(team.get('members', []))}")
    return 0


def _do_workflow(orch: YamlOrchestrator, name: str, as_json: bool) -> int:
    wf = orch.get_workflow(name)
    if wf is None:
        print(_error(_("cli_workflow_not_found", name=name)), file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(wf, indent=2, ensure_ascii=False, default=str))
    else:
        print(_bold(f"Workflow: {name}"))
        print(f"  {_info('Steps:')} {len(wf.get('steps', []))}")
    return 0


def _do_run_workflow(orch: YamlOrchestrator, name: str) -> int:
    with Spinner(_("spinner_execute")):
        result = orch.run_workflow(name)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if result.get("status") == "ok":
        print(_success(_("done")))
        return 0
    print(_error(_("error")))
    return 1


def _do_package(orch: YamlOrchestrator, kind: str, name: str, output_dir: str) -> int:
    output_path = os.path.abspath(output_dir)
    if kind == "agent":
        agent = orch.get_agent(name)
        if agent is None:
            print(_error(_("cli_agent_not_found", name=name)), file=sys.stderr)
            return 1
        packager = AgentPackager(output_dir=output_path)
        with Spinner(_("spinner_default")):
            path = packager.package(name, agent)
        print(_success(_("cli_packaged_to", name=name, path=path)))
        return 0
    if kind == "team":
        team = orch.get_team(name)
        if team is None:
            print(_error(_("cli_team_not_found", name=name)), file=sys.stderr)
            return 1
        packager = TeamPackager(output_dir=output_path)
        with Spinner(_("spinner_default")):
            path = packager.package(name, team)
        print(_success(_("cli_packaged_to", name=name, path=path)))
        return 0
    if kind == "workflow":
        wf = orch.get_workflow(name)
        if wf is None:
            print(_error(_("cli_workflow_not_found", name=name)), file=sys.stderr)
            return 1
        packager = WorkflowPackager(output_dir=output_path)
        with Spinner(_("spinner_default")):
            path = packager.package(name, wf)
        print(_success(_("cli_packaged_to", name=name, path=path)))
        return 0
    return 1


def _do_deploy(orch: YamlOrchestrator, kind: str, name: str) -> int:
    deployer = Deployer()
    if kind == "agent":
        agent = orch.get_agent(name)
        if agent is None:
            print(_error(_("cli_agent_not_found", name=name)), file=sys.stderr)
            return 1
        with Spinner(_("spinner_deploy")):
            result = deployer.register_agent(name, agent)
    elif kind == "team":
        team = orch.get_team(name)
        if team is None:
            print(_error(_("cli_team_not_found", name=name)), file=sys.stderr)
            return 1
        with Spinner(_("spinner_deploy")):
            result = deployer.register_team(name, team)
    elif kind == "workflow":
        wf = orch.get_workflow(name)
        if wf is None:
            print(_error(_("cli_workflow_not_found", name=name)), file=sys.stderr)
            return 1
        with Spinner(_("spinner_deploy")):
            result = deployer.register_workflow(name, wf)
    else:
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if result.get("status") == "ok":
        print(_success(_("cli_deployed", name=name)))
        return 0
    return 1


def _do_load_all(orch: YamlOrchestrator) -> int:
    with Spinner(_("loading")):
        data = orch.load_all()
    total = (
        len(data.get("agents", {}))
        + len(data.get("teams", {}))
        + len(data.get("workflows", {}))
    )
    print(_success(_("cli_loaded_entities", count=total)))
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return 0


def _do_serve(args: argparse.Namespace) -> int:
    """Start AgentRuntime HTTP server"""
    # Create session store
    store_kwargs = {}
    if args.session_backend == "file":
        store_kwargs["base_dir"] = args.session_dir
    elif args.session_backend == "redis":
        store_kwargs["redis_url"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    session_store = create_session_store(args.session_backend, **store_kwargs)

    # Create server
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

    # AgentOS registration
    if args.agentos_url:
        print(_info(_("cli_server_registering")))
        result = server.register_with_agentos(args.agentos_url)
        print(f"  {_info(result)}")

    # Start service
    print(_info(_("cli_server_starting")))
    server.serve()
    return 0


# ============================================================
# Command Group 2: Market Agent (Market)
# ============================================================

def _add_market_parsers(sub: argparse._SubParsersAction) -> None:
    """market command group: health / search / download / show / run"""
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
    print(f"  [{idx:>2}] {name:<35s} {_warning('⭐')}{rating:>4.1f}  {_info('⬇')}{downloads:>5}  {desc}")


def _do_market_health(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    try:
        with Spinner(_("loading")):
            h = market.health()
        print(_bold(_("market_service") + f": {args.market_url}"))
        print(f"  {_info(_('market_status') + ':')} {h.get('status', 'unknown')}")
        print(f"  {_info(_('market_version') + ':')} {h.get('version', 'n/a')}")
        print(f"  {_info(_('market_agents_count') + ':')} {h.get('agents_count', 0)}")
        print(f"  {_info(_('market_teams_count') + ':')} {h.get('teams_count', 0)}")
        print(f"  {_info(_('market_workflows_count') + ':')} {h.get('workflows_count', 0)}")
        return 0
    except Exception as e:
        print(_error(_("err_market_connect", error=str(e))), file=sys.stderr)
        return 1


def _do_market_search(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    q = args.query or ""
    with Spinner(_("spinner_default")):
        items = market.search_and_list(q=q, entity_type=args.type, page_size=args.limit)

    print(_bold(_("market_searching", query=q or "*", type=args.type, limit=args.limit)))
    print()
    if not items:
        print(f"  {_warning(_('market_no_results'))}")
        return 0

    for i, item in enumerate(items, 1):
        _print_market_row(item, i)
    print(f"\n  {_info(_('market_total_results', count=len(items)))}")
    return 0


def _do_market_download(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    agent_id = args.agent_id

    print(_info(_("market_downloading", agent_id=agent_id)))
    try:
        with Spinner(_("spinner_download")):
            agent_json, from_cache = market.fetch_agent_json(
                agent_id, use_cache=not args.no_cache, force_refresh=args.refresh
            )
    except Exception as e:
        print(_error(_("market_download_failed", error=str(e))), file=sys.stderr)
        return 1

    source = _("market_got_from_cache") if from_cache else _("market_got_from_market")
    print(f"  {_success('✓')} {source}")
    print(f"  {_success('✓')} {_('market_schema_version')}: {agent_json.get('schema_version', '?')}")

    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{agent_id}.json"
        out_path.write_text(json.dumps(agent_json, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {_success('✓')} {_('market_saved_to', path=out_path)}")
    else:
        print()
        print(json.dumps(agent_json, indent=2, ensure_ascii=False))
    return 0


def _do_market_show(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    agent_id = args.agent_id

    try:
        with Spinner(_("loading")):
            agent_json, _ = market.fetch_agent_json(
                agent_id, use_cache=not args.no_cache, force_refresh=args.refresh
            )
    except Exception as e:
        print(_error(_("market_download_failed", error=str(e))), file=sys.stderr)
        return 1

    identity = agent_json.get("identity", {}) or {}
    capabilities = agent_json.get("capabilities", []) or []
    mcps = agent_json.get("mcp_servers", []) or []
    instructions = agent_json.get("instructions", {}) or {}

    print(_bold(f"{_('market_agent_details')}: {agent_id}"))
    print(f"  {_info(_('run_agent') + ':')} {identity.get('display_name', identity.get('name', agent_id))}")
    print(f"  {_info(_('market_schema_version') + ':')} v{agent_json.get('schema_version', '?')}")
    print(f"  {_info(_('market_author') + ':')} {identity.get('author', 'n/a')}")
    print(f"  {_info(_('market_category') + ':')} {identity.get('category', 'n/a')}")
    tags = identity.get("tags") or []
    if tags:
        print(f"  {_info(_('market_tags') + ':')} {', '.join(tags[:10])}")

    print(f"\n  {_bold(_('market_capabilities', count=len(capabilities)))}:")
    for c in capabilities:
        ctype = c.get("type", "?")
        cname = c.get("name", "?")
        cdesc = (c.get("description") or "")[:60]
        print(f"    - [{ctype:<10s}] {cname:<25s} {cdesc}")

    if mcps:
        print(f"\n  {_bold(_('market_mcp_servers', count=len(mcps)))}:")
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
        print(f"\n  {_bold(_('market_system_prompt'))}:")
        snippet = prompt[:400].replace("\n", "\n    ")
        print(f"    {snippet}...")
    return 0


def _do_market_run(args: argparse.Namespace) -> int:
    market = _get_market_client(args)
    agent_id = args.agent_id

    api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("KIMI_API_KEY") or ""
    webbridge_token = args.webbridge_token or os.environ.get("WEBBRIDGE_TOKEN", "")

    if not api_key:
        print(_error(_("err_no_api_key")), file=sys.stderr)
        return 1

    print(_bold(_("run_preparing", agent_id=agent_id)))
    print()

    try:
        with Spinner(_("spinner_download")):
            agent_json, from_cache = market.fetch_agent_json(
                agent_id, use_cache=not args.no_cache, force_refresh=args.refresh
            )
    except Exception as e:
        print(_error(_("market_download_failed", error=str(e))), file=sys.stderr)
        return 1

    print(f"  {_success('✓')} {_('run_agent')}: {agent_json.get('identity', {}).get('display_name', agent_id)}")
    print(f"  {_success('✓')} {_('run_schema')}: v{agent_json.get('schema_version', '?')}")
    print(f"  {_success('✓')} {_('run_source')}: {'cache' if from_cache else 'market'}")
    print(f"  {_success('✓')} {_('run_model')}: {args.model_provider}/{args.model_id}")
    print(f"  {_success('✓')} {_('run_base_url')}: {args.base_url}")
    print(f"  {_success('✓')} {_('run_api_key')}: {'set' if api_key else _error('missing')}")
    print(f"  {_success('✓')} {_('run_webbridge_token')}: {'set' if webbridge_token else 'not set (optional)'}")

    runtime = AgentRuntime(
        agent_id=agent_id,
        agent_json=agent_json,
        api_key=api_key,
        webbridge_token=webbridge_token,
        model_provider=args.model_provider,
        model_id=args.model_id,
        base_url=args.base_url,
    )

    # Initialize MCP servers
    print()
    print(f"  {_info(_('run_init_mcp'))}")
    with Spinner(_("spinner_default")):
        connected = runtime.initialize_mcps()
    print(f"  {_success('✓')} {_('run_connected_mcps', count=len(connected), names=connected)}")

    mcp_count = len(runtime.mcp_servers)
    if mcp_count > 0 and not connected and not args.allow_no_mcp:
        if not args.yes:
            if not confirm(_("run_mcp_required_prompt"), default=False):
                print(_warning(_("cancelled")))
                return 0

    # Determine run mode
    interactive_mode = args.interactive or (not args.message)

    if interactive_mode:
        if args.message:
            print(f"\n  {_info(_('run_interactive_mode') + f' (first message: {args.message!r})')}")
            runtime.chat_interactive(first_message=args.message)
        else:
            print(f"\n  {_info(_('run_interactive_mode'))}")
            runtime.chat_interactive()
    else:
        with Spinner(_("spinner_execute")):
            history = runtime.chat(user_message=args.message, max_turns=args.max_turns)
        print(f"\n  {_success('✓')} {_('run_chat_complete', count=len(history))}")

    return 0


# ============================================================
# Main Entry Point
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

    # --- Local commands ---
    _add_local_parsers(sub)

    # --- Market commands ---
    _add_market_parsers(sub)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cmd = args.command

    # --- Market command group ---
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
        # Show help for unknown subcommand
        parser.parse_args(["market", "--help"])
        return 1

    # --- Local command group ---
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

    if cmd == "init":
        if args.template:
            return run_template_wizard(args.template)
        return run_setup_wizard()

    if cmd == "template":
        tcmd = args.template_command
        if tcmd == "list" or tcmd is None:
            print("Available templates:\n")
            for info in list_builtin_templates():
                print(f"  {info['id']:<20s} {info['name']:<25s} {info['description']}")
                print(f"                       tags: {info['tags']}  version: {info['version']}")
            return 0
        if tcmd == "use":
            template = get_builtin_template(args.name)
            if template is None:
                print(f"Template '{args.name}' not found", file=sys.stderr)
                return 1
            output_dir = os.path.abspath(args.output)
            agent_name = template["identity"]["name"]
            out_path = os.path.join(output_dir, agent_name, "agent.json")
            written = write_template_to_file(template, out_path)
            print(f"Generated from template '{args.name}': {written}")
            return 0
        parser.parse_args(["template", "--help"])
        return 1

    if cmd == "serve":
        return _do_serve(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

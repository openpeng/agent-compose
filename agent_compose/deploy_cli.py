"""
agent-compose deploy_cli — 向后兼容入口。

注意：新代码请使用 `agent_compose.cli`。这里只是对其做一个薄包装，
以便已有的调用方式 (python -m agent_compose.deploy_cli ...) 继续可用。

映射关系:
    deploy_cli health      -> cli market health
    deploy_cli search ...  -> cli market search ...
    deploy_cli download ... -> cli market download ...
    deploy_cli show ...    -> cli market show ...
    deploy_cli run ...     -> cli market run ...
"""
import sys
from agent_compose.cli import main as cli_main


def _map_args(argv):
    """将 deploy_cli 的子命令映射到 agent_compose.cli 的 market 子命令。"""
    if not argv:
        return ["market", "health"]

    first = argv[0]
    if first in ("health", "search", "download", "show", "run"):
        return ["market"] + argv
    # 未知子命令：按原样传递（例如 --help 等），由主 CLI 处理
    return argv


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    new_argv = _map_args(list(argv))
    return cli_main(new_argv)


if __name__ == "__main__":
    sys.exit(main())

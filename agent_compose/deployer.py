"""
Deployer - 部署管理器

将 MarketClient 与本地部署流程对接，提供:
- 从 Market 下载并部署 Agent
- 本地 Agent 缓存管理
- 部署状态跟踪
- 批量部署操作
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent_runtime import AgentRuntime
from .market_client import MarketClient


class Deployer:
    """部署管理器：桥接 MarketClient 与本地部署流程"""

    def __init__(
        self,
        market_client: Optional[MarketClient] = None,
        deploy_dir: str = "./.deployed",
    ):
        self.market_client = market_client or MarketClient()
        self.deploy_dir = os.path.abspath(deploy_dir)
        os.makedirs(self.deploy_dir, exist_ok=True)

    # ---------- 核心部署操作 ----------

    async def deploy(
        self,
        agent_id: str,
        version: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """从 Market 下载并部署 Agent

        Args:
            agent_id: Agent 标识
            version: 版本号，None 表示 latest
            force: 是否强制重新部署（覆盖本地缓存）

        Returns:
            部署信息字典，包含 agent_id, version, path, deployed_at, from_cache
        """
        version_spec = version or "latest"
        deploy_path = os.path.join(self.deploy_dir, agent_id, version_spec)
        agent_json_path = os.path.join(deploy_path, "agent.json")

        # 检查本地是否已部署
        if os.path.exists(agent_json_path) and not force:
            with open(agent_json_path, "r", encoding="utf-8") as f:
                agent_json = json.load(f)
            resolved_version = agent_json.get("identity", {}).get("version", version_spec)
            return {
                "agent_id": agent_id,
                "version": resolved_version,
                "path": deploy_path,
                "deployed_at": self._get_deployed_at(agent_id, resolved_version),
                "from_cache": True,
                "status": "already_deployed",
            }

        # 清理旧版本（如果 force=True 或不存在）
        if os.path.exists(deploy_path):
            shutil.rmtree(deploy_path)

        # 从 Market 下载
        download_result = await self.market_client.download_agent(
            agent_id=agent_id,
            output_dir=os.path.join(self.deploy_dir, agent_id),
            version=version,
            skip_cache=force,
        )

        if not download_result.success:
            return {
                "agent_id": agent_id,
                "version": version_spec,
                "path": "",
                "deployed_at": None,
                "from_cache": False,
                "status": "failed",
                "error": download_result.message,
            }

        # 如果下载目录名与 version_spec 不一致，需要移动/重命名
        downloaded_path = download_result.output_path
        if downloaded_path != deploy_path:
            if os.path.exists(deploy_path):
                shutil.rmtree(deploy_path)
            os.makedirs(os.path.dirname(deploy_path), exist_ok=True)
            shutil.move(downloaded_path, deploy_path)

        # 读取 agent.json 获取真实版本号
        with open(os.path.join(deploy_path, "agent.json"), "r", encoding="utf-8") as f:
            agent_json = json.load(f)
        resolved_version = agent_json.get("identity", {}).get("version", version_spec)

        # 写入部署元数据
        meta_path = os.path.join(deploy_path, ".deploy_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "agent_id": agent_id,
                    "version": resolved_version,
                    "deployed_at": datetime.now().isoformat(),
                    "from_cache": download_result.from_cache,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        return {
            "agent_id": agent_id,
            "version": resolved_version,
            "path": deploy_path,
            "deployed_at": datetime.now().isoformat(),
            "from_cache": download_result.from_cache,
            "status": "deployed",
        }

    def get_deployed_agent(
        self,
        agent_id: str,
        version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """从本地缓存读取已部署的 Agent 配置

        Args:
            agent_id: Agent 标识
            version: 版本号，None 表示 latest

        Returns:
            agent.json 内容字典，如果未找到则返回 None
        """
        version_spec = version or "latest"
        agent_json_path = os.path.join(self.deploy_dir, agent_id, version_spec, "agent.json")

        if not os.path.exists(agent_json_path):
            return None

        with open(agent_json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_deployed(self) -> List[Dict[str, Any]]:
        """列出所有已部署的 Agent

        Returns:
            已部署 Agent 列表，每项包含 agent_id, version, path, deployed_at
        """
        results = []
        if not os.path.exists(self.deploy_dir):
            return results

        for agent_id in os.listdir(self.deploy_dir):
            agent_dir = os.path.join(self.deploy_dir, agent_id)
            if not os.path.isdir(agent_dir):
                continue

            for version in os.listdir(agent_dir):
                version_dir = os.path.join(agent_dir, version)
                if not os.path.isdir(version_dir):
                    continue

                agent_json_path = os.path.join(version_dir, "agent.json")
                if not os.path.exists(agent_json_path):
                    continue

                deployed_at = self._get_deployed_at(agent_id, version)
                results.append({
                    "agent_id": agent_id,
                    "version": version,
                    "path": version_dir,
                    "deployed_at": deployed_at,
                })

        return results

    def undeploy(
        self,
        agent_id: str,
        version: Optional[str] = None,
    ) -> bool:
        """取消部署（删除本地缓存）

        Args:
            agent_id: Agent 标识
            version: 版本号，None 表示删除该 agent_id 下所有版本

        Returns:
            是否成功删除
        """
        agent_dir = os.path.join(self.deploy_dir, agent_id)

        if not os.path.exists(agent_dir):
            return False

        if version:
            version_dir = os.path.join(agent_dir, version)
            if os.path.exists(version_dir):
                shutil.rmtree(version_dir)
                # 如果该 agent_id 下没有版本了，删除 agent_id 目录
                if not os.listdir(agent_dir):
                    shutil.rmtree(agent_dir)
                return True
            return False
        else:
            # 删除该 agent_id 下所有版本
            shutil.rmtree(agent_dir)
            return True

    def deploy_from_file(
        self,
        agent_id: str,
        agent_json: Dict[str, Any],
        version: str = "local",
    ) -> Dict[str, Any]:
        """从本地文件/字典直接部署 Agent

        Args:
            agent_id: Agent 标识
            agent_json: Agent 配置字典
            version: 版本号，默认 "local"

        Returns:
            部署信息字典
        """
        deploy_path = os.path.join(self.deploy_dir, agent_id, version)

        if os.path.exists(deploy_path):
            shutil.rmtree(deploy_path)
        os.makedirs(deploy_path, exist_ok=True)

        agent_json_path = os.path.join(deploy_path, "agent.json")
        with open(agent_json_path, "w", encoding="utf-8") as f:
            json.dump(agent_json, f, indent=2, ensure_ascii=False)

        # 写入部署元数据
        meta_path = os.path.join(deploy_path, ".deploy_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "agent_id": agent_id,
                    "version": version,
                    "deployed_at": datetime.now().isoformat(),
                    "from_cache": False,
                    "source": "local_file",
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        return {
            "agent_id": agent_id,
            "version": version,
            "path": deploy_path,
            "deployed_at": datetime.now().isoformat(),
            "from_cache": False,
            "status": "deployed",
            "source": "local_file",
        }

    def get_agent_runtime(
        self,
        agent_id: str,
        version: Optional[str] = None,
        **runtime_kwargs: Any,
    ) -> AgentRuntime:
        """获取已部署 Agent 的配置并创建 AgentRuntime

        Args:
            agent_id: Agent 标识
            version: 版本号，None 表示 latest
            **runtime_kwargs: 传递给 AgentRuntime 构造函数的额外参数
                (api_key, model_provider, model_id, webbridge_token, base_url 等)

        Returns:
            AgentRuntime 实例

        Raises:
            FileNotFoundError: 如果 Agent 未部署
        """
        agent_json = self.get_deployed_agent(agent_id, version)
        if agent_json is None:
            raise FileNotFoundError(
                f"Agent '{agent_id}' (version={version or 'latest'}) is not deployed. "
                f"Call deploy() first."
            )

        return AgentRuntime(
            agent_id=agent_id,
            agent_json=agent_json,
            **runtime_kwargs,
        )

    # ---------- 批量操作 ----------

    async def batch_deploy(
        self,
        agent_ids: List[str],
        version: Optional[str] = None,
        force: bool = False,
        continue_on_error: bool = True,
    ) -> List[Dict[str, Any]]:
        """批量部署多个 Agent

        Args:
            agent_ids: Agent 标识列表
            version: 版本号，None 表示 latest
            force: 是否强制重新部署
            continue_on_error: 遇到错误时是否继续

        Returns:
            每个 Agent 的部署结果列表
        """
        results = []
        for agent_id in agent_ids:
            try:
                result = await self.deploy(agent_id, version=version, force=force)
                results.append(result)
            except Exception as e:
                results.append({
                    "agent_id": agent_id,
                    "version": version or "latest",
                    "path": "",
                    "deployed_at": None,
                    "from_cache": False,
                    "status": "failed",
                    "error": str(e),
                })
                if not continue_on_error:
                    break
        return results

    # ---------- 向后兼容方法 (旧 Deployer 接口) ----------

    def register_agent(self, name: str, agent: Dict[str, Any]) -> Dict[str, Any]:
        """向后兼容：注册 Agent（直接保存到本地）"""
        result = self.deploy_from_file(name, agent, version="local")
        result["status"] = "ok"
        result["deployed"] = {"type": "agent", "name": name}
        return result

    def register_team(self, name: str, team: Dict[str, Any]) -> Dict[str, Any]:
        """向后兼容：注册 Team（直接保存到本地）"""
        result = self.deploy_from_file(name, team, version="local")
        result["status"] = "ok"
        result["deployed"] = {"type": "team", "name": name}
        return result

    def register_workflow(self, name: str, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """向后兼容：注册 Workflow（直接保存到本地）"""
        result = self.deploy_from_file(name, workflow, version="local")
        result["status"] = "ok"
        result["deployed"] = {"type": "workflow", "name": name}
        return result

    def deploy_to_agentos(self, kind: str, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """向后兼容：部署到 AgentOS"""
        if kind not in ("agent", "team", "workflow"):
            return {"status": "error", "message": f"Invalid type: {kind}"}
        result = self.deploy_from_file(name, data, version="local")
        result["status"] = "ok"
        result["deployed"] = {"type": kind, "name": name, "deploy_url": f"agentos://{kind}/{name}"}
        return result

    # ---------- 内部工具方法 ----------

    def _get_deployed_at(self, agent_id: str, version: str) -> Optional[str]:
        """读取部署时间"""
        meta_path = os.path.join(self.deploy_dir, agent_id, version, ".deploy_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return meta.get("deployed_at")
        return None

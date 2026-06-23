"""
Skill & MCP Resolver - v3.1 运行时引用解析器

提供 Agent 启动时的 Skill/MCP 引用解析、下载、缓存管理功能。

功能:
- 解析 agent.json 中的 SkillRef 和 MCPRef 引用
- 版本约束匹配 (^, ~, >=, *, 精确版本)
- 本地缓存管理 (~/.agent-hub/cache/)
- 从市场下载依赖包
- 合并解析后的 Skill/MCP 到 Agent 上下文

用法:
    resolver = SkillMCPResolver(market_url="https://market.aitboy.cn")
    resolved = await resolver.resolve_agent(agent_json)
    # resolved 包含合并后的 skills 和 mcp_servers
"""
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ResolvedSkill:
    """解析后的 Skill"""

    ref: str
    version: str
    name: str
    display_name: str
    description: str
    content: str = ""  # SKILL.md 内容
    capabilities: List[str] = field(default_factory=list)
    scripts: Dict[str, str] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    source: str = "market"  # market / local / inline
    cache_path: Optional[str] = None
    from_cache: bool = False


@dataclass
class ResolvedMCP:
    """解析后的 MCP Server"""

    ref: str
    version: str
    name: str
    display_name: str
    description: str
    config: Dict[str, Any] = field(default_factory=dict)  # mcp-config.json 内容
    tools: List[str] = field(default_factory=list)
    required_env: List[str] = field(default_factory=list)
    source: str = "market"
    cache_path: Optional[str] = None
    from_cache: bool = False
    env_override: Dict[str, str] = field(default_factory=dict)


@dataclass
class ResolutionResult:
    """引用解析结果"""

    skills: List[ResolvedSkill] = field(default_factory=list)
    mcp_servers: List[ResolvedMCP] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ============================================================
# 版本解析
# ============================================================

class VersionConstraint:
    """语义化版本约束解析器"""

    def __init__(self, constraint: str):
        self.constraint = constraint.strip()
        self._parsed = self._parse()

    def _parse(self) -> Tuple[str, Any]:
        c = self.constraint
        if c == "*" or c == "latest":
            return ("any", None)
        if c.startswith("^"):
            return ("caret", self._parse_version(c[1:]))
        if c.startswith("~"):
            return ("tilde", self._parse_version(c[1:]))
        if c.startswith(">="):
            return ("gte", self._parse_version(c[2:]))
        if c.startswith(">"):
            return ("gt", self._parse_version(c[1:]))
        if "<=" in c:
            parts = c.split("<=")
            return ("range", (self._parse_version(parts[0].strip()), self._parse_version(parts[1].strip())))
        # 精确版本
        try:
            return ("exact", self._parse_version(c))
        except ValueError:
            return ("any", None)

    @staticmethod
    def _parse_version(v: str) -> Tuple[int, int, int]:
        """解析版本字符串为 (major, minor, patch)"""
        v = v.strip().lstrip("v")
        parts = v.split(".")
        while len(parts) < 3:
            parts.append("0")
        return (int(parts[0]), int(parts[1]), int(parts[2]))

    def match(self, version: str) -> bool:
        """检查版本是否匹配约束"""
        op, target = self._parsed
        if op == "any":
            return True

        v = self._parse_version(version)

        if op == "exact":
            return v == target

        if op == "caret":
            # ^1.2.3 => >=1.2.3 <2.0.0
            return v >= target and v[0] == target[0]

        if op == "tilde":
            # ~1.2.3 => >=1.2.3 <1.3.0
            return v >= target and v[0] == target[0] and v[1] == target[1]

        if op == "gte":
            return v >= target

        if op == "gt":
            return v > target

        if op == "range":
            low, high = target
            return low <= v <= high

        return False

    def find_best(self, versions: List[str]) -> Optional[str]:
        """从版本列表中找到最佳匹配"""
        valid = [v for v in versions if self.match(v)]
        if not valid:
            return None
        # 按版本号降序排列，返回最新匹配版本
        valid.sort(key=self._parse_version, reverse=True)
        return valid[0]


# ============================================================
# 缓存管理
# ============================================================

class CacheManager:
    """Skill/MCP 本地缓存管理器

    缓存目录结构:
        ~/.agent-hub/cache/
        ├── skills/
        │   ├── html-anything@1.0.0/
        │   └── text-summarizer@2.1.0/
        ├── mcp-servers/
        │   └── tapd@1.0.0/
        └── index.json
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or os.path.expanduser("~/.agent-hub/cache")
        self.skills_dir = os.path.join(self.cache_dir, "skills")
        self.mcp_dir = os.path.join(self.cache_dir, "mcp-servers")
        self.index_path = os.path.join(self.cache_dir, "index.json")
        self._ensure_dirs()
        self._index = self._load_index()

    def _ensure_dirs(self):
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.mcp_dir, exist_ok=True)

    def _load_index(self) -> Dict[str, Any]:
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"skills": {}, "mcp_servers": {}, "version": "1.0.0"}

    def _save_index(self):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2, ensure_ascii=False)

    def get_skill_path(self, ref: str, version: str) -> Optional[str]:
        """获取 Skill 缓存路径"""
        key = f"{ref}@{version}"
        path = os.path.join(self.skills_dir, key)
        if os.path.exists(path) and os.path.exists(os.path.join(path, "skill.json")):
            return path
        return None

    def get_mcp_path(self, ref: str, version: str) -> Optional[str]:
        """获取 MCP Server 缓存路径"""
        key = f"{ref}@{version}"
        path = os.path.join(self.mcp_dir, key)
        if os.path.exists(path) and os.path.exists(os.path.join(path, "mcp-server.json")):
            return path
        return None

    def store_skill(self, ref: str, version: str, package_path: str) -> str:
        """存储 Skill 包到缓存"""
        key = f"{ref}@{version}"
        target_dir = os.path.join(self.skills_dir, key)

        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)

        # 解压包
        os.makedirs(target_dir, exist_ok=True)
        with tarfile.open(package_path, "r:gz") as tar:
            tar.extractall(target_dir)

        # 处理顶层目录
        entries = [e for e in os.listdir(target_dir) if os.path.isdir(os.path.join(target_dir, e))]
        if len(entries) == 1:
            inner_dir = os.path.join(target_dir, entries[0])
            # 将内层文件移到外层
            for item in os.listdir(inner_dir):
                shutil.move(os.path.join(inner_dir, item), os.path.join(target_dir, item))
            os.rmdir(inner_dir)

        # 更新索引
        self._index["skills"][key] = {
            "ref": ref,
            "version": version,
            "path": target_dir,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._save_index()

        return target_dir

    def store_mcp(self, ref: str, version: str, package_path: str) -> str:
        """存储 MCP Server 包到缓存"""
        key = f"{ref}@{version}"
        target_dir = os.path.join(self.mcp_dir, key)

        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)

        os.makedirs(target_dir, exist_ok=True)
        with tarfile.open(package_path, "r:gz") as tar:
            tar.extractall(target_dir)

        # 处理顶层目录
        entries = [e for e in os.listdir(target_dir) if os.path.isdir(os.path.join(target_dir, e))]
        if len(entries) == 1:
            inner_dir = os.path.join(target_dir, entries[0])
            for item in os.listdir(inner_dir):
                shutil.move(os.path.join(inner_dir, item), os.path.join(target_dir, item))
            os.rmdir(inner_dir)

        self._index["mcp_servers"][key] = {
            "ref": ref,
            "version": version,
            "path": target_dir,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._save_index()

        return target_dir

    def check_update_needed(self, ref: str, version: str, kind: str = "skill") -> bool:
        """检查是否需要更新（用于 * 或 ^ 约束）"""
        key = f"{ref}@{version}"
        index = self._index.get(kind + "s", {})
        entry = index.get(key)
        if not entry:
            return True

        downloaded_at = entry.get("downloaded_at", "")
        if not downloaded_at:
            return True

        # 每日检查一次
        try:
            from datetime import datetime
            dt = datetime.strptime(downloaded_at, "%Y-%m-%dT%H:%M:%SZ")
            return (datetime.utcnow() - dt).days >= 1
        except ValueError:
            return True

    def clean_unused(self, max_age_days: int = 30) -> List[str]:
        """清理长时间未使用的缓存"""
        removed = []
        cutoff = time.time() - (max_age_days * 86400)

        for kind, dir_path in [("skills", self.skills_dir), ("mcp_servers", self.mcp_dir)]:
            if not os.path.exists(dir_path):
                continue
            for entry in os.listdir(dir_path):
                entry_path = os.path.join(dir_path, entry)
                if os.path.isdir(entry_path):
                    mtime = os.path.getmtime(entry_path)
                    if mtime < cutoff:
                        shutil.rmtree(entry_path)
                        removed.append(entry)
                        # 从索引中移除
                        if entry in self._index.get(kind, {}):
                            del self._index[kind][entry]

        self._save_index()
        return removed

    def list_cached(self, kind: str = "skill") -> List[Dict[str, str]]:
        """列出已缓存的条目"""
        results = []
        index = self._index.get(kind + "s", {})
        for key, info in index.items():
            results.append({
                "ref": info.get("ref", key.split("@")[0]),
                "version": info.get("version", key.split("@")[1] if "@" in key else ""),
                "path": info.get("path", ""),
                "downloaded_at": info.get("downloaded_at", ""),
            })
        return results

    def clear(self, kind: Optional[str] = None):
        """清空缓存"""
        if kind == "skill" or kind is None:
            if os.path.exists(self.skills_dir):
                shutil.rmtree(self.skills_dir)
            os.makedirs(self.skills_dir, exist_ok=True)
            self._index["skills"] = {}

        if kind == "mcp" or kind is None:
            if os.path.exists(self.mcp_dir):
                shutil.rmtree(self.mcp_dir)
            os.makedirs(self.mcp_dir, exist_ok=True)
            self._index["mcp_servers"] = {}

        self._save_index()


# ============================================================
# 市场客户端（Skill/MCP 专用）
# ============================================================

class MarketDependencyClient:
    """用于下载 Skill/MCP 依赖的市场客户端"""

    def __init__(self, base_url: str = "https://market.aitboy.cn", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def list_skill_versions(self, ref: str) -> List[str]:
        """获取 Skill 的所有版本"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/skills/{ref}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                data = response.json()
                versions = data.get("versions", [])
                if not versions and data.get("version"):
                    versions = [data.get("version")]
                return versions
        except Exception:
            return []

    async def download_skill(self, ref: str, version: str, output_path: str):
        """下载 Skill 包"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/skills/{ref}/download?version={version}",
                headers=self._headers(),
            )
            response.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(response.content)

    async def list_mcp_versions(self, ref: str) -> List[str]:
        """获取 MCP Server 的所有版本"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/mcp-servers/{ref}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                data = response.json()
                versions = data.get("versions", [])
                if not versions and data.get("version"):
                    versions = [data.get("version")]
                return versions
        except Exception:
            return []

    async def download_mcp(self, ref: str, version: str, output_path: str):
        """下载 MCP Server 包"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/mcp-servers/{ref}/download?version={version}",
                headers=self._headers(),
            )
            response.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(response.content)


# ============================================================
# Skill/MCP 解析器
# ============================================================

class SkillMCPResolver:
    """Skill & MCP 引用解析器

    解析 agent.json 中的 SkillRef 和 MCPRef，下载依赖并合并到 Agent 上下文。
    """

    def __init__(
        self,
        market_url: str = "https://market.aitboy.cn",
        api_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ):
        self.market = MarketDependencyClient(base_url=market_url, api_key=api_key)
        self.cache = CacheManager(cache_dir=cache_dir)
        self.result = ResolutionResult()

    def _is_reference(self, item: Dict[str, Any]) -> bool:
        """判断是否为引用模式（而非内联定义）"""
        return "ref" in item and item.get("ref")

    def _is_inline(self, item: Dict[str, Any]) -> bool:
        """判断是否为内联定义"""
        return not self._is_reference(item)

    # ---------- Skill 解析 ----------

    async def _resolve_skill_ref(self, ref_item: Dict[str, Any]) -> Optional[ResolvedSkill]:
        """解析 Skill 引用"""
        ref = ref_item.get("ref", "")
        version_constraint = ref_item.get("version", "*")
        market_url = ref_item.get("market_url", self.market.base_url)

        if not ref:
            self.result.errors.append("Skill ref 为空")
            return None

        # 1. 解析版本约束
        constraint = VersionConstraint(version_constraint)

        # 2. 检查本地缓存
        # 先尝试精确版本
        cache_path = self.cache.get_skill_path(ref, version_constraint)
        if cache_path:
            return self._load_skill_from_cache(ref, version_constraint, cache_path)

        # 3. 查询市场获取可用版本
        versions = await self.market.list_skill_versions(ref)
        if not versions:
            self.result.errors.append(f"Skill '{ref}' 在市场未找到")
            return None

        best_version = constraint.find_best(versions)
        if not best_version:
            self.result.errors.append(
                f"Skill '{ref}' 无版本匹配约束 '{version_constraint}'，可用版本: {versions}"
            )
            return None

        # 4. 再次检查缓存（解析后的精确版本）
        cache_path = self.cache.get_skill_path(ref, best_version)
        if cache_path:
            return self._load_skill_from_cache(ref, best_version, cache_path)

        # 5. 从市场下载
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp_path = tmp.name

            await self.market.download_skill(ref, best_version, tmp_path)

            # 6. 存储到缓存
            cache_path = self.cache.store_skill(ref, best_version, tmp_path)

            # 7. 加载
            skill = self._load_skill_from_cache(ref, best_version, cache_path)
            skill.from_cache = False

            os.unlink(tmp_path)
            return skill

        except Exception as e:
            self.result.errors.append(f"下载 Skill '{ref}@{best_version}' 失败: {e}")
            return None

    def _load_skill_from_cache(self, ref: str, version: str, cache_path: str) -> ResolvedSkill:
        """从缓存加载 Skill"""
        skill_json_path = os.path.join(cache_path, "skill.json")

        with open(skill_json_path, "r", encoding="utf-8") as f:
            skill_json = json.load(f)

        identity = skill_json.get("identity", {})
        content_info = skill_json.get("content", {})

        # 加载 SKILL.md 内容
        content = ""
        if content_info.get("source") == "file" and content_info.get("file"):
            skill_md_path = os.path.join(cache_path, content_info.get("file", "SKILL.md"))
            if os.path.exists(skill_md_path):
                with open(skill_md_path, "r", encoding="utf-8") as f:
                    content = f.read()
        elif content_info.get("source") == "inline":
            content = content_info.get("content", "")
        else:
            # 默认尝试加载 SKILL.md
            skill_md_path = os.path.join(cache_path, "SKILL.md")
            if os.path.exists(skill_md_path):
                with open(skill_md_path, "r", encoding="utf-8") as f:
                    content = f.read()

        return ResolvedSkill(
            ref=ref,
            version=version,
            name=identity.get("name", ref),
            display_name=identity.get("display_name", ref),
            description=identity.get("description", ""),
            content=content,
            capabilities=skill_json.get("capabilities", []),
            scripts=skill_json.get("scripts", {}),
            parameters=skill_json.get("parameters", {}),
            source="market",
            cache_path=cache_path,
            from_cache=True,
        )

    def _resolve_inline_skill(self, skill_item: Dict[str, Any]) -> ResolvedSkill:
        """解析内联 Skill"""
        return ResolvedSkill(
            ref=skill_item.get("name", "inline"),
            version=skill_item.get("version", "1.0.0"),
            name=skill_item.get("name", ""),
            display_name=skill_item.get("display_name", ""),
            description=skill_item.get("description", ""),
            content=skill_item.get("content", skill_item.get("instructions", "")),
            capabilities=skill_item.get("capabilities", []),
            parameters=skill_item.get("parameters", {}),
            source="inline",
        )

    # ---------- MCP 解析 ----------

    async def _resolve_mcp_ref(self, ref_item: Dict[str, Any]) -> Optional[ResolvedMCP]:
        """解析 MCP Server 引用"""
        ref = ref_item.get("ref", "")
        version_constraint = ref_item.get("version", "*")
        env_override = ref_item.get("env_override", {})

        if not ref:
            self.result.errors.append("MCP ref 为空")
            return None

        constraint = VersionConstraint(version_constraint)

        # 检查缓存
        cache_path = self.cache.get_mcp_path(ref, version_constraint)
        if cache_path:
            return self._load_mcp_from_cache(ref, version_constraint, cache_path, env_override)

        # 查询市场
        versions = await self.market.list_mcp_versions(ref)
        if not versions:
            self.result.errors.append(f"MCP Server '{ref}' 在市场未找到")
            return None

        best_version = constraint.find_best(versions)
        if not best_version:
            self.result.errors.append(
                f"MCP Server '{ref}' 无版本匹配约束 '{version_constraint}'"
            )
            return None

        # 再次检查缓存
        cache_path = self.cache.get_mcp_path(ref, best_version)
        if cache_path:
            return self._load_mcp_from_cache(ref, best_version, cache_path, env_override)

        # 下载
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp_path = tmp.name

            await self.market.download_mcp(ref, best_version, tmp_path)
            cache_path = self.cache.store_mcp(ref, best_version, tmp_path)
            mcp = self._load_mcp_from_cache(ref, best_version, cache_path, env_override)
            mcp.from_cache = False

            os.unlink(tmp_path)
            return mcp

        except Exception as e:
            self.result.errors.append(f"下载 MCP '{ref}@{best_version}' 失败: {e}")
            return None

    def _load_mcp_from_cache(
        self, ref: str, version: str, cache_path: str, env_override: Dict[str, str] = None
    ) -> ResolvedMCP:
        """从缓存加载 MCP Server"""
        mcp_server_json_path = os.path.join(cache_path, "mcp-server.json")

        with open(mcp_server_json_path, "r", encoding="utf-8") as f:
            mcp_json = json.load(f)

        identity = mcp_json.get("identity", {})

        # 加载 mcp-config.json
        config = {}
        config_info = mcp_json.get("config", {})
        if config_info.get("source") == "file" and config_info.get("file"):
            config_path = os.path.join(cache_path, config_info.get("file", "mcp-config.json"))
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
        elif config_info.get("source") == "inline":
            config = config_info.get("content", {})
        else:
            # 默认尝试加载 mcp-config.json
            config_path = os.path.join(cache_path, "mcp-config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)

        # 应用 env_override
        if env_override and config:
            if "env" in config:
                config["env"].update(env_override)
            else:
                config["env"] = env_override

        return ResolvedMCP(
            ref=ref,
            version=version,
            name=identity.get("name", ref),
            display_name=identity.get("display_name", ref),
            description=identity.get("description", ""),
            config=config,
            tools=mcp_json.get("tools", []),
            required_env=mcp_json.get("required_env", []),
            source="market",
            cache_path=cache_path,
            from_cache=True,
            env_override=env_override or {},
        )

    def _resolve_inline_mcp(self, mcp_item: Dict[str, Any]) -> ResolvedMCP:
        """解析内联 MCP Server"""
        return ResolvedMCP(
            ref=mcp_item.get("name", "inline"),
            version=mcp_item.get("version", "1.0.0"),
            name=mcp_item.get("name", ""),
            display_name=mcp_item.get("display_name", ""),
            description=mcp_item.get("description", ""),
            config={
                "type": mcp_item.get("type", "stdio"),
                "command": mcp_item.get("command", ""),
                "args": mcp_item.get("args", []),
                "env": mcp_item.get("env", {}),
            },
            source="inline",
        )

    # ---------- 主解析入口 ----------

    async def resolve_agent(self, agent_json: Dict[str, Any]) -> ResolutionResult:
        """解析 Agent 的所有 Skill 和 MCP 引用

        Args:
            agent_json: agent.json 字典

        Returns:
            ResolutionResult: 包含解析后的 skills 和 mcp_servers
        """
        self.result = ResolutionResult()

        # 解析 skills
        skills = agent_json.get("skills", [])
        for skill_item in skills:
            if self._is_reference(skill_item):
                resolved = await self._resolve_skill_ref(skill_item)
                if resolved:
                    self.result.skills.append(resolved)
            else:
                self.result.skills.append(self._resolve_inline_skill(skill_item))

        # 解析 mcp_servers
        mcp_servers = agent_json.get("mcp_servers", [])
        for mcp_item in mcp_servers:
            if self._is_reference(mcp_item):
                resolved = await self._resolve_mcp_ref(mcp_item)
                if resolved:
                    self.result.mcp_servers.append(resolved)
            else:
                self.result.mcp_servers.append(self._resolve_inline_mcp(mcp_item))

        return self.result

    def merge_to_agent(self, agent_json: Dict[str, Any], result: ResolutionResult) -> Dict[str, Any]:
        """将解析结果合并回 agent.json

        将引用的 Skill 内容合并到 instructions，将 MCP 配置合并到 mcp_servers。
        """
        merged = dict(agent_json)

        # 合并 Skill 内容到 system prompt
        if result.skills:
            skill_sections = []
            for skill in result.skills:
                if skill.content:
                    skill_sections.append(f"\n## Skill: {skill.display_name or skill.name}\n\n{skill.content}")

            if skill_sections:
                instructions = merged.get("instructions", {}) or {}
                current_content = instructions.get("content", "")
                merged_content = current_content + "\n\n".join(skill_sections)
                instructions["content"] = merged_content
                merged["instructions"] = instructions

        # 合并 capabilities
        all_capabilities = list(merged.get("capabilities", []) or [])
        for skill in result.skills:
            for cap in skill.capabilities:
                if cap not in all_capabilities:
                    all_capabilities.append(cap)
        merged["capabilities"] = all_capabilities

        # 合并 MCP servers（将引用的 MCP 配置转换为内联格式）
        final_mcps = []
        for mcp in result.mcp_servers:
            if mcp.source == "inline":
                final_mcps.append({
                    "name": mcp.name,
                    "type": mcp.config.get("type", "stdio"),
                    "command": mcp.config.get("command", ""),
                    "args": mcp.config.get("args", []),
                    "env": mcp.config.get("env", {}),
                })
            else:
                # 引用解析后的 MCP，使用 config 中的配置
                cfg = dict(mcp.config)
                cfg["name"] = mcp.name
                final_mcps.append(cfg)

        merged["mcp_servers"] = final_mcps

        return merged

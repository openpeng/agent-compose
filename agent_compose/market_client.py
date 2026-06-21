"""
Market Client - Market 管理客户端

提供与 Agent Market 服务交互的功能:
- Agent/Team/Workflow 上传下载
- 批量操作
- 版本管理（列表、对比、差异）
- 发布流水线
- 搜索和查询
- 本地缓存管理
"""

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ============ 数据模型 ============


@dataclass
class AgentInfo:
    """Agent 信息"""

    id: str
    name: str
    display_name: str
    version: str
    description: str
    author: str
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    downloads: int = 0
    rating: float = 0.0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class VersionInfo:
    """版本信息"""

    version: str
    created_at: str
    changelog: str = ""
    author: str = ""
    downloads: int = 0


@dataclass
class SearchResult:
    """搜索结果"""

    items: List[AgentInfo]
    total: int
    limit: int
    offset: int


@dataclass
class UploadResult:
    """上传结果"""

    success: bool
    agent_id: str
    agent_name: str
    version: str
    market_url: str
    message: str = ""


@dataclass
class DownloadResult:
    """下载结果"""

    success: bool
    agent_id: str
    output_path: str
    message: str = ""
    from_cache: bool = False


@dataclass
class VersionDiff:
    """版本差异"""

    version_a: str
    version_b: str
    added_files: List[str] = field(default_factory=list)
    removed_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    unchanged_files: List[str] = field(default_factory=list)
    identity_changes: Dict[str, Tuple[Any, Any]] = field(default_factory=dict)
    instruction_changes: Dict[str, Tuple[Any, Any]] = field(default_factory=dict)


# ============ Market 客户端 ============


class MarketClient:
    """Market API 客户端"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ):
        self.base_url = base_url or os.environ.get("MARKET_API_URL", "http://localhost:8321")
        self.api_key = api_key or os.environ.get("MARKET_API_KEY")
        self.cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "agent-compose-market-cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        import httpx

        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                response = await client.get(url, params=params, headers=self._headers())
            elif method == "POST":
                response = await client.post(url, json=json_data, headers=self._headers())
            elif method == "DELETE":
                response = await client.delete(url, headers=self._headers())
            else:
                raise ValueError(f"Unsupported method: {method}")

            if response.status_code == 404:
                raise FileNotFoundError(f"Resource not found: {path}")
            if response.status_code in (401, 403):
                raise PermissionError(f"Authentication failed for: {path}")
            response.raise_for_status()

            if response.status_code == 204:
                return None
            return response.json()

    # ============ Agent 操作 ============

    async def upload_agent(
        self,
        agent_dir: str,
        force: bool = False,
    ) -> UploadResult:
        """上传 Agent 到 Market"""
        import httpx

        agent_dir = os.path.abspath(agent_dir)
        agent_json_path = os.path.join(agent_dir, "agent.json")

        if not os.path.exists(agent_json_path):
            raise FileNotFoundError(f"agent.json not found in {agent_dir}")

        with open(agent_json_path, "r", encoding="utf-8") as f:
            agent_json = json.load(f)

        agent_name = agent_json["identity"]["name"]
        version = agent_json["identity"]["version"]

        # 打包
        package_path = self._pack_directory(agent_dir, agent_name, version)

        try:
            url = f"{self.base_url}/api/v1/agents"

            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(package_path, "rb") as f:
                    files = {
                        "file": (f"{agent_name}-v{version}.tar.gz", f, "application/gzip"),
                    }
                    data = {"force": "true" if force else "false"}
                    headers = {}
                    if self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"

                    response = await client.post(url, files=files, data=data, headers=headers)

                if response.status_code == 409:
                    raise ValueError(f"Agent {agent_name}@{version} already exists. Use force=True to overwrite.")
                response.raise_for_status()

                result = response.json()
                return UploadResult(
                    success=True,
                    agent_id=result.get("id", agent_name),
                    agent_name=agent_name,
                    version=version,
                    market_url=f"{self.base_url}/agents/{agent_name}",
                    message=result.get("message", "Agent uploaded successfully"),
                )
        finally:
            if os.path.exists(package_path):
                os.unlink(package_path)

    async def download_agent(
        self,
        agent_id: str,
        output_dir: str,
        version: Optional[str] = None,
        skip_cache: bool = False,
    ) -> DownloadResult:
        """从 Market 下载 Agent"""
        import httpx

        version_spec = version or "latest"

        # 检查缓存
        if not skip_cache:
            cached = self._get_from_cache(agent_id, version_spec)
            if cached:
                output_path = os.path.join(output_dir, agent_id)
                shutil.copytree(cached, output_path, dirs_exist_ok=True)
                return DownloadResult(
                    success=True,
                    agent_id=agent_id,
                    output_path=output_path,
                    message="Agent loaded from cache",
                    from_cache=True,
                )

        # 从 Market 下载
        url = f"{self.base_url}/api/v1/agents/{agent_id}/download"
        if version:
            url += f"?version={version}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            os.makedirs(output_dir, exist_ok=True)
            package_path = os.path.join(output_dir, f"{agent_id}.tar.gz")

            with open(package_path, "wb") as f:
                f.write(response.content)

            # 解压
            extract_dir = os.path.join(output_dir, agent_id)
            self._extract_package(package_path, extract_dir)
            os.unlink(package_path)

            # 更新缓存
            resolved_version = version or "0.0.0"
            agent_json_path = os.path.join(extract_dir, "agent.json")
            if os.path.exists(agent_json_path):
                with open(agent_json_path, "r") as f:
                    aj = json.load(f)
                    resolved_version = aj.get("identity", {}).get("version", resolved_version)

            self._save_to_cache(agent_id, extract_dir, resolved_version)

            return DownloadResult(
                success=True,
                agent_id=agent_id,
                output_path=extract_dir,
                message="Agent downloaded successfully",
                from_cache=False,
            )

    async def get_agent(self, agent_id: str) -> AgentInfo:
        """获取 Agent 信息"""
        data = await self._request("GET", f"/api/v1/agents/{agent_id}")
        return self._parse_agent_info(data)

    async def search_agents(
        self,
        query: Optional[str] = None,
        tag: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResult:
        """搜索 Agent"""
        params = {"limit": limit, "offset": offset}
        if query:
            params["q"] = query
        if tag:
            params["tag"] = tag
        if category:
            params["category"] = category

        data = await self._request("GET", "/api/v1/agents", params=params)
        agents = [self._parse_agent_info(a) for a in data.get("agents", [])]
        return SearchResult(
            items=agents,
            total=data.get("total", len(agents)),
            limit=limit,
            offset=offset,
        )

    async def list_agent_versions(self, agent_id: str) -> List[VersionInfo]:
        """列出 Agent 的所有版本"""
        data = await self._request("GET", f"/api/v1/agents/{agent_id}/versions")
        versions = data.get("versions", data)
        return [
            VersionInfo(
                version=v.get("version", ""),
                created_at=v.get("created_at", ""),
                changelog=v.get("changelog", ""),
                author=v.get("author", ""),
                downloads=v.get("downloads", 0),
            )
            for v in versions
        ]

    # ============ 批量操作 ============

    async def batch_upload(
        self,
        agent_dirs: List[str],
        force: bool = False,
        continue_on_error: bool = True,
    ) -> List[UploadResult]:
        """批量上传 Agent"""
        results = []
        for agent_dir in agent_dirs:
            try:
                result = await self.upload_agent(agent_dir, force=force)
                results.append(result)
            except Exception as e:
                results.append(
                    UploadResult(
                        success=False,
                        agent_id="",
                        agent_name=os.path.basename(agent_dir),
                        version="",
                        market_url="",
                        message=str(e),
                    )
                )
                if not continue_on_error:
                    break
        return results

    async def batch_download(
        self,
        agent_ids: List[str],
        output_dir: str,
        version: Optional[str] = None,
        continue_on_error: bool = True,
    ) -> List[DownloadResult]:
        """批量下载 Agent"""
        results = []
        for agent_id in agent_ids:
            try:
                result = await self.download_agent(agent_id, output_dir, version=version)
                results.append(result)
            except Exception as e:
                results.append(
                    DownloadResult(
                        success=False,
                        agent_id=agent_id,
                        output_path="",
                        message=str(e),
                    )
                )
                if not continue_on_error:
                    break
        return results

    # ============ 版本对比 ============

    async def compare_versions(
        self,
        agent_id: str,
        version_a: str,
        version_b: str,
    ) -> VersionDiff:
        """对比两个版本的差异"""
        # 下载两个版本
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_a = os.path.join(tmpdir, "a")
            dir_b = os.path.join(tmpdir, "b")

            await self.download_agent(agent_id, dir_a, version=version_a, skip_cache=True)
            await self.download_agent(agent_id, dir_b, version=version_b, skip_cache=True)

            return self._diff_directories(dir_a, dir_b, version_a, version_b)

    def _diff_directories(
        self,
        dir_a: str,
        dir_b: str,
        version_a: str,
        version_b: str,
    ) -> VersionDiff:
        """对比两个目录的差异"""
        files_a = self._list_files(dir_a)
        files_b = self._list_files(dir_b)

        rel_a = {os.path.relpath(f, dir_a) for f in files_a}
        rel_b = {os.path.relpath(f, dir_b) for f in files_b}

        added = sorted(rel_b - rel_a)
        removed = sorted(rel_a - rel_b)
        common = sorted(rel_a & rel_b)

        modified = []
        unchanged = []
        for rel_path in common:
            content_a = self._read_file(os.path.join(dir_a, rel_path))
            content_b = self._read_file(os.path.join(dir_b, rel_path))
            if content_a != content_b:
                modified.append(rel_path)
            else:
                unchanged.append(rel_path)

        # 解析 agent.json 的 identity 和 instructions 变化
        identity_changes = {}
        instruction_changes = {}

        agent_json_a = os.path.join(dir_a, "agent.json")
        agent_json_b = os.path.join(dir_b, "agent.json")
        if os.path.exists(agent_json_a) and os.path.exists(agent_json_b):
            with open(agent_json_a, "r") as f:
                json_a = json.load(f)
            with open(agent_json_b, "r") as f:
                json_b = json.load(f)

            identity_a = json_a.get("identity", {})
            identity_b = json_b.get("identity", {})
            for key in set(identity_a.keys()) | set(identity_b.keys()):
                if identity_a.get(key) != identity_b.get(key):
                    identity_changes[key] = (identity_a.get(key), identity_b.get(key))

            inst_a = json_a.get("instructions", {})
            inst_b = json_b.get("instructions", {})
            for key in set(inst_a.keys()) | set(inst_b.keys()):
                if inst_a.get(key) != inst_b.get(key):
                    instruction_changes[key] = (inst_a.get(key), inst_b.get(key))

        return VersionDiff(
            version_a=version_a,
            version_b=version_b,
            added_files=added,
            removed_files=removed,
            modified_files=modified,
            unchanged_files=unchanged,
            identity_changes=identity_changes,
            instruction_changes=instruction_changes,
        )

    # ============ 发布流水线 ============

    async def publish_pipeline(
        self,
        agent_dir: str,
        bump_type: str = "patch",  # major / minor / patch
        changelog: str = "",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """发布流水线: 自动版本升级 + 验证 + 上传"""
        agent_json_path = os.path.join(agent_dir, "agent.json")
        if not os.path.exists(agent_json_path):
            raise FileNotFoundError(f"agent.json not found in {agent_dir}")

        with open(agent_json_path, "r", encoding="utf-8") as f:
            agent_json = json.load(f)

        current_version = agent_json["identity"]["version"]
        new_version = self._bump_version(current_version, bump_type)

        # 更新版本号
        agent_json["identity"]["version"] = new_version

        # 添加 changelog
        if changelog:
            changelog_path = os.path.join(agent_dir, "CHANGELOG.md")
            self._update_changelog(changelog_path, new_version, changelog)

        if dry_run:
            return {
                "dry_run": True,
                "current_version": current_version,
                "new_version": new_version,
                "agent_name": agent_json["identity"]["name"],
                "changelog": changelog,
            }

        # 保存更新后的 agent.json
        with open(agent_json_path, "w", encoding="utf-8") as f:
            json.dump(agent_json, f, indent=2, ensure_ascii=False)

        # 验证
        worker_yaml_path = os.path.join(agent_dir, "worker.yaml")
        if os.path.exists(worker_yaml_path):
            # 可以在这里调用验证逻辑
            pass

        # 上传
        result = await self.upload_agent(agent_dir, force=True)

        return {
            "dry_run": False,
            "current_version": current_version,
            "new_version": new_version,
            "agent_name": agent_json["identity"]["name"],
            "upload_result": result,
            "changelog": changelog,
        }

    # ============ 本地缓存管理 ============

    def _cache_key(self, agent_id: str, version: str) -> str:
        return hashlib.md5(f"{agent_id}:{version}".encode()).hexdigest()

    def _get_from_cache(self, agent_id: str, version: str) -> Optional[str]:
        key = self._cache_key(agent_id, version)
        cache_path = os.path.join(self.cache_dir, key)
        if os.path.exists(cache_path):
            return cache_path
        return None

    def _save_to_cache(self, agent_id: str, agent_dir: str, version: str) -> None:
        key = self._cache_key(agent_id, version)
        cache_path = os.path.join(self.cache_dir, key)
        if os.path.exists(cache_path):
            shutil.rmtree(cache_path)
        shutil.copytree(agent_dir, cache_path)

    def clear_cache(self) -> None:
        """清空本地缓存"""
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

    def list_cache(self) -> List[Dict[str, str]]:
        """列出缓存内容"""
        results = []
        if not os.path.exists(self.cache_dir):
            return results
        for entry in os.listdir(self.cache_dir):
            path = os.path.join(self.cache_dir, entry)
            if os.path.isdir(path):
                agent_json_path = os.path.join(path, "agent.json")
                if os.path.exists(agent_json_path):
                    with open(agent_json_path, "r") as f:
                        aj = json.load(f)
                    results.append({
                        "agent_id": aj.get("identity", {}).get("name", entry),
                        "version": aj.get("identity", {}).get("version", "unknown"),
                        "cache_key": entry,
                        "path": path,
                    })
        return results

    # ============ 私有工具方法 ============

    def _pack_directory(self, directory: str, name: str, version: str) -> str:
        """打包目录为 tar.gz"""
        tmpdir = tempfile.mkdtemp(prefix="agent-compose-")
        package_path = os.path.join(tmpdir, f"{name}-v{version}.tar.gz")

        with tarfile.open(package_path, "w:gz") as tar:
            tar.add(directory, arcname=os.path.basename(directory))

        return package_path

    def _extract_package(self, package_path: str, extract_dir: str) -> None:
        """解压 tar.gz 包"""
        os.makedirs(extract_dir, exist_ok=True)
        with tarfile.open(package_path, "r:gz") as tar:
            # 获取顶层目录名
            top_level = None
            for member in tar.getmembers():
                parts = member.name.split("/")
                if len(parts) > 1:
                    top_level = parts[0]
                    break

            if top_level:
                tar.extractall(path=os.path.dirname(extract_dir))
                # 如果解压后的目录名与 extract_dir 不同，需要移动
                extracted = os.path.join(os.path.dirname(extract_dir), top_level)
                if extracted != extract_dir and os.path.exists(extracted):
                    if os.path.exists(extract_dir):
                        shutil.rmtree(extract_dir)
                    shutil.move(extracted, extract_dir)
            else:
                tar.extractall(path=extract_dir)

    def _list_files(self, directory: str) -> List[str]:
        """递归列出目录中的所有文件"""
        files = []
        for root, _, filenames in os.walk(directory):
            for filename in filenames:
                files.append(os.path.join(root, filename))
        return files

    def _read_file(self, path: str) -> str:
        """读取文件内容"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except (UnicodeDecodeError, IOError):
            return ""

    def _parse_agent_info(self, data: Dict[str, Any]) -> AgentInfo:
        """解析 Agent 信息"""
        identity = data.get("identity", data)
        return AgentInfo(
            id=data.get("id", identity.get("name", "")),
            name=identity.get("name", ""),
            display_name=identity.get("display_name", identity.get("name", "")),
            version=identity.get("version", ""),
            description=identity.get("description", ""),
            author=identity.get("author", ""),
            category=data.get("category", "general"),
            tags=identity.get("tags", []),
            downloads=data.get("downloads", 0),
            rating=data.get("rating", 0.0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def _bump_version(self, version: str, bump_type: str) -> str:
        """升级版本号"""
        parts = version.split(".")
        while len(parts) < 3:
            parts.append("0")

        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

        if bump_type == "major":
            major += 1
            minor = 0
            patch = 0
        elif bump_type == "minor":
            minor += 1
            patch = 0
        else:  # patch
            patch += 1

        return f"{major}.{minor}.{patch}"

    def _update_changelog(self, changelog_path: str, version: str, content: str) -> None:
        """更新 CHANGELOG.md"""
        timestamp = time.strftime("%Y-%m-%d")
        entry = f"## [{version}] - {timestamp}\n\n{content}\n\n"

        existing = ""
        if os.path.exists(changelog_path):
            with open(changelog_path, "r", encoding="utf-8") as f:
                existing = f.read()

        if existing:
            # 在第一个 ## 之前插入
            lines = existing.split("\n")
            insert_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("## "):
                    insert_idx = i
                    break
            lines.insert(insert_idx, entry.rstrip())
            new_content = "\n".join(lines)
        else:
            new_content = f"# Changelog\n\n{entry}"

        with open(changelog_path, "w", encoding="utf-8") as f:
            f.write(new_content)

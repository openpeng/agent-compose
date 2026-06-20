"""
MarketClient - Agent 市场服务客户端 (market.aitboy.cn)

提供:
- 搜索 Agent/Team/Workflow
- 获取详情
- 下载 agent.json
- 本地缓存管理
"""
import json
import tarfile
import hashlib
import io
import os
import time
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple


DEFAULT_MARKET_URL = "https://market.aitboy.cn"

def _default_cache_dir() -> Path:
    """获取默认缓存目录：先尝试 HOME，失败则回退到项目/临时目录"""
    candidates = []
    # 1) 环境变量指定
    env_dir = os.environ.get("AGENT_HUB_CACHE_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    # 2) 标准用户目录
    try:
        candidates.append(Path.home() / ".agent-hub" / "market-cache")
    except Exception:
        pass
    # 3) 项目内缓存
    try:
        candidates.append(Path(__file__).resolve().parent.parent / ".cache" / "market-cache")
    except Exception:
        pass
    # 4) 系统临时目录
    candidates.append(Path(tempfile.gettempdir()) / "agent-hub-cache")

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            # 测试可写
            test_file = path / ".write_test"
            test_file.touch()
            test_file.unlink()
            return path
        except Exception:
            continue
    return Path(tempfile.gettempdir()) / "agent-hub-cache"

DEFAULT_CACHE_DIR = _default_cache_dir()


class MarketClient:
    """Agent 市场服务客户端"""

    def __init__(
        self,
        base_url: str = DEFAULT_MARKET_URL,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        cache_ttl_seconds: int = 3600,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl_seconds
        self.timeout = timeout

    # ---------- HTTP 工具 ----------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.base_url + path
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if qs:
                url += "?" + qs
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "agent-hub/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)

    def _get_raw(self, path: str) -> bytes:
        url = self.base_url + path
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "agent-hub/1.0",
                "Accept": "application/octet-stream, application/json, */*",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    # ---------- 健康检查 ----------

    def health(self) -> Dict[str, Any]:
        return self._get("/api/v1/health")

    # ---------- 搜索 / 列表 ----------

    def search_agents(
        self,
        q: str = "",
        category: str = "",
        tags: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        return self._get(
            "/api/v1/agents",
            {
                "q": q,
                "category": category,
                "tags": tags,
                "page": page,
                "page_size": page_size,
                "sort": "downloads",
                "order": "desc",
            },
        )

    def list_teams(self, q: str = "", page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        return self._get("/api/v1/teams", {"q": q, "page": page, "page_size": page_size})

    def list_workflows(self, q: str = "", page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        return self._get("/api/v1/workflows", {"q": q, "page": page, "page_size": page_size})

    def discover(self) -> Dict[str, Any]:
        return self._get("/api/v1/discover")

    # ---------- 详情 ----------

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        return self._get(f"/api/v1/agents/{urllib.parse.quote(agent_id)}")

    def get_team(self, team_id: str) -> Dict[str, Any]:
        return self._get(f"/api/v1/teams/{urllib.parse.quote(team_id)}")

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return self._get(f"/api/v1/workflows/{urllib.parse.quote(workflow_id)}")

    # ---------- 下载 ----------

    def download_agent_package(self, agent_id: str) -> bytes:
        return self._get_raw(f"/api/v1/agents/{urllib.parse.quote(agent_id)}/download")

    def download_team_package(self, team_id: str) -> bytes:
        return self._get_raw(f"/api/v1/teams/{urllib.parse.quote(team_id)}/download")

    def download_workflow_package(self, workflow_id: str) -> bytes:
        return self._get_raw(f"/api/v1/workflows/{urllib.parse.quote(workflow_id)}/download")

    # ---------- 解析包 ----------

    @staticmethod
    def extract_json_from_package(data: bytes) -> Dict[str, Any]:
        """从 tar.gz 包中提取 JSON 内容（优先取根目录下的 *.json）"""
        try:
            tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
            # 找根目录的 json 文件
            target_member = None
            candidate_members = []
            for m in tar.getmembers():
                if m.isfile() and m.name.endswith(".json"):
                    # 优先根目录的 json
                    if "/" not in m.name:
                        candidate_members.insert(0, m)
                    else:
                        candidate_members.append(m)
            if candidate_members:
                target_member = candidate_members[0]
            if target_member:
                f = tar.extractfile(target_member)
                if f:
                    content = f.read().decode("utf-8")
                    tar.close()
                    return json.loads(content)
            tar.close()
        except Exception:
            pass
        # 回退：尝试直接解析为 JSON
        try:
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"无法从包中提取 JSON: {e}")

    # ---------- 缓存 ----------

    def _cache_path(self, entity_type: str, entity_id: str) -> Path:
        safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in entity_id)
        return self.cache_dir / f"{entity_type}-{safe_id}.json"

    def _cache_meta_path(self, entity_type: str, entity_id: str) -> Path:
        p = self._cache_path(entity_type, entity_id)
        return p.with_suffix(p.suffix + ".meta")

    def _read_cache(self, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        cpath = self._cache_path(entity_type, entity_id)
        mpath = self._cache_meta_path(entity_type, entity_id)
        if not cpath.exists() or not mpath.exists():
            return None
        try:
            meta = json.loads(mpath.read_text(encoding="utf-8"))
            ts = meta.get("cached_at", 0)
            if (time.time() - ts) > self.cache_ttl:
                return None
            return json.loads(cpath.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_cache(self, entity_type: str, entity_id: str, data: Dict[str, Any]) -> None:
        cpath = self._cache_path(entity_type, entity_id)
        mpath = self._cache_meta_path(entity_type, entity_id)
        cpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        mpath.write_text(json.dumps({"cached_at": time.time(), "entity_id": entity_id}, ensure_ascii=False), encoding="utf-8")

    # ---------- 高层 API ----------

    def fetch_agent_json(
        self,
        agent_id: str,
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> Tuple[Dict[str, Any], bool]:
        """
        获取 agent.json 内容。优先从 tar.gz 包中提取（包含最新声明与 MCP 配置），
        如包下载失败则回退到 detail API 的 json_content 字段。

        Returns:
            (agent_json_dict, from_cache_bool)
        """
        cache_key = f"json-{agent_id}"
        if use_cache and not force_refresh:
            cached = self._read_cache("agent", cache_key)
            if cached is not None:
                return cached, True

        # 1) 优先：下载 tar.gz 包，提取真实的 agent.json
        try:
            data = self.download_agent_package(agent_id)
            obj = self.extract_json_from_package(data)
            self._write_cache("agent", cache_key, obj)
            return obj, False
        except Exception:
            pass

        # 2) 回退：使用详情接口的 json_content 字段
        try:
            detail = self.get_agent(agent_id)
            jc = detail.get("json_content") or ""
            if jc:
                obj = json.loads(jc) if isinstance(jc, str) else jc
                self._write_cache("agent", cache_key, obj)
                return obj, False
        except Exception:
            pass

        raise RuntimeError(f"无法从市场获取 agent: {agent_id}")

    def fetch_team_json(self, team_id: str, use_cache: bool = True, force_refresh: bool = False) -> Tuple[Dict[str, Any], bool]:
        """获取 team.json 内容。优先从 tar.gz 包中提取，如失败则回退到 detail API 的 json_content 字段。"""
        cache_key = f"json-{team_id}"
        if use_cache and not force_refresh:
            cached = self._read_cache("team", cache_key)
            if cached is not None:
                return cached, True
        # 优先从 tar.gz 包提取
        try:
            data = self.download_team_package(team_id)
            obj = self.extract_json_from_package(data)
            self._write_cache("team", cache_key, obj)
            return obj, False
        except Exception:
            pass
        # 回退
        detail = self.get_team(team_id)
        jc = detail.get("json_content") or ""
        if jc:
            obj = json.loads(jc) if isinstance(jc, str) else jc
            self._write_cache("team", cache_key, obj)
            return obj, False
        raise RuntimeError(f"无法从市场获取 team: {team_id}")

    def fetch_workflow_json(self, workflow_id: str, use_cache: bool = True, force_refresh: bool = False) -> Tuple[Dict[str, Any], bool]:
        """获取 workflow.json 内容。优先从 tar.gz 包中提取，如失败则回退到 detail API 的 json_content 字段。"""
        cache_key = f"json-{workflow_id}"
        if use_cache and not force_refresh:
            cached = self._read_cache("workflow", cache_key)
            if cached is not None:
                return cached, True
        # 优先从 tar.gz 包提取
        try:
            data = self.download_workflow_package(workflow_id)
            obj = self.extract_json_from_package(data)
            self._write_cache("workflow", cache_key, obj)
            return obj, False
        except Exception:
            pass
        # 回退
        detail = self.get_workflow(workflow_id)
        jc = detail.get("json_content") or ""
        if jc:
            obj = json.loads(jc) if isinstance(jc, str) else jc
            self._write_cache("workflow", cache_key, obj)
            return obj, False
        raise RuntimeError(f"无法从市场获取 workflow: {workflow_id}")

    # ---------- 便捷工具 ----------

    def search_and_list(
        self,
        q: str,
        entity_type: str = "agents",
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """统一搜索接口"""
        if entity_type in ("agents", "agent"):
            result = self.search_agents(q=q, page_size=page_size)
            return result.get("items", [])
        elif entity_type in ("teams", "team"):
            result = self.list_teams(q=q, page_size=page_size)
            return result.get("items", [])
        elif entity_type in ("workflows", "workflow"):
            result = self.list_workflows(q=q, page_size=page_size)
            return result.get("items", [])
        return []

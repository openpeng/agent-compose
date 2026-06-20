import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class RemoteAgentLoader:
    """加载远程 Agent 配置（支持 Market 和 URL 来源，并带缓存）。

    支持的引用格式：
    - market://agent-name                 （从 Market 下载）
    - market://agent-name@1.0.0           （指定版本）
    - https://example.com/agent.json      （从 URL 下载）
    - file:///path/to/agent.json          （从本地文件加载）
    """

    DEFAULT_CACHE_DIR = ".agent_compose_cache"
    DEFAULT_MARKET_URL = "https://market.agent-hub.dev"

    def __init__(self, cache_dir: Optional[str] = None, market_url: Optional[str] = None):
        self.cache_dir = Path(cache_dir or os.path.join(os.path.expanduser("~"), self.DEFAULT_CACHE_DIR))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.market_url = market_url or os.environ.get("AGENT_MARKET_URL", self.DEFAULT_MARKET_URL)
        self._cache: Dict[str, Dict[str, Any]] = {}

    def fetch_from_market(self, ref: str) -> Optional[Dict[str, Any]]:
        cache_key = f"market::{ref}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            agent_name, version = self._parse_market_ref(ref)
        except Exception:
            agent_name, version = ref, None

        file_cache = self._get_cache_path(f"market_{agent_name}_{version or 'latest'}.json")
        if file_cache.exists() and self._is_cache_fresh(file_cache):
            return self._read_cache_file(file_cache)

        data = self._fetch_market_data(agent_name, version)
        if data is not None:
            self._cache[cache_key] = data
            self._write_cache_file(file_cache, data)
        return data

    def fetch_from_url(self, url: str) -> Optional[Dict[str, Any]]:
        cache_key = f"url::{url}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        file_cache = self._get_cache_path(f"url_{self._hash(url)}.json")
        if file_cache.exists() and self._is_cache_fresh(file_cache):
            data = self._read_cache_file(file_cache)
            if data:
                self._cache[cache_key] = data
            return data

        data = self._fetch_url_data(url)
        if data:
            self._cache[cache_key] = data
            self._write_cache_file(file_cache, data)
        return data

    def fetch_from_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        actual_path = file_path.replace("file://", "") if file_path.startswith("file://") else file_path
        if not os.path.exists(actual_path):
            return None
        try:
            with open(actual_path, "r", encoding="utf-8") as f:
                import json as _json
                data = _json.load(f)
                return data
        except Exception:
            return None

    def _parse_market_ref(self, ref: str):
        clean = ref.replace("market://", "")
        if "@" in clean:
            name, version = clean.split("@", 1)
        else:
            name, version = clean, None
        return name, version

    def _fetch_market_data(self, agent_name: str, version: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            import urllib.request
            url = f"{self.market_url}/api/agents/{agent_name}"
            if version:
                url += f"?version={version}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                import json as _json
                return _json.loads(data)
        except Exception:
            return None

    def _fetch_url_data(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            import urllib.request
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                import json as _json
                return _json.loads(data)
        except Exception:
            return None

    def _get_cache_path(self, filename: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
        return self.cache_dir / safe

    def _is_cache_fresh(self, path: Path, hours: int = 24) -> bool:
        try:
            import time
            age = time.time() - os.path.getmtime(str(path))
            return age < hours * 3600
        except Exception:
            return False

    def _read_cache_file(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            import json as _json
            with open(str(path), "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return None

    def _write_cache_file(self, path: Path, data: Dict[str, Any]) -> None:
        try:
            import json as _json
            with open(str(path), "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _hash(self, s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    def resolve(self, ref: str) -> Optional[Dict[str, Any]]:
        if not ref:
            return None
        if ref.startswith("market://"):
            return self.fetch_from_market(ref)
        if ref.startswith("http://") or ref.startswith("https://"):
            return self.fetch_from_url(ref)
        if ref.startswith("file://"):
            return self.fetch_from_file(ref)
        return None

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


def _read_yaml(path: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


class ConfigResolver:
    """解析 YAML 配置中的引用和变量。

    支持的引用语法：
    - $ref: path/to/file.yml[::section]   — 引用 YAML 文件中的某个 section
    - $file: path/to/file.txt              — 引用外部文本文件（如 prompt）
    - ${VAR}                               — 环境变量
    - ${VAR:-default}                      — 环境变量（带默认值）
    """

    def __init__(self, definitions_dir: Optional[str] = None):
        self.definitions_dir = definitions_dir

    def resolve(self, config: Any, base_dir: str) -> Any:
        return self._resolve_value(config, base_dir)

    def _resolve_value(self, value: Any, base_dir: str) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return self._resolve_string(value, base_dir)
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                if k == "$ref":
                    ref_data = self._resolve_ref(v, base_dir)
                    if isinstance(ref_data, dict):
                        merged = self._resolve_value(ref_data, base_dir)
                        return merged
                    return self._resolve_value(ref_data, base_dir)
                if k == "$file":
                    return self._resolve_file(v, base_dir)
                result[k] = self._resolve_value(v, base_dir)
            return result
        if isinstance(value, list):
            return [self._resolve_value(item, base_dir) for item in value]
        return value

    def _resolve_string(self, s: str, base_dir: str) -> str:
        return self._substitute_env_vars(s)

    def _resolve_ref(self, ref: str, base_dir: str) -> Any:
        parts = ref.split("::")
        file_path = parts[0]
        section = parts[1] if len(parts) > 1 else None

        search_paths = [
            os.path.join(base_dir, file_path),
            os.path.join(base_dir, "definitions", file_path),
        ]
        if self.definitions_dir:
            search_paths.append(os.path.join(self.definitions_dir, file_path))

        resolved_path = None
        for p in search_paths:
            if os.path.exists(p):
                resolved_path = p
                break

        if not resolved_path:
            return ref

        data = _read_yaml(resolved_path)
        if data is None:
            return ref

        if section:
            sections = section.split(".")
            current = data
            for sec in sections:
                if isinstance(current, dict) and sec in current:
                    current = current[sec]
                else:
                    return ref
            return current
        return data

    def _resolve_file(self, file_path: str, base_dir: str) -> str:
        search_paths = [
            os.path.join(base_dir, file_path),
            os.path.join(base_dir, "prompts", file_path),
        ]

        resolved_path = None
        for p in search_paths:
            if os.path.exists(p):
                resolved_path = p
                break

        if not resolved_path:
            return file_path

        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return file_path

    def _substitute_env_vars(self, text: str) -> str:
        pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}'

        def replace(match):
            var_name = match.group(1)
            default = match.group(2)
            value = os.environ.get(var_name)
            if value is not None:
                return value
            if default is not None:
                return default
            return match.group(0)

        return re.sub(pattern, replace, text)

    @staticmethod
    def deep_merge(base: Any, override: Any) -> Any:
        if base is None:
            return override
        if override is None:
            return base
        if isinstance(base, dict) and isinstance(override, dict):
            result = dict(base)
            for key, value in override.items():
                if key in result:
                    result[key] = ConfigResolver.deep_merge(result[key], value)
                else:
                    result[key] = value
            return result
        if isinstance(base, list) and isinstance(override, list):
            return list(base) + list(override)
        return override

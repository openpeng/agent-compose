"""
v3.0 兼容层 - 支持旧版 Agent 格式

在 v3.1 运行时中保持对 v3.0 格式的向后兼容性:
- subagents type: "skill" 仍然支持（deprecated）
- skills/ 目录下的 agent.json 仍然支持（deprecated）
- 检测到 v3.0 格式时发出 deprecation warning

用法:
    from agent_compose.compat_v30 import detect_v30_format, migrate_v30_to_v31

    if detect_v30_format(agent_json):
        warnings.warn("Agent uses deprecated v3.0 format. Consider migrating to v3.1.")
        agent_json = migrate_v30_to_v31(agent_json)
"""
import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def detect_v30_format(agent_json: Dict[str, Any]) -> bool:
    """检测是否为 v3.0 格式

    v3.0 特征:
    - schema_version 为 "3.0" 或缺失
    - subagents 中存在 type: "skill" 的项
    - skills/ 目录下使用 agent.json（而非 skill.json）
    """
    schema_version = agent_json.get("schema_version", "")

    # schema_version 为 3.0 或更早
    if schema_version in ("3.0", "2.0", "1.0", ""):
        return True

    # 存在 subagents type: "skill"
    subagents = agent_json.get("subagents", [])
    for sa in subagents:
        if sa.get("type") == "skill":
            return True

    return False


def detect_v30_skills(agent_dir: Path) -> List[Path]:
    """检测 skills 目录下是否存在 v3.0 格式的 agent.json"""
    v30_skills = []
    skills_dir = agent_dir / "skills"

    if not skills_dir.exists():
        return v30_skills

    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue

        agent_json_path = skill_dir / "agent.json"
        skill_json_path = skill_dir / "skill.json"

        if agent_json_path.exists() and not skill_json_path.exists():
            # 只有 agent.json 没有 skill.json，说明是 v3.0 格式
            try:
                with open(agent_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("type") == "skill":
                    v30_skills.append(agent_json_path)
            except (json.JSONDecodeError, IOError):
                pass

    return v30_skills


def migrate_v30_to_v31(agent_json: Dict[str, Any]) -> Dict[str, Any]:
    """将 v3.0 agent.json 迁移为 v3.1 格式（内存中）

    注意：此函数只修改内存中的字典，不写入文件。
    如需持久化迁移，请使用 agent_deploy.migrate 工具。
    """
    new_agent = dict(agent_json)

    # 更新 schema_version
    new_agent["schema_version"] = "3.1"

    # 迁移 subagents type: "skill" -> skills 数组
    subagents = new_agent.get("subagents", [])
    skills_refs = []
    new_subagents = []

    for sa in subagents:
        if sa.get("type") == "skill":
            skill_name = sa.get("name", "")
            skill_path = sa.get("path", "")

            if skill_name:
                skills_refs.append({
                    "name": skill_name,
                    "path": skill_path or f"skills/{skill_name}/skill.json",
                    "source": "local"
                })
        else:
            new_subagents.append(sa)

    if skills_refs:
        # 合并到现有 skills 数组
        existing_skills = new_agent.get("skills", [])
        if existing_skills:
            existing_names = {s.get("name", "") for s in existing_skills}
            for ref in skills_refs:
                if ref["name"] not in existing_names:
                    existing_skills.append(ref)
        else:
            new_agent["skills"] = skills_refs

        # 更新 subagents
        if new_subagents:
            new_agent["subagents"] = new_subagents
        else:
            # 如果 subagents 为空，删除该字段
            if "subagents" in new_agent:
                del new_agent["subagents"]

    return new_agent


def load_skill_v30(skill_dir: Path) -> Dict[str, Any]:
    """从 v3.0 格式的 skill 目录加载 skill 信息

    兼容读取 skills/<name>/agent.json (type: "skill")
    """
    agent_json_path = skill_dir / "agent.json"

    if not agent_json_path.exists():
        raise FileNotFoundError(f"No agent.json found in {skill_dir}")

    with open(agent_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 转换为 v3.1 格式的 skill 信息
    identity = data.get("identity", data)

    skill_info = {
        "ref": identity.get("name", skill_dir.name),
        "version": identity.get("version", "1.0.0"),
        "name": identity.get("name", skill_dir.name),
        "display_name": identity.get("display_name", identity.get("name", "")),
        "description": identity.get("description", ""),
        "content": "",
        "capabilities": data.get("capabilities", []),
        "source": "local",
        "path": str(skill_dir / "agent.json"),
        "deprecated": True,  # 标记为 deprecated v3.0 格式
    }

    # 加载 instructions 内容
    instructions = data.get("instructions", {})
    if instructions.get("source") == "inline":
        skill_info["content"] = instructions.get("content", "")
    elif instructions.get("source") == "file":
        inst_file = instructions.get("file", "SKILL.md")
        inst_path = skill_dir / inst_file
        if inst_path.exists():
            with open(inst_path, "r", encoding="utf-8") as f:
                skill_info["content"] = f.read()

    # 如果没有 instructions，尝试直接读取 SKILL.md
    if not skill_info["content"]:
        skill_md_path = skill_dir / "SKILL.md"
        if skill_md_path.exists():
            with open(skill_md_path, "r", encoding="utf-8") as f:
                skill_info["content"] = f.read()

    return skill_info


def compat_load_agent(agent_json: Dict[str, Any], agent_dir: Optional[Path] = None) -> Dict[str, Any]:
    """兼容加载 Agent，自动检测并迁移 v3.0 格式

    这是 agent-compose 运行时应该调用的入口函数。
    """
    if detect_v30_format(agent_json):
        warnings.warn(
            "Agent uses deprecated v3.0 format (subagents type: 'skill' detected). "
            "Consider migrating to v3.1 with: agent-deploy migrate --from 3.0 --to 3.1 <path>",
            DeprecationWarning,
            stacklevel=2,
        )
        agent_json = migrate_v30_to_v31(agent_json)

    # 检查 skills 目录下的 v3.0 格式
    if agent_dir:
        v30_skills = detect_v30_skills(agent_dir)
        if v30_skills:
            warnings.warn(
                f"Found {len(v30_skills)} skill(s) using deprecated v3.0 format (agent.json). "
                f"Consider migrating to skill.json format.",
                DeprecationWarning,
                stacklevel=2,
            )

    return agent_json

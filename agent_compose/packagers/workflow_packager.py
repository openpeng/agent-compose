import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class WorkflowPackager:
    """将 Workflow 配置打包为标准 workflow.json 格式。"""

    def __init__(self, output_dir: str = "dist"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def package(self, name: str, workflow_config: Dict[str, Any], raw_config: Optional[Dict[str, Any]] = None) -> Path:
        deploy = (raw_config or {}).get("deploy", {}) or {}

        workflow_json = self._build_workflow_json(name, workflow_config, deploy)

        output_path = self.output_dir / f"{name}"
        output_path.mkdir(parents=True, exist_ok=True)

        json_path = output_path / "workflow.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(workflow_json, f, indent=2, ensure_ascii=False, default=str)

        return output_path

    def _build_workflow_json(self, name: str, workflow_config: Dict[str, Any], deploy: Dict[str, Any]) -> Dict[str, Any]:
        steps = self._collect_steps(workflow_config)
        deps = self._resolve_dependencies(workflow_config)
        return {
            "schema_version": "1.0.0",
            "type": "workflow",
            "name": deploy.get("name", name),
            "version": deploy.get("version", "1.0.0"),
            "description": workflow_config.get("description", ""),
            "author": deploy.get("author", ""),
            "category": deploy.get("category", "pipeline"),
            "tags": deploy.get("tags", []),
            "entry_task": workflow_config.get("entry_task", ""),
            "inputs": workflow_config.get("inputs", []),
            "outputs": workflow_config.get("outputs", []),
            "steps": steps,
            "dependencies": deps,
        }

    def _collect_steps(self, workflow_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_steps = workflow_config.get("steps", []) or []
        return [s for s in raw_steps if isinstance(s, dict)]

    def _resolve_dependencies(self, workflow_config: Dict[str, Any]) -> Dict[str, Any]:
        agent_refs = set()
        team_refs = set()

        steps = workflow_config.get("steps", []) or []
        for step in steps:
            if isinstance(step, dict):
                step_type = step.get("type", "agent")
                if step_type == "agent":
                    agent_ref = step.get("agent") or step.get("agent_ref")
                    if agent_ref:
                        agent_refs.add(str(agent_ref))
                elif step_type == "team":
                    team_ref = step.get("team") or step.get("team_ref")
                    if team_ref:
                        team_refs.add(str(team_ref))

                cases = step.get("cases") or {}
                if isinstance(cases, dict):
                    for case_steps in cases.values():
                        if isinstance(case_steps, list):
                            for cs in case_steps:
                                if isinstance(cs, dict):
                                    ar = cs.get("agent") or cs.get("agent_ref")
                                    if ar:
                                        agent_refs.add(str(ar))
                                    tr = cs.get("team") or cs.get("team_ref")
                                    if tr:
                                        team_refs.add(str(tr))

        return {
            "agents": sorted(list(agent_refs)),
            "teams": sorted(list(team_refs)),
        }

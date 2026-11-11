"""LLM 提供方选择（仅用于 P7 对话生成信息源）。

优先级（高 → 低）：
1. config/llm.yaml 中 providers_file 指定的文件（相对于 info_hub 根目录或绝对路径）
2. info_hub 根目录下的 providers.json（本项目专属）
3. 主项目的 providers.json（通过主项目 load_config 的外部项目回退逻辑获得）

主项目的 providers 结构与 agent_config 完全沿用，本项目不维护自己的 provider 格式。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from core.settings import Settings

LLM_CONFIG_NAME = "llm.yaml"


class LLMUnavailable(RuntimeError):
    """没有可用的 LLM 配置，或主项目不可导入。"""


@dataclass
class ProviderChoice:
    source: str            # "explicit" | "project" | "main" | "none"
    path: Optional[str]    # 实际使用的 providers 文件路径（none 时为 None）
    label: str             # 面向用户的说明


def main_project_root(info_hub_root: Path) -> Optional[Path]:
    """info_hub 位于 <主项目>/external_projects/info_hub 时，返回主项目根。"""
    candidate = info_hub_root.resolve().parents[1]
    if (candidate / "src" / "mini_agent").exists():
        return candidate
    return None


def _read_llm_yaml(root: Path) -> dict[str, Any]:
    path = root / "config" / LLM_CONFIG_NAME
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise LLMUnavailable("config/llm.yaml 顶层必须是映射")
    return data


def resolve_provider(settings: Settings) -> ProviderChoice:
    root = settings.paths.root
    cfg = _read_llm_yaml(root)
    explicit = cfg.get("providers_file")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = (root / p).resolve()
        if not p.exists():
            raise LLMUnavailable(f"config/llm.yaml 指定的 providers 文件不存在: {p}")
        return ProviderChoice("explicit", str(p), f"使用 config/llm.yaml 指定的配置：{p}")
    own = root / "providers.json"
    if own.exists():
        return ProviderChoice("project", str(own), f"使用本项目的 providers.json：{own}")
    main = main_project_root(root)
    if main and (main / "providers.json").exists():
        return ProviderChoice("main", str(main / "providers.json"),
                              "使用主项目的 providers.json（本项目未单独配置）")
    return ProviderChoice("none", None, "未找到 providers 配置：请在主项目或本项目中提供 providers.json")


def _ensure_main_importable(root: Path) -> None:
    main = main_project_root(root)
    if main is None:
        return
    src = str(main / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    os.environ.setdefault("MINI_AGENT_MAIN_PROJECT_ROOT", str(main))


class HelperLLM:
    """对主项目 LLMHelper 的薄封装，只暴露 ask(prompt, system) -> str。"""

    def __init__(self, helper: Any) -> None:
        self._helper = helper

    def ask(self, prompt: str, system: str = "") -> str:
        return self._helper.ask(prompt, system=system)


def build_llm(settings: Settings) -> HelperLLM:
    choice = resolve_provider(settings)
    if choice.source == "none":
        raise LLMUnavailable(choice.label)
    root = settings.paths.root
    _ensure_main_importable(root)
    try:
        from mini_agent.config import load_config
        from mini_agent.llm.service import LLMHelper
    except ImportError as exc:
        raise LLMUnavailable(f"无法导入主项目 mini_agent：{exc}") from exc
    app_cfg = load_config(
        project_root=root,
        verbose=False,
        sandbox=True,
        auto_approve=True,
        providers_config_file=Path(choice.path) if choice.path else None,
    )
    return HelperLLM(LLMHelper.from_config(app_cfg))


def provider_status(settings: Settings) -> dict[str, Any]:
    choice = resolve_provider(settings)
    return {"source": choice.source, "path": choice.path, "label": choice.label}

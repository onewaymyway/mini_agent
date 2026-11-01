"""providers/skill_bridge.py — 通过 mini_agent 的 Agnes skill 调用图像与视频生成。

为什么走 skill：skill（.claude/skills/gen_image_with_text、gen_video_with_text）已经包含
  * API key 解析：AGNES_API_KEYS → 项目根目录 providers.json（providers.agnes.api_keys，
    或 llm_fallback_chain 中 provider=agnes 的 api_keys）→ AGNES_API_KEY；
  * 限流自动切换 key、非重试错误识别、轮询与下载；
因此本项目不再自行解析 key 或拼接 HTTP 请求，只做参数转换与结果包装。

skill 的查找：环境变量 NOVEL_TO_VIDEO_SKILLS_DIR 优先；否则从本文件向上查找 .claude/skills。
两个 skill 各自带有 agnes_tools.py 与 agnes_key_pool.py（模块名相同），这里用不同的模块名分别加载，
避免相互覆盖。key 池实现两份完全相同（已在 skill 中校验），图像与视频共用图像 skill 的加载结果。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional

SKILLS_ENV = "NOVEL_TO_VIDEO_SKILLS_DIR"
IMAGE_SKILL = "gen_image_with_text"
VIDEO_SKILL = "gen_video_with_text"
_MARK = Path(".claude") / "skills" / IMAGE_SKILL / "agnes_tools.py"


class SkillUnavailable(RuntimeError):
    pass


def find_skills_dir() -> Path:
    env = os.environ.get(SKILLS_ENV, "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if (p / IMAGE_SKILL / "agnes_tools.py").is_file():
            return p
        raise SkillUnavailable(f"{SKILLS_ENV} 指向的目录中找不到 {IMAGE_SKILL}：{p}")
    here = Path(__file__).resolve()
    for base in [here, *here.parents]:
        cand = base / _MARK
        if cand.is_file():
            return cand.parents[1]  # .claude/skills
    raise SkillUnavailable("找不到 mini_agent 的 Agnes skill（.claude/skills）。请设置环境变量 "
                           f"{SKILLS_ENV}，或将本项目放在 mini_agent 仓库内")


def _load(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SkillUnavailable(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_CACHE: Dict[str, Any] = {}


def modules() -> Dict[str, ModuleType]:
    """加载并缓存两个 skill 的模块（key 池、图像客户端、视频客户端）。"""
    if _CACHE:
        return _CACHE
    skills = find_skills_dir()
    img_dir = skills / IMAGE_SKILL
    vid_dir = skills / VIDEO_SKILL
    # agnes_tools.py 内部通过 `from agnes_key_pool import ...` 引用 key 池，
    # 因此先以标准名加载图像 skill 的 key 池，再加载两个 agnes_tools。
    kp = _load("agnes_key_pool", img_dir / "agnes_key_pool.py")
    img = _load("n2v_skill_image_tools", img_dir / "agnes_tools.py")
    vid = _load("n2v_skill_video_tools", vid_dir / "agnes_tools.py")
    _CACHE.update(key_pool=kp, image=img, video=vid, skills_dir=skills, image_dir=img_dir)
    return _CACHE


def key_config() -> Dict[str, Any]:
    """返回 key 来源与数量（不含 key 内容），用于界面与诊断。"""
    m = modules()
    cfg = m["key_pool"].load_agnes_config(str(m["image_dir"]))
    src = "环境变量 AGNES_API_KEYS" if os.environ.get("AGNES_API_KEYS", "").strip() else (
        "providers.json" if cfg["keys"] and not os.environ.get("AGNES_API_KEY") else
        "环境变量 AGNES_API_KEY" if cfg["keys"] else "未找到")
    return {"count": len(cfg["keys"]), "source": src, "rotation": cfg["rotation"],
            "skills_dir": str(m["skills_dir"])}


def build_pool() -> Optional[Any]:
    """按 skill 的规则构建 key 池；没有任何 key 时返回 None。"""
    m = modules()
    return m["key_pool"].build_key_pool(str(m["image_dir"]))


def providers_json_path() -> Optional[Path]:
    """skill 实际会读取的 providers.json 路径（从 skill 目录向上查找）；没有则 None。"""
    m = modules()
    return m["key_pool"]._find_providers_json(m["image_dir"])

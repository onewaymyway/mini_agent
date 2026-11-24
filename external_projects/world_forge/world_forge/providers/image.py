"""providers/image.py — 图像生成封装（接口与 novel_to_video 的 ImageProvider 保持一致）。

ImageProvider.generate() 永不抛异常，统一返回 ImageResult：
  ok=True  → 已写入 save_path
  ok=False → error 为可读原因

实现：
  * AgnesSkillImageProvider：经 mini_agent 的 gen_image_with_text skill 调用真实模型。
    key 解析沿用 skill 规则（AGNES_API_KEYS → providers.json → AGNES_API_KEY），本项目不解析 key。
    多参考图（角色立绘）经 edit_image 发送，最多 3 张，长边缩至 1024。
  * StubImageProvider：PIL 占位图，用于测试与无 key 的离线演示。
"""

from __future__ import annotations

import base64
import importlib.util
import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Optional, Sequence

from ..paths import PROJECT_ROOT, mini_agent_root

MAX_REFERENCE_IMAGES = 3
REFERENCE_MAX_EDGE = 1024
SKILL_NAME = "gen_image_with_text"


@dataclass
class ImageResult:
    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None
    ref_count: int = 0


class ImageProvider:
    name = "base"
    is_stub = False

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 reference_images: Optional[Sequence[Path]] = None) -> ImageResult:  # pragma: no cover
        raise NotImplementedError


def encode_reference(path: Path) -> str:
    """读取参考图，长边超过上限时等比缩小，返回 PNG data URI。"""
    from PIL import Image

    with Image.open(path) as im:
        im.load()
        img = im.convert("RGB")
    long_edge = max(img.size)
    if long_edge > REFERENCE_MAX_EDGE:
        scale = REFERENCE_MAX_EDGE / long_edge
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _find_skill_dir() -> Optional[Path]:
    env = os.environ.get("WORLD_FORGE_SKILLS_DIR", "").strip()
    cands = [Path(env)] if env else []
    root = mini_agent_root()
    if root is not None:
        cands.append(root / ".claude" / "skills")
    cands.append(PROJECT_ROOT.parent.parent / ".claude" / "skills")
    for c in cands:
        if (c / SKILL_NAME / "agnes_tools.py").is_file():
            return c
    return None


_MODS: Dict[str, ModuleType] = {}


def _load_skill_modules() -> Dict[str, ModuleType]:
    """加载 gen_image_with_text skill 的 key 池与图像客户端（只加载一次）。"""
    if _MODS:
        return _MODS
    skills = _find_skill_dir()
    if skills is None:
        raise RuntimeError("找不到 gen_image_with_text skill（可设置 WORLD_FORGE_SKILLS_DIR）")
    img_dir = skills / SKILL_NAME
    spec = importlib.util.spec_from_file_location("wf_agnes_key_pool", img_dir / "agnes_key_pool.py")
    kp = importlib.util.module_from_spec(spec)
    sys.modules["wf_agnes_key_pool"] = kp
    spec.loader.exec_module(kp)
    # skill 内部按标准名 agnes_key_pool 引用 key 池，这里补一个别名
    sys.modules.setdefault("agnes_key_pool", kp)
    spec2 = importlib.util.spec_from_file_location("wf_skill_image_tools", img_dir / "agnes_tools.py")
    img = importlib.util.module_from_spec(spec2)
    sys.modules["wf_skill_image_tools"] = img
    spec2.loader.exec_module(img)
    _MODS.update(key_pool=kp, image=img, image_dir=img_dir)
    return _MODS


class SkillUnavailable(RuntimeError):
    pass


class AgnesSkillImageProvider(ImageProvider):
    name = "agnes"

    def __init__(self) -> None:
        try:
            m = _load_skill_modules()
        except Exception as exc:  # noqa: BLE001
            raise SkillUnavailable(str(exc)) from exc
        pool = m["key_pool"].build_key_pool(str(m["image_dir"]))
        if pool is None:
            raise SkillUnavailable("未找到可用的 Agnes API key（AGNES_API_KEYS / providers.json / AGNES_API_KEY）")
        self.client = m["image"].AgnesImageClient(key_pool=pool, use_system_proxy=False)

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 reference_images: Optional[Sequence[Path]] = None) -> ImageResult:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        refs: List[str] = []
        refs_paths = [Path(p) for p in (reference_images or []) if Path(p).is_file()][:MAX_REFERENCE_IMAGES]
        try:
            refs = [encode_reference(p) for p in refs_paths]
        except Exception as exc:  # noqa: BLE001 - 损坏参考图不应中断整批
            return ImageResult(False, error=f"参考图无法读取：{exc}")
        try:
            if refs:
                result = self.client.edit_image(image_url=refs, prompt=prompt, size="1K", ratio=ratio,
                                                save_path=str(save_path))
            else:
                result = self.client.generate_image(prompt=prompt, size="1K", ratio=ratio,
                                                    save_path=str(save_path))
        except Exception as exc:  # noqa: BLE001
            return ImageResult(False, error=f"{type(exc).__name__}: {exc}", ref_count=len(refs))
        if result.get("success") and save_path.is_file():
            return ImageResult(True, path=str(save_path), ref_count=len(refs))
        err = result.get("error")
        return ImageResult(False, error=str(err)[:300] if err else "未知错误", ref_count=len(refs))


class StubImageProvider(ImageProvider):
    name = "stub"
    is_stub = True

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 reference_images: Optional[Sequence[Path]] = None) -> ImageResult:
        from PIL import Image, ImageDraw

        names = [Path(p).name for p in (reference_images or [])][:MAX_REFERENCE_IMAGES]
        self.calls.append({"prompt": prompt, "refs": names, "ratio": ratio})
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (768, 1024), (38, 44, 56))
        draw = ImageDraw.Draw(img)
        draw.rectangle([24, 24, 744, 1000], outline=(120, 140, 170), width=4)
        ascii_prompt = prompt.encode("ascii", "ignore").decode("ascii")
        lines = ["STUB PANEL", ""] + [ascii_prompt[i:i + 44] for i in range(0, min(len(ascii_prompt), 220), 44)]
        for i, line in enumerate(lines):
            draw.text((48, 60 + i * 28), line, fill=(230, 230, 230))
        img.save(save_path, format="PNG")
        return ImageResult(True, path=str(save_path), ref_count=len(names))

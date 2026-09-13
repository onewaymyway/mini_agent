"""common.py — novel-video-studio 各脚本共用的工具函数/常量。

收敛此前分散在 5 个脚本里各自复制的同一段逻辑（`next_doc/
novel_video_studio_fix_plan_v2.md` 问题3），以及问题1新增的
ffmpeg/ffprobe/中文字体自动探测逻辑，避免以后改一处忘了改另一处。

用法（和现有 `from tts_engine import ...`/`from voice_mapping import ...`
的引用方式保持一致，脚本与 common.py 同级，不引入新的打包/路径约定）：

    from common import macro_scene_dir_name, MIN_SEC, MAX_SEC, ROUGH_CHARS_PER_SEC
    from common import resolve_ffmpeg, resolve_ffprobe, resolve_font_path

已知不一致（如实记录，不强行统一）：
    `--macro-id` 这个 CLI 参数目前两种风格并存——`check_clips_v2.py`/
    `check_assets_and_audio_v2.py` 是 `nargs="*"` 可传多个，
    `check_scene_detail.py`/`synthesize_scene_audio.py`/
    `compose_macro_scene.py` 是单值。这属于行为变更，风险和收益需要
    单独评审，本次重构不改，新增脚本时按实际需要在两种风格里选一种，
    不要凭感觉现造第三种风格。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# 目录命名
# ---------------------------------------------------------------------------

def macro_scene_dir_name(macro_id: str) -> str:
    """把 `macro_01` 这类 id 转成对应的工作目录名 `macro_scene_01`。

    统一命名，替换各脚本里此前并存的 `_macro_dir_name`/
    `_macro_scene_dir_name` 两种不同的函数名（逻辑完全一致）。
    """
    if macro_id.startswith("macro_"):
        return f"macro_scene_{macro_id[len('macro_'):]}"
    return f"macro_scene_{macro_id}"


# ---------------------------------------------------------------------------
# 时长相关常量
# ---------------------------------------------------------------------------

# 视频生成接口的硬性时长范围（clip 秒数）。此前在
# generate_scene_videos_v2.py/check_scene_detail.py 里各自定义了一份
# （命名风格还不一样），本次统一到这里，各处改成从这里导入。
MIN_SEC = 4
MAX_SEC = 12

# 中文口播粗估语速（字/秒）。这是一个非常粗糙的经验值，仅用于规划阶段
# /配音后复核阶段的"提前预警"，不代表真实 TTS 配音时长——真实时长受
# 标点停顿、语气词拉长、TTS 引擎本身语速设置等因素影响，只能等真实
# 配音后才能确定。此前在 check_scene_detail.py 和
# check_assets_and_audio_v2.py 里各自定义了一份，数值一致但没有机制
# 保证同步，本次统一到这里。
ROUGH_CHARS_PER_SEC = 4.5


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe / 字体自动探测
# （next_doc/novel_video_studio_fix_plan_v2.md 问题1）
# ---------------------------------------------------------------------------

# 常见 conda/mamba 环境相关关键词，用于在有多个 conda 环境时优先选择
# 和本项目相关的环境（而不是随机选中第一个匹配项）。
_CONDA_ENV_NAME_HINTS = ("novel", "mv", "video")


def _conda_env_bin_dir(env_prefix: str) -> Path:
    """给定一个 conda 环境的 prefix 路径，返回其可执行文件所在目录。"""
    if os.name == "nt":
        return Path(env_prefix) / "Library" / "bin"
    return Path(env_prefix) / "bin"


def _iter_conda_env_prefixes():
    """依次产出：当前激活环境优先，其次是名字命中项目关键词的环境，
    最后是其它环境——调用方按顺序探测，找到第一个存在目标文件的即可。
    """
    seen = set()

    current = os.environ.get("CONDA_PREFIX")
    if current and current not in seen:
        seen.add(current)
        yield current

    conda_exe = shutil.which("conda") or shutil.which("mamba")
    if not conda_exe:
        return
    try:
        r = subprocess.run(
            [conda_exe, "env", "list", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return
        import json
        envs = json.loads(r.stdout).get("envs", []) or []
    except Exception:
        return

    hinted = [e for e in envs if any(h in Path(e).name.lower() for h in _CONDA_ENV_NAME_HINTS)]
    rest = [e for e in envs if e not in hinted]
    for e in hinted + rest:
        if e not in seen:
            seen.add(e)
            yield e


def _resolve_media_bin(env_var: str, exe_name: str) -> tuple[str | None, list[str]]:
    """按优先级查找 ffmpeg/ffprobe 可执行文件。

    返回 (找到的路径或 None, 已尝试过的查找方式说明列表)——找不到时
    调用方用这份说明列表拼出"明确报错"，而不是静默 fallback 成一条
    不存在的硬编码路径。
    """
    tried: list[str] = []
    win_exe = f"{exe_name}.exe"

    env_path = os.environ.get(env_var)
    tried.append(f"环境变量 {env_var}" + (f"（={env_path}）" if env_path else "（未设置）"))
    if env_path and Path(env_path).exists():
        return env_path, tried

    try:
        import imageio_ffmpeg
        imgio_ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        if exe_name == "ffmpeg":
            candidate = imgio_ffmpeg_path
        else:
            # imageio_ffmpeg 只打包 ffmpeg，ffprobe 按同目录同名规则猜测
            imgio_dir = Path(imgio_ffmpeg_path).parent
            imgio_name = Path(imgio_ffmpeg_path).name.replace("ffmpeg", "ffprobe")
            candidate = str(imgio_dir / imgio_name)
        tried.append(f"imageio_ffmpeg（{candidate}）")
        if Path(candidate).exists():
            return candidate, tried
    except ImportError:
        tried.append("imageio_ffmpeg（未安装）")

    conda_prefixes_checked = []
    for prefix in _iter_conda_env_prefixes():
        bin_dir = _conda_env_bin_dir(prefix)
        candidate = bin_dir / (win_exe if os.name == "nt" else exe_name)
        conda_prefixes_checked.append(str(candidate))
        if candidate.exists():
            tried.append(f"conda 环境自动探测（命中 {candidate}）")
            return str(candidate), tried
    if conda_prefixes_checked:
        tried.append("conda 环境自动探测（已检查: " + "; ".join(conda_prefixes_checked) + "）")
    else:
        tried.append("conda 环境自动探测（未找到 conda/mamba 命令或没有任何环境）")

    found = shutil.which(exe_name)
    tried.append(f"系统 PATH（shutil.which('{exe_name}')" + (f" -> {found}）" if found else "，未找到）"))
    if found:
        return found, tried

    return None, tried


def resolve_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件，找不到时抛出带具体原因的异常。

    查找优先级：环境变量 NOVEL_FFMPEG_PATH > imageio_ffmpeg（pip 包自带
    的独立二进制，最可靠的跨平台方式）> conda 环境自动探测（当前激活
    环境优先，其次是名字含 novel/mv/video 的环境，再次是其它环境）>
    系统 PATH。
    """
    found, tried = _resolve_media_bin("NOVEL_FFMPEG_PATH", "ffmpeg")
    if found:
        return found
    raise RuntimeError(
        "找不到 ffmpeg 可执行文件，已尝试以下方式均未命中：\n  - "
        + "\n  - ".join(tried)
        + "\n请安装 ffmpeg（如 `pip install imageio-ffmpeg` 或用 conda 安装到某个环境），"
        "或设置环境变量 NOVEL_FFMPEG_PATH 指向具体的 ffmpeg 可执行文件。"
    )


def resolve_ffprobe() -> str:
    """查找 ffprobe 可执行文件，找不到时抛出带具体原因的异常。

    查找优先级同 `resolve_ffmpeg()`，环境变量换成 NOVEL_FFPROBE_PATH。
    """
    found, tried = _resolve_media_bin("NOVEL_FFPROBE_PATH", "ffprobe")
    if found:
        return found
    raise RuntimeError(
        "找不到 ffprobe 可执行文件，已尝试以下方式均未命中：\n  - "
        + "\n  - ".join(tried)
        + "\n请安装 ffmpeg 套件（通常和 ffmpeg 一起装，如 `pip install imageio-ffmpeg` "
        "或用 conda 安装到某个环境），或设置环境变量 NOVEL_FFPROBE_PATH 指向具体的 "
        "ffprobe 可执行文件。"
    )


# 跨平台常见中文字体候选路径，按平台常见安装位置列举。
_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",     # 黑体
    # Linux（常见发行版直接可装）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # 文泉驿微米黑
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",    # 文泉驿正黑
    # macOS
    "/System/Library/Fonts/PingFang.ttc",   # 苹方
    "/Library/Fonts/Arial Unicode.ttf",
]


def resolve_font_path(cli_font_path: str | None = None) -> str:
    """查找中文字幕字体文件路径，找不到时抛出带具体原因的异常。

    查找优先级：`--font-path` CLI 参数（调用方传入，最高优先级，用户
    显式指定时永远尊重）> 环境变量 NOVEL_FONT_PATH（次高优先级）>
    跨平台常见字体候选列表 > 都找不到时明确报错，不再允许静默使用一个
    不存在的路径。
    """
    if cli_font_path:
        if not Path(cli_font_path).exists():
            raise RuntimeError(
                f"--font-path 指定的字体文件不存在: {cli_font_path}\n"
                "请确认路径正确，或去掉该参数改用自动探测/NOVEL_FONT_PATH。"
            )
        return cli_font_path

    env_path = os.environ.get("NOVEL_FONT_PATH")
    if env_path:
        if not Path(env_path).exists():
            raise RuntimeError(
                f"环境变量 NOVEL_FONT_PATH 指定的字体文件不存在: {env_path}\n"
                "请确认路径正确，或取消设置该环境变量改用自动探测。"
            )
        return env_path

    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate

    raise RuntimeError(
        "以上候选路径均未找到中文字体，请安装一个中文字体或设置 "
        "NOVEL_FONT_PATH 环境变量指向具体字体文件（也可以用 --font-path "
        "参数显式指定）。已尝试的候选路径：\n  - " + "\n  - ".join(_FONT_CANDIDATES)
    )

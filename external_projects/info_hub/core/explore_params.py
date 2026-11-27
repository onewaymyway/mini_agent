"""P19-1：自动探索 / 复查参数的统一来源与看板覆盖层。

优先级（高 → 低）：
  1. 看板保存的覆盖值：data/state/runtime_config.json 的 explore_params.<分组>.<键>
  2. config/discovery.yaml 中的同名字段（用户手改的基线）
  3. 本模块注册表中的默认值

每个参数都在 SCHEMA 中登记类型、取值范围与中文说明。保存时逐项校验，任何一项不合法都整体拒绝，
不会写入部分结果。看板只需调用 describe / save / reset 三个函数即可，不需要知道文件结构。

注意：只改 discovery.yaml 不会覆盖已在看板保存过的值；需要回到 yaml 时，在看板点“恢复默认”。
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import yaml

from core.settings import load_settings, read_runtime_config
from core.store import atomic_write_json, file_lock

KEY = "explore_params"
DISCOVERY_FILE = "discovery.yaml"
_WINDOW_RE = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off", "")


def _spec(key: str, label: str, kind: str, default: Any, *, lo: Any = None, hi: Any = None,
          choices: tuple[str, ...] = (), hint: str = "", group: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "default": default, "min": lo, "max": hi,
            "choices": list(choices), "hint": hint, "group": group}


SCHEMA: dict[str, list[dict[str, Any]]] = {
    "autoexplore": [
        _spec("daily", "每天探索站点数", "int", 20, lo=0, hi=200, group="额度",
              hint="每天最多探索的站点数，0 表示停止新探索"),
        _spec("min_interval_hours", "同站最小探索间隔（小时）", "float", 1, lo=0, hi=720, group="额度"),
        _spec("render_daily", "每天渲染次数上限", "int", 40, lo=0, hi=1000, group="额度",
              hint="有头浏览器渲染的每日总次数"),
        _spec("host_daily_pages", "单站每天页面访问上限", "int", 30, lo=1, hi=1000, group="额度"),
        _spec("frontier_batch", "每轮从队列取出的页数", "int", 10, lo=1, hi=200, group="队列"),
        _spec("frontier_max_queued", "待访问队列容量", "int", 500, lo=10, hi=100000, group="队列"),
        _spec("page_links_cap", "每页最多入队链接数", "int", 20, lo=1, hi=200, group="队列"),
        _spec("cross_site", "允许跨站入队", "bool", False, group="队列",
              hint="开启后，已接受来源页面中的外链可以进入队列"),
        _spec("max_listing_pages", "每站最多探索栏目页数", "int", 4, lo=0, hi=50, group="站内"),
        _spec("max_feeds_per_site", "每站最多登记订阅入口数", "int", 3, lo=0, hi=20, group="站内"),
        _spec("max_apis_per_site", "每站最多登记接口数", "int", 3, lo=0, hi=20, group="站内"),
        _spec("harvest_limit", "线索站点数上限", "int", 30, lo=0, hi=500, group="线索"),
        _spec("min_items", "预览最少条目数", "int", 3, lo=1, hi=100, group="门槛"),
        _spec("min_completeness", "预览字段完整度下限", "float", 0.8, lo=0, hi=1, group="门槛",
              hint="标题与链接都有的条目比例"),
        _spec("render", "渲染方式", "choice", "off", choices=("off", "headed"), group="渲染",
              hint="off 不使用浏览器；headed 在页面无列表时用有头浏览器渲染"),
        _spec("background", "后台自动探索", "bool", False, group="后台",
              hint="由 tick 调用；开启后仍可随时暂停"),
        _spec("background_every_hours", "后台两次运行的最小间隔（小时）", "float", 6, lo=0.1, hi=168, group="后台"),
        _spec("background_window", "后台运行窗口（HH:MM-HH:MM）", "text", "00:00-24:00", group="后台",
              hint="如 22:00-06:00，支持跨午夜"),
        _spec("low_yield_checks", "判定低产出所需的探索次数", "int", 5, lo=1, hi=100, group="产出"),
        _spec("low_yield_threshold", "低产出产出分阈值", "float", 0.1, lo=0, hi=1, group="产出"),
        _spec("recheck_days", "低产出站点复查间隔（天）", "int", 30, lo=1, hi=365, group="产出"),
    ],
    "review": [
        _spec("enabled", "开启周期复查", "bool", False, group="总开关",
              hint="由 tick 每天最多运行一轮"),
        _spec("every_days", "正常源复查间隔（天）", "int", 30, lo=1, hi=365, group="节奏"),
        _spec("failing_recheck_days", "失败源复查间隔（天）", "int", 7, lo=1, hi=365, group="节奏"),
        _spec("daily", "每轮最多复查源数", "int", 10, lo=0, hi=100, group="节奏"),
        _spec("retire_after", "连续失败多少次建议退役", "int", 3, lo=1, hi=50, group="退役"),
        _spec("auto_retire", "自动停用（不只是建议）", "bool", False, group="退役"),
        _spec("max_alternatives", "每次最多生成替代候选数", "int", 3, lo=0, hi=10, group="替代"),
        _spec("redraft", "允许模型重建失效页面草稿", "bool", True, group="替代"),
    ],
}

SECTION_TITLES = {"autoexplore": "自动探索", "review": "复查与退役"}


def _specs(section: str) -> dict[str, dict[str, Any]]:
    if section not in SCHEMA:
        raise ValueError(f"未知参数分组：{section}")
    return {s["key"]: s for s in SCHEMA[section]}


def defaults(section: str) -> dict[str, Any]:
    return {k: s["default"] for k, s in _specs(section).items()}


def coerce(spec: dict[str, Any], value: Any) -> Any:
    """按注册表校验并转换一个值；不合法抛出 ValueError（中文说明）。"""
    label = spec["label"]
    kind = spec["kind"]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"{label} 必须是开或关")
    if kind == "int":
        if isinstance(value, bool):
            raise ValueError(f"{label} 必须是整数")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            value = int(value.strip())
        if not isinstance(value, int):
            raise ValueError(f"{label} 必须是整数")
        result: Any = value
    elif kind == "float":
        if isinstance(value, bool):
            raise ValueError(f"{label} 必须是数字")
        try:
            result = float(value.strip()) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} 必须是数字")
        if not math.isfinite(result):
            raise ValueError(f"{label} 必须是有限数字")
    elif kind == "choice":
        result = str(value).strip()
        if result not in spec["choices"]:
            raise ValueError(f"{label} 只能是：{' / '.join(spec['choices'])}")
        return result
    elif kind == "text":
        result = str(value).strip()
        if spec["key"] == "background_window":
            if not _WINDOW_RE.match(result):
                raise ValueError(f"{label} 格式应为 HH:MM-HH:MM")
            for part in result.split("-"):
                hh, mm = part.split(":")
                if int(hh) > 24 or int(mm) > 59 or (int(hh) == 24 and int(mm) != 0):
                    raise ValueError(f"{label} 时间超出范围")
        return result
    else:
        raise ValueError(f"{label} 类型未知")
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and result < lo:
        raise ValueError(f"{label} 不能小于 {lo}")
    if hi is not None and result > hi:
        raise ValueError(f"{label} 不能大于 {hi}")
    return result


def _runtime_path(root: Path) -> Path:
    try:
        return load_settings(root).paths.state_dir / "runtime_config.json"
    except (OSError, ValueError):
        return Path(root) / "data" / "state" / "runtime_config.json"


def _yaml_section(root: Path, section: str) -> dict[str, Any]:
    try:
        cfg = yaml.safe_load((Path(root) / "config" / DISCOVERY_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    raw = cfg.get(section) or {}
    return raw if isinstance(raw, dict) else {}


def overrides(root: Path, section: str | None = None) -> dict[str, dict[str, Any]]:
    """看板保存的覆盖值（已校验；损坏或不合法的项被忽略，不影响其余项）。"""
    stored = read_runtime_config(_runtime_path(root)).get(KEY)
    if not isinstance(stored, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for sec in ([section] if section else list(SCHEMA)):
        if sec not in SCHEMA or not isinstance(stored.get(sec), dict):
            continue
        specs = _specs(sec)
        clean: dict[str, Any] = {}
        for k, v in stored[sec].items():
            if k in specs:
                try:
                    clean[k] = coerce(specs[k], v)
                except ValueError:
                    continue
        if clean:
            out[sec] = clean
    return out


def raw_section(root: Path, section: str) -> dict[str, Any]:
    """供各模块读取的“原始”分组：yaml 字段被看板覆盖值替换。只包含注册表中的键。"""
    specs = _specs(section)
    merged = {k: v for k, v in _yaml_section(root, section).items() if k in specs}
    merged.update(overrides(root, section).get(section, {}))
    return merged


def describe(root: Path, section: str) -> dict[str, Any]:
    """看板展示用：每个参数的当前值、默认值、来源（看板 / yaml / 默认）与取值范围。"""
    specs = _specs(section)
    yaml_raw = _yaml_section(root, section)
    over = overrides(root, section).get(section, {})
    rows = []
    for key, spec in specs.items():
        if key in over:
            source, value = "dashboard", over[key]
        elif key in yaml_raw:
            source = "yaml"
            try:
                value = coerce(spec, yaml_raw[key])
            except ValueError:
                value, source = spec["default"], "default"
        else:
            source, value = "default", spec["default"]
        rows.append({**spec, "value": value, "source": source})
    return {"section": section, "title": SECTION_TITLES.get(section, section), "rows": rows,
            "file": f"config/{DISCOVERY_FILE}"}


def save(root: Path, section: str, values: dict[str, Any], *, now_iso: str = "") -> dict[str, Any]:
    """保存一组参数（全部校验通过才写入）。返回保存后的 describe 结果。"""
    specs = _specs(section)
    if not isinstance(values, dict) or not values:
        raise ValueError("没有可保存的内容")
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if key not in specs:
            raise ValueError(f"未知参数：{key}")
        clean[key] = coerce(specs[key], value)
    path = _runtime_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = read_runtime_config(path)
        stored = data.get(KEY) if isinstance(data.get(KEY), dict) else {}
        sec = dict(stored.get(section) or {})
        sec.update(clean)
        stored[section] = sec
        data[KEY] = stored
        if now_iso:
            data["updated_at"] = now_iso
        atomic_write_json(path, data)
    return describe(root, section)


def reset(root: Path, section: str, key: str | None = None) -> dict[str, Any]:
    """恢复默认：key 为空时清空该分组的全部看板覆盖值，否则只清除一项（回到 yaml 或默认值）。"""
    specs = _specs(section)
    if key is not None and key not in specs:
        raise ValueError(f"未知参数：{key}")
    path = _runtime_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = read_runtime_config(path)
        stored = data.get(KEY) if isinstance(data.get(KEY), dict) else {}
        if section in stored:
            if key is None:
                stored.pop(section, None)
            else:
                stored[section] = {k: v for k, v in dict(stored[section]).items() if k != key}
                if not stored[section]:
                    stored.pop(section, None)
        data[KEY] = stored
        atomic_write_json(path, data)
    return describe(root, section)

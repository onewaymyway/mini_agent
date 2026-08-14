"""
tests/test_config_nested_blocks_wiring.py

回归守护：防止 `config/models.py` 新增的子配置块（`AppConfig` 上的
`XxxConfig = field(default_factory=XxxConfig)` 字段）只定义了 dataclass，
却忘了接入 `config/loader.py` 的加载逻辑——这类问题曾经在 `scheduler`、
`cycle_tuning`、`execution_phase` 三个 block 上先后出现（见
`param_registry.py` 里对应的注释、
`next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md`）：
dataclass 字段和默认值都在，`agent_config.json` 里对应的 key 却被静默
忽略，`load_config()` 返回的永远是默认值，不是"没配置"意义上的默认值，
而是"配了也没用"意义上的默认值——两者从 API 角度看不出区别，只能靠
运行时验证 + 这份测试兜底。

两条覆盖：
  1. 静态检查：`AppConfig` 上每一个 `XxxConfig` 子配置字段，要么在
     `param_registry.NESTED_CONFIG_BLOCKS` 里注册（走通用加载机制），
     要么在 loader.py 源码里能找到对应的手写 `<attr>_cfg = ...` 赋值 ——
     两种路径都算"接入了"，但**必须**接入其中一种。
  2. 端到端检查：对当前已注册进 `NESTED_CONFIG_BLOCKS` 的每个 block，
     实际写一份 `agent_config.json`，把该 block 第一个"简单类型"
     （bool/int/float/str）字段改成一个非默认值，跑一次 `load_config()`，
     断言改动确实体现在返回的 `AppConfig` 上——不只是"字段存在"，而是
     "配置文件里的值真的被读出来了"。
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path

import pytest

from mini_agent.config import models as _models
from mini_agent.config.loader import load_config
from mini_agent.config.param_registry import NESTED_CONFIG_BLOCKS


def _appconfig_subconfig_attrs() -> dict:
    """返回 `{attr_name: dataclass_type}`，只挑 `AppConfig` 上类型本身也是
    一个 `@dataclass` 的字段（排除 str/int/Path/list 等普通字段）。"""
    out = {}
    for f in dataclasses.fields(_models.AppConfig):
        t = f.type
        # AppConfig 里字段类型标注基本都是直接类引用（非字符串 forward-ref），
        # 但保险起见两种都处理一下。
        if isinstance(t, str):
            cls = getattr(_models, t.strip('"'), None)
        else:
            cls = t
        if cls is not None and dataclasses.is_dataclass(cls) and cls is not _models.AppConfig:
            out[f.name] = cls
    return out


def test_every_appconfig_subblock_is_registered_or_handwritten():
    loader_src = Path(_models.__file__).with_name("loader.py").read_text(encoding="utf-8")
    registered = {s.attr_name for s in NESTED_CONFIG_BLOCKS}

    missing = []
    for attr in _appconfig_subconfig_attrs():
        in_registry = attr in registered
        handwritten = re.search(rf"\b{re.escape(attr)}_cfg\s*=", loader_src) is not None
        passed_through = re.search(rf"\n\s*{re.escape(attr)}={re.escape(attr)}_cfg,", loader_src) is not None
        if not (in_registry or handwritten):
            missing.append(f"{attr}: 既未注册进 NESTED_CONFIG_BLOCKS，也没有在 loader.py 里手写加载")
        elif not passed_through:
            missing.append(f"{attr}: 有 {attr}_cfg 变量，但没有以 {attr}={attr}_cfg 传给 AppConfig(...)")

    assert not missing, (
        "以下子配置块未被 config/loader.py 正确加载（agent_config.json 里对应 "
        "key 会被静默忽略）：\n" + "\n".join(missing)
    )


def _first_simple_field(cls) -> tuple:
    """挑该 dataclass 第一个 bool/int/float/str 类型、带明确默认值的字段，
    返回 `(field_name, default_value, override_value)`。找不到则返回 None
    （比如 block 里全是 Optional/list 字段，跳过端到端检查，静态检查已经
    覆盖了"有没有接入加载"这一点）。"""
    for f in dataclasses.fields(cls):
        default = f.default if f.default is not dataclasses.MISSING else None
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            continue
        if isinstance(default, bool):
            return f.name, default, (not default)
        if isinstance(default, int):
            return f.name, default, default + 1
        if isinstance(default, float):
            return f.name, default, default + 1.0
        if isinstance(default, str) and default:
            return f.name, default, default + "_overridden"
    return None


@pytest.mark.parametrize("spec", NESTED_CONFIG_BLOCKS, ids=lambda s: s.attr_name)
def test_registered_block_actually_loads_from_config_file(tmp_path, monkeypatch, spec):
    found = _first_simple_field(spec.dataclass_type)
    if found is None:
        pytest.skip(f"{spec.attr_name}: 没有可覆盖的简单类型字段，跳过端到端验证")
    field_name, default_value, override_value = found

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg_path = tmp_path / "agent_config.json"
    cfg_path.write_text(json.dumps({spec.attr_name: {field_name: override_value}}), encoding="utf-8")

    cfg = load_config(project_root=tmp_path)
    block = getattr(cfg, spec.attr_name)
    actual = getattr(block, field_name)
    assert actual == override_value, (
        f"agent_config.json 里 {spec.attr_name}.{field_name}={override_value!r} "
        f"没有被 load_config() 读取到（实际值 {actual!r}，dataclass 默认值 "
        f"{default_value!r}）——该 block 可能没有被正确接入加载流程。"
    )

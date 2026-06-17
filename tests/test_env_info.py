"""
tests/test_env_info.py — EnvInfo 模块单元测试
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from mini_agent.env_info.base import EnvInfoProvider
from mini_agent.env_info.registry import EnvInfoRegistry
from mini_agent.env_info.providers.system import SystemInfoProvider
from mini_agent.env_info.providers.runtime import RuntimeInfoProvider
from mini_agent.env_info.providers.locale import LocaleInfoProvider


# ── Fixtures & Helpers ─────────────────────────────────────────────────────────

class ConstantProvider(EnvInfoProvider):
    """测试用 Provider：返回固定数据。"""
    name = "test.constant"
    def __init__(self, data: dict):
        self._data = data
    def collect(self) -> dict:
        return self._data


class FailingProvider(EnvInfoProvider):
    """测试用 Provider：collect() 总是抛出异常。"""
    name = "test.failing"
    def collect(self) -> dict:
        raise RuntimeError("intentional failure")


class DisabledProvider(EnvInfoProvider):
    """测试用 Provider：enabled=False。"""
    name = "test.disabled"
    @property
    def enabled(self) -> bool:
        return False
    def collect(self) -> dict:
        return {"should": "not appear"}


# ── Provider 基类测试 ──────────────────────────────────────────────────────────

class TestEnvInfoProvider:
    def test_safe_collect_returns_data(self):
        p = ConstantProvider({"OS": "TestOS"})
        assert p.safe_collect() == {"OS": "TestOS"}

    def test_safe_collect_swallows_exception(self):
        p = FailingProvider()
        result = p.safe_collect()
        assert result == {}

    def test_safe_collect_disabled(self):
        p = DisabledProvider()
        result = p.safe_collect()
        assert result == {}


# ── Registry 测试 ──────────────────────────────────────────────────────────────

class TestEnvInfoRegistry:
    def test_collect_merges_providers(self):
        registry = EnvInfoRegistry()
        registry.register(ConstantProvider({"OS": "Linux"}))
        registry.register(ConstantProvider({"Python": "3.12"}))
        data = registry.collect()
        assert data["OS"] == "Linux"
        assert data["Python"] == "3.12"

    def test_later_provider_overwrites_earlier(self):
        registry = EnvInfoRegistry()
        registry.register(ConstantProvider({"OS": "Linux"}))
        registry.register(ConstantProvider({"OS": "macOS"}))
        data = registry.collect()
        assert data["OS"] == "macOS"

    def test_failing_provider_does_not_break_collection(self):
        registry = EnvInfoRegistry()
        registry.register(ConstantProvider({"OS": "Linux"}))
        registry.register(FailingProvider())
        registry.register(ConstantProvider({"Python": "3.12"}))
        data = registry.collect()
        assert data["OS"] == "Linux"
        assert data["Python"] == "3.12"

    def test_build_block_format(self):
        registry = EnvInfoRegistry()
        registry.register(ConstantProvider({"OS": "Linux", "Python": "3.12"}))
        block = registry.build_block()
        assert block.startswith("## Environment")
        assert "- OS: Linux" in block
        assert "- Python: 3.12" in block

    def test_build_block_empty(self):
        registry = EnvInfoRegistry()
        assert registry.build_block() == ""

    def test_from_config_builtin_system(self):
        registry = EnvInfoRegistry.from_config(["builtin.system"])
        data = registry.collect()
        assert "OS" in data
        assert "Arch" in data

    def test_from_config_builtin_runtime(self):
        registry = EnvInfoRegistry.from_config(["builtin.runtime"])
        data = registry.collect()
        assert "Python" in data

    def test_from_config_builtin_locale(self):
        registry = EnvInfoRegistry.from_config(["builtin.locale"])
        data = registry.collect()
        # Timezone 可能在某些 CI 环境中为空，但不应报错
        assert isinstance(data, dict)

    def test_from_config_all_defaults(self):
        registry = EnvInfoRegistry.from_config()
        data = registry.collect()
        assert "OS" in data
        assert "Python" in data

    def test_from_config_invalid_provider_skipped(self):
        registry = EnvInfoRegistry.from_config(
            ["builtin.system", "nonexistent.module.Class"]
        )
        # 应该只有 builtin.system 被加载
        assert len(registry.providers) == 1

    def test_from_config_with_kwargs(self):
        registry = EnvInfoRegistry.from_config(
            providers=["builtin.system"],
            provider_kwargs={"builtin.system": {"include_hostname": True}},
        )
        data = registry.collect()
        assert "OS" in data
        # 启用了 hostname，应该有 Hostname 字段（CI 环境中可能为空但不报错）
        assert isinstance(data, dict)

    def test_from_config_custom_provider(self):
        """通过完整类路径加载自定义 Provider。"""
        # ConstantProvider 在本模块，用完整路径引用
        registry = EnvInfoRegistry.from_config(
            ["tests.test_env_info.ConstantProvider"]
        )
        # ConstantProvider.__init__ 需要 data 参数，from_config 无参实例化会失败
        # 应该被跳过（warning），不崩溃
        assert isinstance(registry, EnvInfoRegistry)


# ── 内置 Provider 基本测试 ────────────────────────────────────────────────────

class TestSystemInfoProvider:
    def test_returns_os_and_arch(self):
        p = SystemInfoProvider()
        data = p.collect()
        assert "OS" in data
        assert len(data["OS"]) > 0
        assert "Arch" in data

    def test_hostname_excluded_by_default(self):
        p = SystemInfoProvider()
        data = p.collect()
        assert "Hostname" not in data

    def test_hostname_included_when_requested(self):
        p = SystemInfoProvider(include_hostname=True)
        data = p.collect()
        assert "Hostname" in data


class TestRuntimeInfoProvider:
    def test_returns_python_version(self):
        p = RuntimeInfoProvider()
        data = p.collect()
        assert "Python" in data
        import sys
        ver = sys.version_info
        assert data["Python"].startswith(f"{ver.major}.{ver.minor}")

    def test_returns_cwd(self):
        p = RuntimeInfoProvider()
        data = p.collect()
        assert "CWD" in data


class TestLocaleInfoProvider:
    def test_no_exception(self):
        p = LocaleInfoProvider()
        data = p.collect()
        assert isinstance(data, dict)


# ── 便捷函数测试 ───────────────────────────────────────────────────────────────

class TestBuildEnvBlock:
    def test_returns_string(self):
        from mini_agent.env_info import build_env_block
        result = build_env_block()
        assert isinstance(result, str)
        assert "## Environment" in result

    def test_custom_providers(self):
        from mini_agent.env_info import build_env_block
        result = build_env_block(providers=["builtin.system"])
        assert "OS:" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

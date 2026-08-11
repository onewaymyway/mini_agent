#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
plugin_system.py - 插件系统

支持动态加载新网站适配器和评估器插件。
"""

import importlib
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Any, Type


class BasePlugin:
    """插件基类"""
    
    @property
    def name(self) -> str:
        raise NotImplementedError
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    def initialize(self) -> bool:
        """初始化插件"""
        return True
    
    def cleanup(self) -> None:
        """清理资源"""
        pass


class PluginSystem:
    """插件系统 - 支持动态加载新网站适配器和评估器"""
    
    def __init__(self, plugin_dirs: List[str] = None):
        self.plugin_dirs = [Path(d) for d in (plugin_dirs or ["src/adapters", "src/evaluators"])]
        self._plugins: Dict[str, BasePlugin] = {}
        self._load_plugins()
    
    def _load_plugins(self) -> None:
        """加载所有插件"""
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.exists():
                continue
            
            for plugin_file in plugin_dir.glob("*.py"):
                if plugin_file.name.startswith('_'):
                    continue
                
                try:
                    plugin = self._load_plugin(plugin_file)
                    if plugin:
                        self._plugins[plugin.name] = plugin
                        print(f"加载插件成功: {plugin.name} v{plugin.version}")
                except Exception as e:
                    print(f"加载插件失败 {plugin_file.name}: {e}")
    
    def _load_plugin(self, plugin_file: Path) -> Optional[BasePlugin]:
        """加载单个插件"""
        try:
            spec = importlib.util.spec_from_file_location(
                plugin_file.stem, plugin_file
            )
            if spec is None or spec.loader is None:
                return None
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 查找插件类
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    issubclass(attr, BasePlugin) and 
                    attr is not BasePlugin):
                    return attr()
            
            return None
        except Exception as e:
            print(f"加载插件模块失败 {plugin_file.name}: {e}")
            return None
    
    def register_plugin(self, plugin: BasePlugin) -> None:
        """注册插件"""
        self._plugins[plugin.name] = plugin
    
    def get_plugin(self, name: str) -> Optional[BasePlugin]:
        """获取插件"""
        return self._plugins.get(name)
    
    def list_plugins(self) -> List[str]:
        """列出所有插件"""
        return list(self._plugins.keys())
    
    def initialize_all(self) -> Dict[str, bool]:
        """初始化所有插件"""
        results = {}
        for name, plugin in self._plugins.items():
            try:
                results[name] = plugin.initialize()
            except Exception as e:
                print(f"初始化插件 {name} 失败: {e}")
                results[name] = False
        return results
    
    def cleanup_all(self) -> None:
        """清理所有插件"""
        for plugin in self._plugins.values():
            try:
                plugin.cleanup()
            except Exception as e:
                print(f"清理插件 {plugin.name} 失败: {e}")
    
    def get_plugin_info(self, name: str) -> Optional[Dict[str, Any]]:
        """获取插件信息"""
        plugin = self._plugins.get(name)
        if plugin:
            return {
                "name": plugin.name,
                "version": plugin.version,
                "type": type(plugin).__name__,
            }
        return None


# 导出公共接口
__all__ = [
    "BasePlugin",
    "PluginSystem",
]

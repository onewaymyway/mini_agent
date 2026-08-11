# -*- coding: utf-8 -*-
"""
适配器插件目录

所有数据源适配器实现应放在此目录下，每个适配器文件对应一个数据源。
适配器会自动被 AdapterManager 发现并注册。

适配器命名规范：
- 文件名: {source_name}_adapter.py
- 类名: {SourceName}Adapter
- 继承: Adapter
"""

from .base_adapter import BaseAdapter

__all__ = ['BaseAdapter']

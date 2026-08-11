# -*- coding: utf-8 -*-
"""
统一配置管理模块

提供全局配置管理，包括：
- 请求超时配置
- 重试策略配置
- 数据源优先级配置
- 日志配置

使用示例：
    from finance_toolkit.config import get_config, Config
    
    # 获取配置实例
    config = get_config()
    
    # 访问配置
    timeout = config.request_timeout
    max_retries = config.max_retries
    
    # 修改配置
    config.request_timeout = 60
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from pathlib import Path
import json

logger = logging.getLogger(__name__)


@dataclass
class RequestConfig:
    """请求配置"""
    timeout: float = 30.0                    # 请求超时时间（秒）
    max_retries: int = 3                     # 最大重试次数
    retry_backoff: List[float] = field(default_factory=lambda: [1, 2, 5])  # 重试退避因子
    connect_timeout: float = 10.0            # 连接超时时间（秒）
    read_timeout: float = 30.0               # 读取超时时间（秒）
    write_timeout: float = 30.0              # 写入超时时间（秒）
    keepalive_timeout: float = 30.0          # 连接保持时间（秒）
    max_connections: int = 100               # 最大连接数
    max_keepalive_connections: int = 20      # 最大保持连接数
    # Kline 专用配置
    kline_timeout: float = 30.0              # K线数据超时（秒）
    kline_max_retries: int = 3               # K线最大重试次数
    kline_backoff: List[float] = field(default_factory=lambda: [1, 2, 5])  # K线重试退避因子


@dataclass
class SourceConfig:
    """数据源配置"""
    name: str                                # 数据源名称
    priority: int = 0                        # 优先级（越小越高）
    enabled: bool = True                     # 是否启用
    timeout: float = 30.0                    # 超时时间
    max_retries: int = 3                     # 最大重试次数
    circuit_breaker_threshold: int = 5       # 熔断器阈值
    circuit_breaker_reset_timeout: int = 60  # 熔断器重置时间


@dataclass
class ProxyConfig:
    """代理配置"""
    enabled: bool = False                     # 是否启用代理
    http_proxy: Optional[str] = None          # HTTP 代理地址
    https_proxy: Optional[str] = None         # HTTPS 代理地址
    socks_proxy: Optional[str] = None         # SOCKS 代理地址
    trust_env: bool = False                   # 是否信任环境变量代理设置
    proxy_rotation: bool = False              # 是否启用代理轮换
    proxy_pool: List[str] = field(default_factory=list)  # 代理池
    proxy_check_interval: int = 300           # 代理检测间隔（秒）


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"                      # 日志级别
    log_file: Optional[str] = None           # 日志文件路径
    max_bytes: int = 10 * 1024 * 1024        # 单个日志文件最大大小（10MB）
    backup_count: int = 5                    # 备份文件数量
    use_json: bool = False                   # 是否使用 JSON 格式


@dataclass
class Config:
    """
    全局配置类

    管理所有配置项，支持从文件加载和程序设置。
    """
    request: RequestConfig = field(default_factory=RequestConfig)
    sources: Dict[str, SourceConfig] = field(default_factory=dict)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    
    # 默认数据源优先级
    default_source_priority: List[str] = field(
        default_factory=lambda: ['akshare', 'eastmoney', 'sina']
    )
    
    # 数据验证配置
    validation: Dict[str, Any] = field(default_factory=lambda: {
        'check_continuity': True,
        'check_outliers': True,
        'outlier_std': 3.0,
    })
    
    def __post_init__(self):
        """初始化默认数据源配置"""
        if not self.sources:
            self.sources = {
                'akshare': SourceConfig(
                    name='akshare',
                    priority=1,
                    enabled=True,
                    timeout=self.request.timeout,
                    max_retries=self.request.max_retries,
                ),
                'eastmoney': SourceConfig(
                    name='eastmoney',
                    priority=2,
                    enabled=True,
                    timeout=self.request.timeout,
                    max_retries=self.request.max_retries,
                ),
                'sina': SourceConfig(
                    name='sina',
                    priority=3,
                    enabled=True,
                    timeout=self.request.timeout,
                    max_retries=self.request.max_retries,
                ),
            }
    
    def get_source_config(self, source_name: str) -> Optional[SourceConfig]:
        """获取指定数据源配置"""
        return self.sources.get(source_name)
    
    def get_enabled_sources(self) -> List[str]:
        """获取启用的数据源列表（按优先级排序）"""
        enabled = [
            name for name, config in sorted(
                self.sources.items(),
                key=lambda x: x[1].priority
            )
            if config.enabled
        ]
        return enabled
    
    def load_from_file(self, config_path: str):
        """
        从 JSON 配置文件加载配置

        Args:
            config_path: 配置文件路径
        """
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"配置文件不存在：{config_path}")
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 加载请求配置
            if 'request' in data:
                self.request = RequestConfig(**data['request'])

            # 加载数据源配置
            if 'sources' in data:
                for name, config_data in data['sources'].items():
                    self.sources[name] = SourceConfig(**config_data)

            # 加载日志配置
            if 'logging' in data:
                self.logging = LoggingConfig(**data['logging'])

            # 加载代理配置
            if 'proxy' in data:
                self.proxy = ProxyConfig(**data['proxy'])

            # 加载验证配置
            if 'validation' in data:
                self.validation.update(data['validation'])

            logger.info(f"配置已从 {config_path} 加载")

        except Exception as e:
            logger.error(f"加载配置文件失败：{e}")
    
    def save_to_file(self, config_path: str):
        """
        保存配置到 JSON 文件
        
        Args:
            config_path: 配置文件路径
        """
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            data = {
                'request': {
                    'timeout': self.request.timeout,
                    'max_retries': self.request.max_retries,
                    'retry_backoff': self.request.retry_backoff,
                    'connect_timeout': self.request.connect_timeout,
                    'read_timeout': self.request.read_timeout,
                    'write_timeout': self.request.write_timeout,
                    'keepalive_timeout': self.request.keepalive_timeout,
                    'max_connections': self.request.max_connections,
                    'max_keepalive_connections': self.request.max_keepalive_connections,
                },
                'sources': {
                    name: {
                        'name': config.name,
                        'priority': config.priority,
                        'enabled': config.enabled,
                        'timeout': config.timeout,
                        'max_retries': config.max_retries,
                        'circuit_breaker_threshold': config.circuit_breaker_threshold,
                        'circuit_breaker_reset_timeout': config.circuit_breaker_reset_timeout,
                    }
                    for name, config in self.sources.items()
                },
                'logging': {
                    'level': self.logging.level,
                    'log_file': self.logging.log_file,
                    'max_bytes': self.logging.max_bytes,
                    'backup_count': self.logging.backup_count,
                    'use_json': self.logging.use_json,
                },
                'proxy': {
                    'enabled': self.proxy.enabled,
                    'http_proxy': self.proxy.http_proxy,
                    'https_proxy': self.proxy.https_proxy,
                    'socks_proxy': self.proxy.socks_proxy,
                    'trust_env': self.proxy.trust_env,
                    'proxy_rotation': self.proxy.proxy_rotation,
                    'proxy_pool': self.proxy.proxy_pool,
                    'proxy_check_interval': self.proxy.proxy_check_interval,
                },
                'validation': self.validation,
            }
            
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"配置已保存到 {config_path}")
        
        except Exception as e:
            logger.error(f"保存配置文件失败：{e}")


# 全局配置实例
_default_config: Optional[Config] = None


def get_config() -> Config:
    """
    获取全局配置实例（单例模式）
    
    Returns:
        Config 实例
    """
    global _default_config
    if _default_config is None:
        _default_config = Config()
    return _default_config


def reset_config():
    """重置全局配置实例（用于测试）"""
    global _default_config
    _default_config = None


def setup_config(
    timeout: float = 30.0,
    max_retries: int = 3,
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    config_path: Optional[str] = None
):
    """
    快速配置全局设置
    
    Args:
        timeout: 请求超时时间（秒）
        max_retries: 最大重试次数
        log_level: 日志级别
        log_file: 日志文件路径
        config_path: 配置文件路径（如果提供则从文件加载）
    """
    config = get_config()
    
    if config_path:
        config.load_from_file(config_path)
    else:
        # 设置请求配置
        config.request.timeout = timeout
        config.request.max_retries = max_retries
        
        # 设置数据源超时
        for source in config.sources.values():
            source.timeout = timeout
            source.max_retries = max_retries
        
        # 设置日志配置
        config.logging.level = log_level
        config.logging.log_file = log_file


# 便捷函数
get_default_config = get_config


def get_proxy_config() -> ProxyConfig:
    """获取代理配置"""
    return get_config().proxy


def set_proxy(
    enabled: bool = False,
    http_proxy: Optional[str] = None,
    https_proxy: Optional[str] = None,
    socks_proxy: Optional[str] = None,
    trust_env: bool = False,
):
    """
    设置代理配置

    Args:
        enabled: 是否启用代理
        http_proxy: HTTP 代理地址
        https_proxy: HTTPS 代理地址
        socks_proxy: SOCKS 代理地址
        trust_env: 是否信任环境变量代理设置
    """
    config = get_config()
    config.proxy.enabled = enabled
    config.proxy.http_proxy = http_proxy
    config.proxy.https_proxy = https_proxy
    config.proxy.socks_proxy = socks_proxy
    config.proxy.trust_env = trust_env
    logger.info(f"代理配置已更新: enabled={enabled}, trust_env={trust_env}")


def get_proxy_url(proxy_type: str = 'https') -> Optional[str]:
    """
    获取代理 URL

    Args:
        proxy_type: 代理类型 ('http', 'https', 'socks')

    Returns:
        代理 URL 或 None
    """
    proxy = get_proxy_config()
    if not proxy.enabled:
        return None

    proxy_map = {
        'http': proxy.http_proxy,
        'https': proxy.https_proxy,
        'socks': proxy.socks_proxy,
    }
    return proxy_map.get(proxy_type)


def is_proxy_available() -> bool:
    """
    检查代理是否可用

    Returns:
        True 如果代理已启用且有有效地址
    """
    proxy = get_proxy_config()
    if not proxy.enabled:
        return False

    # 检查是否有有效的代理地址
    has_proxy = any([
        proxy.http_proxy,
        proxy.https_proxy,
        proxy.socks_proxy,
    ])
    return has_proxy


def get_trust_env() -> bool:
    """
    获取是否信任环境变量代理设置

    Returns:
        True 如果信任环境变量
    """
    return get_proxy_config().trust_env


def add_proxy_to_pool(proxy_url: str):
    """
    添加代理到代理池

    Args:
        proxy_url: 代理 URL
    """
    proxy = get_proxy_config()
    if proxy_url not in proxy.proxy_pool:
        proxy.proxy_pool.append(proxy_url)
        logger.info(f"代理已添加到池: {proxy_url}")


def remove_proxy_from_pool(proxy_url: str):
    """
    从代理池移除代理

    Args:
        proxy_url: 代理 URL
    """
    proxy = get_proxy_config()
    if proxy_url in proxy.proxy_pool:
        proxy.proxy_pool.remove(proxy_url)
        logger.info(f"代理已从池移除: {proxy_url}")


def rotate_proxy() -> Optional[str]:
    """
    轮换代理（从代理池中选择一个）

    Returns:
        选中的代理 URL 或 None
    """
    proxy = get_proxy_config()
    if not proxy.proxy_rotation or not proxy.proxy_pool:
        return get_proxy_url('https') or get_proxy_url('http')

    # 简单轮询：选择第一个代理
    return proxy.proxy_pool[0] if proxy.proxy_pool else None

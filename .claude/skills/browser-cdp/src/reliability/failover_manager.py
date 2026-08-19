# -*- coding: utf-8 -*-
"""
故障转移管理器

提供网站故障转移能力，当主网站不可用时自动切换到备用网站。
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class FailoverStrategy:
    """故障转移策略枚举"""
    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    RANDOM = "random"


class FailoverManager:
    """故障转移管理器"""
    
    def __init__(self, primary: str, backups: List[str]):
        self.primary = primary
        self.backups = backups
        self.current_index = 0
        self.failed_sites: set = set()
        self._site_status: Dict[str, bool] = {primary: True}
        for b in backups:
            self._site_status[b] = True
    
    def get_next_site(self) -> str:
        """获取下一个可用网站"""
        # 主站可用时优先返回主站（故障恢复后应回到主站）
        if self.primary not in self.failed_sites:
            logger.info("Primary site is available, using primary")
            return self.primary

        # 主站不可用，按顺序尝试备用网站
        if self.current_index < len(self.backups):
            site = self.backups[self.current_index]
            self.current_index += 1
            if site not in self.failed_sites:
                logger.info(f"Failover to backup site: {site}")
                return site

        # 所有备用都失败，循环重试第一个未标记失败的备用
        logger.warning("All backup sites failed, cycling through backups")
        self.current_index = 0
        for site in self.backups:
            if site not in self.failed_sites:
                return site
        # 全部失败，返回主站（让调用方重试）
        return self.primary
    
    def mark_failed(self, site: str):
        """标记网站失败"""
        self.failed_sites.add(site)
        self._site_status[site] = False
        logger.warning(f"Site marked as failed: {site}")
    
    def mark_success(self, site: str):
        """标记网站成功"""
        self.failed_sites.discard(site)
        self._site_status[site] = True
        # 如果回到主网站成功，重置索引
        if site == self.primary:
            self.current_index = 0
    
    def get_status(self) -> Dict[str, any]:
        return {
            "primary": self.primary,
            "backups": self.backups,
            "current_site": self.backups[self.current_index - 1] if self.current_index > 0 else self.primary,
            "failed_sites": list(self.failed_sites),
            "site_status": dict(self._site_status),
        }
    
    def reset(self):
        """重置故障转移状态"""
        self.current_index = 0
        self.failed_sites.clear()
        for site in self._site_status:
            self._site_status[site] = True
        logger.info("Failover manager reset")


_global_failover: Optional[FailoverManager] = None


def get_failover_manager(primary: str, backups: List[str]) -> FailoverManager:
    """获取或创建故障转移管理器"""
    global _global_failover
    if _global_failover is None:
        _global_failover = FailoverManager(primary, backups)
    return _global_failover


def reset_failover_manager():
    """重置全局故障转移管理器"""
    global _global_failover
    _global_failover = None

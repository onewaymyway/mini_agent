"""
perception/behavior — 用户行为感知系统（默认关闭）

用途：让 agent 感知用户在终端/浏览器上的行为（前台窗口切换、空闲状态、
浏览器页面访问等），作为上下文线索。

隐私边界（务必保持）：
  - 不采集聊天软件的消息内容，聊天类 App 只当普通前台窗口处理。
  - 不做键盘按键内容记录，只用"距上次输入时长"判断在场/空闲。
  - 剪贴板只记录"发生了复制"这一事实，不落地剪贴板内容。
  - 手机端只接受地理围栏标签（home/work/other），不接受原始经纬度，
    服务端会强制剔除任何坐标字段；不读通知正文/短信聊天内容；
    健康数据只接受日聚合数字，不接受心率曲线等细粒度数据。
  - 所有开关默认关闭，需要用户显式开启。

快速使用：
    from mini_agent.perception.behavior import get_manager

    mgr = get_manager()
    mgr.set_enabled(True)
    mgr.set_collector_enabled("active_window", True)
    events = mgr.query(limit=50)
"""

from .config import BehaviorConfig, load_behavior_config, save_behavior_config
from .events import ActivityEvent, BehaviorEventStore
from .manager import BehaviorPerceptionManager, get_manager
from .analyzer import generate_daily_summary, load_daily_summary

__all__ = [
    "BehaviorConfig", "load_behavior_config", "save_behavior_config",
    "ActivityEvent", "BehaviorEventStore",
    "BehaviorPerceptionManager", "get_manager",
    "generate_daily_summary", "load_daily_summary",
]

"""
perception/behavior/mobile_setup.py — 手机端接入模板生成

手机端没法像桌面那样装一个常驻线程去主动采集，能做的都是"手机上的自动化
工具在特定时机主动 POST 一条事件"，复用桌面这边已有的
/v1/perception/report 接口，只是换一批外部上报源（kind="mobile"）。

硬性边界（服务端 manager.report_external 里也会再校验一次，双重保险）：
  - 只允许上报"地理围栏标签"（如 "home"/"work"/"other"），绝不接受原始经纬度。
    标签判断必须在手机本地完成（Tasker/快捷指令的"到达/离开某地"触发器本身
    就是本地判断，不需要把坐标发出来）。
  - 不读通知正文、不读短信/聊天内容，只读"来源 App + 时间"这类元数据。
  - 健康数据只上报日聚合数字（步数、睡眠时长），不上报心率曲线/GPS 轨迹等
    细粒度数据。

依赖：手机和跑 mini_agent 的电脑需要在同一局域网（或用户自己做内网穿透），
否则手机端够不到 http://<电脑IP>:<port>/v1/perception/report，这是网络前提，
不是本模块能解决的事情，只在文档里提醒。
"""

from __future__ import annotations


def android_usage_report_template(report_url: str, api_token: str, report_token: str) -> str:
    """Tasker/MacroDroid 里配置 HTTP Request 动作时可以直接抄的 JSON body 模板。

    典型触发方式：Tasker Profile 用 "Display On/Off" 或 "App" 事件触发，
    周期性（比如每 15 分钟）读一次 UsageStatsManager 聚合出的前台 App 名 +
    时长，拼进 events 数组里 POST 过来。这段拼接逻辑需要 Tasker 插件
    （如 AutoTools）或用户自己写一个几十行的辅助 App 来完成，本模板只
    规定"发过来的 JSON 应该长什么样"。
    """
    return f"""POST {report_url}
Headers:
  Authorization: Bearer {api_token}
  Content-Type: application/json

Body (示例，app_name/duration_sec 由 Tasker/AutoTools 从 UsageStatsManager 读出后拼入):
{{
  "source": "android_usage",
  "kind": "mobile",
  "token": "{report_token}",
  "events": [
    {{
      "event_type": "app_focus",
      "app_name": "com.tencent.mm",
      "duration_sec": 320
    }},
    {{
      "event_type": "screen_unlock"
    }}
  ]
}}

事件类型约定：
  app_focus       — 前台 App 包名 + 停留时长（和桌面 active_window 语义一致）
  screen_unlock   — 每次解锁上报一条（不带 duration），用于统计"摸手机次数"
  screen_off      — 息屏事件
  geofence        — meta.label 只能是 "home"/"work"/"other" 这类标签，
                     绝不能是经纬度（服务端也会强制剔除 lat/lon 字段）
  health_daily    — meta 里放 {{"steps": 8000, "sleep_hours": 7.5}} 这种日聚合数字
"""


def ios_shortcuts_template(report_url: str, api_token: str, report_token: str) -> str:
    """iOS 快捷指令"个人自动化"里配置"获取 URL 内容"动作时的参数模板。

    典型用法：
      1. 快捷指令 App → 自动化 → 新建个人自动化 → 选择触发条件
         （打开某个 App / 到达某个地点 / 到了某个时间点等）
      2. 添加动作"获取 URL 内容"，方法选 POST，按下面配置
      3. 关闭"运行前询问"，这样触发时会静默执行

    地理围栏场景：触发条件直接选"到达/离开"某个已保存地点，这样判断本身
    就是手机本地做的，快捷指令里手动把 label 写死成 "home"/"work" 即可，
    不涉及任何坐标传输。
    """
    return f"""URL: {report_url}
Method: POST
Headers:
  Authorization: Bearer {api_token}
  Content-Type: application/json

Request Body (JSON，按触发场景替换 events 内容):

# 场景1：到达/离开某地点自动化 → label 手动写死，不要用坐标变量
{{
  "source": "ios_shortcuts",
  "kind": "mobile",
  "token": "{report_token}",
  "events": [ {{ "event_type": "geofence", "meta": {{ "label": "home" }} }} ]
}}

# 场景2：打开某个 App 时触发
{{
  "source": "ios_shortcuts",
  "kind": "mobile",
  "token": "{report_token}",
  "events": [ {{ "event_type": "app_focus", "app_name": "com.apple.mobilesafari" }} ]
}}

# 场景3：定时（比如每晚 23:00）从"健康"App 读步数/睡眠后上报日聚合
{{
  "source": "ios_shortcuts",
  "kind": "mobile",
  "token": "{report_token}",
  "events": [ {{ "event_type": "health_daily", "meta": {{ "steps": 8000, "sleep_hours": 7.5 }} }} ]
}}
"""

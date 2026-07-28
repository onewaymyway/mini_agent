"""external_input/builtin/weather.py — WeatherInputSource（P7 示例来源）

设计背景见 next_doc/external_input_gateway_design.md §3.6。

这是继 `builtin/watch.py`（P4）之后，External Input Gateway 的第二个
内置 `ExternalInputSource` 实现，用途是给"如何新增一种外部输入来源"
提供一个完整可跑的示例：监控某个经纬度未来若干小时的天气预报，命中
降雨概率阈值或极端气温阈值时产生事件。

选用 Open-Meteo（https://open-meteo.com）作为数据源：免费、不需要 API
key、返回结构简单的 JSON，避免示例代码被"怎么申请密钥""怎么处理鉴权"
这类与网关设计无关的细节喧宾夺主。

硬约束（与 watch.py 一致，继承自 ExternalInputSource.poll() 的约束）：
本文件不调用 LLM，只做纯脚本抓取 + 阈值判断；跨轮询状态全部通过
`state` dict 传递。

规则说明（`params` 支持的键）：

- ``latitude`` / ``longitude``（必填）：监控地点的经纬度。
- ``rain_probability_threshold``（可选，默认 60）：未来
  ``lookahead_hours`` 小时内，只要有一个小时的降雨概率
  （precipitation_probability，单位 %）达到或超过这个阈值，就产生一条
  ``signal="rain_alert"`` 事件。
- ``temperature_high_threshold`` / ``temperature_low_threshold``
  （可选）：未来 ``lookahead_hours`` 小时内的最高/最低气温超过/低于
  阈值时，分别产生 ``signal="high_temperature"`` /
  ``signal="low_temperature"`` 事件。三个阈值都不配置时，本 source
  只做"日常预报摘要"（见下）。
- ``lookahead_hours``（可选，默认 12）：向前看多少小时的预报。
- ``daily_summary``（可选，默认 false）：为 true 时，每天第一次轮询会
  额外产生一条 ``signal="daily_forecast"`` 的低优先级摘要事件（today
  的最高/最低气温 + 最大降雨概率），供 `notify_only` 落点做"每日简报"
  这类场景，不需要每次都配置具体阈值。

跟 `channel` 的关系（P7）：本 source 不在 `poll()` 里手写
`ExternalInputEvent.channel`，而是让网关（poller.py）按 sources.yaml
里该条目的 `channel` 统一回填——多数用户不需要关心 channel 具体怎么
分配；`sources.yaml` 缺省不写 `channel` 时会退化成 `type`（即
"weather"），效果和显式写 `channel: weather`一样。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    register_source,
)

_DEFAULT_TIMEOUT = 10
_DEFAULT_LOOKAHEAD_HOURS = 12
_DEFAULT_RAIN_PROBABILITY_THRESHOLD = 60
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherFetchError(RuntimeError):
    """抓取/解析天气预报失败。直接向上抛给 GatewayPoller，交由其统一的
    退避熔断处理（跟 watch.py::WatchFetchError 同样的分工），本文件不
    重复实现重试逻辑。"""


def _fetch_hourly_forecast(
    latitude: float, longitude: float, timeout: float = _DEFAULT_TIMEOUT
) -> dict:
    """调用 Open-Meteo 免费预报 API，取小时级 温度/降雨概率。不需要 API
    key，响应是简单 JSON，不引入除 requests 之外的新依赖（项目已声明）。
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - 环境应始终已安装
        raise WeatherFetchError("weather source 需要 requests 库") from exc

    try:
        resp = requests.get(
            _FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m,precipitation_probability",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except WeatherFetchError:
        raise
    except Exception as exc:
        raise WeatherFetchError(f"抓取天气预报失败: {exc}") from exc


def _slice_lookahead(hourly: dict, lookahead_hours: int) -> dict:
    """把 Open-Meteo 返回的 hourly 数组截取"从现在起未来 N 小时"这一段。
    Open-Meteo 的 hourly.time 是本地时间（因为请求带了 timezone=auto）
    升序排列的 ISO 字符串，找到第一个 >= 当前时间的下标作为起点即可，
    不需要额外的时区换算。
    """
    times = hourly.get("time") or []
    now_str = time.strftime("%Y-%m-%dT%H:00")
    start_idx = 0
    for i, t in enumerate(times):
        if t >= now_str:
            start_idx = i
            break
    end_idx = min(start_idx + max(lookahead_hours, 1), len(times))
    return {
        "time": times[start_idx:end_idx],
        "temperature_2m": (hourly.get("temperature_2m") or [])[start_idx:end_idx],
        "precipitation_probability": (
            hourly.get("precipitation_probability") or []
        )[start_idx:end_idx],
    }


@register_source("weather")
class WeatherInputSource(ExternalInputSource):
    """监控某个地点的天气预报，命中降雨概率/极端气温阈值时产生事件。

    示例 `sources.yaml` 片段::

        sources:
          - id: home_weather
            type: weather
            interval_seconds: 1800   # 半小时轮询一次即可，天气预报不需要
                                       # 高频轮询
            channel: weather          # 缺省即等于 type，可以不写
            params:
              latitude: 39.9042
              longitude: 116.4074
              rain_probability_threshold: 60
              temperature_high_threshold: 35
              temperature_low_threshold: 0
              lookahead_hours: 12
              daily_summary: true
    """

    source_type = "weather"

    def poll(
        self, params: dict, state: dict
    ) -> tuple[list[ExternalInputEvent], dict]:
        latitude = params.get("latitude")
        longitude = params.get("longitude")
        if latitude is None or longitude is None:
            raise WeatherFetchError(
                "weather source 需要在 params 里配置 latitude/longitude"
            )

        lookahead_hours = int(params.get("lookahead_hours", _DEFAULT_LOOKAHEAD_HOURS))
        data = _fetch_hourly_forecast(float(latitude), float(longitude))
        hourly = data.get("hourly") or {}
        window = _slice_lookahead(hourly, lookahead_hours)

        events: list[ExternalInputEvent] = []
        new_state = dict(state)
        now = time.time()
        today_key = time.strftime("%Y-%m-%d")

        rain_threshold = params.get(
            "rain_probability_threshold", _DEFAULT_RAIN_PROBABILITY_THRESHOLD
        )
        high_threshold = params.get("temperature_high_threshold")
        low_threshold = params.get("temperature_low_threshold")

        temps = [t for t in window["temperature_2m"] if t is not None]
        rain_probs = [
            p for p in window["precipitation_probability"] if p is not None
        ]
        max_rain_prob = max(rain_probs) if rain_probs else None
        max_temp = max(temps) if temps else None
        min_temp = min(temps) if temps else None

        # ── 降雨提醒：未来窗口内命中一次阈值 → 未命中之前不重复告警 ──────
        # 语义跟 watch.py 的 threshold 模式一致：只在"未命中 -> 命中"的
        # 边沿发一次事件，持续命中（比如连续下了好几个小时的雨）不会
        # 每轮轮询都重复告警。
        if rain_threshold is not None and max_rain_prob is not None:
            hit = max_rain_prob >= float(rain_threshold)
            was_hit = bool(state.get("rain_hit"))
            if hit and not was_hit:
                events.append(
                    ExternalInputEvent(
                        id=f"rain:{today_key}:{int(now)}",
                        source_id=str(params.get("source_id", "")),
                        source_type=self.source_type,
                        signal="rain_alert",
                        title=f"未来{lookahead_hours}小时降雨概率达 {max_rain_prob:.0f}%",
                        detail=f"阈值 {rain_threshold}%，窗口内最高降雨概率 {max_rain_prob:.0f}%。",
                        fields={
                            "max_rain_probability": max_rain_prob,
                            "threshold": rain_threshold,
                            "lookahead_hours": lookahead_hours,
                        },
                        suggested_tier="tick",
                    )
                )
            new_state["rain_hit"] = hit

        # ── 极端气温提醒：同样只在边沿触发一次 ───────────────────────────
        if high_threshold is not None and max_temp is not None:
            hit = max_temp >= float(high_threshold)
            was_hit = bool(state.get("high_temp_hit"))
            if hit and not was_hit:
                events.append(
                    ExternalInputEvent(
                        id=f"high_temp:{today_key}:{int(now)}",
                        source_id=str(params.get("source_id", "")),
                        source_type=self.source_type,
                        signal="high_temperature",
                        title=f"未来{lookahead_hours}小时最高气温达 {max_temp:.1f}°C",
                        detail=f"阈值 {high_threshold}°C。",
                        fields={"max_temperature": max_temp, "threshold": high_threshold},
                        suggested_tier="tick",
                    )
                )
            new_state["high_temp_hit"] = hit

        if low_threshold is not None and min_temp is not None:
            hit = min_temp <= float(low_threshold)
            was_hit = bool(state.get("low_temp_hit"))
            if hit and not was_hit:
                events.append(
                    ExternalInputEvent(
                        id=f"low_temp:{today_key}:{int(now)}",
                        source_id=str(params.get("source_id", "")),
                        source_type=self.source_type,
                        signal="low_temperature",
                        title=f"未来{lookahead_hours}小时最低气温达 {min_temp:.1f}°C",
                        detail=f"阈值 {low_threshold}°C。",
                        fields={"min_temperature": min_temp, "threshold": low_threshold},
                        suggested_tier="tick",
                    )
                )
            new_state["low_temp_hit"] = hit

        # ── 可选的每日摘要：同一天只发一次，跟阈值告警互不影响 ───────────
        if params.get("daily_summary") and state.get("daily_summary_date") != today_key:
            if max_temp is not None and min_temp is not None:
                events.append(
                    ExternalInputEvent(
                        id=f"daily:{today_key}",
                        source_id=str(params.get("source_id", "")),
                        source_type=self.source_type,
                        signal="daily_forecast",
                        title=f"今日天气摘要：{min_temp:.1f}°C ~ {max_temp:.1f}°C",
                        detail=(
                            f"未来{lookahead_hours}小时最高降雨概率 "
                            f"{max_rain_prob:.0f}%"
                            if max_rain_prob is not None
                            else "无降雨概率数据"
                        ),
                        fields={
                            "max_temperature": max_temp,
                            "min_temperature": min_temp,
                            "max_rain_probability": max_rain_prob,
                        },
                        suggested_tier="cron",
                    )
                )
                new_state["daily_summary_date"] = today_key

        return events, new_state

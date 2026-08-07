package com.miniagent.behavior

import android.content.Context
import android.content.SharedPreferences

/**
 * 所有采集开关默认 false，跟桌面端 mini_agent 的 behavior perception 系统保持
 * 同样的"默认关闭，用户显式开启"原则。
 *
 * report_url / api_token / report_token 需要用户从 mini_agent 里执行
 * `/behavior mobile android` 拿到的模板手动填进来。
 */
object Prefs {
    private const val FILE = "behavior_prefs"

    private fun sp(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    // ── 上报目标配置 ──────────────────────────────────────────────────────
    var Context.reportUrl: String
        get() = sp(this).getString("report_url", "") ?: ""
        set(v) = sp(this).edit().putString("report_url", v).apply()

    var Context.apiToken: String
        get() = sp(this).getString("api_token", "") ?: ""
        set(v) = sp(this).edit().putString("api_token", v).apply()

    var Context.reportToken: String
        get() = sp(this).getString("report_token", "") ?: ""
        set(v) = sp(this).edit().putString("report_token", v).apply()

    // ── 采集开关（默认全部 false）─────────────────────────────────────────
    var Context.usageStatsEnabled: Boolean
        get() = sp(this).getBoolean("usage_stats_enabled", false)
        set(v) = sp(this).edit().putBoolean("usage_stats_enabled", v).apply()

    var Context.screenEventsEnabled: Boolean
        get() = sp(this).getBoolean("screen_events_enabled", false)
        set(v) = sp(this).edit().putBoolean("screen_events_enabled", v).apply()

    var Context.geofenceEnabled: Boolean
        get() = sp(this).getBoolean("geofence_enabled", false)
        set(v) = sp(this).edit().putBoolean("geofence_enabled", v).apply()

    var Context.healthConnectEnabled: Boolean
        get() = sp(this).getBoolean("health_connect_enabled", false)
        set(v) = sp(this).edit().putBoolean("health_connect_enabled", v).apply()

    // 上一次 UsageStats 轮询到的时间戳，避免重复上报同一段时间的事件
    var Context.lastUsageStatsQueryTs: Long
        get() = sp(this).getLong("last_usage_stats_query_ts", System.currentTimeMillis() - 15 * 60_000L)
        set(v) = sp(this).edit().putLong("last_usage_stats_query_ts", v).apply()

    // home/work 地点的经纬度只存在设备本地，永远不上报
    var Context.homeLat: Float
        get() = sp(this).getFloat("home_lat", Float.NaN)
        set(v) = sp(this).edit().putFloat("home_lat", v).apply()
    var Context.homeLng: Float
        get() = sp(this).getFloat("home_lng", Float.NaN)
        set(v) = sp(this).edit().putFloat("home_lng", v).apply()
    var Context.workLat: Float
        get() = sp(this).getFloat("work_lat", Float.NaN)
        set(v) = sp(this).edit().putFloat("work_lat", v).apply()
    var Context.workLng: Float
        get() = sp(this).getFloat("work_lng", Float.NaN)
        set(v) = sp(this).edit().putFloat("work_lng", v).apply()

    fun isConfigured(ctx: Context): Boolean =
        ctx.reportUrl.isNotBlank() && ctx.apiToken.isNotBlank() && ctx.reportToken.isNotBlank()
}

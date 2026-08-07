package com.miniagent.behavior

import android.content.Context
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import com.miniagent.behavior.Prefs.apiToken
import com.miniagent.behavior.Prefs.reportToken
import com.miniagent.behavior.Prefs.reportUrl

/**
 * 统一封装对 mini_agent `/v1/perception/report` 接口的调用。
 *
 * 边界（和桌面端约定一致，服务端也会再校验一次，这里客户端先自觉一遍）：
 *   - source 用来标识来源类型（android_usage / screen_event / geofence / health_daily）
 *   - kind 固定是 "mobile"
 *   - geofence 事件只允许携带 label 字段，绝不能携带经纬度
 */
object ReportClient {
    private const val TAG = "MiniAgentReport"

    private val client = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(3, TimeUnit.SECONDS)
        .build()

    /**
     * events: List<Pair<eventType, meta>>，meta 里禁止出现 lat/lon 之类的键，
     * 调用方（各 Worker/Receiver）负责保证这一点，这里只做兜底剔除。
     */
    fun report(ctx: Context, source: String, events: List<JSONObject>): Boolean {
        if (!Prefs.isConfigured(ctx)) {
            Log.w(TAG, "report skipped: not configured yet")
            return false
        }

        // 客户端兜底：任何事件的 meta 里如果混进了坐标字段，直接剔除，不依赖服务端兜底。
        val sanitized = JSONArray()
        for (e in events) {
            val meta = e.optJSONObject("meta")
            meta?.let {
                for (key in listOf("lat", "lon", "latitude", "longitude", "gps", "coordinates")) {
                    it.remove(key)
                }
            }
            sanitized.put(e)
        }

        val body = JSONObject().apply {
            put("source", source)
            put("kind", "mobile")
            put("token", ctx.reportToken)
            put("events", sanitized)
        }

        return try {
            val req = Request.Builder()
                .url(ctx.reportUrl)
                .addHeader("Authorization", "Bearer ${ctx.apiToken}")
                .addHeader("Content-Type", "application/json")
                .post(body.toString().toRequestBody("application/json".toMediaType()))
                .build()
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    Log.w(TAG, "report failed: HTTP ${resp.code} ${resp.body?.string()}")
                }
                resp.isSuccessful
            }
        } catch (e: Exception) {
            // 手机和电脑不在同一局域网、电脑没开机等都会走到这里，静默失败不重试风暴。
            Log.w(TAG, "report error: ${e.message}")
            false
        }
    }
}

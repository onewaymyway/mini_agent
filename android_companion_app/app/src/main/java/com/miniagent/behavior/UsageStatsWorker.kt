package com.miniagent.behavior

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.os.Process
import android.provider.Settings
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.miniagent.behavior.Prefs.lastUsageStatsQueryTs
import com.miniagent.behavior.Prefs.usageStatsEnabled
import org.json.JSONObject

/**
 * 定期（15 分钟一次）读取自上次运行以来的 App 前台切换事件，聚合成
 * "app_focus: {app_name, duration_sec}" 上报，跟桌面端 active_window
 * 采集器的事件语义保持一致。
 *
 * 只用 MOVE_TO_FOREGROUND / MOVE_TO_BACKGROUND 这两种事件配对算时长，
 * 不读任何页面内容、不读通知。
 */
class UsageStatsWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        if (!applicationContext.usageStatsEnabled) return Result.success()
        if (!hasUsageAccess(applicationContext)) return Result.success()

        val usm = applicationContext.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val since = applicationContext.lastUsageStatsQueryTs
        val now = System.currentTimeMillis()

        val events = usm.queryEvents(since, now)
        val usageEvent = UsageEvents.Event()

        // packageName -> 上次进入前台的时间戳
        val foregroundSince = mutableMapOf<String, Long>()
        val reportBatch = mutableListOf<JSONObject>()

        while (events.hasNextEvent()) {
            events.getNextEvent(usageEvent)
            when (usageEvent.eventType) {
                UsageEvents.Event.MOVE_TO_FOREGROUND -> {
                    foregroundSince[usageEvent.packageName] = usageEvent.timeStamp
                }
                UsageEvents.Event.MOVE_TO_BACKGROUND -> {
                    val start = foregroundSince.remove(usageEvent.packageName) ?: continue
                    val durationSec = (usageEvent.timeStamp - start) / 1000.0
                    if (durationSec < 1) continue
                    reportBatch.add(JSONObject().apply {
                        put("event_type", "app_focus")
                        put("app_name", usageEvent.packageName)
                        put("duration_sec", durationSec)
                    })
                }
            }
        }

        if (reportBatch.isNotEmpty()) {
            ReportClient.report(applicationContext, "android_usage", reportBatch)
        }
        applicationContext.lastUsageStatsQueryTs = now
        return Result.success()
    }

    companion object {
        /** 引导用户去系统设置里手动开启"使用情况访问权限"，这是特殊权限，代码里不能直接申请。 */
        fun requestUsageAccessIfNeeded(ctx: Context) {
            if (!hasUsageAccess(ctx)) {
                val intent = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                ctx.startActivity(intent)
            }
        }

        fun hasUsageAccess(ctx: Context): Boolean {
            val appOps = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
            val mode = appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName
            )
            return mode == AppOpsManager.MODE_ALLOWED
        }
    }
}

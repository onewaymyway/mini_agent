package com.miniagent.behavior

import android.content.Context
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.miniagent.behavior.Prefs.geofenceEnabled
import com.miniagent.behavior.Prefs.healthConnectEnabled
import com.miniagent.behavior.Prefs.usageStatsEnabled
import java.util.concurrent.TimeUnit

/**
 * 按当前开关状态（重新）注册/取消各周期任务。App 启动、用户切换开关、
 * 开机自启时都会调用一次，保证 WorkManager 里的任务和 Prefs 里的开关状态一致。
 */
object WorkScheduler {
    private const val USAGE_STATS_WORK = "usage_stats_periodic"
    private const val HEALTH_CONNECT_WORK = "health_connect_periodic"

    fun syncAll(ctx: Context) {
        val wm = WorkManager.getInstance(ctx)

        if (ctx.usageStatsEnabled) {
            val req = PeriodicWorkRequestBuilder<UsageStatsWorker>(15, TimeUnit.MINUTES).build()
            wm.enqueueUniquePeriodicWork(USAGE_STATS_WORK, ExistingPeriodicWorkPolicy.KEEP, req)
        } else {
            wm.cancelUniqueWork(USAGE_STATS_WORK)
        }

        if (ctx.healthConnectEnabled) {
            val req = PeriodicWorkRequestBuilder<HealthConnectWorker>(1, TimeUnit.DAYS).build()
            wm.enqueueUniquePeriodicWork(HEALTH_CONNECT_WORK, ExistingPeriodicWorkPolicy.KEEP, req)
        } else {
            wm.cancelUniqueWork(HEALTH_CONNECT_WORK)
        }

        if (ctx.geofenceEnabled) {
            GeofenceHelper.registerGeofences(ctx)
        } else {
            GeofenceHelper.unregisterGeofences(ctx)
        }

        // screen_events 靠 manifest 里静态注册的 ScreenEventReceiver，
        // 开关只在 receiver 内部判断，不需要在这里额外调度。
    }
}

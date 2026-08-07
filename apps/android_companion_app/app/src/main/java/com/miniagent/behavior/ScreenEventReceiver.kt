package com.miniagent.behavior

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.Data
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.miniagent.behavior.Prefs.screenEventsEnabled

/**
 * 监听解锁 (USER_PRESENT) 和息屏 (SCREEN_OFF)，只上报"发生了这个事件"，
 * 不带任何内容。用于估算"一天摸手机多少次"这类专注度信号。
 *
 * BroadcastReceiver 不能直接做网络请求（onReceive 必须快速返回），
 * 所以这里丢给 WorkManager 的一次性任务去发。
 */
class ScreenEventReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!context.screenEventsEnabled) return

        val eventType = when (intent.action) {
            Intent.ACTION_USER_PRESENT -> "screen_unlock"
            Intent.ACTION_SCREEN_OFF -> "screen_off"
            else -> return
        }

        val data = Data.Builder().putString("event_type", eventType).build()
        val work = OneTimeWorkRequestBuilder<ScreenEventReportWorker>()
            .setInputData(data)
            .build()
        WorkManager.getInstance(context).enqueue(work)
    }
}

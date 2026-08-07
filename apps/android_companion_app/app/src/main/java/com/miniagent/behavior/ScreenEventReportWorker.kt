package com.miniagent.behavior

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import org.json.JSONObject

class ScreenEventReportWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val eventType = inputData.getString("event_type") ?: return Result.success()
        val event = JSONObject().apply { put("event_type", eventType) }
        ReportClient.report(applicationContext, "screen_event", listOf(event))
        return Result.success()
    }
}

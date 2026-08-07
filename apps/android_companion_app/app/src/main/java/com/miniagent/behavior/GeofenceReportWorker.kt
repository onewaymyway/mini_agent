package com.miniagent.behavior

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import org.json.JSONObject

/**
 * 只把 label（"home"/"work"）和 enter/exit 状态发出去，meta 里绝不放坐标。
 * ReportClient.report 也会做一次兜底剔除，双重保险。
 */
class GeofenceReportWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val label = inputData.getString("label") ?: return Result.success()
        val transition = inputData.getString("transition") ?: return Result.success()

        val event = JSONObject().apply {
            put("event_type", "geofence")
            put("meta", JSONObject().apply {
                put("label", label)
                put("transition", transition)
            })
        }
        ReportClient.report(applicationContext, "geofence", listOf(event))
        return Result.success()
    }
}

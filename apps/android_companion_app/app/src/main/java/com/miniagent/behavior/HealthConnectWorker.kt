package com.miniagent.behavior

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.request.AggregateRequest
import androidx.health.connect.client.time.TimeRangeFilter
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.miniagent.behavior.Prefs.healthConnectEnabled
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.temporal.ChronoUnit

/**
 * 每天跑一次，只读"今天的步数总和"和"最近一次睡眠时长"这两个日聚合数字，
 * 不读心率、不读分钟级明细、不读运动轨迹。
 */
class HealthConnectWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        if (!applicationContext.healthConnectEnabled) return Result.success()

        val available = HealthConnectClient.getSdkStatus(applicationContext) ==
                HealthConnectClient.SDK_AVAILABLE
        if (!available) return Result.success()

        val client = HealthConnectClient.getOrCreate(applicationContext)

        val zone = ZoneId.systemDefault()
        val now = Instant.now()
        val startOfDay = now.atZone(zone).truncatedTo(ChronoUnit.DAYS).toInstant()

        var steps = 0L
        var sleepHours = 0.0

        try {
            val stepsResult = client.aggregate(
                AggregateRequest(
                    metrics = setOf(StepsRecord.COUNT_TOTAL),
                    timeRangeFilter = TimeRangeFilter.between(startOfDay, now)
                )
            )
            steps = stepsResult[StepsRecord.COUNT_TOTAL] ?: 0L
        } catch (e: Exception) {
            // 没有授权或没有数据源时静默跳过，不影响其它采集
        }

        try {
            val sleepResult = client.aggregate(
                AggregateRequest(
                    metrics = setOf(SleepSessionRecord.SLEEP_DURATION_TOTAL),
                    timeRangeFilter = TimeRangeFilter.between(startOfDay.minus(Duration.ofHours(12)), now)
                )
            )
            val duration = sleepResult[SleepSessionRecord.SLEEP_DURATION_TOTAL]
            sleepHours = (duration?.toMinutes() ?: 0L) / 60.0
        } catch (e: Exception) {
            // 同上
        }

        if (steps > 0 || sleepHours > 0) {
            val event = JSONObject().apply {
                put("event_type", "health_daily")
                put("meta", JSONObject().apply {
                    put("steps", steps)
                    put("sleep_hours", Math.round(sleepHours * 10) / 10.0)
                })
            }
            ReportClient.report(applicationContext, "health_daily", listOf(event))
        }
        return Result.success()
    }
}

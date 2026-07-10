package com.miniagent.behavior

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingClient
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationServices
import com.miniagent.behavior.Prefs.homeLat
import com.miniagent.behavior.Prefs.homeLng
import com.miniagent.behavior.Prefs.workLat
import com.miniagent.behavior.Prefs.workLng

/**
 * 地理围栏只用来在设备本地判断"进入/离开 home 或 work"，经纬度全程只存在
 * 本机 SharedPreferences 里（Prefs.homeLat/homeLng/workLat/workLng），
 * 从不通过网络发送——GeofenceBroadcastReceiver 收到事件后只把 "home"/"work"
 * 这个标签字符串交给 ReportClient。
 *
 * 用户需要在 App 里手动设置一次 home/work 的坐标（比如"用当前位置设为家"），
 * 这个设置本身也只写本地，不上传。
 */
object GeofenceHelper {
    private const val RADIUS_METERS = 150f
    const val GEOFENCE_ID_HOME = "home"
    const val GEOFENCE_ID_WORK = "work"

    @SuppressLint("MissingPermission")
    fun registerGeofences(ctx: Context) {
        val client: GeofencingClient = LocationServices.getGeofencingClient(ctx)
        val fences = mutableListOf<Geofence>()

        if (!ctx.homeLat.isNaN() && !ctx.homeLng.isNaN()) {
            fences.add(buildGeofence(GEOFENCE_ID_HOME, ctx.homeLat.toDouble(), ctx.homeLng.toDouble()))
        }
        if (!ctx.workLat.isNaN() && !ctx.workLng.isNaN()) {
            fences.add(buildGeofence(GEOFENCE_ID_WORK, ctx.workLat.toDouble(), ctx.workLng.toDouble()))
        }
        if (fences.isEmpty()) return

        val request = GeofencingRequest.Builder()
            .setInitialTrigger(GeofencingRequest.INITIAL_TRIGGER_ENTER)
            .addGeofences(fences)
            .build()

        client.addGeofences(request, geofencePendingIntent(ctx))
    }

    fun unregisterGeofences(ctx: Context) {
        LocationServices.getGeofencingClient(ctx).removeGeofences(geofencePendingIntent(ctx))
    }

    private fun buildGeofence(id: String, lat: Double, lng: Double): Geofence =
        Geofence.Builder()
            .setRequestId(id)
            .setCircularRegion(lat, lng, RADIUS_METERS)
            .setExpirationDuration(Geofence.NEVER_EXPIRE)
            .setTransitionTypes(Geofence.GEOFENCE_TRANSITION_ENTER or Geofence.GEOFENCE_TRANSITION_EXIT)
            .build()

    private fun geofencePendingIntent(ctx: Context): PendingIntent {
        val intent = Intent(ctx, GeofenceBroadcastReceiver::class.java)
        return PendingIntent.getBroadcast(
            ctx, 0, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }
}

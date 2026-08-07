package com.miniagent.behavior

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.Data
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingEvent
import com.miniagent.behavior.Prefs.geofenceEnabled

class GeofenceBroadcastReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!context.geofenceEnabled) return

        val geofencingEvent = GeofencingEvent.fromIntent(intent) ?: return
        if (geofencingEvent.hasError()) return

        val transition = geofencingEvent.geofenceTransition
        val label = geofencingEvent.triggeringGeofences?.firstOrNull()?.requestId ?: return

        // 只把 "enter"/"exit" + label（"home"/"work"）交给上报 worker，坐标到此为止不再传递。
        val transitionStr = when (transition) {
            Geofence.GEOFENCE_TRANSITION_ENTER -> "enter"
            Geofence.GEOFENCE_TRANSITION_EXIT -> "exit"
            else -> return
        }

        val data = Data.Builder()
            .putString("label", label)
            .putString("transition", transitionStr)
            .build()
        val work = OneTimeWorkRequestBuilder<GeofenceReportWorker>().setInputData(data).build()
        WorkManager.getInstance(context).enqueue(work)
    }
}

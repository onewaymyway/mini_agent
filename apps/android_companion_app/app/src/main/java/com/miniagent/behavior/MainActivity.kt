package com.miniagent.behavior

import android.Manifest
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Switch
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.miniagent.behavior.Prefs.apiToken
import com.miniagent.behavior.Prefs.geofenceEnabled
import com.miniagent.behavior.Prefs.healthConnectEnabled
import com.miniagent.behavior.Prefs.homeLat
import com.miniagent.behavior.Prefs.homeLng
import com.miniagent.behavior.Prefs.reportToken
import com.miniagent.behavior.Prefs.reportUrl
import com.miniagent.behavior.Prefs.screenEventsEnabled
import com.miniagent.behavior.Prefs.usageStatsEnabled
import com.miniagent.behavior.Prefs.workLat
import com.miniagent.behavior.Prefs.workLng

class MainActivity : AppCompatActivity() {

    private val locationPermissionRequestCode = 1001

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val editReportUrl = findViewById<EditText>(R.id.editReportUrl)
        val editApiToken = findViewById<EditText>(R.id.editApiToken)
        val editReportToken = findViewById<EditText>(R.id.editReportToken)
        val btnSaveConfig = findViewById<Button>(R.id.btnSaveConfig)

        val switchUsageStats = findViewById<Switch>(R.id.switchUsageStats)
        val btnGrantUsageAccess = findViewById<Button>(R.id.btnGrantUsageAccess)
        val switchScreenEvents = findViewById<Switch>(R.id.switchScreenEvents)
        val switchGeofence = findViewById<Switch>(R.id.switchGeofence)
        val btnSetHome = findViewById<Button>(R.id.btnSetHome)
        val btnSetWork = findViewById<Button>(R.id.btnSetWork)
        val switchHealthConnect = findViewById<Switch>(R.id.switchHealthConnect)

        // 回填已保存的配置/开关状态
        editReportUrl.setText(reportUrl)
        editApiToken.setText(apiToken)
        editReportToken.setText(reportToken)
        switchUsageStats.isChecked = usageStatsEnabled
        switchScreenEvents.isChecked = screenEventsEnabled
        switchGeofence.isChecked = geofenceEnabled
        switchHealthConnect.isChecked = healthConnectEnabled

        btnSaveConfig.setOnClickListener {
            reportUrl = editReportUrl.text.toString().trim()
            apiToken = editApiToken.text.toString().trim()
            reportToken = editReportToken.text.toString().trim()
            Toast.makeText(this, "配置已保存", Toast.LENGTH_SHORT).show()
        }

        switchUsageStats.setOnCheckedChangeListener { _, checked ->
            usageStatsEnabled = checked
            WorkScheduler.syncAll(this)
        }
        btnGrantUsageAccess.setOnClickListener {
            UsageStatsWorker.requestUsageAccessIfNeeded(this)
        }

        switchScreenEvents.setOnCheckedChangeListener { _, checked ->
            screenEventsEnabled = checked
        }

        switchGeofence.setOnCheckedChangeListener { _, checked ->
            if (checked) ensureLocationPermission()
            geofenceEnabled = checked
            WorkScheduler.syncAll(this)
        }
        btnSetHome.setOnClickListener { captureCurrentLocationAs(isHome = true) }
        btnSetWork.setOnClickListener { captureCurrentLocationAs(isHome = false) }

        switchHealthConnect.setOnCheckedChangeListener { _, checked ->
            healthConnectEnabled = checked
            WorkScheduler.syncAll(this)
            if (checked) {
                Toast.makeText(this, "首次使用请在弹出的 Health Connect 权限页里授权步数/睡眠读取", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun ensureLocationPermission() {
        val fine = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
        if (fine != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION),
                locationPermissionRequestCode
            )
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val bg = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            if (bg != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION),
                    locationPermissionRequestCode
                )
            }
        }
    }

    /**
     * 只把坐标写进本地 SharedPreferences（Prefs.homeLat/homeLng 等），
     * 从始至终不经过 ReportClient，也就不会被上传。
     */
    @Suppress("DEPRECATION")
    private fun captureCurrentLocationAs(isHome: Boolean) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ensureLocationPermission()
            return
        }
        val lm = getSystemService(LOCATION_SERVICE) as LocationManager
        val providers = lm.getProviders(true)
        var location: Location? = null
        for (provider in providers) {
            location = lm.getLastKnownLocation(provider)
            if (location != null) break
        }
        if (location == null) {
            Toast.makeText(this, "拿不到当前位置，请确认定位已开启后重试", Toast.LENGTH_SHORT).show()
            return
        }
        if (isHome) {
            homeLat = location.latitude.toFloat()
            homeLng = location.longitude.toFloat()
        } else {
            workLat = location.latitude.toFloat()
            workLng = location.longitude.toFloat()
        }
        WorkScheduler.syncAll(this)
        Toast.makeText(this, if (isHome) "已设为「家」" else "已设为「公司」", Toast.LENGTH_SHORT).show()
    }
}

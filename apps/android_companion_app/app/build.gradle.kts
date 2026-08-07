plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.miniagent.behavior"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.miniagent.behavior"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.12.0")

    // 周期性后台任务：读 UsageStats / Health Connect 数据并上报
    implementation("androidx.work:work-runtime-ktx:2.9.0")

    // 地理围栏（只用于本地判断 home/work/other 标签，坐标不出设备）
    implementation("com.google.android.gms:play-services-location:21.2.0")

    // Health Connect：读步数/睡眠日聚合
    implementation("androidx.health.connect:connect-client:1.1.0-alpha07")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}

# mini_agent Behavior Companion（Android 伴侣 App）

配合桌面端 mini_agent 的用户行为感知系统使用，采集移动端的"工作/生活画像"
信号，通过已有的 `/v1/perception/report`（`kind=mobile`）接口上报。

> **重要**：这是一份**未在真机/模拟器上编译验证过**的源码脚手架
> （沙盒环境没有 Android SDK/Gradle），请在自己的 Android Studio
> （Giraffe 或更新版本）里打开、同步依赖、跑一遍再实际使用。以下是已知
> 大概率需要你调整的地方：
> - `androidx.health.connect:connect-client` 的版本号和 API 一直在变，
>   如果编译报错请去 Health Connect 官方文档核对最新版本号和权限字符串
>   （`android.permission.health.READ_STEPS` / `READ_SLEEP`）
> - `com.google.android.gms:play-services-location` 版本号建议换成你
>   同步时的最新稳定版
> - 没有提供任何 launcher icon 资源，运行前建议用 Android Studio 的
>   "New → Image Asset" 生成一个，否则会用系统默认图标（不影响功能）
> - `minSdk = 26`：Health Connect 官方建议的最低版本更高（部分机型需要
>   单独安装 Health Connect App），如果你的目标机型版本较低，可以把
>   健康数据这个开关相关代码整体去掉

## 功能范围（对应桌面端方案里商量好的四项）

| 功能 | 对应文件 | 默认状态 |
|---|---|---|
| App 使用统计（前台切换+时长） | `UsageStatsWorker.kt` | 关闭，需要用户在系统设置里单独授权"使用情况访问权限" |
| 屏幕解锁/息屏 | `ScreenEventReceiver.kt` | 关闭 |
| 地理围栏（只报 home/work 标签） | `GeofenceHelper.kt` / `GeofenceBroadcastReceiver.kt` | 关闭，需要用户在 App 里手动"把当前位置设为家/公司" |
| 健康日聚合（步数/睡眠） | `HealthConnectWorker.kt` | 关闭，需要 Health Connect 授权 |

## 隐私边界（务必保持，别为了"顺手"就加大采集范围）

- **不读通知正文**：本 App 没有声明 `NotificationListenerService`，
  完全不接触通知内容。如果以后要加"只读通知来源不读正文"，需要非常
  谨慎地控制只暴露 `getPackageName()`，不要碰 `getNotification()` 里
  的 extras。
- **不读短信/聊天内容**：同前面桌面端商量好的边界，聊天类信息完全不碰。
- **地理位置只在设备本地判断标签**：`Prefs.homeLat/homeLng/workLat/workLng`
  只写 SharedPreferences，`ReportClient.report()` 会强制剔除任何
  `lat/lon/latitude/longitude/gps/coordinates` 字段，双重保险防止误传。
- **健康数据只读日聚合**：`HealthConnectWorker` 只用 `AggregateRequest`
  拿"今天步数总和"和"睡眠总时长"，不读取分钟级采样、不读心率、不读
  运动轨迹。
- **命令行/浏览器等信息不在这个 App 的范围内**：那些是桌面端的能力，
  手机端只做上面四项。

## 构建步骤

1. 用 Android Studio 打开 `android_companion_app/` 目录
2. 等待 Gradle 同步（如果依赖版本报错，按上面"重要"提示调整版本号）
3. 真机运行（Health Connect / UsageStats 权限在模拟器上体验有限，
   建议真机测试）
4. 打开 App，把桌面上执行 `/behavior mobile android` 拿到的
   `URL / Authorization Bearer token / token 字段` 分别填进
   "上报地址 / API Token / Report Token" 三个输入框，点"保存配置"
5. 按需打开各开关；App 使用统计需要额外点"去系统设置授权"完成
   系统级别的单独授权

## 网络前提

手机和跑 mini_agent 的电脑必须在同一局域网，上报地址里的 IP 需要填
电脑的局域网 IP（不是 127.0.0.1）。如果两边不在同一网络，需要用户自己
搭建内网穿透，这不是本 App 能力范围内的事情。

## 已知未覆盖 / 有意不做的

- 不做屏幕录制/截图
- 不做 Accessibility Service 读取其它 App 界面内容（这类权限侵入性
  太强，能拿到几乎任何 App 的界面文本，不符合我们商量好的采集边界）
- 不做通知正文抓取
- 不做精确 GPS 轨迹上报，只有 home/work/other 标签

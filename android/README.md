# Android 客户端（M2）

手机采集音频（麦克风 / 其他应用的媒体声音）→ 经 WebSocket 上传主机 → 悬浮窗显示双语字幕。

- **minSdk 29（Android 10）**：媒体声音捕获（AudioPlaybackCapture）的硬性要求
- **targetSdk 34**、Kotlin 1.9.24、Jetpack Compose、OkHttp 4.12

## 构建

依赖：JDK 17、Android SDK（platform 34 + build-tools 34.0.0）。Gradle 由 wrapper 提供（8.7）。

```bash
cd android
export JAVA_HOME=/c/Users/38915/dev/jdk17/jdk-17.0.20.1+1   # 或设置为你自己的 JDK 17
./gradlew assembleDebug
# Windows CMD/PowerShell：gradlew.bat assembleDebug
```

产物：`android/app/build/outputs/apk/debug/app-debug.apk`（约 23 MB，debug 签名）。

## 验证

```bash
cd android
./gradlew testDebugUnitTest   # 4 项 JVM 测试：帧格式与大端 seq、48k→16k 重采样
./gradlew lintDebug           # 静态检查（要求 0 error）
```

## 安装

| 设备 | 方式 |
|---|---|
| Android 10+（含 HarmonyOS 4.x，底层仍为 Android） | `adb install -r app-debug.apk`，或拷贝 APK 到手机点击安装 |
| HarmonyOS NEXT（5/6） | 只能经「卓易通」等 Android 容器安装；**媒体声音与跨应用悬浮窗大概率不可用**，仅麦克风模式可做冒烟测试 |

## 使用步骤

1. 手机与主机处于**同一局域网**（或手机热点直连主机）。
2. 主机启动服务端：`cd server && ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000`。
3. 打开 App，填写主机 IP（`ipconfig` 查看）与端口 8000。
4. 选择采集模式：
   - **麦克风**：授权录音权限即可。
   - **媒体声音**：授权录音权限后，系统会弹出「屏幕/音频捕获」确认框；随后播放目标 App（建议 YouTube 等允许捕获的应用）。
5. 打开「显示在其他应用上层」并授予悬浮窗权限，字幕会悬浮显示。
6. 点击「开始」；切换到其他应用后采集与字幕继续（前台服务保活）。

## 权限说明

| 权限 | 用途 |
|---|---|
| `RECORD_AUDIO` | 麦克风采集；媒体声音捕获同样要求 |
| `FOREGROUND_SERVICE` + `..._MICROPHONE` / `..._MEDIA_PROJECTION` | 退出 App 后继续采集（Android 14 强制） |
| `POST_NOTIFICATIONS` | 前台服务通知（Android 13+） |
| `SYSTEM_ALERT_WINDOW` | 悬浮窗字幕 |
| MediaProjection 授权 | 系统弹窗，每次开始媒体模式时确认 |

## 代码结构

```
app/src/main/java/com/sloughel/bilingualsubtitle/
├── MainActivity.kt       控制页 + 权限申请（录音/通知/悬浮窗/MediaProjection）
├── AppState.kt           进程内 UI 状态（StateFlow），Activity 与服务共享
├── CaptureService.kt     前台服务：连接、采集、逐帧发送、stop 冲刷
├── audio/
│   ├── AudioSource.kt    接口 + 100ms 定长帧累积器
│   ├── MicSource.kt      AudioRecord；16k 优先，48k/44.1k 回退
│   ├── MediaSource.kt    MediaProjection + AudioPlaybackCapture
│   └── LinearResampler.kt 流式线性重采样
├── net/
│   ├── Protocol.kt       音频帧编码（6 字节头 + 3200B PCM16）
│   └── WsClient.kt       握手、config/start、字幕解析、指数退避重连
├── overlay/
│   └── OverlayManager.kt 悬浮窗：两行双语、拖动、透明度、关闭
└── ui/
    └── ControlScreen.kt  Compose 控制页
```

## 已知限制

- 目标应用若禁止音频捕获（`allowAudioPlaybackCapture=false`，如部分 DRM 内容），媒体模式会没有声音，需改用麦克风模式（FM-06）。
- 悬浮窗需要系统授权；部分系统对后台弹窗/悬浮窗有额外限制。
- HarmonyOS NEXT 的 Android 容器不暴露 MediaProjection 与跨应用悬浮窗，且无法用 adb/logcat 调试；M3 验收请优先使用 Android 真机。

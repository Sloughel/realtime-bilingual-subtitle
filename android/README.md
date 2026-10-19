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

产物：`android/app/build/outputs/apk/debug/bilingual-subtitle-v<版本>-debug.apk`
（当前为 `bilingual-subtitle-v0.1.3-debug.apk`，约 23 MB，debug 签名）。
文件名带版本号，客户端页面顶部也显示版本，便于确认安装的是哪一版。

## 验证

```bash
cd android
./gradlew testDebugUnitTest   # 4 项 JVM 测试：帧格式与大端 seq、48k→16k 重采样
./gradlew lintDebug           # 静态检查（要求 0 error）
```

## 安装

| 设备 | 方式 |
|---|---|
| Android 10+（含 HarmonyOS 4.x，底层仍为 Android） | `adb install -r bilingual-subtitle-v0.1.2-debug.apk`，或拷贝 APK 到手机点击安装 |
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

## 媒体声音排障

媒体模式采集时，「采集模式」卡与状态卡会显示「媒体音量：xx%」（捕获音频的 RMS；
未开始时显示「未开始」）。按以下判据定位：

| 现象 | 含义 | 处理 |
|---|---|---|
| 音量 > 0%，但无字幕 | 音频已捕获，问题在服务端/断句 | 检查主机日志、网络与翻译 |
| 音量 0%，8 秒后出现提示 | 捕获为静音 | 见下 |
| 出现红色错误文字 | 初始化/授权失败 | 按文字提示处理 |

捕获为静音的常见原因：

- **系统应用/图库禁止捕获**：华为「图库」等系统播放器通常关闭了 `allowAudioPlaybackCapture`
  （实测本地视频经图库播放时电平为 0%）。换用第三方播放器（VLC、MX Player）或浏览器播放。
- **目标应用禁止捕获**：部分视频应用（含 DRM 内容）设置 `allowAudioPlaybackCapture=false`。
  换用 YouTube 或本地播放器测试；Bilibili/部分国内视频 App 可能不可捕获。
- **音频用途不匹配**：本版仅捕获 `USAGE_MEDIA` / `USAGE_GAME` / `USAGE_UNKNOWN`；
  微信语音、通话使用 `USAGE_VOICE_COMMUNICATION`，不在范围。
- **尚未开始播放**：先点开始再播放也可以，8 秒提示会在检测到声音后自动消失。
- **OEM 限制**：部分华为机型对纯音频投影不保持会话；本版已加 1×1 VirtualDisplay 保活。
  若仍为 0%，改用麦克风模式。

## 已知限制

- 目标应用若禁止音频捕获（`allowAudioPlaybackCapture=false`，如部分 DRM 内容），媒体模式会没有声音，需改用麦克风模式（FM-06）。
- 悬浮窗需要系统授权；部分系统对后台弹窗/悬浮窗有额外限制。
- HarmonyOS NEXT 的 Android 容器不暴露 MediaProjection 与跨应用悬浮窗，且无法用 adb/logcat 调试；M3 验收请优先使用 Android 真机。

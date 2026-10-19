# 实时双语字幕系统（Realtime Bilingual Subtitle）

手机采集音频 → 主机识别与翻译 → 手机悬浮窗显示实时双语字幕。

本仓库为软件工程课程项目。设计文档见 [`docs/`](docs/)。

## 项目状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 环境准备 | 已完成 |
| M1 | 服务端全链路（协议 + 断句 + ASR + 翻译） | **已完成并实测通过** |
| M2 | Android 客户端（双采集模式 + 悬浮窗） | **已完成（Redmi Pad SE 真机验证）** |
| M3 | 端到端联调 | 未开始 |
| M4 | 扩展（互联网 / 本地模型 / Linux / iOS·鸿蒙） | 未开始 |
| M5 | 报告与演示 | 未开始 |

> **接续开发（或新会话恢复上下文）请先读
> 《[05-项目状态与交接说明](docs/05-项目状态与交接说明.md)》**——其中记录了环境路径、
> 已踩过的坑、已验证事实、已确认决策与 M1 实现要点。

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.10+ | 服务端运行时 |
| JDK | 17 | Android 构建（Java 8 不满足新版 Gradle 要求） |
| Android SDK | platform-tools + API 34 + build-tools 34.0.0 | adb 与构建 |
| GPU | NVIDIA，显存 ≥6GB | faster-whisper CUDA 加速（CPU 亦可但较慢） |

### 本机开发环境（已配置）

安装在用户目录下，无需管理员权限：

| 组件 | 路径 |
|---|---|
| JDK 17 | `C:\Users\38915\dev\jdk17\jdk-17.0.20.1+1` |
| Android SDK | `C:\Users\38915\dev\android-sdk` |
| adb | 随 Android SDK 的 platform-tools 提供 |

已写入用户环境变量：`JAVA_HOME`、`ANDROID_HOME`、`ANDROID_SDK_ROOT`，并把 `jdk17\bin`、
`android-sdk\platform-tools`、`android-sdk\cmdline-tools\latest\bin` 加入 `PATH`。

> **注意**：环境变量对已打开的终端不生效，需新开终端；或临时执行
> `export JAVA_HOME="C:\Users\38915\dev\jdk17\jdk-17.0.20.1+1"`。

## 快速开始（服务端）

```bash
# 1. 安装依赖（首次）
.venv/Scripts/python.exe -m pip install -r server/requirements.txt

# 2. 配置密钥
cp server/.env.example server/.env    # 然后填入 TRANSLATE_API_KEY

# 3. 环境自检
.venv/Scripts/python.exe scripts/check_env.py

# 4. 验证 ASR 链路（用自带测试音频）
.venv/Scripts/python.exe scripts/verify_asr.py

# 5. 验证翻译链路（需先在 server/.env 填入翻译 API 密钥）
.venv/Scripts/python.exe scripts/verify_translate.py
```

## 运行服务端（M1）

**Windows 一键启动**：双击仓库根目录的 **`start_server.bat`**。它会加载配置与模型、
打印手机端应填写的 IP（默认路由出口 + 其他候选），然后监听 8000 端口。

**停止/重启**：双击 **`stop_server.bat`**（按端口查找，只会结束 Python 进程并先确认）。

**端口被占用**：若提示 `[Errno 10048]`，说明已有一个服务端在运行。启动器会直接打印
占用端口的 PID 与三种处理方式（直接使用 / 停止后重启 / 改用其他端口），不再是原始 traceback。

```bash
# 启动（监听 0.0.0.0:8000，端点 /ws）——推荐，会打印手机端应填的 IP
.venv/Scripts/python.exe server/run_server.py
# 或直接 uvicorn（不打印 IP 提示）
(cd server && ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000)
#   状态接口：GET /health、GET /status

# 单元 / 集成测试（45 项）
(cd server && ../.venv/Scripts/python.exe -m pytest -q)

# 端到端自测：离线音频 → 真实识别 + 翻译 → 校验字幕 JSON，输出延迟报告
.venv/Scripts/python.exe scripts/e2e_test.py --repeats 2

# 不用 Android 客户端也能验证：向已启动的服务端灌入离线音频
.venv/Scripts/python.exe scripts/mock_client.py --wav server/tests/fixtures/sample_en.wav --realtime

# NFR-03 判据工具：ASR 词错率（WER） 与 翻译质量抽检（含人工评审表）
.venv/Scripts/python.exe scripts/eval_asr_wer.py
.venv/Scripts/python.exe scripts/eval_translation.py
```

服务端默认参数与预算依据见 [`docs/03-接口与通信协议设计.md`](docs/03-接口与通信协议设计.md) §9.4 与 §11；
M1 的实测数据（ASR P95 616ms、翻译 P95 961ms、端到端 P95 2209ms 等）见
[`docs/04-开发计划与任务分解.md`](docs/04-开发计划与任务分解.md) 的「M1 完成记录」。

### 跨网络连接（ZeroTier）

本机已加入 ZeroTier 网络（虚拟网卡 `10.243.9.41/24`）。手机不在同一局域网时，让手机也加入
同一个 ZeroTier 网络，即可把服务器地址填成 ZeroTier 地址。启动器会自动识别 ZeroTier 网卡并
把它排在「手机端填写」首位。

1. 手机安装 **ZeroTier One**，加入与主机相同的 Network ID，并在 my.zerotier.com 中授权该设备。
2. 首次使用：在 PC 上**以管理员身份运行** `allow_firewall.bat`。它只放行来自 ZeroTier 网段
   （`10.243.9.0/24`）的 TCP 8000 入站，不影响其他网络。
3. 双击 `start_server.bat` 启动服务端，手机端填写启动器打印的 `10.243.9.41:8000`。
4. 连通性自检：手机浏览器打开 `http://10.243.9.41:8000/health`，能看到 JSON 即成功。

> ZeroTier 在二层对流量加密，音频只在虚拟局域网内传输，与本项目"音频不出私有网络"的隐私定位一致；
> 但应用层仍是 `ws://` 明文，E1 的 wss 与 token 鉴权仍按扩展计划实施。

### 更换网络（热点 / 家用局域网）

不需要改代码：服务端监听 `0.0.0.0`，App 里的服务器地址与端口**可直接编辑并自动保存**。
换网络时通常只需要更新防火墙放行范围：

1. 双击 **`network_config.bat`**（会自动请求管理员权限）。
2. 选 `1`：查看本机网卡，找到与手机同网段的 IPv4（热点/家用 Wi-Fi 通常是 `WLAN`，
   例如 `192.168.49.5`）。
3. 选 `4` → `2`：选择该网卡对应的网段（默认路由所在网段会标出并排在第一）；临时演示也可选
   `1` 允许所有来源。
4. 若改了端口或监听地址：先 `stop_server.bat`，再 `start_server.bat` 重启。
5. 手机 App 填第 2 步看到的 IP 与端口。

> 热点网段（如 `192.168.43.x` / `192.168.49.x`）由手机分配，换热点后地址可能变化，
> 重新执行第 2 步确认即可。

## 构建 Android 客户端（M2）

```bash
cd android
export JAVA_HOME=/c/Users/38915/dev/jdk17/jdk-17.0.20.1+1   # 或你自己的 JDK 17
./gradlew assembleDebug          # 产物：app/build/outputs/apk/debug/bilingual-subtitle-v<版本>-debug.apk
./gradlew testDebugUnitTest      # 帧格式 / 重采样单元测试
./gradlew lintDebug              # 静态检查
```

安装、权限与已知限制见 [`android/README.md`](android/README.md)。
注意：**HarmonyOS NEXT（5/6）只能经 Android 容器运行**，媒体声音与跨应用悬浮窗大概率不可用；
HarmonyOS 4.x 及普通 Android 10+ 设备可正常安装。M3 验收建议使用 Android 真机。

## M3 联调（真机）

1. 重启服务端（`stop_server.bat` → `start_server.bat`），使跨会话统计与延迟修复生效；
2. 双击 **`monitor_m3.bat`** 开始采样（每 10s 记录 RSS/GPU 到 `logs/m3_monitor.csv`，默认 45 分钟）；
3. 按 [`docs/04-开发计划与任务分解.md`](docs/04-开发计划与任务分解.md) 的「M3 操作单」执行：
   媒体模式 ≥10 分钟 → 热切麦克风 ≥10 分钟 → 断网重连 → 服务端重启 → 停止/开始 → 悬浮窗操作；
4. 结束后访问 `http://127.0.0.1:8000/status` 与 `/latency`，连同 `logs/m3_monitor.csv` 交给 agent 分析。

### 网络说明

本项目的模型权重托管在 HuggingFace，国内网络需二选一：

```bash
# 方案 A：使用国内镜像（推荐）
export HF_ENDPOINT=https://hf-mirror.com

# 方案 B：走本地代理
export HTTPS_PROXY=http://127.0.0.1:10810
```

模型首次使用时自动下载并缓存到 `~/.cache/huggingface`，后续离线可用。

## AI 协作记录

课程要求提交人机协作过程记录。本仓库提供自动提取工具，从 ZCode 会话日志还原：

```bash
# 生成/更新 docs/AI协作记录.md
.venv/Scripts/python.exe scripts/export_collab_record.py

# 指定会话（默认取最新的会话日志）
.venv/Scripts/python.exe scripts/export_collab_record.py --session 812a17c4
```

输出内容按「轮次」组织，每轮含：用户提示词原文、助手回复、工具调用清单（工具名 + 参数摘要）、
模型调用异常记录。系统内部提示词、注入消息、推理过程与工具返回原文均已过滤。

> 建议**在项目收尾时重新运行一次**再提交，这样记录会自动包含全部开发过程；
> 中途生成的版本只覆盖到当时的进度。日志格式与拼接机制见脚本头部注释。

### 摘录的可校验性

生成的文件头部记录了**源日志的文件名、大小、行数、SHA256**，以及所用脚本与参数，
便于评审核验"摘录确实来自该日志"：

```bash
# 1. 计算原始会话日志的哈希，与记录头部所列比对
.venv/Scripts/python.exe -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" \
  ~/.zcode/cli/rollout/model-io-sess_<会话ID>.jsonl

# 2. 以相同参数重新生成，与原文件逐字比对
.venv/Scripts/python.exe scripts/export_collab_record.py --session <会话ID> --output /tmp/regenerated.md
diff /tmp/regenerated.md docs/AI协作记录.md   # 时间戳/哈希段会不同，正文应一致
```

头部统计数字由渲染结果反推，与正文条目严格一致：轮次数、用户提示词数、助手回复段数、
工具调用次数、异常次数均可逐一数验。

> 若课程要求提交原始日志，可附上 ZCode「导出日志」生成的 zip。注意该 zip 含**本机全部
> 会话**（不止本项目）与模型配置文件，提交前请确认内容范围。

## 目录结构

```
bilingual-subtitle/
├── docs/          # 设计文档（需求 / 架构 / 协议 / 计划）、状态交接说明、AI 协作记录
├── server/        # Python 服务端（M1 已实现）
│   ├── app/       # main / settings / protocol / dll / buffer / segmenter / asr / translate / session / connection / telemetry
│   ├── tests/     # 45 项单元与集成测试；fixtures 内含测试音频与语料
│   ├── .env.example
│   └── requirements.txt
├── android/       # Android 客户端（M2 已实现）：Gradle/Kotlin/Compose，见 android/README.md
├── scripts/       # 环境自检、ASR/翻译验证、模拟客户端、端到端测试、WER 与翻译抽检
└── README.md
```

## 安全约定

- **API 密钥只存服务端** `server/.env`，该文件已被 `.gitignore` 排除，严禁提交到仓库。
- 提交前请确认 `git status` 中没有 `.env`。

## 隐私设计

基础版架构下，音频仅在局域网内传输，发送到翻译 API 的只有识别后的**文本**；扩展为本地模型后，
音频与文本均不出内网，可满足企业保密场景（见 [`docs/01-需求规格说明书.md`](docs/01-需求规格说明书.md) §1.2）。

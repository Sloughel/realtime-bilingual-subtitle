# 实时双语字幕系统（Realtime Bilingual Subtitle）

手机采集音频 → 主机识别与翻译 → 手机悬浮窗显示实时双语字幕。

本仓库为软件工程课程项目。设计文档见 [`docs/`](docs/)。

## 项目状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 环境准备 | 已完成 |
| M1 | 服务端全链路（协议 + 断句 + ASR + 翻译） | 未开始 |
| M2 | Android 客户端（双采集模式 + 悬浮窗） | 未开始 |
| M3 | 端到端联调 | 未开始 |
| M4 | 扩展（互联网 / 本地模型 / Linux / iOS·鸿蒙） | 未开始 |
| M5 | 报告与演示 | 未开始 |

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
├── docs/          # 设计文档（需求 / 架构 / 协议 / 计划）
├── server/        # Python 服务端
│   ├── app/       # 应用代码（M1 阶段实现）
│   ├── tests/     # 测试与测试音频
│   ├── .env.example
│   └── requirements.txt
├── android/       # Android 客户端（M2 阶段实现）
├── scripts/       # 环境自检、ASR 验证、模拟客户端等工具
└── README.md
```

## 安全约定

- **API 密钥只存服务端** `server/.env`，该文件已被 `.gitignore` 排除，严禁提交到仓库。
- 提交前请确认 `git status` 中没有 `.env`。

## 隐私设计

基础版架构下，音频仅在局域网内传输，发送到翻译 API 的只有识别后的**文本**；扩展为本地模型后，
音频与文本均不出内网，可满足企业保密场景（见 [`docs/01-需求规格说明书.md`](docs/01-需求规格说明书.md) §1.2）。

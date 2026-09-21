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

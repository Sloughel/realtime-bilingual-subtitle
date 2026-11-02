# 贡献指南

面向二次开发者与组内后续维护者。**先读 [`docs/09-扩展点与二次开发指南.md`](docs/09-扩展点与二次开发指南.md)**，
那里写了"改哪里"；本文只写"怎么改才不会被退回"。

## 1. 环境

```bash
# Linux / macOS
python3 -m venv .venv
.venv/bin/python -m pip install -r server/requirements.txt
cp server/.env.example server/.env      # 填 TRANSLATE_API_KEY 或改用本地 Ollama

# Windows（PowerShell / cmd）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r server\requirements.txt
```

GPU 机器：`WHISPER_DEVICE=cuda`（默认），CUDA 12 + cuDNN 9 的运行时由 requirements 提供。
CPU 机器：`WHISPER_DEVICE=cpu`、`WHISPER_COMPUTE_TYPE=int8`，延迟会明显变高。

> 若 `faster-whisper` 在 `decode_audio()` 处报
> `TypeError: open() got an unexpected keyword argument 'metadata_errors'`，
> 是 PyAV 版本太新：`pip install "av==13.1.0"`（详见 `server/requirements.txt` 注释）。

## 2. 提交前自查清单

```bash
# 1) 全量测试（当前基线：75 passed）
cd server && ../.venv/bin/python -m pytest --tb=no

# 2) 静态检查（Android 侧）
cd android && ./gradlew lintDebug testDebugUnitTest

# 3) 密钥与用户数据不得入库
git status --short
git diff --cached | grep -iE "sk-[a-z0-9]{10,}|AUTH_TOKEN=.{8,}" && echo "发现疑似密钥，停止提交"
```

- `server/.env`、`certs/`、`logs/`、`*.key` 均在 `.gitignore` 内，**不要** `git add -f`。
- 提交信息用中文或英文均可，但要说清"改了什么 + 为什么"；一个提交只做一件事。
- 不要提交模型权重、测试音频以外的音频文件、APK 产物。

## 3. 代码约定

### 3.1 服务端

- **失败不抛异常**：翻译/识别失败一律降级并返回可判定的结果（`degraded=True` 等），
  由 `session.py` 决定要不要发 `error`。异常只用于"配置错误、模型缺失"这类启动期问题。
- **协议字段只增不改**：`protocol.py` 的常量与消息结构是客户端兼容契约；新增字段必须可选，
  旧客户端忽略即可。不要改已有字段的类型或语义（03 文档 §12）。
- **不打印密钥**：日志里只允许出现"已配置/未配置"、长度、后端名。任何 `print(settings.xxx)`
  形式的调试代码都不接受。
- **配置项集中在 `settings.py`**：新增开关走 `Settings` + `.env.example` + 03 文档 §9.4 三处同步，
  并在 `runtime_warnings()` 里给出"配错了会怎样"的提示。
- 新增公开行为要有测试；测试**不许联网**（用替身客户端或本地 HTTP 桩，
  参考 `tests/test_ollama_local_http.py`）。

### 3.2 Android

- 采集层只负责"按时送 100ms / 1600 采样点"，帧化交给 `FrameAccumulator`，断句在服务端。
- 新增 UI 开关写到 `AppState`（单一状态源），不要在多处 remember 局部状态；
  `WsClient` 从 `AppState.state.value` 读取，避免参数层层透传（E1 的 token/wss 就是这么做的）。
- Kotlin 侧改动请附一次 `assembleDebug` 通过的结果——当前仓库的 Android 改动尚未在
  本机编译过（见 10 文档 §6 待办 1）。

## 4. 文档同步

改到以下内容时，请顺手更新对应文档，不要留"代码与文档不一致"：

| 改动 | 需要同步 |
|---|---|
| 协议字段 / 错误码 | `docs/03-接口与通信协议设计.md` §2/§6 + 变更记录 |
| 新增配置项 | `server/.env.example`、`docs/03` §9.4 |
| 新增扩展面 | `docs/09-扩展点与二次开发指南.md` |
| 里程碑状态 | `README.md` 状态表、`docs/04`、`docs/05` |

## 5. 隐私红线

- 音频与字幕属于用户数据：默认**不落盘**（`SUBTITLE_HISTORY=false`），需要落盘时由部署者显式开启。
- 提交代码、日志、截图、Issue 时不要带上真实的字幕内容、内网 IP、令牌。
- 不要把明文 `ws://` 直接暴露到公网：跨网络必须 `wss` **且** 配置 `AUTH_TOKEN`
  （E1 的完整定义，见 03 文档 §2）。

## 6. 许可

提交即表示同意以仓库的 MIT 协议发布。

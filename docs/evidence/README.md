# 测试证据

本目录保存报告中关键结论的**原始数据副本**，便于评审核对。仓库的 `logs/` 目录被
`.gitignore` 排除，因此这里保存的是测试结束后从本机复制出的稳定快照，不参与运行时写入。

| 文件 | 内容 | 来源 |
|---|---|---|
| `m3_final_status.json` | M3 最终 `/status`：83 句延迟分位、错误/降级计数、token 与成本 | 2026-10-19 从 `http://127.0.0.1:8000/status` 获取 |
| `m3_final_latency.json` | M3 最终 `/latency`：86 条逐句记录（含 3 条空识别） | 同上，`/latency` |
| `m3_monitor.csv` | M3 真机期间 RSS/GPU 采样（20:15:49–20:27:05，10s 间隔） | `scripts/monitor_server.py` |
| `m5_soak_monitor.csv` | NFR-04 30 分钟满载期间 RSS/GPU 采样（8010 实例，10s 间隔） | `scripts/monitor_server.py --port 8010` |
| `m5_soak_summary.md` | NFR-04 采样汇总（时长、轮次、句数、RSS/GPU 区间、错误） | 由 `m5_soak_monitor.csv` 与 `m5_soak_client.log` 分析生成 |
| `20min_media_status.json` / `20min_media_latency.json` | 2026-10-26 20 分钟媒体模式诊断的 `/status` 与逐句记录 | 真机测试后保存 |
| `20min_media_monitor.csv` | 上述诊断期间 RSS/GPU 采样（11.5 分钟，非完整 20 分钟） | `scripts/monitor_server.py` |
| `20min_media_summary.md` | 诊断汇总：154 句、raw P95 5236ms、剔除 9 条离群后 2523ms、媒体重连缺陷定位 | 分析生成 |

说明：

- `/latency` 记录只含句序号、音频区间与各阶段耗时，**不含**原文、译文或任何密钥；
- `m3_final_status.json` 中的成本为按配置单价折算的估算值，不是账单；
- M3 的 83 句为有效句，另有 3 条空识别记录保留在 `/latency` 中，不计入延迟分位；
- M5 的 30 分钟采样使用独立服务实例（127.0.0.1:8010），不污染 M3 的 8000 端口统计数据；
- 采样主机为开发机（RTX 4060 8GB），其他硬件的绝对延迟会不同，但方法与判据可复用。

复现方式：

```bash
# M3 / M5 资源采样
.venv/Scripts/python.exe scripts/monitor_server.py --port 8000 --duration-min 30 --out logs/soak.csv

# 取状态与逐句明细
curl -s http://127.0.0.1:8000/status  > logs/status.json
curl -s http://127.0.0.1:8000/latency > logs/latency.json

# 30 分钟持续音频负载（另起实例时把端口改为 8010）
.venv/Scripts/python.exe scripts/mock_client.py --port 8010 --wav server/tests/fixtures/sample_en.wav --realtime
```

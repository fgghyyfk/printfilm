# 数字人 GPU 推理服务

把 InfiniteTalk / LongCat-Video-Avatar 1.5 包成 HTTP 服务，供主站「数字人口播」模式调用：照片 + 一段配音 → 对口型视频。

## 前提

在 GPU 机器（Linux 或 Windows 的 WSL2 Ubuntu）上，先用 `../bench/setup_all.sh` 装好两个模型的 conda 环境与权重（见 `../bench/README.md`）。
24GB 显卡（3090/4090）会自动走低显存模式，内存建议 ≥ 64GB（WSL 需在 `.wslconfig` 给到 50GB 以上并开 swap）。

## 启动

```bash
cd deploy/avatar-gpu/server
AVATAR_API_KEY=$(openssl rand -hex 16) AVATAR_PRELOAD=1 AVATAR_WARMUP=1 bash start.sh
```

- 默认监听 `0.0.0.0:8780`，默认引擎 `infinitetalk`（`AVATAR_DEFAULT_ENGINE=longcat` 可改）
- `AVATAR_PRELOAD=1`：启动即加载默认引擎（约 15 分钟），否则第一个任务时再加载
- `AVATAR_WARMUP=1`（InfiniteTalk）：加载后先空跑一段预热。24GB 卡加载后部分权重在 swap 里，首次推理特别慢（3090 实测首段 29 分钟、之后约 6 分钟/段），预热把这段时间放在启动阶段
- 任务与成片在 `server/data/`，成片保留 3 天（`AVATAR_KEEP_SECONDS`）
- 日志：服务输出在前台；模型工作进程日志在 `data/worker_<引擎>.log`

## 主站配置

管理后台「系统设置 → 数字人」：填服务地址（如 `http://10.10.1.84:8780`）与上面的 API Key，保存后点「测试连接」。

WSL2 默认只把端口转发到 Windows 本机的 localhost，局域网其它机器访问需要在 Windows 管理员 PowerShell 里加端口转发与防火墙规则：

```powershell
$ip = (wsl -d Ubuntu-22.04 hostname -I).Trim().Split(" ")[0]
netsh interface portproxy add v4tov4 listenport=8780 listenaddress=0.0.0.0 connectport=8780 connectaddress=$ip
New-NetFirewallRule -DisplayName "avatar-gpu 8780" -Direction Inbound -Protocol TCP -LocalPort 8780 -Action Allow
```

（WSL 重启后 IP 会变，需要重跑第一、二行。）临时联调也可以在主站机器上开 SSH 隧道：`ssh -N -L 8780:localhost:8780 user@gpu-host`。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/jobs` | multipart：`image`、`audio` 文件；`engine`（infinitetalk/longcat）、`prompt`、`resolution`（480p/720p） |
| GET | `/v1/jobs/{id}` | `status`：queued / loading / running / succeeded / failed，另有 `progress`、`message`、`error`、`queue_position` |
| GET | `/v1/jobs/{id}/video` | 下载成片 mp4 |
| DELETE | `/v1/jobs/{id}` | 取消排队中的任务 |
| GET | `/v1/health` | 当前引擎、队列长度（不需鉴权） |

设置了 `AVATAR_API_KEY` 时，除 health 外都要带 `Authorization: Bearer <key>`。

## 运行方式

- 同一时间只跑一个任务，其余排队；模型加载一次后常驻
- 两个引擎各在自己的 conda 环境里跑一个工作进程；任务换引擎时先关掉旧进程再加载新引擎（24GB 显卡放不下两套）
- 服务重启时，未完成的任务标记失败；主站流水线会在下次继续时重新提交这些段

## 3090（WSL2）实测参考

| 引擎 | 首次加载 | 生成速度 | 显存峰值 |
|---|---|---|---|
| InfiniteTalk 4 步加速 | 约 16 分钟（+ 首次推理预热约 25 分钟） | 预热后约 2 分钟 / 秒成片（每 81 帧片段 5.5–8 分钟）；单次跑 46.7 秒含首段共 160 分钟 | 15.8GB |
| LongCat 1.5 INT8 8 步 | 约 13 分钟 | 约 2 分钟 / 秒成片（46.7 秒用 99 分钟） | 16.4GB |

单卡 24GB 适合试用和小批量；量产建议 A100 80G / 4090 多卡，或租云 GPU。

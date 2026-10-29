# 数字人 P0 实测（云 GPU）

目的：用同一份约 60 秒的中文口播，横向对比几种数字人方案，用数据决定量产用哪个引擎。对比维度是口型准确度、人脸稳定性、速度和每分钟成本。背景和选型见 `docs/superpowers/plans/2026-10-28-avatar-talking-video.md`。

| 用例 | 模式 | 说明 |
|---|---|---|
| `longcat_i2v_480p` | 照片 → 视频 | LongCat-Video-Avatar 1.5，8 步蒸馏 + INT8 |
| `infinitetalk_i2v_480_fast` | 照片 → 视频 | InfiniteTalk + lightx2v 4 步 LoRA（量产速度） |
| `infinitetalk_v2v_480_fast` | 底片 → 换台词 | InfiniteTalk 视频配音（需要底片） |
| `latentsync_v2v` | 底片 → 只改嘴型 | LatentSync 1.6（需要底片） |
| `seedance` | 照片 + 音色 → 视频 | 现有 Seedance 2.5，本地通过开放 API 跑，作为基线 |

可选的高画质档：`longcat_i2v_720p`、`infinitetalk_i2v_480`（官方 40 步，很慢）、`infinitetalk_i2v_720_fast`。

## 0. 开机器

- **推荐配置**：2 张 80GB 卡（A100、H100、H800 或 A800），Ubuntu 22.04，CUDA 12.x 驱动，**数据盘 ≥ 250GB**。权重约 145GB，另加 conda 环境和输出。LongCat 官方示例用的就是 2 卡，InfiniteTalk 多卡会更快。
- **单卡 80GB**：设 `NGPU=1` 也能跑，但官方没有给出单卡显存数据，未验证。
- **单卡 24GB**（RTX 3090/4090，含 Windows + WSL2）：脚本检测到单卡显存低于 40GB 会自动进入低显存模式。
  - LongCat：文本编码器放到 CPU 上跑，补丁见 `patch_longcat_lowvram.py`。
  - InfiniteTalk：开 `--num_persistent_param_in_dit 0`，DiT 参数按需从内存搬进显存，速度明显变慢。
  - 内存需要 64GB 以上。WSL 默认只给一半内存，要在 Windows 的 `%USERPROFILE%\.wslconfig` 里写 `memory=56GB`、`swap=48GB`，然后执行 `wsl --shutdown` 生效。
- **国内网络**（国内云或局域网机器）：安装前先 `export USE_CN_MIRROR=1`，apt、pip、conda 和 HuggingFace 都会改走国内镜像。只想让权重走镜像，就只设 `USE_HF_MIRROR=1`。
- 计费按开机时长算，测完记得**关机或释放**。

## 1. 本地准备素材（Mac）

把以下素材准备好放到本地：

- **半身照**：竖屏，正脸清晰，背景就是想要的场景。只有本人或已授权的形象才能用。
- **口播稿**：可改 `inputs/script.txt`，约 240 字对应 1 分钟。
- **可选底片**：本人对着镜头自然说话 60 秒以上的竖屏视频，内容随意，固定机位，用于两个「底片」用例。
- **画面描述**：`inputs/prompt.txt` 是英文画面描述，按照片内容改一下（人物外貌、服装、场景）。

然后在 `backend/` 目录下运行：

```bash
.venv/bin/python ../deploy/avatar-gpu/bench/prepare_inputs.py --portrait ~/Desktop/portrait.jpg --base-video ~/Desktop/base.mov
```

关于配音：
- 默认用 edge-tts 按口播稿合成配音。
- edge-tts 7.2.3 连不上微软服务，需升级到 7.3.1（修复在 `fix/edge-tts-7.3.1` 分支）。仍失败时脚本会自动改用 macOS 的「婷婷」语音，只适合跑通流程。
- 正式对比建议加 `--audio 录音或项目配音.wav`，用真人录音或项目 TTS 的成品。

产出都在 `inputs/`：`audio.wav`、`voice_ref.wav`、`portrait.png`、`base.mp4`、`manifest.json`。这些文件已被 .gitignore 忽略，不会提交。

## 2. 上传到 GPU 机

```bash
rsync -av --exclude results deploy/avatar-gpu/bench/ root@<GPU机IP>:/root/bench/
```

## 3. GPU 机安装

建议在 tmux 里跑，断线不会中断。下载加安装通常要 1–3 小时，取决于带宽。

```bash
cd /root/bench
bash setup_common.sh
bash setup_longcat.sh && bash setup_infinitetalk.sh && bash setup_latentsync.sh
```

- 三个模型的代码和权重都放在 `/root/avatar-bench`，可用 `WORK_DIR` 改到数据盘，例如 `export WORK_DIR=/root/autodl-tmp/avatar-bench`。
- flash-attn 如果拉不到预编译包会本地编译，比较慢，属于正常现象。

## 4. 先冒烟、再全量

先用 10 秒音频冒烟，确认每个用例都能跑通：

```bash
LIMIT_SEC=10 bash run_bench.sh
```

冒烟通过后跑全量（全长音频、默认 4 个用例）：

```bash
bash run_bench.sh
```

只跑指定用例：

```bash
CASES="longcat_i2v_720p infinitetalk_i2v_480" bash run_bench.sh
```

- 每个用例的结果在 `results/<用例>/`：`output.mp4` 是成片，`run.log` 是日志，`meta.json` 记录耗时和各卡显存峰值。
- 某个用例失败会打印日志末尾 20 行，并继续跑下一个。

## 5. Seedance 基线（本地跑，可与第 4 步同时进行）

前置条件：
- 本地后端在运行。
- 已在用户端「API Key」页创建 Key。注意这会正常扣费。
- 半身照和 `inputs/voice_ref.wav` 已上传到 OSS 或图床，拿到 https 链接。Seedance 拉不到本机文件。

在 `backend/` 目录下运行：

```bash
PF_API_KEY=<你的Key> .venv/bin/python ../deploy/avatar-gpu/bench/seedance_baseline.py --image-url https://.../portrait.png --voice-url https://.../voice_ref.wav
```

- 加 `--dry-run` 可以先只看分段和请求体，不提交。
- 跑完后把结果传到 GPU 机，和其它用例一起评分：

```bash
rsync -av deploy/avatar-gpu/bench/results/seedance root@<GPU机IP>:/root/bench/results/
```

## 6. 评分

`GPU_PRICE` 填每卡每小时的价格（元），用来估算每分钟成本：

```bash
GPU_PRICE=8 bash score.sh
```

- **`results/report.md`**：客观指标对比表，附一张主观打分表。
  - 口型同步：SyncNet 置信度。
  - 人脸一致性：ArcFace 相似度，看均值和最低值。
  - 偏色：前后 5 秒的色差 ΔE。
  - 实时倍数、显存峰值、每分钟成本。
- **`results/compare_grid.mp4`**：所有用例并排的宫格视频，配统一的口播音轨。Seedance 的口型对应的是它自己生成的声音，在宫格里看它的口型不准，要单独打开它的 `output.mp4` 看。

把 `results/` 拉回本地，看完宫格视频后填主观打分：

```bash
rsync -av root@<GPU机IP>:/root/bench/results/ deploy/avatar-gpu/bench/results/
```

## 7. 收尾

- 关机或释放 GPU 实例。
- 带着 `report.md` 和填好的主观分回来定引擎，然后进入 P1 MVP。

## 常见问题

| 现象 | 处理 |
|---|---|
| 显存不足（OOM） | LongCat 保持默认 INT8；单卡显存低于 40GB 时脚本自动进入低显存模式，也可手动设 `LOW_VRAM=1` 强制开启 |
| 进程被系统杀掉（内存不足） | InfiniteTalk 的安装脚本已打补丁，权重改为逐个分片加载；内存仍不够就加大 swap |
| HuggingFace 下载慢或失败 | `export USE_HF_MIRROR=1` 后重跑对应的 setup，已下载的文件会跳过 |
| SyncNet 报 Face not detected | 画面里人脸太小或被遮挡，换一张脸部更大的半身照 |
| InfiniteTalk 超过 1 分钟后偏色 | 官方已知问题，会在偏色 ΔE 上体现；量产时按段生成后再拼接 |
| WSL2 下 24GB 卡分配显存提前失败 | 脚本已在 WSL 下改用 `PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync`（`expandable_segments` 在 WSL 会提前失败，默认分配器只能用到约 14.6GB） |
| LongCat 在 24GB 卡上 OOM | 低显存补丁：文本编码器与 whisper 放 CPU（fp32），INT8 DiT 按层换入显存，前 `LONGCAT_RESIDENT_BLOCKS`（默认 4）层常驻，续写 KV 缓存放内存；不要锁页内存（WSL 下锁 10GB 要一个多小时） |
| InfiniteTalk 卡在文本编码很久、显卡 0% | T5 在 CPU 上用 bf16 极慢；补丁已改为 CPU 上转 fp32 计算，并修正官方 CPU 分支返回列表导致的报错 |
| InfiniteTalk 导入报 `torch has no attribute accelerator` | 官方依赖只写下限，装到了新版；安装脚本已锁 `diffusers==0.34.0 transformers==4.51.3 peft==0.15.2 xfuser==0.4.1` |

## 量产服务

实测选定引擎后，用 `../server/` 把模型包成常驻 HTTP 服务供主站调用，见 `../server/README.md`。

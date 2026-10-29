# 数字人口播视频方案（调研 + 实施计划）

状态：P0 实测完成，P1 MVP 已接入（2026-10-29）。

- GPU：局域网 Win10 + RTX 3090（WSL2），实测见 `deploy/avatar-gpu/server/README.md` 末尾
- 引擎：InfiniteTalk、LongCat 1.5 都接入，后台设默认、项目可单独选
- 先做照片数字人；真人分身（视频→视频配音）留到 P2

### P1 实际落地

| 部分 | 位置 |
|---|---|
| GPU 推理服务（提交 / 查询 / 下载，模型常驻，单卡串行） | `deploy/avatar-gpu/server/` |
| 主站客户端（含 ARK_MOCK 模拟） | `backend/app/services/avatar_video.py` |
| 科普 `pipeline_mode=avatar`：逐段配音、逐段对口型 | `backend/app/services/kepu_avatar.py`，接入 `pipeline.py` 各阶段入口 |
| 阶段就绪：数字人要求每段单独配音 | `kepu_stages.project_audio_ready` |
| 合成：中部标题条（黄 + 白两行）、逐句字幕、裁满竖屏 / 宽画面模糊填充 | `ffmpeg_compose.py`（`subtitle_layout=band`、`video_fit=cover`） |
| 数据：`projects.avatar_image_url / avatar_engine`、`shots.avatar_job_id` | `models.py` + `main._apply_schema_patches` |
| 接口：上传半身照、服务状态 | `backend/app/api/project_avatar.py` |
| 后台：系统设置 → 数字人（地址、Key、默认引擎、分辨率、段长、单价） | `admin/src/components/settings/AvatarSettingsPanel.tsx` |
| 用户端：风格页「数字人口播」+ 照片上传 + 引擎选择 | `frontend/src/components/studio/AvatarSetupBlock.tsx` |

与原计划的差异：段视频没有走 `awaiting_poll` 注册（科普现有视频阶段也是流水线内等待），改为流水线内轮询，
GPU 任务 id 记在 `shots.avatar_job_id`，进程重启后接着等同一任务；形象库（`avatars` 表）暂未做，先按项目上传照片。

## 1. 目标形态

参考视频号「董小姐的长寿笔记」（A2gRegmLir）：

- 竖屏 9:16，真人半身固定机位，场景是高级感室内背景
- 中部有一条半透明深色底的两行大字标题条，第一行黄色、第二行白色，带描边
- 观点类口播，时长 1–2 分钟

要做到：一键从「主题/口播稿 + 选定数字人」生成成片，包括配音、对口型视频、标题条、逐句字幕、可选 BGM 和 B-roll。

## 2. 现状

- 工程里没有数字人或对口型能力。
- TokenFree（44 个模型）和 BytePlus（文本、Seedream、Seedance）都没有对口型模型。
- 唯一能凑合的近似方案：Seedance 2.5 的 r2v，参考图加参考音频，原生配音时口型对的是它自己生成的声音。局限是台词可能被改动，不同片段之间人脸会轻微变化。

## 3. 开源模型调研（2026-10）

| 项目 | 输入 | 许可（代码 / 权重） | 中文 | 长视频 | 资源 | 结论 |
|---|---|---|---|---|---|---|
| [LongCat-Video-Avatar 1.5](https://huggingface.co/meituan-longcat/LongCat-Video-Avatar-1.5)（美团，2026-05） | 图 + 音频 + 文本；视频续写 | MIT / MIT | ✅ Whisper-Large 编码，中英评测 | ✅ 分段续写 | 480P/720P；8 步蒸馏；INT8；官方示例是 2 卡 | **首选（照片数字人）**，效果最新，需要实测显存和速度 |
| [InfiniteTalk](https://github.com/MeiGen-AI/InfiniteTalk)（美团 MeiGen） | 图→视频；**视频→视频配音** | Apache-2.0 / Apache-2.0 | ✅ chinese-wav2vec2 | ✅ 不限长（图生约 1 分钟后偏色） | Wan2.1-14B；480/720P；FP8、多卡；fal 托管 $0.2/秒 | **首选（真人分身 + 照片）**，生态成熟（ComfyUI、fal） |
| [LatentSync 1.6](https://github.com/bytedance/LatentSync)（字节） | **仅视频→视频**，只改嘴部 | Apache-2.0 / 页面未写明 | ✅ 1.5 起优化中文 | 按视频长度 | 512×512；推理 18GB；fal 托管 40 秒内 $0.2 | **真人分身的低成本方案**，权重许可需要再确认 |
| [Duix-Avatar](https://github.com/duixcom/Duix-Avatar)（硅基） | 真人视频训练分身 + 文本/音频 | 自定义：用户超 10 万或年收入超 1000 万美元需另签 | ✅ | — | Docker，约 70GB 镜像，4070 起；本地 HTTP 接口 | 备选：开箱即用，但部署重、协议有上限 |
| [MuseTalk](https://github.com/TMElyralab/MuseTalk)（腾讯音乐） | 视频/图 + 音频 | MIT | ✅ | — | 约 12GB | 偏实时直播，离线成片画质不如上面几个 |
| [Wan2.2-S2V](https://github.com/Wan-Video/Wan2.2) | 图 + 音频 | Apache-2.0 | ✅ | 有限 | 14B | 备选 |
| [EchoMimic v3](https://github.com/antgroup/echomimic_v3)（蚂蚁） | 图 + 音频 | Apache-2.0 | ✅ | 有限 | 较轻 | 备选 |
| Sonic | — | **CC BY-NC（禁止商用）** | | | | 排除 |
| HunyuanVideo-Avatar | — | 腾讯社区协议，有地域限制 | | | | 排除 |
| Wav2Lip / SadTalker | — | 研究用途 / 老旧，嘴部只有 96px | | | | 排除 |
| AptAvatar（淘宝直播） | 图 + 音频，2 步推理 | Apache-2.0 / **权重未发布** | ✅ | ✅ | | 持续关注 |
| OpenTalking | 实时对话框架，封装 Wav2Lip/MuseTalk 等 | Apache-2.0 | | | | 方向是实时交互，不需要 |

可借鉴的工程实践（来自 [digital-human-koubo](https://github.com/andyhuo520/digital-human-koubo)，MIT）：

- 每段不超过 15 秒
- 先出第 1 段确认，再批量生成
- 用 whisper 回听，比对台词
- 统一各段音色，响度标准化到 -14 LUFS
- 字幕按字级时间轴生成

## 4. 两种生产模式

1. **照片数字人**：一张半身照 + TTS 音频，用 LongCat-Avatar 或 InfiniteTalk 的图生视频。门槛低，可以批量做 IP。
2. **真人分身**（最接近参考视频）：用户录一段 30–60 秒的底片，之后每篇新稿只做「视频→视频配音」，用 InfiniteTalk 或 LatentSync。真实感最高，口型精确对齐我们自己的 TTS 音频。

## 5. 架构

```
口播稿 ─▶ 切段(≤15~25s) ─▶ TTS(qwen-tts/edge-tts) ─▶ AvatarEngine.submit(图或底片, 音频)
                                                     │  awaiting_poll（复用 poller）
                                                     ▼
                         片段视频 ─▶ whisper 校对 ─▶ ffmpeg：拼接 + 响度 + 标题条 + 逐句字幕 + BGM/B-roll ─▶ 成片
```

- **AvatarEngine 接口**：`submit()` 和 `poll()`。实现包括：
  - `seedance`：现成，作为兜底
  - `infinitetalk`
  - `longcat`
  - `latentsync`
  - `fal`：托管 API，不自建 GPU 时的过渡方案
- **GPU 推理服务**：新建 `deploy/avatar-gpu/`，用一个很薄的 FastAPI 封装 `/submit` 和 `/status`，单独部署在 GPU 机器上。主站只把它当作一个上游。
- **渠道配置**：在 `provider_profiles.py` 新增 `avatar` 能力和 provider 类型（地址 + Key），在后台配置。延续 BytePlus 提交的做法，不跨渠道自动回退。
- **任务**：在 `handlers.py` 登记 `("kepu","avatar_segment")`，一段一个 TaskRun。科普新增 `pipeline_mode=avatar`，在 `kepu_stages.py` 定义阶段判断，计费预扣和流水线恢复两边同步。
- **数据**：
  - `avatars` 表：名称、半身照或底片、场景、音色、标题样式、授权确认
  - `_apply_schema_patches()` 和 conftest 同步修改
- **合成**：在 `ffmpeg_compose.py` 外新建 `avatar_compose.py`，不再往大文件里加代码。
  - 标题条模板
  - 逐句字幕，复用 `_timed_caption_windows`
  - 片段之间做中景和近景交替，盖住接缝
- **前端**：
  - 数字人形象库页面
  - 创建项目时选「数字人口播」
  - 标题条预览
- **合规**：上传真人形象时必须勾选本人授权；成片默认带「AI 生成」标识。

## 6. 分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 实测（1–2 天） | 租 1 台 A100 80G 或 2 张 4090。用同一份 60 秒中文口播，跑 LongCat-Avatar、InfiniteTalk 的图生视频，再用一段录制底片跑 InfiniteTalk 和 LatentSync 的视频配音，另跑 Seedance 作为基线 | 对比表：口型准确度、人脸稳定性、1 分钟视频耗时、单分钟成本 |
| P1 MVP | 形象库 + AvatarEngine（选定引擎 + seedance 兜底）+ GPU 服务 + 标题条/字幕合成 | 一键出参考视频风格的纯口播成片 |
| P2 | 真人分身模式、B-roll 插画面、多套标题样式、片头片尾 | |
| P3 | whisper 台词校对和自动重生成、音色克隆、响度统一 | |

## 7. 成本参考

- fal 托管 InfiniteTalk 480P 为 $0.2/秒，1 分钟约 $12，只适合试用。
- LatentSync 托管很便宜，40 秒内 $0.2。
- 批量生产建议自建 GPU，P0 实测后再算单分钟成本。

## 8. 待确认

1. GPU 资源：用云 GPU（按量计费或常驻）还是自有显卡？
2. P1 先做哪种模式：照片数字人，还是真人分身？
3. LatentSync 权重的商用许可需要到 HuggingFace 模型卡二次确认。

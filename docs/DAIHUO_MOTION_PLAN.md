# 商品带货：动态展示视频（方案）

> 状态：**P1/P2 已实现，真机视频验收待开通视频模型** · 2026-11-10
> 前置：带货 P1 静图口播片已上线（`docs/DAIHUO_FLOW.md`）；电商工作台 E0–E6 已完成（`docs/ECOM_SUITE_PLAN.md`）

## 1. 目标

现在的带货视频只用静图加推拉镜头，看起来像幻灯片。本方案让带货视频的画面真正动起来，同时保住两样东西：

1. **商品不走样**：外形、颜色、logo 与套图一致。
2. **口播闭环不变**：仍然是「AI 写口播 → 配音 → 字幕 → 合成」，这一点是我们和竞品的差异化。

不做：数字人带货（原 P2，另行排期）、服饰模特上身（原 P3）。

## 2. 视频形式

在现有视频设置面板（`components/daihuo/DaihuoVideoPanel.tsx`，目前只有画幅和音色）新增「视频形式」三选一：

| 形式 | 画面 | 费用 | 适用 |
|---|---|---|---|
| 静图口播（现有，默认） | 每张图配推拉镜头 | 写稿 + 配音 | 预算敏感、商品细节多 |
| **混合展示（推荐）** | 第 1 镜和 AI 标出的 1～2 个卖点镜头做动态视频，其余用静图推拉 | 写稿 + 配音 + 2～3 段视频 | 大多数商品 |
| 全动态 | 每一镜都做动态视频 | 写稿 + 配音 + 每镜视频 | 外形简单、强调质感的商品 |

用户在分镜编辑页可以逐镜切换「动态 / 静图」，切换后只对新改成动态的镜头补生成视频。

## 3. 关键设计

### 3.1 复用 `full` 流水线，不新增模式

带货项目选择混合展示或全动态时，`pipeline_mode` 设为 `full`，静图口播仍为 `image_text`。理由如下：

- `resolve_kepu_billing_phase()` 已有 `script → assets → videos → compose` 四段。带货分镜建好时图已就绪，会自然进入视频阶段，计费与恢复共用同一套判定。
- 逐镜视频已有任务号持久化、超时收尾、隐私拦截降级（`kepu_video_tasks.py`、`pipeline.py` 视频阶段），不用重写。

**静图镜头的标记**：给 `video_skip_reason` 新增取值 `static`（与已有的 `privacy` 并列）。

- `kepu_stages.shot_video_skipped()` 把 `static` 也视为已收敛，`_estimate_videos_fen` 自然不再为这些镜头预扣费用。
- 视频阶段遇到 `static` 直接跳过，不提交上游，也**不**触发隐私降级配音（`kepu_privacy_audio` 只处理 `privacy`）。

### 3.2 声音：带货一律后期配音

`full` 模式默认让 Seedance 自带口播（`_kepu_seedance_native_audio`），带货必须关掉，否则口播会出现两遍。

- 新增 `_is_daihuo(project)`（`ecom_set_id` 非空，或模板为 `DAIHUO_TEMPLATE_ID`），在 `_kepu_seedance_native_audio` 与 `_kepu_seedance_sfx_audio` 中与 `image_text` / `avatar` 一样返回 False。
- 结果：视频请求 `generate_audio=false`，口播走现有 TTS，`project_audio_ready` 判定不变。
- 不加音效。商品演示（倒水、开盖）的音效后续视实测效果再开，默认不要。

### 3.3 动作描述：写稿时一起产出

`build_daihuo_shots` 目前写死 `video_prompt=""`、`camera="slow push in"`。改为：

- `daihuo/script.py` 的写稿 prompt 让 LLM 为每段多输出两个字段：`motion`（是否建议做动态，布尔值）和 `video_prompt`（一句商品动作描述）。
- 动作只能从**商品动作白名单**中选，并补充具体对象：环绕展示、缓慢推近细节、微距扫过材质、光影扫过表面、手部拿起展示、使用演示（倒水、开盖、按压等，仅限商品本身支持的动作）。白名单写在新文件 `daihuo/motion.py`，同时提供 mock 输出。
- 统一追加约束句：「商品外形、颜色、logo 与参考图完全一致，不新增文字和其他品牌标识，镜头稳定」。
- 输出照常走 `sanitize_shot_fields(mode=REPLACE)` 做极限词替换。
- 混合模式下，以 LLM 给出的 `motion` 为准，但强制首镜为动态，动态镜头总数不超过 3 个；全动态模式下忽略 `motion`。

### 3.4 首帧图：不能用补边图

现在为了适配画幅，套图会被补成「原图居中 + 同图模糊背景」。这张图如果拿去生成视频，模型会把模糊边也一起动起来，效果很差。动态镜头的首帧按以下顺序取：

1. 原图画幅已等于视频画幅（例如 9:16 套图做 9:16 视频）：直接用无字底图。
2. 画幅不同：用 Seedream 以底图为商品参考，扩图成目标画幅的满幅场景图。复用 `ecom_jobs` 的结构化参考图通路（`image_ref_inline.py` 内嵌发送），按张计入出图费用。
3. 扩图失败：该镜回退为静图，标记 `static`，并在编辑页提示原因。

静图镜头仍沿用补边图，不变。

### 3.5 首帧还是参考图（P0 实验决定）

`ark.gen_video_i2v` 在传了画幅时会把图片当 `reference_image` 发送（`drama/seedance_i2v_role.py`）。这是因为纯首帧模式实测可能吐出横屏。但参考图模式下模型会「重画」商品，保真度明显低于首帧模式。

由于 3.4 已经让首帧图与目标画幅一致，带货动态镜头**优先尝试 `first_frame` 且不传画幅**：

- 给 `gen_video_i2v` 增加可选参数 `image_role`，默认值保持现有行为，只有带货传入。
- 拿到成片后检查画幅（ffprobe），不一致就由合成环节的 `_pad_or_trim_video` 裁切或补边兜底。
- 是否可行由 P0 实验决定；如果首帧模式画幅失控严重，就退回参考图模式，并把商品底图放进 `extra_image_urls` 加强约束。

### 3.6 合成：混合模式的静图镜头要保留推拉

`ffmpeg_compose` 只在 `mode == "image_text"` 时走推拉（`_image_to_video_kenburns`）；`full` 模式下没有视频的镜头走 `_image_to_video`，是完全静止的画面。

改动：`full` 模式下 `video_skip_reason == "static"` 的镜头同样走推拉分支，叠字和字幕的参数与 `image_text` 一致，保证混合片前后风格统一。`privacy` 跳过的镜头保持现有行为。

### 3.7 前置条件：必须开 OSS

`ark._resolve_image_ref(prefer_https=True)` 要求视频首帧是公网 https 地址，本地 `/static` 图会直接报「Seedance 需要公网可访问的图片 URL」。处理方式：

- 视频设置面板在未启用 OSS 时把混合展示和全动态置灰，提示「需要启用 OSS 存储」。后端建项目时同样校验，返回中文 `detail`。
- 不尝试给视频走 data URI：代码注释与实测都表明 Seedance 视频会拒收。

## 4. 数据与接口

**数据（只做新增）**
- 不新增列：`pipeline_mode`、`video_prompt`、`camera`、`video_skip_reason` 都是已有字段。
- `video_skip_reason` 新增取值 `static`（常量 `VIDEO_SKIP_REASON_STATIC`，放在 `kepu_stages.py`）。
- `ecom_job_videos` 已保存图片快照，不需要改。

**接口**
- 现有建项目和报价接口（套图 `POST /api/tools/ecom-set/{id}/project`、批量 `POST /api/tools/ecom/jobs/{id}/project[/quote]`）增加 `video_form` 参数：`static` | `mixed` | `motion`，默认 `static`，旧调用行为不变。幂等键冲突校验要把 `video_form` 加进去（与画幅、音色同等处理）。
- 新增 `PATCH /api/projects/{id}/shots/{shot_id}/motion`：切换单镜的动态/静图。切到动态时清空 `video_skip_reason`，并按需生成首帧图；切到静图时写入 `static`，已有视频保留但不参与合成。只允许带货项目调用。

**后端文件**
- `services/daihuo/motion.py`（新）：动作白名单、动态镜头选择规则、首帧取图（直接用底图或扩图）。
- `services/daihuo/script.py`：写稿 prompt 和解析增加 `motion` 与 `video_prompt`。
- `services/daihuo/service.py`：`build_daihuo_shots` 按视频形式写入 `video_prompt`、`camera` 和 `video_skip_reason`。
- `services/daihuo/images.py`：建项目和报价接收 `video_form`，`full` 模式报价时加上视频阶段估价。
- `services/pipeline.py`：只改 `_kepu_seedance_*_audio` 的判断，以及视频阶段对 `static` 的跳过（小改，不加新逻辑块）。
- `services/kepu_stages.py`、`services/ffmpeg_compose.py`、`services/ark.py`：按 3.1、3.5、3.6 做小改。

**前端**
- `DaihuoVideoPanel.tsx`：视频形式改为三选一，带报价明细（视频按镜头数和秒数估算），未开 OSS 时置灰。
- 分镜编辑页：带货项目的每镜显示「动态 / 静图」开关和动作描述输入框；动态镜头完成后可预览视频。

## 5. 计费

| 环节 | 计费 |
|---|---|
| 写稿、配音、合成 | 不变 |
| 首帧扩图 | 仅在画幅不一致时产生，按张收费，与套图生图同价 |
| 动态镜头视频 | 走现有 `videos` 阶段：按镜头秒数、模型与分辨率估价预扣，以上游实际用量结算；`static` 镜头不计费 |

报价接口返回 `image_fen`（扩图）与 `video_fen` 两项，前端分项展示。默认分辨率沿用 `ark_video_resolution`（480p）；用户选高清则为 720p，与科普一致。

需同步更新 `docs/BILLING.md` 中的带货部分。

## 6. 排期

**P0 实验（约 1 天，不改代码，先做）**
用 3～4 个真实商品（马克杯、保温壶、护肤瓶、一件布艺），每个商品测 3 种动作，在工具中心图生视频和脚本直调两条通路下对比：
- `first_frame` 不传画幅 vs `reference_image` 传画幅：商品保真度与输出画幅
- 满幅扩图首帧 vs 补边图首帧
- 5 秒和 8 秒两个时长

验收标准：logo 可辨认，外形无明显形变，画幅可控。结果记录在本文档第 8 节，并据此确定 3.5 的最终做法。若商品保真普遍不达标，方案降级为「仅首镜动态」。

**P1（约 4～5 天）**
后端：3.1～3.7 的全部改动以及接口变更。前端：视频设置三选一与报价。测试：
- 就绪判定：`static` 镜头不阻塞视频阶段、不预扣费用
- 声音：带货 `full` 项目的视频请求 `generate_audio=false`
- 合成：混合模式下静图镜头走推拉
- 幂等：`video_form` 冲突时拒绝
- conftest 不涉及新列，无需同步 schema

**P2（约 2 天）**
分镜编辑页逐镜切换与动作描述编辑、单镜重生成视频。

## 7. 风险

| 风险 | 应对 |
|---|---|
| 商品在视频中变形或 logo 走样 | P0 定通路；动作白名单只允许小幅运动；默认推荐混合模式，控制动态镜头数量 |
| 首帧模式画幅失控 | 合成环节裁切或补边兜底；不行就退回参考图模式 |
| 视频比口播段长或短 | 时长按口播估算并限制在 `seedance_duration_min`～10 秒；合成时 `_pad_or_trim_video` 对齐 |
| 成片里出现其他品牌文字 | 约束句 + 现有内容审核；不合格的镜头一键切回静图 |
| 本地部署未开 OSS | 前后端双重拦截，并给出明确提示 |

## 8. P0 实验记录（2026-11-10）

**阻塞**：本机 BytePlus 账号（ModelArk）未开通任何视频模型。探测 `dreamina-seedance-2-5-260628`、`dreamina-seedance-2-0-260128`、`dreamina-seedance-2-0-fast-260128`、`seedance-1-0-pro-250528`、`seedance-1-0-pro-fast-251015` 均返回 `ModelNotOpen`；TokenFree 渠道停用。首帧 vs 参考图、data URI 是否被视频接口接受、时长对比三项实验待开通后补做（脚本见下方「复测方法」）。

已用真模型验证的部分（新镜像 + 本机 BytePlus 配置，不含视频）：

| 项目 | 结果 |
|---|---|
| 写稿动作建议（deepseek-v4-flash） | ✅ 4 段都输出合法 `motion`/`action`/`video`；动作选择合理（首镜环绕、材质微距、「一按出水」选使用演示、细节光影扫过）。模型倾向全部标 `motion=true`，混合模式靠上限（3）截断 |
| 首帧扩图（dola-seedream-5-0-pro） | ✅ 1:1 补边图 → 9:16 满幅：壶身外形、金色按压头、水位窗、底部 logo 保持，模糊带被替换为连贯的卧室场景，无拼接痕迹 |
| 3:4 → 9:16 中心裁切 | ✅ 保留 75% 宽度，商品未被裁到（保温壶样例） |

**默认值（待真机确认）**：`DAIHUO_VIDEO_IMAGE_ROLE=first_frame`（首帧已是目标画幅，不传 ratio），`DAIHUO_VIDEO_INLINE_REFS=true`（未开 OSS 时首帧压成 data URI 发送）。若真机证明视频接口拒收 data URI，把后者设为 false，动态展示将要求启用 OSS（前后端已有拦截与提示）；若首帧模式画幅失控，把前者设为 `reference_image`。

**复测方法**：开通视频模型后，按第 6 节 P0 用例直接调 `/contents/generations/tasks`（5 组：首帧 3:4 / 参考图 9:16 / 首帧 9:16 裁切推近 / 首帧 9:16 环绕 / 首帧 + ratio），再跑一条混合展示项目全流程。

## 9. 实现记录（2026-11-10）

**后端**
- `services/daihuo/motion.py`（新）：视频形式、动作白名单、动态镜头选择（混合：首镜必选 + 模型建议，上限 `DAIHUO_MOTION_MAX_SHOTS`）、满幅首帧（画幅保留 ≥75% 时中心裁切，否则视频阶段扩图）、带货视频提示词与请求参数。
- `services/daihuo/motion_stage.py`（新）：视频阶段为动态镜头准备首帧并组装请求；扩图失败该镜降级为静图并推送进度提示。
- `services/daihuo/motion_edit.py`（新）+ `PATCH /api/tools/ecom/projects/{id}/shots/{shot_id}/motion`：逐镜切换动态/静图与动作；静图口播项目首次打开动态时整体转为混合展示；有进行中任务时拒绝。
- 写稿：`daihuo/script.py` 动态展示时追加动作要求，解析 `motion`/`action`/`video`；`build_daihuo_shots` 按形式写 `video_prompt`、`camera`（动作短名）、`video_skip_reason`。
- 流水线：`kepu_stages` 新增 `VIDEO_SKIP_REASON_STATIC` 并视为视频已就绪；带货项目在 `project_audio_ready` 与 `_kepu_seedance_*_audio` 中一律后期配音；视频阶段跳过静图镜头、不串上一镜尾帧、使用带货提示词与首帧；单镜重生视频同样处理；合成时静图镜头走推拉，带货以口播节奏定时长（视频按口播裁切/定格）。
- `ark.gen_video_i2v` / `gen_and_wait_video`：新增 `image_role`、`inline_refs`、`prompt_as_json` 参数，默认行为不变。
- 数据：`projects.daihuo_video_form`（`_apply_schema_patches` 补列）；`ShotOut` 输出 `video_skip_reason`；建项目/报价接口新增 `video_form`，幂等冲突校验包含视频形式。
- 配置：`DAIHUO_VIDEO_IMAGE_ROLE`、`DAIHUO_VIDEO_INLINE_REFS`、`DAIHUO_MOTION_MAX_SHOTS`。

**前端**
- `DaihuoVideoPanel`：视频形式三选一（混合展示标「推荐」），分项报价与确认文案；动态展示不可用时禁止提交并显示原因。
- 分镜页：带货项目每镜显示「动态 / 静图」切换与镜头动作下拉（`components/daihuo/DaihuoShotMotion.tsx`）；静图镜头隐藏「生成视频」；`lib/status.ts` 的阶段判定与镜头状态识别静图镜头，带货动态展示要求先配音。

**验证**
- 后端新增 `tests/test_daihuo_motion.py` 17 例；前端新增 `tests/daihuoMotion.test.mjs`。
- 本地 mock 端到端（独立 API :8010 + 临时 PostgreSQL/Redis）：批量精修 3 图 → 混合展示建项目 → 写稿（2 动态 + 1 静图）→ 配音 → 视频（只出 2 个动态镜头）→ 合成（容器内 FFmpeg）成片 13.75 秒，静图镜头有推拉、字幕正常、时长跟口播；分镜页切换静图→动态、改动作后只补出这两镜视频，扩图路径生成满幅首帧。
- 本机 Homebrew FFmpeg 缺 `drawtext`，合成只能在容器内验证（与本改动无关）。

# 系统优化清单（2026-11 全盘审计）

> 来源：2026-11-04 对科普 / 漫剧 / 带货+工具中心 / 横切体验四块的只读审计。
> 标「已抽查」的条目已人工核对代码；行号为审计时位置，可能有几行偏移，以描述为准。
> 标「待验证」的需先复现再动手。

## 执行约定（给实现者）

- 先读根目录 `CLAUDE.md` 与 `docs/STANDARDS.md`；用户可见文案、报错 `detail`、提交信息一律简体中文，不回传堆栈。
- 新增 DB 列：写进 `backend/app/main.py` 的 `_apply_schema_patches()`（只允许 additive DDL），并同步 `backend/tests/conftest.py` 的测试 schema。
- 改了计费逻辑要同步 `docs/BILLING.md`。
- **每条一个独立提交**，提交信息格式 `fix(域): 中文描述`；每条都补对应单测（纯单测优先，不依赖 PG）。
- 校验：`cd backend && .venv/bin/python -m pytest -q`（需 PG 的集成测试可在无库时跳过）；`cd frontend && npm run lint && npx tsc -b --noEmit`。
- 不要顺手重构无关代码；`services/pipeline.py` 新增逻辑优先拆新文件。

---

## P0 · 会丢东西 / 卡死 / 多扣钱

### [x] 0. 先修：未提交的火山方舟北京区域改动打挂 3 个单测（已抽查）
- 位置：`backend/app/services/provider_profiles.py`，把 `VOLC_ARK_BEIJING` 加进了 `BYTEPLUS_BASES`。
- 现象：以下 3 个测试失败；回退该改动后同文件 39 个测试全过。
  - `tests/test_tokenfree_image.py::test_uses_tokenfree_image_on_tokenfree_host`
  - `tests/test_tokenfree_video.py::test_remap_video_path_ignores_volces_host`
  - `tests/test_tokenfree_video.py::test_prepare_video_create_body_always_wraps`（`KeyError: 'prompt'`）
- 原因：其它代码用 `BYTEPLUS_BASES` 判断「是否走 Ark 原生协议」，volces 主机进了集合后 TokenFree 路径被误判。
- 改法：区分「后台可选的官方区域列表」与「协议判定」两种用途（例如单独 `ARK_NATIVE_BASES` / 或让判定函数显式排除），确认 volces 北京区确实应走 Ark 原生协议后，同步修正测试期望。
- 验收：上述 3 个测试 + `tests/test_provider_profiles_volc.py` 全过。
- 完成（2026-11-04）：`BYTEPLUS_BASES` 仅保留国际两区域，`ARK_NATIVE_BASES` / `is_ark_native()` 显式覆盖北京；保留 `is_byteplus()` 兼容调用方。北京受控 HTTP 回归确认图片 `/images/generations`、视频创建/查询 `/contents/generations/tasks` 均走原生协议；更新旧 TokenFree-only 预期并补相似恶意域名反例，后台北京选项保留。
- 审查补正：模型目录旧逻辑会把北京地址改发 TokenFree 并携带北京 Key。新增回归先观察到 **9 failed、2 passed**；现对 Ark 目录中文拒绝、未知地址拒绝，覆盖保存地址时禁止复用原 Key，显式新地址及新 Key 才可查询 TokenFree。覆盖 `ark` / `auto`、真实保存 `channel_id`、地址/密钥 override 与恶意相似域名，均使用合成密钥与受控 HTTP。
- 验证：`backend/.venv/bin/python .tmp/optimization-20261104/run_tests.py -q tests/test_upstream_model_catalog.py tests/test_tokenfree_image.py tests/test_tokenfree_video.py tests/test_provider_profiles.py tests/test_provider_profiles_volc.py` → **79 passed**（含独立 PostgreSQL 测试库，5 条已有依赖弃用警告）；`cd admin && npx tsc -b --noEmit` 通过。未部署、未调用真实付费上游。

### [x] 1. 科普：保存任意分镜编辑都会清空该镜画面和视频（已抽查）
- 位置：`backend/app/api/projects.py` 约 892 行 `elif "img_prompt" in data:`；前端 `frontend/src/pages/studio/StoryboardPage.tsx` 约 626/633 行每次保存都带 `img_prompt`。
- 问题：上方已算好 `scene_dirty`（比较新旧值），但非数字人分支用的是「字段是否存在」。
- 改法：该分支改为 `elif scene_dirty:`；保留后续 `video_prompt / segment_script` 分支逻辑不变（同样建议改为比较值是否真的变化）。
- 验收：只改字幕/旁白保存后 `image_url / video_url / last_frame_url` 不变；改画面描述后才清空。补单测。
- 完成（2026-11-04）：按真实画面变化及流水线实际使用的脚本比较；同值、叠字和旁白保存保留画面/视频/尾帧，真实改图同时清除 `image_ark_url`。历史空脚本、独立首帧与视频动作的前端同步路径有回归，保留数字人与阶段语义。
- 验证：`backend/.venv/bin/python .tmp/optimization-20261104/run_tests.py -q tests/test_kepu_shot_edits.py tests/test_kepu_shot_edit_demote.py tests/test_kepu_phase_billing.py tests/test_kepu_native_audio.py tests/test_seedance_segments.py` → **86 passed**（5 条已有依赖弃用警告）；规格与质量复审通过，`git diff --check` 通过。未部署、未调用真实付费上游。

### [x] 2. 科普：单镜重绘/重配音失败后项目卡死在「生成中」
- 位置：`backend/app/api/projects.py` 约 932/960/985 行（先把项目 status 提交成 IMAGING/VIDEOING/AUDIOING 再建任务）；`backend/app/services/pipeline.py` 约 1606/1668/1769 行 `regen_shot_*` 无 except；executor 预扣失败也不回写项目状态。
- 现象：失败后前端无 active_tasks，按钮可点；但后端 generate/PATCH 只看 status，一律 409「生成进行中」。前端从未调用 `api.cancelProject`（`frontend/src/api.ts` 约 542 行）。
- 改法：
  1. 任务创建成功后再改项目状态（或同一事务）。
  2. `regen_shot_*` 失败时回写项目为可编辑态（FAILED 或回到上一稳定态）并写中文 `error_msg`。
  3. generate / PATCH 的忙碌守卫改为「是否存在活跃 TaskRun」，参照现有 `_ensure_compose_allowed`。
  4. 分镜页加「取消生成」按钮（二次确认），调用 `cancelProject`。
- 验收：模拟 regen 抛异常 → 项目可再次编辑/生成，页面显示中文原因。
- 完成（2026-11-04）：重绘、配音与合成的任务和项目状态同事务提交；预扣失败与已有 handler 失败路径共用恢复逻辑，并行镜头一成功一失败仍保留中文原因。生成、项目编辑与取消以用户隔离的活跃任务为准，取消请求未终态前保持忙碌；分镜页接入二次确认取消与等待停止。
- 验证：`backend/.venv/bin/python .tmp/optimization-20261104/run_tests.py -q tests/test_kepu_task_recovery.py tests/test_task_executor_freeze_steps.py tests/test_kepu_cancel_propagation.py tests/test_side_task_gate.py tests/test_project_runtime_view.py` → **75 passed**（5 条已有依赖弃用警告）；前端 `npm test` **13 passed**，lint 0 error（39 条既有 warning），`npx tsc -b --noEmit` 通过。规格复审与质量审查通过；未部署、未真实收费。重做磁盘清理与单镜编辑合成门禁留第 18 项。

### [x] 3. 漫剧：保存分镜覆盖服务端写的视频历史版本与尾帧
- 位置：`backend/app/api/drama/episodes.py` 约 575-577 行，用客户端 `video` / `params` 整体覆盖。
- 现象：前端轮询只回填 `generation`，服务端收尾写的 `video_versions`、`lastFrameUrl` 不在本地；界面又提示「入队后可继续编辑」，一保存就丢。双标签页编辑时，后保存的一方会把对方新增分镜当多余删掉并取消其视频任务。
- 改法：
  1. `params` 改为合并：服务端维护的字段（`video_versions`、`lastFrameUrl`、生成相关）以服务端为准。
  2. 处于生成中/已有服务端结果的分镜，忽略客户端传入的 `video`。
  3. 分集加 `version`（或 `updated_at`）乐观锁，冲突返回 409 + 中文提示「内容已在别处更新，请刷新」。
  4. 删除分镜改为只删客户端明确标记删除的，而不是「不在列表里就删」。
- 验收：生成完成后再保存，历史版本与尾帧仍在；两个标签页并发保存不会互删。
- 完成（2026-11-04）：分集新增原子 `revision` 乐观锁，缺版本或旧版本明确拒绝；仅删除经过归属和冲突校验的 `deleted_ids`，保留服务端媒体、历史、尾帧与生成元数据。四处前端保存绑定编辑快照，设置请求跨等待不借用新版本；seed 和旧 AI 草稿也受版本约束。
- 并发与事务：提交、下载、补尾帧与公共失败回填不会以旧 JSON 覆盖新稿；媒体、价目和用量查询在分集锁外准备，结果与费用同事务提交。取消补账快照超出覆盖范围时整体回滚，已发生 LLM 成本保留；管理员邮件在成功提交后检查并串行去重。已同步 `docs/BILLING.md`。
- 验证：`backend/.venv/bin/python .tmp/optimization-20261104/run_tests.py -q` → **1143 passed，2 skipped**（6 条已有依赖弃用/异步连接警告）；前端 `npm test` **19 passed**，lint 0 error（39 条既有 warning），`npx tsc -b --noEmit` 与 `git diff --check` 通过。规格复审与质量审查通过；未部署、未真实收费。
- 边界：旧客户端和缺少合法版本的旧重拆任务会明确失败；未保存本地稿的编辑 epoch 保护由第 17 项闭合，结算读取之后的迟到上游成本由第 20 项处理。

### [x] 4. 漫剧：画布自动保存用旧图覆盖资产图（已抽查）
- 位置：`backend/app/api/drama/canvas.py` 约 80-90 行，`if url:` 把节点 `mediaUrl` 写回 `asset.url / asset.cover`。
- 现象：画布打开期间在资产库重做角色图，拖动节点后被写回旧图（且带 API 主机前缀）；视频节点会把 mp4 写进 cover，缩略图坏掉。
- 改法：画布保存只存布局（`params["canvas"]`、名称等），不再写资产媒体字段；媒体只由资产库/生成流程写。
- 验收：画布开着时换资产图 → 拖动节点保存 → 资产图仍为新图。
- 完成（2026-11-04）：已有资产自动保存仅更新名称和画布参数，保留媒体 URL、封面与类型；新节点保留媒体 URL，音视频仅接受明确的图片封面，独立上传与导入流程保持。
- 验证：`backend/.venv/bin/python .tmp/optimization-20261104/run_tests.py -q tests/test_drama_canvas.py tests/test_asset_params_merge.py tests/test_asset_image_versions.py tests/test_seed_asset_params_structure.py` → **26 passed**（1 条已有依赖弃用警告）；规格与质量审查通过，`git diff --check` 通过。未做浏览器操作验收、未部署。

### [x] 5. 豆包 TTS 疑似重复计费（两处记账已抽查，第二笔是否真的入账待验证）
- 位置：`backend/app/services/pipeline.py` 约 349 行，整片配音后无条件 `_record_usage_est(..., "tts")`；`backend/app/services/volc_tts.py` 约 109 行成功后再 `record_avatar_ai("avatar_tts", ...)`。
- 先验证：非数字人上下文中 `record_avatar_ai` 是否真的写 `usage_events`。
- 改法：TTS 调用返回实际服务商（volc / tokenfree / edge），按实际服务商只记一笔。同时修反向问题：`services/kepu_avatar.py` 约 118 行只要配了火山 Key 就跳过通用 TTS 记账，回退到其它服务商时会漏计。
- 验收：开计费跑一次豆包配音，usage_events 只有一条；火山失败回退时也只有一条且服务商正确。更新 `docs/BILLING.md`。
- 完成（2026-11-04）：真实数据库复现普通科普一次配音落两行，改为按每次实际成功的付费响应统一记账，保留字符串 URL 兼容与局部服务商结果；同一次调用不再补记通用费用。静音、写盘或转码失败后的真实已发生费用保留，多个真实付费响应分别计费，edge/mock 不记付费估价，原费率及镜数估量不变。
- 事务与失败：显式漫剧音色复用既有估价，付费前持久化原任务和冻结，费用使用独立短事务；其它轻量任务默认语义保持。记账失败只在明确回滚后本地重试，持续或确认不明时保媒体、成本快照与冻结进入待核对，普通取消和分镜作废均不能当零费用退款；恢复扫描不会重排存活请求或覆盖刚完成任务。已同步 `docs/BILLING.md`。
- 验证：`backend/.venv/bin/python .tmp/optimization-20261104/run_tests.py -q` → **1181 passed，2 skipped**（6 条已有依赖弃用/异步连接警告）；真实独立连接、故障注入、并发和恢复交错均有红绿回归，规格复审与质量复审通过（质量独立专项 54 passed），`git diff --check` 通过。最终测试使用隔离数据库、外网阻断及排他测试锁；未部署、未调用真实付费上游。
- 边界：费用待核对任务需人工处理，未自动追扣历史；镜头服务商持久化、试听缓存与原音频路径隔离继续由第 23 / 18 项闭合。

### [ ] 6. 带货：极限词自动替换改坏句意，且用户无感知
- 位置：`backend/app/services/daihuo/script.py` 约 84-87 行；`backend/app/api/projects.py` 约 835 行；词表 `backend/app/services/compliance/ad_words.py` 约 90-196 行。
- 实测：「续航最多12小时」→「续航多12小时」；「第一印象」→「领先印象」；「治疗仪配件」→「仪配件」。用户手改的旁白也会被静默替换。
- 改法：
  1. 词表补白名单/上下文排除（如「最多」后接数字、「第一印象」「最低温」等常见搭配）。
  2. 替换结果返回替换明细（原词、新词、位置），前端高亮并允许单条撤销。
  3. 用户手动编辑后的旁白只提示不自动替换。
- 验收：上述 3 个例句不再被改错；替换明细可见可撤销。补单测覆盖例句。

### [ ] 7. 任务孤儿回收无次数上限
- 位置：`backend/app/services/tasks/scheduler.py` 约 401-443 行，无条件改回 `pending`；`services/tasks/` 内无 `attempt_count` 递增。
- 现象：每次都会把进程搞崩的任务（如 FFmpeg OOM）无限重跑，一直占用户并发槽。
- 改法：回收时 `attempt_count += 1`，超过阈值（新增配置，默认 3）标 failed、走结算退冻结、写中文失败原因。
- 验收：单测模拟连续回收 → 第 N 次后变 failed 且冻结金额退回。

---

## P1 · 主力功能可用性

### [ ] 8. 漫剧：恢复「一键生成未出图」（已抽查）
- 位置：`frontend/src/pages/drama/AssetsStep.tsx` 742-743 行 `tab !== 'voice' && false`。
- 背景：视频生成在 `backend/app/services/drama/generation.py` 约 883 行遇缺参考图直接拒绝，新用户需逐张出图，是到第一集的最大卡点。
- 改法：确认当初隐藏原因（看 git log / blame）后恢复；按钮前显示将出图数量与预估费用，二次确认。出视频前若缺参考图，给出「去一键出图」的引导而不是只报错。

### [ ] 9. 科普：接上实时进度，补取消
- 位置：`frontend/src/api.ts` 约 680 行 `eventsUrl` 无调用方；`StoryboardPage.tsx` 约 184 行每 1.5s 拉整项目。
- 改法：分镜页订阅 SSE，展示「出图 3/8」「第 N 镜隐私跳过」「合成重试中」等消息与当前镜头高亮；SSE 断开时回退到轮询。取消按钮见第 2 条。

### [ ] 10. 统一中文报错，禁止回传上游原文
- 位置：
  - `backend/app/services/pipeline.py` 约 644 行 `str(exc)` 直接给用户
  - `backend/app/api/tools.py` 约 85/110 行所有 ValueError 一律 402（像余额不足）；约 126 行回传原始异常
  - `backend/app/services/ecom_set/jobs.py` 约 179 行上游原文显示在套图卡片（`frontend/.../EcomSetResults.tsx` 约 100 行）
  - `backend/app/services/drama/episode_compose.py` / `episodes.py` 约 896 行 500 回传异常原文
- 改法：做一个公共的「异常 → 中文短句 + 错误码」映射（可参考前端 `dramaGenError.ts` 的思路），原文只进日志；402 只用于余额不足，参数错误用 400/422。

### [ ] 11. 前端登录态：统一处理 401，别因断网登出
- 位置：`frontend/src/api/http.ts` 约 45-60 行无统一 401；`frontend/src/components/SiteNav.tsx` 约 56 行 `api.me()` 任意失败都删 token；`api/tools.ts` 约 15 行、`api/ecomSet.ts` 约 90 行各自写死「未登录」。
- 改法：transport 层遇 401 清 token 并跳 `/auth?next=当前路径`（复用 `safeNextPath`）；网络错误/5xx 不清 token。删掉各处重复的 401 文案。

### [ ] 12. 漫剧整集合成改为异步任务
- 位置：`backend/app/services/drama/episode_compose.py` 约 102 行；`backend/app/api/drama/episodes.py` 约 896 行，HTTP 请求内同步逐镜 x264 重编码，输出文件名固定。
- 现象：镜头多时 nginx 504；并发两次互相覆盖或拿到缓存旧片。
- 改法：注册新 TaskRun 类型（在 `services/tasks/handlers.py` 的 `HANDLERS` 登记 `("drama", "episode_compose")`），前端轮询任务状态；输出文件名带时间戳或任务 ID。

### [ ] 13. 工具中心：离开页面后视频记录永远「生成中」
- 位置：`backend/app/api/tools.py` 约 131 行只在前端轮询时回写 ToolRun；poller 结算时不更新 ToolRun；`SettingsToolRunsPanel.tsx` 不重拉。
- 改法：poller 在结算 tools 域任务时同步回写 ToolRun 的状态与结果 URL；记录面板对进行中的条目轮询刷新。

### [ ] 14. 漫剧：每镜角色超 3 个被静默丢弃
- 位置：`backend/app/services/drama/fragment_asset_limit.py` 约 8/62 行。
- 改法：前端分镜编辑与批量生成前给出 warning「本镜引用 N 个角色，仅前 3 个生效」，并允许用户调整顺序。

### [ ] 15. 漫剧：资产换图后老镜头无「过期」标记
- 位置：`frontend/src/pages/drama/EpisodeEditPage.tsx` 的 `shouldSkipGenerateAllFragment` 只比较正文与资产 ID。
- 改法：生成时在分镜上记录所用资产的 `updated_at`（或版本号）；资产更新后对比标出「形象已更新，建议重生成」，批量生成提供「含过期镜头」选项。

### [ ] 16. 漫剧：画布 Backspace 直接删后端资产
- 位置：`frontend/src/pages/drama/canvas/CanvasStore.tsx` 约 307 行，无确认，失败静默。
- 改法：删除前二次确认；失败 toast 提示；撤销能恢复则恢复，否则改软删除。

### [ ] 17. 漫剧分镜编辑无自动保存 / 离开提示
- 位置：`frontend/src/pages/drama/EpisodeEditPage.tsx`。
- 改法：防抖自动保存（依赖第 3 条的合并与乐观锁先完成）+ `beforeunload` 未保存提示。

### [ ] 18. 科普：编辑与合成竞态；重做误删素材
- 位置：`backend/app/api/projects.py` 约 62 行 `_demote_after_edit` 状态集合含 COMPOSING，`update_shot` 无忙碌守卫；restart 先删磁盘素材再建任务，余额不足回滚后文件已删。
- 改法：COMPOSING 期间禁止编辑（409 中文提示）；restart 改为任务建成后再删素材（或先移到临时目录，成功后删除）。

### [ ] 19. 科普：隐私拦截镜头在「视频内置口播」模式下无声
- 位置：`backend/app/services/pipeline.py` 约 1333 行，提示文案「成片将缺该镜」也不准确。
- 改法：被拦截镜头静图合成时回退 TTS 配该镜旁白；修正提示文案。

### [ ] 20. 视频超时重试过长（待验证计费）
- 位置：`backend/app/services/ark.py` 约 1917 行 poll timeout 视为可重试，单镜最长约 45 分钟；每次重试重新提交上游。漫剧 `services/drama/jobs.py` 约 1517 行轮询无总时长上限。
- 改法：设置单镜总时长上限（配置项）；重试前尽量复用/查询原上游任务而不是重提；先确认旧任务是否仍计费。

---

## P2 · 体验打磨

### [ ] 21. 全站「我的任务」抽屉 + 完成提醒
- 背景：`tasksApi` 仅漫剧在用；科普/工具离开页面就看不到进度，无通知，排队时不知道前面几个。
- 改法：顶栏任务入口，基于 `/api/tasks` 列表展示状态与「排队中（前方 N 个）」；完成时 toast + 标签页标题提示。

### [ ] 22. 轮询策略
- 位置：`StoryboardPage.tsx` 约 184 行（1.5s）、`HistoryPage.tsx` 约 169 行（2s 拉整页），错误静默、无退避、无超时。
- 改法：页面隐藏时暂停（`visibilitychange`）、失败指数退避并显示「连接中断，重试中」、请求加 AbortController 超时。

### [ ] 23. 配音：记录实际服务商，试听不缓存回退结果
- 位置：`backend/app/services/ark.py` 约 2086-2135 行 TTS 回退链；`backend/app/services/voices.py` 约 307 行试听永久缓存、试听文案写死「适合科普……」。
- 改法：镜头上记录实际 TTS 服务商，回退时在页面提示「本段使用备用音色」；试听缓存键含服务商，回退产物不缓存；试听文案按场景区分。

### [ ] 24. 带货面板：加音色试听与预估价
- 位置：`frontend/.../DaihuoVideoPanel.tsx`。

### [ ] 25. 合规提示带替换建议，一键替换
- 位置：`backend/app/services/compliance/content.py` `_hit_rows` 只返回词与分类。
- 改法：Hit 带 `suggestion`；科普/漫剧编辑页提示条支持一键替换。

### [ ] 26. 首页注册后直达创作页
- 位置：`frontend/src/pages/HomePage.tsx` 约 51 行 `next=/`，改为 `AVATAR_PATH`（开口播创作页）。

### [ ] 27. 科普「一键跑完全片」选项
- 背景：现在每阶段手动「继续」，全片 4 次。可在创建时提供「全自动」开关，计费仍按阶段预扣（必须与 `kepu_stages.resolve_kepu_billing_phase()` 语义一致）。

### [ ] 28. 科普出图并行模式（待评估）
- 位置：`backend/app/services/pipeline.py` 约 1071 行逐镜串行（每镜参考上一镜），`pipeline_image_concurrency=3` 实际无效。
- 改法：提供「不参考上一镜」的并行模式，或首镜定调后其余并行参考首镜。

### [ ] 29. 作品库加分享 / 复制链接
- 位置：`frontend/src/pages/HistoryPage.tsx`。

---

## P3 · 运维与长期

### [ ] 30. OSS 上传失败自动补传
- 位置：`backend/app/services/oss_queue.py` 约 56 行 `create_task` 发后不管；补传只能手跑 `scripts/backfill_oss_media.py`。
- 改法：在现有维护循环里定时调用 `backfill_pending_local_media`。

### [ ] 31. 本地生成目录清理
- 背景：只有删科普项目时清理（`pipeline.py` 约 670 行），`static/generated` 无过期清理；漫剧/工具产物是否清理待验证。
- 改法：OSS 回填成功后删本地副本（FFmpeg 需要时再拉回），或按天数清理无引用文件。

### [ ] 32. i18n 漏翻
- 背景：pages/components 中 119 个 tsx 含中文，仅 49 个接入 i18n。重灾区：`CreateProjectPage.tsx`（~136 处）、`EpisodeEditPage.tsx`（~90）、`StoryboardPage.tsx`（~82）、`DramaListPage.tsx`、`lib/billingError.ts` 余额不足弹窗。
- 改法：按「首个作品路径 → 计费弹窗 → 其余」顺序迁移，可分多个 PR。

---

## 待验证（先复现再决定）

- 套图与带货项目一对一绑定，换画幅/音色后无法再出第二条视频。
- 火山方舟国内站模型 ID 与价目/计费是否匹配（需实测一次）。
- 视频超时重试时，旧的上游任务是否仍被计费（见第 20 条）。

# 电商模块整体规划（电商工作台 ECOM SUITE）

> 2026-11-05 · 参考竞品 Picset（picsetai.cn）的功能矩阵，结合我们已上线的「一图出套图 → 商品带货视频」链路制定。
> 实施分支：`feat/ecom-suite`；任务按 E0→E6 顺序执行，每个任务一个提交。

## 1. 目标

把现在零散的「一图出套图 + 工具中心 i2p/ecom 小工具」升级成一个**电商工作台**：商家从一张随手拍的产品图出发，能完成「精修白底 → 套图/风格复刻/买家秀/SKU 图 → 多语言 → 带货口播视频」的全链路。与 Picset 的差异化是**图到口播视频的闭环**（他们的视频只是素材生成，没有口播）。

不做：万能画布（漫剧已有 React Flow 画布）、服装模特试穿（垂直品类，后续视需求再议）、邀请返利/子账号（属于全站商业化，不在本规划）。

## 2. 现状（2026-11-05）

| 能力 | 位置 | 说明 |
|---|---|---|
| 一图出套图 | `services/ecom_set/*`、`api/ecom_set.py`、`pages/DaihuoPage.tsx` | 8 个固定槽位、6 平台、5 风格、4/6/8 张；视觉 LLM 规划 → 用户改文案 → 每张一个 TaskRun 生底图 → PIL 排版文字；zip 下载 |
| 商品带货视频 | `services/daihuo/*` | 套图底图直接当分镜，LLM 写口播，走 `image_text` 流水线 |
| 广告法合规 | `services/compliance/*` | 文案/场景违禁词替换，后台可加词 |
| 工具中心 | `services/studio_tools.py`（599 行，已超限） | i2p 白底/场景/详情长图仅用第 1 张参考图；ecom 主图拼接是 ffmpeg 拼接 |
| 生图底座 | `ark.py: gen_image(prompt, negative, ref_urls, …, style_ref_urls)` | ⚠ TokenFree 两条通路里参考图只以 URL 文本拼进提示词，**不是结构化图片输入**，参考图遵循度不可靠 |
| 抠图 / OCR / 翻译 | — | 完全没有；依赖里只有 Pillow |

## 3. 功能规划与优先级

| 编号 | 功能 | 价值 | 说明 |
|---|---|---|---|
| E0 | **电商批量任务底座 + 图片精修/白底/透明底** | 地基 | 通用「批量出图任务」模型/接口/前端组件；参考图结构化传入；首个落地功能：批量精修（≤20 张） |
| E1 | **风格复刻** | 高 | 参考设计图 + 产品图（≤6）→ 同构图光影配色的新图；单图 / 批量（1 参考 × N 产品） |
| E2 | **套图增强** | 高 | 自定义槽位、新增「广告图」类、目标语言（中/英/日/无文字）、清晰度 2K/4K、套图页「去精修白底」入口 |
| E3 | **买家秀 / 种草图** | 中高 | 产品图 + 可选模特/场景参考 → 生活化晒图，可「裂变」再出一批 |
| E4 | **SKU 替换** | 中 | 1 张场景参考 × N 个产品（≤20）→ 换主体保构图 |
| E5 | **图片翻译** | 中 | 自家套图：翻译文案后 PIL 重排（零生图成本）；外部图片：视觉 LLM 识字 → 翻译 → 参考图重绘 |
| E6 | **电商工作台入口 + 串联 + 文档** | 高 | `/ecom` 总入口、导航、跨工具「送到下一步」、历史；更新 BILLING/ECOM 文档 |

## 4. 共用技术方案（E0 落地，后续任务复用）

### 4.1 数据模型（`models_ecom.py` 追加，新表靠 `create_all`）
- `EcomJob`（`ecom_jobs`）：`user_id, kind(refine|style|buyer_show|sku|translate), status(queued|running|done|partial|failed), title, params JSON, created_at/updated_at`
- `EcomJobItem`（`ecom_job_items`，唯一 `(job_id, idx)`）：`inputs JSON`（本地路径/URL、角色：product/style/model/scene/source）、`prompt, ratio, size, result_url, status, error, task_run_id`
- 上传图存 `static/generated/p0/ecom/u{uid}/jobs/j{id}/`，FFmpeg/PIL 只读本地；OSS 规则沿用 `storage`

### 4.2 任务与计费
- 每张图一个 TaskRun：`("studio", "ecom_job_image")` 登记在 `tasks/handlers.py` 的 `HANDLERS`
- 建任务前 `ensure_balance_for_task_batch` 整批校验余额；预估价复用 `_catalog_image_fen`，按清晰度分 2k/4k 档
- 失败重抛 → 不扣费；`record_seedream_image_usage(domain="studio", extra_raw={"tool_id": "ecom_<kind>"})`
- `ARK_MOCK=true` 下每个 kind 都要有本地 mock 出图（参考 `ecom_set/layout.mock_base`），保证无 Key 能联调与单测

### 4.3 参考图结构化传入
- `gen_image` 新增关键字参数 `structured_refs: bool = False`；为 True 时在 TokenFree `/responses` 通路把参考图作为 `input_image` 内容块传入（gpt-image 通路按其接口支持情况传图，不支持则保持原文本方式并打日志）
- **默认关闭**，只有电商新功能显式开启，避免影响科普/漫剧现有出图
- 每个参考图带角色说明（"图1 为产品主体，必须保持外形/颜色/logo；图2 仅参考风格"）

### 4.4 接口（`api/ecom_jobs.py`，挂 `/api/tools/ecom-jobs`）
- `GET /options`：各 kind 的上限、比例、清晰度、单价
- `POST /{kind}`（multipart）：建任务并入队
- `GET ""`（服务端分页 `page/page_size/meta.total`，可按 kind 筛选）、`GET /{id}`
- `POST /{id}/items/{idx}/retry`、`GET /{id}/download`（zip）

### 4.5 前端
- `frontend/src/api/ecomJobs.ts`；`components/ecomJobs/`：多图上传（带角色）、参数面板、结果网格（加载/空/错三态，单张重试/下载）、历史列表
- 每个功能一个页面 `pages/ecom/*Page.tsx`，只做编排；i18n `locales/{zh,en}/ecomJobs.ts`
- 样式用 `printfilm.css` + `pf-*`，**不引 Tailwind**

### 4.6 透明底（不引入重依赖）
透明底 = AI 生成纯白底图 → Pillow 从四边泛洪去除近白像素生成 alpha（容差可调）→ PNG。不引 rembg/onnxruntime，避免镜像膨胀；效果不足再另立任务。

## 5. 约束（每个任务都要遵守）
- `docs/STANDARDS.md`：单文件 ≤500 行、函数顶部中文功能注释、`api/` 只做校验调 service、对外报错中文短句
- 新列只能 additive，写进 `main._apply_schema_patches()`；新表进 `create_all`
- 用户可见文案中英双语；广告文案过 `compliance.check_text`
- 改计费要同步 `docs/BILLING.md`
- 测试：纯单测为主（mock 模式），需要库的用 `db_session`（本机 PG :15432 已在运行）
- 不改 `.env`、不提交生成媒体/`dist/`

## 6. 已知问题（顺手修）
- `main._apply_schema_patches()` 中 `projects (user_id, ecom_set_id, daihuo_request_id)` 唯一索引在 `ecom_set_id` 列补丁之前创建，老库会失败 → E0 调整顺序

## 7. 执行记录
| 任务 | 执行者 | 状态 | 提交 |
|---|---|---|---|
| E0 底座 + 精修 | Codex CLI | 待执行 | |
| E1 风格复刻 | Codex CLI | 待执行 | |
| E2 套图增强 | Codex CLI | 待执行 | |
| E3 买家秀/种草图 | Codex CLI | 待执行 | |
| E4 SKU 替换 | Codex CLI | 待执行 | |
| E5 图片翻译 | Codex CLI | 待执行 | |
| E6 工作台入口/文档 | Codex CLI | 待执行 | |

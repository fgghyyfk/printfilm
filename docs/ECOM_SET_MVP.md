# 一图出套图（电商套图）MVP 设计方案

> 历史 MVP 设计归档 · 2026-11-03；当前实现与后续设计以 [电商工作台整体规划](./ECOM_SUITE_PLAN.md) 为准。
> E0–E6 已扩展为 `/ecom` 工作台；套图仍在 `/daihuo`，支持自定义槽位、多语言、2K/4K、任意电商成功图引用及带货视频串联。本文保留初版范围和当时的现状盘点，不作为当前能力清单。
> 对标：Picset AI（一张商品实拍图 → 一整套带文案、排版的电商图）

## 1. 目标与范围

**一句话**：用户上传一张商品图，填品名和平台，AI 先给出一套「出图方案」，用户确认后批量生成 6 张带卖点文案的电商图，可以单张重做、改文案，最后打包下载。

**MVP 做**

- 一张商品图 → 自动规划 6 张套图（张数可选 4/6/8）
- 方案可编辑：每张图的用途、画面描述、标题、卖点文案都能在生成前改
- 文字后叠加：模型只出无字底图，文案由服务端按版式模板渲染上去（不让模型直接写中文，避免错别字）
- 平台尺寸预设：淘宝/天猫、京东、拼多多、抖音、小红书、Amazon
- 单张重新生成（收费）、单张改文案重新排版（免费，不调模型）
- 打包下载 zip

**MVP 不做**（放到第 8 节后续阶段）

- 服装模特试穿、人台图
- 抠图 + 合成 + 重打光（严格锁定产品外观）
- 在线画布自由编辑
- 爆款风格复刻（技术上已有 `style_ref_urls`，放到第二阶段做入口）

## 2. 现状盘点（可复用的部分）

| 能力 | 位置 | 现状 |
|---|---|---|
| 商品图生图 | `services/studio_tools.py` `run_image_tool`（`i2p` / `ecom`） | 一次一张，提示词强制「无文字」 |
| 生图引擎 | `services/ark.py` `gen_image(prompt, negative, ref_urls, size, style_ref_urls…)` | Seedream 5.0（`model_image`），支持参考图 |
| 画幅 | `studio_tools.RATIO_SIZE` | 只有 1:1 / 16:9 / 9:16 |
| 文字 LLM | `services/llm_client.py` `chat_completions(system, user: str)` | **只能传文字，不支持传图** |
| 工具记录 | `models.ToolRun`（`tool_id` String(16)，`urls`/`params` JSON） | 一条记录一次生成 |
| 工具异步执行 | `studio_tools._dispatch_tool_image` | `asyncio.create_task`，**进程重启会丢任务** |
| 计费 | `run_billed_ephemeral(domain="studio", task_type="tool_image")` | 单张预扣 → 结算 |
| 任务平台 | `services/tasks/`（scheduler/executor/poller，`HANDLERS` 注册表） | 有并发槽位、孤儿回收、心跳 |
| 图片依赖 | `requirements.txt` | **没有 Pillow**（拼图走 FFmpeg） |
| 前端工具 | `frontend/src/lib/toolsCatalog.ts` + `pages/ToolDetailPage.tsx` | 通用单步表单，不适合多步流程 |

结论：生图、计费、任务平台、存储都能直接用；需要新增的是「看图规划」「文字排版渲染」「批量任务编排」和一个多步前端页面。

## 3. 用户流程

```
① 上传商品图 + 填写：品名（必填）、平台、风格、补充卖点（可选）、张数
        │
        ▼  POST /plan（同步，约 10–20 秒，只调 1 次 LLM，按次计费或免费）
② AI 方案：6 张卡片，每张显示「用途 / 画面描述 / 主标题 / 卖点」，可改、可删、可调顺序
        │
        ▼  POST /generate（预扣 N 张生图费用）
③ 生成中：网格逐张出图，失败的单张显示「重试」
        │
        ▼
④ 完成：单张「重新生成」（收费）/「改文案」（免费，秒出）→「打包下载」
```

先出方案、确认后再花钱，好处是用户不会为不满意的规划买单，也给了用户改卖点的机会（AI 识别卖点不准是这类产品的常见槽点）。

## 4. 套图构成（默认 6 张）

| # | 槽位 `slot` | 底图提示要点 | 文案版式 `layout` | 画幅 |
|---|---|---|---|---|
| 1 | `main_white` 主图·白底 | 纯白背景、居中、柔光 | 无字（多数平台主图要求白底无字） | 1:1 |
| 2 | `main_scene` 主图·场景 | 品类相关生活场景、主体占 60% | 左上角主标题 | 1:1 |
| 3 | `selling_1` 核心卖点 | 主体 + 留白区（提示词指定留白方位） | 大标题 + 副标题 | 3:4 |
| 4 | `selling_2` 卖点 2–3 | 同上，换角度/氛围 | 标题 + 3 条要点 | 3:4 |
| 5 | `detail` 细节特写 | 材质、做工微距 | 底部标签条 | 3:4 |
| 6 | `usage` 使用场景 | 真实使用情境，可带手部/人物局部 | 一句场景文案 | 3:4 |

4 张 = 1、2、3、6；8 张 = 6 张 + `selling_3` + `package`（包装/清单）。

## 5. 技术方案

### 5.1 方案规划（看图 → JSON）

- `llm_client.py` 新增 `chat_completions_vision(system, text, image_urls)`，按 OpenAI 兼容格式传 `content: [{type:"text"}, {type:"image_url"}]`，复用现有路由和 Key 的解析逻辑。
- **已实现（MVP）**：没有新增路由能力，改用配置项 `MODEL_VISION`（默认 `seed-2-0-lite-260428`），复用文本渠道的 Base URL 与 Key（`llm_client.chat_completions_vision`）。Seed 系列自动关闭深度思考以提速。以后要在后台切换视觉模型，再给 `logical_model_router` 加 `vision` 能力；**没配置视觉模型时退化为纯文字规划**：只用品名和用户填的卖点，并在页面上提示「填写卖点效果更好」。
- 输出用 `response_format=json_object`，结构如下：

```json
{
  "product": {"name": "…", "category": "…", "material": "…", "colors": ["…"]},
  "items": [
    {"slot": "selling_1", "scene": "底图画面描述（不含文字）", "blank": "top",
     "title": "≤10字", "subtitle": "≤16字", "bullets": ["≤12字", "…"]}
  ]
}
```

- 文案约束写进 system prompt：不用《广告法》极限词（最、第一、国家级、100%…），服务端再用词表过滤一遍，命中就替换或提示。

### 5.2 底图生成

- 提示词 = 槽位模板 + `scene` + 统一后缀「保持参考图中商品的外形、颜色、logo 与包装文字完全一致，只改变背景与光影；画面不含任何文字」+ 留白方位。
- 参考图 = 用户上传的商品图（`publish_public`），调用 `ark.gen_image(ref_urls=[…], size=…)`。
- 统一按 2K 生成，交给排版环节缩放到平台尺寸。

### 5.3 文字排版渲染（新增）

- 新增依赖 **Pillow**。字体不放进仓库：Docker 镜像用 apt 安装 `fonts-noto-cjk`（Noto Sans CJK，与思源黑体同源，OFL 可商用）；本机开发回落到 macOS 自带的黑体/苹方；也可用 `ECOM_FONT_PATH` 指定。
- `layout.py` 定义 5 个版式（`none` / `title_corner` / `title_sub` / `title_bullets` / `label_bar`）：
  - 输入：底图路径、文案、留白方位、主色
  - 文字颜色自动取反：取留白区平均亮度，选深色或浅色字，必要时加半透明底板
  - 自动换行和字号回退，保证不溢出
- 输出：按平台预设裁切、缩放后的最终 JPG。底图单独保存，所以**改文案只需重新渲染，不调模型、不收费**。

### 5.4 平台尺寸预设

`presets.py` 维护平台 → 槽位 → 像素尺寸（上线前按各平台最新规则核对）：

| 平台 | 主图 | 详情/卖点图 | 备注 |
|---|---|---|---|
| 淘宝/天猫 | 800×800 | 750 宽，3:4 | 主图 1 建议白底 |
| 京东 | 800×800 | 750 宽 | |
| 拼多多 | 750×750 | 750 宽 | |
| 抖音 | 800×800 | 750 宽 | 也可出 3:4 |
| 小红书 | 1080×1440（3:4） | 1080×1440 | 主图也用 3:4 |
| Amazon | 2000×2000 纯白（RGB 255） | 1500×1500 | 主图禁止文字/道具 |

### 5.5 任务编排（不再用 create_task）

批量 6–8 张，进程重启丢任务的代价比单张高，所以接入统一任务平台：

- `tasks/handlers.py` 注册 `("studio", "ecom_set_image")`，**一张图一个 TaskRun**。调度器负责全站和单用户的并发槽位，失败重试、孤儿回收也是现成的。
- handler 内部：生成底图 → 下载到本地 → 渲染文字 → 回写这张图的记录 → 走现有计费结算。
- 生成前一次性校验余额够 N 张，每张单独预扣和结算。部分失败时，只扣成功那几张的费用。

### 5.6 数据模型

新建两张表（`create_all` 会自动建；conftest 测试 schema 要同步）：

```
ecom_sets          id, user_id, product_image_url, platform, style, count,
                   plan JSON, status(planning|planned|generating|done|partial|failed),
                   created_at, updated_at
ecom_set_items     id, set_id, idx, slot, layout, prompt, copy JSON,
                   base_url(无字底图), final_url(成图), status, error, task_run_id,
                   created_at, updated_at
```

每张图单独一行，避免多个任务并发改同一个 JSON 字段时互相覆盖。另外在 `tool_runs` 写一条汇总（`tool_id="ecomset"`），个人中心的「我的作品」就能直接看到。

### 5.7 接口（`api/ecom_set.py`，挂在 `/api/tools/ecom-set`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/plan` | multipart 上传商品图和参数 → 建 set，返回方案 |
| PUT | `/{id}/plan` | 保存用户改过的方案 |
| POST | `/{id}/generate` | 校验余额，按 items 批量建 TaskRun |
| GET | `/{id}` | set 和 items 的状态（前端轮询） |
| POST | `/{id}/items/{idx}/regenerate` | 单张重做底图（收费） |
| PUT | `/{id}/items/{idx}/copy` | 改文案后重新渲染（免费，同步返回） |
| GET | `/{id}/download` | 打包 zip |
| GET | `/` | 我的套图列表（服务端分页） |

开放 API `/api/v1` 先不开放，等稳定后再说。

### 5.8 文件拆分（每个 ≤500 行）

```
backend/app/services/ecom_set/
  __init__.py
  presets.py      平台尺寸、槽位定义、默认套图构成
  prompts.py      规划 system prompt、槽位底图模板、极限词表
  planner.py      看图/纯文字规划 → 校验 → 落库
  layout.py       Pillow 文字排版渲染
  jobs.py         TaskRun handler：生图 → 渲染 → 回写
  service.py      建 set、批量入队、单张重做、打包
backend/app/api/ecom_set.py
backend/app/models_ecom.py
frontend/src/pages/EcomSetPage.tsx         多步页面编排
frontend/src/components/ecomSet/*          上传表单 / 方案卡片 / 结果网格 / 文案编辑弹窗
frontend/src/api/ecomSet.ts
frontend/src/lib/ecomSet.ts                纯函数（槽位文案、状态汇总）
```

工具中心入口：在 `toolsCatalog.ts` 加一张「一图出套图」卡片，点击跳转到独立页面，不走通用的 `ToolDetailPage`。

## 6. 计费

- 规划：**按次小额收费**，默认 ¥0.10/次，用配置项 `ECOM_SET_PLAN_FEN=10` 控制，后台可调。登记为 `("studio", "ecom_set_plan")`（`_noop_ephemeral`），走 `run_billed_ephemeral`：先预扣，规划失败全额退回。修改方案（PUT /plan）不调模型，不收费；「重新规划」按一次新规划收费。页面上「生成方案」按钮旁直接标价。
- 生图：每张按现有 `tool_image` 价计；重做同价；改文案免费。
- 落地时同步更新 `docs/BILLING.md`。

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 商品变形（logo、包装文字被改） | 用户不敢用，最大的槽点 | 提示词加强 + 单张重做；第二阶段做抠图合成兜底 |
| TokenFree 没有可用的视觉模型 | 规划不准 | 退化为纯文字规划 + 引导用户填卖点；上线前确认渠道 |
| 文案触犯《广告法》 | 用户商品被平台下架 | prompt 约束 + 服务端词表过滤 |
| 留白区和文字压住主体 | 排版难看 | 提示词指定留白方位；渲染前检测留白区复杂度，过高就加底板 |
| 批量请求触发上游限流 | 出图慢、失败 | 走任务平台槽位；复用 `post_until_not_rate_limited` |
| 字体授权 | 法律风险 | 只用 OFL 字体（思源黑体） |

## 8. 排期与后续

**第 1 周（后端）**：模型/表、presets、planner（含纯文字退化）、layout 渲染、jobs 注册、接口、单测（规划 JSON 校验、版式渲染不溢出、极限词过滤、计费部分失败）。

**第 2 周（前端 + 联调）**：EcomSetPage 四步流程、轮询、单张操作、zip 下载、加载/空态/错误三态、中英文 i18n、`ARK_MOCK=true` 联调、`BILLING.md` 更新。

**第二阶段**
1. 爆款风格复刻：多传一张参考图（`style_ref_urls`），版式配色从参考图提取
2. 抠图（rembg / 上游抠图接口）+ 合成 + 重打光，严格保持商品原样
3. **套图 → 口播带货视频**：把套图作为分镜，接科普 `image_text` 模式（静图 + 旁白），再选数字人口播。这是 Picset 没有的差异点，也最贴合「开口播」的定位
4. 服装模特图、在线画布

## 9. 决策记录

| # | 问题 | 结论（2026-11-03） |
|---|---|---|
| 1 | 规划阶段收不收费 | 按次小额收费，默认 ¥0.10，可配置（见第 6 节） |
| 2 | 套图张数 | 默认 6 张，可选 4 / 8；生成前显示总价 |
| 3 | 视觉模型 | **`seed-2-0-lite-260428`（Dola-Seed-2.0-lite，BytePlus 渠道）**，已开通并实测通过（见下方第二轮） |

**视觉模型实测（2026-11-03，测试图：深蓝色按压式保温壶，家居背景）**

| 渠道 | 模型 | 结果 |
|---|---|---|
| byteplus-default | `deepseek-v4-flash-ga-260731`（当前文本模型） | 能调通，但自己回答「看不到图片」：**不支持看图** |
| byteplus-default | `seed-2-0-lite-260428` / `seed-2-0-mini-260428` / `seed-2-0-pro-260328` / `dola-seed-2-1-turbo-260628` / `glm-5-3-flash-260828` | 账号下有这些模型，但返回 `ModelNotOpen`（未开通） |
| byteplus-default | `seed-1-6-*`、`seed-1-8-*`、`skylark-vision-*` | 404，已下线或没有权限 |
| tokenfree | 全部 | 渠道没有配 Key，测不了 |

**第二轮（同日）**：在账号 3005277965（项目 Key 所在账号，Johor / ap-southeast-1 区域）开通 Dola-Seed-2.0-lite，并在 API Key `printfilm-local-20261028` 的「自定义权限」里勾选该模型。注意：这个 Key 只能调用白名单里的模型，以后接新模型也要先在这里勾上，否则会返回 403 AccessDenied。

| 模型 | 结果 |
|---|---|
| `seed-2-0-lite-260428` | **通过**，耗时 16 秒。识别为「气压式保温暖壶」，颜色宝蓝 + 黑色，卖点（按压出水、提手、保温）全部正确，也如实说明图中没有可辨认的文字 |

价格（官方，Prompt ≤128k）：输入 $0.25 / 百万 tokens，输出 $2 / 百万 tokens。一次规划约 2k 输入 + 1k 输出，成本不到 ¥0.02，按 ¥0.10/次收费有足够余量。

落地待办：
- 管理后台把 `seed-2-0-lite-260428` 加进 byteplus-default 渠道的模型列表，并新增逻辑模型 `vision` 指向它
- 16 秒偏慢，接入时试一下关闭深度思考（`thinking: disabled`），目标压到 5 秒以内

补充：本地环境实际启用的是 BytePlus 直连渠道（文本模型 `deepseek-v4-flash`），TokenFree 渠道是关闭的。选视觉模型要以线上实际启用的渠道为准。

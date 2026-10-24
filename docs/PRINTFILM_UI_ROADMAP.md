# PRINTFILM UI 还原与后续路线图

本文档对应设计图的前端还原结果：已接线能力、与占位清单的关系。

**待开发功能请以 [PLACEHOLDER_BACKLOG.md](./PLACEHOLDER_BACKLOG.md) 为准**（含优先级与代码入口）。本文保留路由对照与视觉约定。

## 路由与设计图对应

| 设计图 | 路由 | 页面组件 |
|--------|------|----------|
| 首页 | `/` | `frontend/src/pages/HomePage.tsx` |
| 漫剧项目列表 | `/drama` | `frontend/src/pages/drama/DramaListPage.tsx`（`workflow=script`） |
| 自由画布列表 | `/canvas` | `frontend/src/pages/drama/CanvasListPage.tsx`（复用列表，`workflow=canvas`） |
| 新建项目 | `/studio/new` | `frontend/src/pages/studio/CreateProjectPage.tsx` |
| 风格配置 | `/studio/:id/style` | `frontend/src/pages/studio/StyleConfigPage.tsx` |
| 分镜工作台 | `/studio/:id` | `frontend/src/pages/studio/StoryboardPage.tsx` |
| 成片编辑器 | `/studio/:id/editor` | `frontend/src/pages/studio/EditorPage.tsx` |
| 我的项目 / 历史 | `/history` | `frontend/src/pages/HistoryPage.tsx` |
| 模板库 | `/templates` | `frontend/src/pages/TemplatesPage.tsx` |
| 定价 | `/pricing` | `frontend/src/pages/PricingPage.tsx` |

兼容旧入口：`/studio?project=` → `/studio/:id`；`/studio?template=` → `/studio/new?template=`。

共享壳层：`SiteNav` / `AppShell`（`frontend/src/components/layout/`），样式 token 见 `frontend/src/index.css` 与 `frontend/src/styles/printfilm.css`。

## 2026-10-24 主导航与自由画布入口改版

**状态：已完成待部署，尚未线上发布。** 顶部横向主菜单改为左侧分组导航，创作入口与资源协作入口分开展示；窄屏通过导航抽屉访问。全屏分镜与自由画布编辑器继续使用独立布局。

| 位置 | 入口 |
|---|---|
| 创作 | 工作台 `/`、漫剧 `/drama`、自由画布 `/canvas`、AI 短视频 `/history`、小工具 `/tools` |
| 资源协作 | 资产 `/assets`、团队 `/teams`、定价 `/pricing` |
| 底部 | 帮助、GitHub、语言切换、账户 |

自由画布提升为一级入口，不再依赖漫剧列表中的二级入口。`/canvas` 展示画布项目，`/drama` 只展示剧本项目；两者复用现有项目列表、创建与管理能力，分别向 `GET /api/drama/projects` 传入 `workflow=canvas`、`workflow=script`，由服务端筛选。旧调用不传 `workflow` 时继续返回全部可访问项目，个人与团队的空间隔离、权限和付款规则沿用现有实现，现有项目编辑地址不变。

部署验收需覆盖两类列表分流、个人/团队空间切换、已有项目直达、桌面左栏及窄屏抽屉；全屏分镜/画布编辑器不应被全局侧栏挤占。对应清单为 [G-05 / G-06](./PLACEHOLDER_BACKLOG.md#2-全局--壳层)。

## 已实现（接线现有 API）

- 登录 / 注册 / 导航账户状态
- 左侧分组主导航与窄屏抽屉，自由画布一级入口及剧本/画布列表分流（已完成待部署）
- 帮助中心（侧边抽屉 UI，需前端部署上线）
- 模板列表、分类筛选、从模板进入创作
- 创建项目（主题 / 完整文案）→ 风格配置 → `generate` 启动流水线
- 风格：切换模板预设、风格/角色提示词、音色试听、`pipeline_mode`、画幅
- 分镜台：进度、分镜表、编辑镜头、重绘/重生视频/重配音、继续生成、合成、预览、发布
- 分镜台：批量调时长、CSV 导出、封面上传 / 首镜封面
- 编辑器：场景列表、预览真实媒体、保存旁白、重绘、重配音
- 历史：列表、状态筛选、搜索、类型筛选、继续编辑、预览、删除、批量 zip、**服务端分页**；已发布按 Work 表区分
- 公开作品展示（首页）
- 全局 DialogHost（alert / confirm / prompt）
- 团队空间：真实成员/邀请、项目与资产分页、个人项目迁入、独立钱包与月额度、编辑占用；见 [TEAM_MODE.md](./TEAM_MODE.md)

## 占位与待开发

详见 **[PLACEHOLDER_BACKLOG.md](./PLACEHOLDER_BACKLOG.md)**，按页面与优先级分类。摘要：

| 优先级 | 方向 |
|--------|------|
| P0 | 前端部署（左侧导航、自由画布一级入口、帮助抽屉、续跑状态文案）、队列去重 / 项目互斥 |
| P1 | 链接/文档导入、BGM+字幕、素材上传替换、加镜调序、导出/分享、定价额度 |
| P2 | 通知、社区、风格商店、平台分发、转场以外的编辑器增强 |
| P3 | 福利、点赞播放量、转场、时间线精修 |

## 建议迭代优先级（历史）

以下条目已部分落地，保留作对照：

1. ~~P0：编辑器旁白保存 + 分镜重生~~（基本可用，继续打磨失败恢复）
2. ~~P1：封面上传~~（已做）；BGM / 字幕样式仍待做
3. P1：素材库（本地上传替换镜头图）— 仍待做
4. P2：平台导出 / 分享链接；通知中心
5. P2：额度与定价；社区作品流
6. P3：转场、时间线精修、文档/链接导入

## 视觉约定

- 主色石灰绿：`#B6FF00`（`--pf-lime`）
- 背景：`#F7F8FA`（`--pf-bg`）
- 卡片白底、圆角约 14px、轻阴影
- 左侧分组主导航：创作、资源协作；次要工具及账户放底部，窄屏使用抽屉
- 全屏分镜与画布编辑器保留独立布局，不嵌入全局侧栏
- 帮助等次要面板：右侧抽屉（`Modal variant=drawer`），避免居中大弹层挡工作台

字体：Space Grotesk（品牌英文）+ Noto Sans SC（正文）。

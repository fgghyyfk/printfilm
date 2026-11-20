# 多渠道与 API Key

管理后台「系统设置 → 模型」可以保存多组 TokenFree / BytePlus / 火山方舟配置。每条配置对应一个 Key 和一组默认模型；同一平台可以添加多条配置。选择一条并点击「保存」后生效，不会自动轮换或跨平台回退。

旧 TokenFree 配置和加密 Key 保留。API 只返回 Key 是否已保存，Key 留空表示不修改；勾选「保存时清除」才清除该条 Key。更改平台或区域时必须重新填写 Key，防止把旧平台凭据发到新地址。

BytePlus / 火山方舟接入：

- 支持 ModelArk 文本、Seedream 图片、Seedance 视频；按账号填写实际模型 ID 或 ep- 接入点 ID。每种能力一个模型，不使用 TokenFree 的别名作为上游 ID。
- BytePlus / 火山方舟暂不支持拉取模型目录，调用该入口会返回中文提示，模型或接入点 ID 需手动填写；不会改向 TokenFree 发送该渠道的 Key。目录查询仅允许 TokenFree 官方地址；覆盖已保存渠道地址时必须重新提供对应 Key。
- 区域支持 BytePlus 国际站的亚太 Johor、欧洲 Dublin，以及火山方舟国内站北京（`https://ark.cn-beijing.volces.com/api/v3`）。国际站区域列表与 Ark 原生协议白名单分开维护；北京使用相同的原生图片和视频路径，不走 TokenFree 包装。Key、接入点和模型须属于同一区域。当前没有验证用户账号区域，也不宣称香港或无审核。
- 未填写某项模型时该能力不可用，不会借用 TokenFree。暂未接入 BytePlus 的独立 TTS；可使用视频模型自带音频。视频使用原生 `/contents/generations/tasks`，图片使用 `/images/generations`。
- 存在任何非终态任务（包括待审、轮询、取消中）时不允许更改渠道配置，以免正在生成的视频改用另一渠道查询。单 API worker 运行；保存期间锁住任务表的写入。
- `ARK_MOCK=true` 仍为显式演示模式；添加 Key 不会自动关闭 Mock。真实调用前在运行参数关闭 Mock 并确认模型已开通。北京区域已通过受控 HTTP 单测验证图片创建、视频创建和查询的完整路径、模型 ID 与凭据；真实账号可用性和计费仍需另行验证。

官方参考：
- https://console.byteplus.com/ark/region%3Aap-southeast-1/docs/region-availability
- https://docs.byteplus.com/en/docs/modelark/quick-start
- https://console.byteplus.com/ark/region%3Aap-southeast-1/docs/create-video-generation-task-api

本机更新：Docker 正常运行后，先备份数据库，再执行 `./deploy/local.sh up -d --build api admin-web`。不需要迁移数据库表。

## 视频改走 Promptoll（Seedance）

后台同时只能有一条生效配置。线上生效的是火山方舟（文本 + Seedream 生图），没有视频模型，漫剧分镜视频跑不了。为此在「系统设置 → 生图」新增「视频改走 Promptoll」开关，只接管视频，文本与生图仍走生效配置。

- **原理**：开关打开且已填 Promptoll Key（与 gpt-image 生图共用，`zai_` 开头）时，组装运行时路由会追加一条只含视频的渠道 `promptoll-video`（`https://api.promptoll.com/v1`，不落库）和三个逻辑模型 `seedance-2.0` / `seedance-2.0-fast` / `seedance-2.5`，分别绑定上游 `dreamina-seedance-2-0-260128` / `dreamina-seedance-2-0-fast-260128` / `dreamina-seedance-2-5-260628`。报价、固定路由、任务恢复、轮询、前台模型目录都走标准路由。实现见 `backend/app/services/promptoll_video.py`。
- **协议**：Promptoll 的 `/v1/contents/generations/tasks` 是方舟协议透传（上游 Zenlayer），请求体与火山方舟相同，多参考图、首尾帧、参考音频、`return_last_frame` 都保留，路径不改写成 `/videos`。
- **成片下载**：上游成片链接需要上游 Key，Promptoll 把 `content.video_url`、`last_frame_url` 改写成 `/v1/contents/generations/tasks/{id}/content|last_frame`，映创下载时只对这两类地址带 Promptoll Key；这类地址不会再作为参考素材发给上游。
- **优先级**：生效配置里已有同名视频模型时以生效配置为准；默认视频模型只在生效配置没有可用视频模型时才设成开关里选的那款。模型可在「模型」页单独停用。
- **计费**：用户侧按「支付计费 → 图片与视频价格」里 Seedance 的秒价计费（与方舟相同）。Promptoll 侧按 token 预扣、出片后按实际 token 结算、失败退回；上游成本（约 $0.06/秒 · 2.0 Fast 480p）未回传到映创，用量明细里成本为待核实。
- **限制**：Seedance 不接受真人人脸参考图（需走上游素材库），含真人的照片会被上游拒绝并按提交失败处理。
- **启用**：无表结构变化。先保存 Promptoll Key，再打开开关并保存；保存后路由立即刷新，无需重启。

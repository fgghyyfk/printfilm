# 多渠道与 API Key

管理后台「系统设置 → 模型」可以保存多组 TokenFree / BytePlus 配置。每条配置对应一个 Key 和一组默认模型；同一平台可以添加多条配置。选择一条并点击「保存」后生效，不会自动轮换或跨平台回退。

旧 TokenFree 配置和加密 Key 保留。API 只返回 Key 是否已保存，Key 留空表示不修改；勾选「保存时清除」才清除该条 Key。更改平台或区域时必须重新填写 Key，防止把旧平台凭据发到新地址。

BytePlus 接入：

- 支持 ModelArk 文本、Seedream 图片、Seedance 视频；按账号填写实际模型 ID 或 ep- 接入点 ID。每种能力一个模型，不使用 TokenFree 的别名作为上游 ID。
- 区域支持亚太 Johor 与欧洲 Dublin，地址来自官方区域文档。Key、接入点和模型须属于同一区域。当前没有验证用户账号区域，也不宣称香港或无审核。
- 未填写某项模型时该能力不可用，不会借用 TokenFree。暂未接入 BytePlus 的独立 TTS；可使用视频模型自带音频。视频使用原生 `/contents/generations/tasks`，图片使用 `/images/generations`。
- 存在任何非终态任务（包括待审、轮询、取消中）时不允许更改渠道配置，以免正在生成的视频改用另一渠道查询。单 API worker 运行；保存期间锁住任务表的写入。
- `ARK_MOCK=true` 仍为显式演示模式；添加 Key 不会自动关闭 Mock。真实调用前在运行参数关闭 Mock 并确认模型已开通。没有真实 Key 时只能验证受控请求协议。

官方参考：
- https://console.byteplus.com/ark/region%3Aap-southeast-1/docs/region-availability
- https://docs.byteplus.com/en/docs/modelark/quick-start
- https://console.byteplus.com/ark/region%3Aap-southeast-1/docs/create-video-generation-task-api

本机更新：Docker 正常运行后，先备份数据库，再执行 `./deploy/local.sh up -d --build api admin-web`。不需要迁移数据库表。

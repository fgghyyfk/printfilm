# 多渠道与 Key 手动切换实施计划

目标：保留 TokenFree，新增 BytePlus，每条配置保存一个 Key 和模型，任一时刻仅一条配置启用。不自动轮换、不跨渠道回退。用户已确认此方案。

技术：复用 system_model_channels（密钥加密）、advanced_config（每个配置的模型）、enabled（唯一生效配置）；不新增数据库表。

1. 编写失败测试：多配置保存后重读与重启保持、Key 留空保留及显式清除、禁止多个 active、请求 URL/Key 成对切换、BytePlus 图片原生 images/generations 与视频 contents/generations/tasks 路径、未配置能力不回落到 TokenFree。
2. 增加 provider_profiles.py：固定 TokenFree 和 BytePlus 官方区域接口，校验 provider/区域/唯一生效、按配置的四类模型建立路由。BytePlus 支持文本/图片/视频，独立 TTS 不支持时明确报错。阻止非终态任务存在时更改配置，避免视频轮询换渠道。
3. model_settings.py 取消读操作强制 TokenFree 和禁用其他渠道；迁移已有 TokenFree 配置且保留加密 Key；保存多个配置，active 模型进入 overlay。旧 TokenFree 预设保持。
4. ark.py / tokenfree_video.py 按实际 provider 选择请求格式；BytePlus 模型 ID 原样传递，图片使用原生 Seedream body，视频保留原生 body。文字复用 OpenAI 兼容客户端。配置缺少 Key 或能力时明确失败，禁止读取其他渠道凭据。
5. 管理后台支持配置列表、添加 TokenFree/BytePlus、名称、Key、区域、每个配置的默认模型及单选生效。Key 不回显，留空不修改。保存后重新读取，TokenFree 预设仍可选，BytePlus 填实际模型/endpoint ID。
6. 验证：独立测试数据库集成测试、受控 HTTP 捕获验证协议与 Key、相关回归测试；两端构建；备份本地数据库；重建 API/后台并以浏览器验证配置表单。保持用户当前 active 和 Mock 状态，不虚报真实 BytePlus 调用成功。

成功标准：两组配置安全保存，只用选中配置的地址和密钥；Key 从不回显到 API、日志和 Git；禁用渠道不被选择；无真实 Key 时仅宣称受控测试通过。

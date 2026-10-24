# 本地媒体模拟

本地 `backend/.env` 设置 `LOCAL_MEDIA_MOCK=true`、`ARK_MOCK=false`，并开启
`TASK_RUNTIME_ENABLED=true`，重启后端。文字 LLM 保持真实请求和即时执行；
图片、视频、语音走正常任务状态流转，生成入口不向媒体供应商发送请求。
视频使用同一段 8 秒彩色测试素材，语音使用测试音，图片使用测试画面。
结果时长与内容不代表所请求的实际生成结果。

素材位于 `backend/static/mock/local/`，可运行
`backend/.venv/Scripts/python.exe backend/scripts/prepare_mock_media.py` 重新生成，需 FFmpeg。
模拟输入记录在 `.tmp/local-ai-requests.jsonl`，包含时间、任务 ID、操作和脱敏参数；
有任务上下文时同时追加 `upstream.request.simulated` 事件。模拟结果带 `simulated` 标记。
模拟拦截发生在媒体 HTTP 发送前，按真实流程完成渠道路由、模型映射、协议封装、
参考素材校验和发布；参考素材可能上传 OSS，媒体生成请求本身不会发给供应商。
日志及管理端保存最终 method、endpoint、脱敏 headers 和 JSON body，
视频包含实际 `/videos` 协议的 `prompt`、`seconds`、`metadata` 等字段。
早期 `business_input` 记录仅是业务输入，不可据此还原当时的最终 HTTP 请求。

保留现有业务计费与团队额度校验，可用于联调消费状态；不产生真实媒体供应商费用。
关闭时将 `LOCAL_MEDIA_MOCK=false` 并重启即可，文字模型配置无需修改。
开关默认关闭，不写入管理端模型配置，避免影响其他环境。

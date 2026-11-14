# docs 目录索引

PRINTFILM（ai_movie）产品与工程文档。

| 文档 | 说明 |
|------|------|
| [API.md](./API.md) | 中文 API 文档入口、登录认证、团队编辑租约、漫剧字段与分页/任务/错误约定 |
| [DIGITAL_ASSET_DEVELOPMENT.md](./DIGITAL_ASSET_DEVELOPMENT.md) | 数字资产市场分期：第一期 Web 广场与版权交易，第二期 App 持续收益；Word 版见同目录 docx |
| [MARKETPLACE.md](./MARKETPLACE.md) | 单视频 + 短剧剧集数字资产市场、客服招募、版权核验、平台投放收益与私有 OSS：配置、接口、操作流程及验收边界 |
| [LOCAL_DEVELOPMENT.md](./LOCAL_DEVELOPMENT.md) | 本机 Windows 环境、后台启停脚本、访问地址与日志位置 |
| [PROJECT_ARCHITECTURE.md](./PROJECT_ARCHITECTURE.md) | 项目深入导读：业务流程、技术架构、任务时序、计费、数据关系、部署与源码阅读地图 |
| [STANDARDS.md](./STANDARDS.md) | **全局规范**（注释、分层、前后端、Git、发布、安全） |
| [BILLING.md](./BILLING.md) | 计费与易支付 |
| [EPISODE_RULES.md](./EPISODE_RULES.md) | **漫剧分集主规范**（开幕/拆镜/脚本/资产/Seedance/门禁/后续清单） |
| [SEEDANCE_2_5.md](./SEEDANCE_2_5.md) | Seedance 2.5 公开参数 / 参考素材上限 / 本仓库映射 |
| [SHOT_SPLITTING.md](./SHOT_SPLITTING.md) | 拆分镜头操作指南（Fragment / Beat、空镜 vs 口播） |
| [PLACEHOLDER_BACKLOG.md](./PLACEHOLDER_BACKLOG.md) | **占位 / 待开发功能总表**（ComingSoon、禁用入口、建议优先级） |
| [PRINTFILM_UI_ROADMAP.md](./PRINTFILM_UI_ROADMAP.md) | UI 还原对照：路由、已接线能力、视觉约定 |
| [PRINTFILM_UI_DESIGN_PROMPTS.md](./PRINTFILM_UI_DESIGN_PROMPTS.md) | 多产品平台 UI 生图提示词（壳 / 工作台 / 漫剧 / 科普 / 工具） |
| [../deploy/README.md](../deploy/README.md) | 中间件 compose / 本地运维补充 |
| [../README.md](../README.md) | 仓库总览与本地启动 |

相关非 docs 备忘：

- `.claude/工作流状态.md` — 线上地址与中间件端口
- `.tmp/` — 本地批处理脚本与抓取文案（勿入库敏感 token）
- `deploy/scripts/deploy_kepu.py` — 全量发布脚本（gitignore，含凭据）

**维护约定**：

- 编码与协作先读 [STANDARDS.md](./STANDARDS.md)；约定变更时同步更新该文。
- 新增 `ComingSoon` / `disabled` 功能入口时，同步更新 `PLACEHOLDER_BACKLOG.md`；功能上线后勾掉对应条目。
- 现网发版记录写在本机 `docs/releases/`（不上开源仓库）。

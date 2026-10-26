# PRINTFILM 用户端

React 19、TypeScript 与 Vite 用户端。管理后台位于仓库的 `admin/`，两端分别构建。

## 本地开发与检查

使用 Node.js 22.21 或更高版本，在本目录执行：

```bash
npm ci
npm run dev
npm test
npm run lint
npm run build
```

`npm test` 使用 Node 内建测试运行器及 TypeScript 类型擦除，无需额外测试框架。覆盖 HTTP 传输、空间请求隔离、切换取消、占用冲突文案与并行分集任务状态。后端角色、邀请、钱包与租约校验由 PostgreSQL 集成测试覆盖；支付测试不会创建真实订单。

## 团队模式

- `/teams` 是真实团队目录，必须登录。团队创建、成员邀请、权限/额度、钱包明细、项目和共享资产都通过 `src/api/teams.ts` 访问服务端，并使用服务器分页与搜索。
- `/teams/:id` 提供概览、项目、资产、成员、钱包和设置。团队项目进入既有 `/drama/projects/:id` 工作台；接口响应的 `team_id` 决定实际空间，不能通过切换菜单把个人内容变成团队内容。
- `/teams/join?token=…` 与 `/team-invitations?token=…` 支持接受邀请；令牌通过 POST 正文验证，登录邮箱须匹配。接受成功后替换 URL 清除令牌。创建或重发后的加入链接仅保留在内存，不使用浏览器存储恢复邀请凭证。
- 所有者管理全队财务、成员额度和所有权；管理员管理邀请与普通成员；成员和管理员只查看本人消费。服务端再次执行全部权限检查。
- 个人和团队钱包独立，团队充值复用 `PaymentModal`，只有用户确认套餐与渠道才创建真实团队订单。页面不会模拟加款。
- 个人项目可整体转入团队；确认前会明确说明不可转回。项目需没有在途任务或编辑占用。跨项目资产复制由后端校验来源/目标空间，不复制前端携带的任意参数。
- `workspaceScope.ts` 只在内存保存当前请求空间，切换时取消旧请求；个人请求不携带团队 ID，团队创作带 `X-Team-Id`。现有项目的实际归属优先于导航选择。短视频、工具和任务中心进入个人空间。
- `editLocks.ts`、`useTeamEditLock.ts` 与 `TeamEditingBoundary.tsx` 为团队写入附加 `X-Edit-Session`、`X-Edit-Token`、`X-Edit-Version`。页面占用每 30 秒续租，120 秒过期，离开时释放；冲突或失锁后进入只读；重新取得占用后先读取最新内容，旧草稿不会自动升级租约后提交。所有者/管理员可确认接管人工占用，系统任务不能强制接管。
- 分集正文按集 PATCH，正文和该集分镜共用稳定集号占用；不同集的任务状态从 `script.params.episode_jobs` 独立读取。画布保存布局，资产写入走独立的资产接口和占用。
- 团队页面及团队项目工作台隐藏个人生成队列和个人余额弹窗，避免把个人任务取消、充值入口混入团队空间。已有任务继续由服务端运行。

不再加载示例团队、固定操作者或演示账单，也不将团队数据写入 `sessionStorage`。

## 目录职责

| 目录 / 文件 | 职责 |
|---|---|
| `src/pages/` | 路由页面与交互编排 |
| `src/components/` | 通用 UI 及按业务分组的组件 |
| `src/hooks/` | 可复用的 React 状态与订阅 |
| `src/lib/` | 纯函数与业务工具，新增逻辑前先查找已有实现 |
| `src/api/http.ts` | 可独立测试的 HTTP 传输、请求头合并与错误文案解析 |
| `src/api/client.ts` | 浏览器配置、登录态、计费错误提示及通用上传的统一入口 |
| `src/api.ts`、`src/api/*.ts` | 各业务接口及类型；通过共享客户端发送请求 |
| `src/styles/printfilm.css` | `pf-*` 语义样式，保持现有视觉体系 |

业务模块通过 `createJsonRequester` 复用请求实现，并保留自身错误提示；单文件上传使用 `uploadFile`，非 JSON 下载使用 `requestResponse`。不要在页面重新拼鉴权头或复制 `fetch` 包装器。

## API 地址

- 未设置 `VITE_API_BASE`：使用当前主机的 `8000` 端口。
- 显式设为空字符串：请求同源 `/api`，供生产 Nginx 反向代理。
- 设为完整地址：请求指定 API 服务。

Vite 未配置开发 API 代理。详细工程规范见仓库的 `docs/STANDARDS.md`。

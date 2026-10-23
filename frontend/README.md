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

`npm test` 使用 Node 内建测试运行器及 TypeScript 类型擦除，无需额外测试框架。目前覆盖 API 地址解析、动态登录态、请求头覆盖、文件上传、下载及错误处理；不需要后端服务。

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

# Windows 本地开发环境

本机项目目录：`C:\code\printfilm`。使用 Python 3.12 虚拟环境、两个 Vite 开发服务器、Docker PostgreSQL 与 Redis。未启用模拟 AI，也未接入真实 AI、支付、OSS 或邮件服务。

## 一键启动与停止

先启动 Docker Desktop，然后在 PowerShell 中运行：

```powershell
cd C:\code\printfilm
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\.work\local-dev\start.ps1
```

脚本在后台运行后端和两个前端，后端启用热重载。重复执行会复用本脚本已启动的进程；其他进程占用目标端口时会停止启动并提示，不会自动终止它。

停止本项目三个应用及两个容器：

```powershell
cd C:\code\printfilm
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\.work\local-dev\stop.ps1
```

停止脚本按本次保存的 PID 和启动时间核对进程，仅停止本项目的服务树；Docker 数据卷保留。重启电脑后重新启动 Docker Desktop 并运行启动脚本即可。

`.work/local-dev` 是本机辅助目录，已被 Git 忽略，脚本不会随仓库提交到其他电脑。上述 ExecutionPolicy 参数只作用于当前命令，不修改系统全局执行策略。

## 访问入口

- 用户端：http://127.0.0.1:5173
- 用户注册登录：http://127.0.0.1:5173/auth
- 管理后台：http://127.0.0.1:5174
- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health
- PostgreSQL：127.0.0.1:15432，数据库和用户均为 `printfilm`。
- Redis：127.0.0.1:16379。

管理后台需要管理员身份。先在用户端注册自己的账号，再将注册邮箱写入 `backend/.env` 的 `ADMIN_BOOTSTRAP_EMAILS`，重启后端即可提升权限。没有预置管理员账号；验收临时账号会删除。

## 本机配置与日志

- `backend/.env`：数据库连接、应用随机密钥、媒体工具绝对路径与功能开关。
- `deploy/.env.prod`：本地 PostgreSQL 用户、随机密码和映射端口。文件名沿用项目现有 Compose 约定，内容用于本地开发。
- `backend/.venv`：项目独立 Python 环境。
- `C:\soft\ffmpeg`：FFmpeg essentials 发行包；程序位于版本子目录的 `bin` 下，已配置到后端，无需手动添加 PATH。
- `.work/local-dev/*.stdout.log`、`*.stderr.log`：各应用日志。
- `.work/local-dev/processes.json`：本机辅助脚本管理的进程信息。
- `.work/local-dev/verification.json`：本地验收摘要，不含测试账号密码和访问令牌。

两份环境文件均在 Git 忽略规则内。数据库密码可在本机环境文件中查看，不要提交到版本库或复制到公开日志。现有其他项目容器不属于本项目的启停范围。

## 在 VS Code 中分别启动

如果需要直接观察终端日志，先用停止脚本停掉后台服务，再依次启动：

```powershell
cd C:\code\printfilm
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.prod up -d --wait
```

后端终端：

```powershell
cd C:\code\printfilm\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

用户端终端：

```powershell
cd C:\code\printfilm\frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

管理端终端：

```powershell
cd C:\code\printfilm\admin
npm.cmd run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

前台启动的应用用各自终端的 Ctrl+C 停止。不要同时使用后台脚本和前台命令抢占同一端口。不需要另开 Celery Worker。数字资产市场启用时，其媒体 Worker 也随后端自动启动、停止和异常恢复，无需单独运行命令；未配置 OSS 时空闲等待。若之前手动开过市场 Worker，请在那个终端按 Ctrl+C 关闭，以后只启动后端即可。

## 验收与外部功能边界

本地验收只访问本机接口，覆盖应用健康、前端入口、注册登录、错误密码、重复注册、数据库与 Redis，以及 FFmpeg 中文字体渲染。验收脚本位于 `.work/local-dev/verify_local.py`，可用后端虚拟环境 Python 重跑。

`ARK_MOCK=false`，AI Key 为空；计费、OSS、SMTP 关闭。未验证真实生成、支付、云上传和发邮件。当前源码部分 AI 路径缺 Key 时会自动回退 mock，这是已有业务行为，本次没有修改，也没有触发这些接口。

前端依赖按各自锁文件安装。更换电脑或清除 node_modules 后，在 `frontend`、`admin` 目录分别执行 `npm.cmd ci`。


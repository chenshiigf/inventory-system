# Windows 本地视觉搜索管理员配置

普通 Uvicorn 不会自动加载 `api/.env`。CLI 评测显式加载该文件，并使用独立的临时管理员凭据；评测成功不代表日常 API 进程已完成鉴权配置。

真实 API 继续使用 `api/app/services/visual_search/auth.py`：Basic Auth 验证 htpasswd 密码，`VISUAL_ADMIN_USERS` 限制管理员，未配置时 503、未登录或非管理员 401、跨站 POST 403。不得在开发环境跳过此依赖。

首次在自己的 PowerShell 创建本地 bcrypt 凭据，隐藏输入两次密码。工具拒绝覆盖已有文件；不要使用生产密码文件或测试用的固定密码。

```powershell
cd D:\inventory-system
api/.venv/Scripts/python.exe api/scripts/create_visual_admin.py --file api/data/visual-search/.htpasswd --user admin
```

在已有的 `api/.env` 中补齐以下配置，保留原有 `DASHSCOPE_API_KEY`，不要输出或提交该文件：

```dotenv
INVENTORY_HTPASSWD_FILE=D:/inventory-system/api/data/visual-search/.htpasswd
VISUAL_ADMIN_USERS=admin
VISUAL_ALLOWED_ORIGINS=http://127.0.0.1:3002,http://localhost:3002
```

如果选择其他用户名，密码文件与名单必须一致。`.env` 和 `api/data/visual-search/.htpasswd` 已被 Git 忽略。API 启动后修改环境文件，需要在自己的终端停止并重启本地 API；不要停止无关服务。

```powershell
cd D:\inventory-system
api/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir api --reload --reload-dir api/app --env-file api/.env --host 127.0.0.1 --port 8002
```

如果本机允许运行本地 PowerShell 脚本，也可用 `api/scripts/start_dev_api.ps1` 执行同样的启动命令。启动命令显式加载本地 `.env`，只监听 `127.0.0.1:8002`，只监视 `api/app` 代码；不执行数据库 migration、seed、构建或索引任务。

## 前端同源登录与开发代理

项目没有 Cookie/JWT 登录会话；生产库存页面使用 Nginx Basic Auth。开发环境由 Next.js 对 `/api/visual-search/:path*` 做同源反代，转发浏览器请求中的 `Authorization`，FastAPI 再验证 htpasswd 与管理员名单。代理没有管理员账号或密码，也不为未登录请求补认证。业务 API 地址仍由原有 `NEXT_PUBLIC_API_BASE_URL` 控制。

在 `web/.env.local` 中可设置服务端变量 `VISUAL_DEV_API_ORIGIN=http://127.0.0.1:8002`，不设置时使用该默认值。仅接受不含凭据、路径或查询参数的本机 HTTP Origin；禁止把密码写入 URL、`NEXT_PUBLIC_*`、前端代码或浏览器存储。修改代理配置后重启自己的 Next.js 开发进程。

```powershell
cd D:\inventory-system\web
pnpm dev --port 3002 --hostname 127.0.0.1
```

浏览器先打开 **前端端口**的 `http://127.0.0.1:3002/api/visual-search/index/status?source=production-catalog`，用本地 admin 账号完成浏览器 Basic Auth 登录，再打开 `http://127.0.0.1:3002/ai-search/`。登录弹窗只在浏览器处理，前端代码不读取密码。直接在 8002 登录的认证空间不能代替 3002 的登录；同一次使用固定前端主机名，`localhost:3002` 也可通过自身同源接口登录。

开发模式的搜索、反馈、状态、只读详情、主图和查询图均请求前端同源路径，显式保留 `source=production-catalog`。Next.js 不改写 `Origin`、`Sec-Fetch-Site` 或认证头来伪装权限，跨站请求仍被 FastAPI 拒绝；POST 不加尾斜杠、不重定向、不自动重试。代理超时与现有前端查询超时一致为 240 秒，不代表模型调用的费用硬限制。

生产构建仍为 `output: export`，不启用开发 rewrites、不新增 Next.js 生产服务。生产继续复用既有 Nginx 同源 `/api` 和 Basic Auth；静态 `out/` 自身不会提供开发代理。没有修改或部署任何生产配置。

无费用检查真实 HTTP 入口：

```powershell
api/.venv/Scripts/python.exe api/scripts/check_visual_dev_auth.py --user admin
# 验证整个前端代理链路，同样不调用付费模型：
api/.venv/Scripts/python.exe api/scripts/check_visual_dev_auth.py --user admin --via-web
```

检查器仅向本机指定的 8002 或 3002 端口发送凭据，禁用系统代理和重定向，不打印或保存密码。它验证完整生产图库就绪、匿名请求 401，并提交故意无效的图片：鉴权通过后应返回图片校验 400，说明已进入搜索处理，尚未调用模型。跨站请求仍应 403，检查前后调用账本计数必须一致。401 表示未登录或凭据不符；403 表示请求来源被拒绝，不能通过降低鉴权解决。

`ready` 是检查器根据现有状态字段计算的验收结果；原始状态 API 使用 `schema_ready`、`api_key_configured`、`indexed` 等字段，没有新增 `ready` 字段。该探针不能代替一张有效照片的真实付费搜索成功验证。

本配置只用于 Windows 本地，不修改 Nginx、生产 htpasswd、生产服务或接口权限。

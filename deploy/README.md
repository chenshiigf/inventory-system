# 生产部署配置模板

本目录记录现场已确认可用的 systemd、Nginx 和环境变量结构。模板不会自动安装或修改服务器；实际配置由管理员维护。本阶段不增加域名、TLS、用户系统或自动部署脚本。

## 模板用途

| 仓库文件 | 用途 |
| --- | --- |
| `inventory-system-api.service.example` | systemd API service 模板，实际 service 放在 `/etc/systemd/system/` |
| `nginx-inventory-system.conf.example` | Nginx 库存系统 server block，由管理员放入 Nginx 配置并启用 |
| `inventory-system.env.example` | `/etc/inventory-system.env` 的变量名称与安全示例；真实值不进入 Git |

运行版本和依赖安装见 [运行环境基线](../docs/runtime-baseline.md)，备份恢复说明见 [备份与恢复](../docs/backup-and-restore.md)。本文件不是完整灾难恢复手册。

## 目录用途

| 路径 | 用途 |
| --- | --- |
| `/srv/inventory-system` | Git 项目代码、API 虚拟环境；API 工作目录是其中的 `api/` |
| `/var/www/inventory-system/out` | Nginx 读取的 Next.js 静态导出产物 |
| `/var/lib/inventory-system` | 持久化数据根目录的安全示例，包含 SQLite、uploads 和 import-previews |
| `/var/backups/inventory-system` | 完整备份集存放位置的示例；不表示已配置自动备份或异机复制 |
| `/etc/inventory-system.env` | systemd 加载的生产环境文件，由管理员维护实际值 |

数据与备份目录是示例，实际位置以管理员配置为准。`inventory` 用户须能读取 API 代码和虚拟环境，并能写入数据库所在目录、uploads 和 preview 目录；SQLite 的日志文件也需要目录写权限。Nginx 须能读取静态导出文件及单独创建的 Basic Auth 密码文件。

## 运行关系

```text
Browser
  → Nginx :8082（server 层 Basic Auth）
      → 静态前端：/var/www/inventory-system/out
      → /api/、/uploads/、/import-previews/
          → FastAPI 127.0.0.1:8102
```

### systemd / FastAPI

- 保留现场启动命令，API 只监听 `127.0.0.1:8102`，不应监听公网。
- 服务用户和组均为 `inventory`，工作目录为 `/srv/inventory-system/api`。
- 使用 `/etc/inventory-system.env`，设置 `PYTHONUNBUFFERED=1`；失败重启间隔为 3 秒。
- 必须保持单 worker。当前 Uvicorn 命令默认启动一个 worker，不增加多 worker 参数，也不设置改变 worker 数量的 `WEB_CONCURRENCY`。
- 导入预览任务状态保存在进程内，多 API worker 不属于当前部署基线。

### Nginx / Basic Auth

- 监听 IPv4 / IPv6 的 `8082` 端口；静态 root 为 `/var/www/inventory-system/out`。
- Basic Auth 位于整个 `server` 层级，各 location 不关闭认证，因此网页、静态资源、API、uploads 和 import-previews 均继承认证。
- `/etc/nginx/.tradecatalog_htpasswd` 必须由管理员单独创建并确保 Nginx 可读，不能提交 Git；模板仅记录其路径，不包含账号、密码或 hash。
- 上传限制为 `150m`；API 的 connect timeout 为 `60s`，send/read timeout 为 `300s`。
- 三个后端 location 的 `proxy_pass` 均不追加路径，保留 `/api/`、`/uploads/`、`/import-previews/` 原始请求路径。
- `/_next/static/` 保留一年 immutable 缓存；静态导出使用 `try_files $uri $uri.html $uri/ =404`，404 页面为 `/404.html`。
- 本模板不额外反代 `/docs`、`/redoc` 或 `/openapi.json`，这些路径按现有静态规则处理。

## 环境变量与前端构建

环境模板只包含 `INVENTORY_DATA_DIR` 和 `DATABASE_URL`。示例 `sqlite:////var/lib/inventory-system/inventory.db` 是 SQLAlchemy 使用的绝对 Unix SQLite 文件路径；显式 `DATABASE_URL` 优先决定数据库位置，不改变 uploads / preview 根目录。

当 `INVENTORY_DATA_DIR` 使用示例值时，uploads 位于 `/var/lib/inventory-system/uploads/`，preview 位于 `/var/lib/inventory-system/import-previews/`。应用没有自动加载生产 `.env` 的逻辑，systemd 通过 `EnvironmentFile` 注入变量。

**Shell 手动执行 backup / restore 不会自动继承 systemd 的环境。** 管理员需在有权限的 Shell 中加载并导出同一环境文件。备份源由当前进程环境决定；restore 的数据目标由 `--target` 明确指定。不要把仅为开发准备的 `api/data/` 当作正式备份源。

前端生产构建使用同源 `/api`。从 `web/` 目录构建时明确设置：

```sh
NEXT_PUBLIC_API_BASE_URL= pnpm build
```

`NEXT_PUBLIC_API_BASE_URL` 是构建时配置，不由 API 的 systemd 环境决定。构建产物是 `web/out/`，由管理员部署到上述 Nginx 静态目录；不要把开发用的 loopback API URL 带入生产产物。

## 秘密与配置边界

Git 只保存 `.example` 模板及说明。真实生产环境值、Basic Auth 密码文件和任何密码、token、secret 均由管理员在仓库外维护。

这组模板忠实记录当前配置，不包含实际服务启停、Nginx reload、数据恢复或服务器修改操作。

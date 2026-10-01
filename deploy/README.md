# 生产部署配置模板

本目录记录现场已确认可用的 systemd、Nginx 和环境变量结构。模板不会自动安装或修改服务器；实际配置由管理员维护。本阶段不增加域名、TLS、用户系统或自动部署脚本。

## 模板用途

| 仓库文件（均为 `.example`） | 安装目标 | 所有者 / 权限 | 用途 |
| --- | --- | --- | --- |
| `inventory-system-api.service.example` | `/etc/systemd/system/inventory-system-api.service` | root:root / 644 | 单 worker API |
| `nginx-inventory-system.conf.example` | `/etc/nginx/sites-available/inventory-system` | root:root / 644 | 8082 静态前端和反代；链接到 sites-enabled |
| `inventory-system.env.example` | `/etc/inventory-system.env` | root:inventory / 640 | 数据目录和 SQLite URL |
| `inventory-db-backup.py.example` | `/usr/local/sbin/inventory-db-backup.py` | root:inventory / 750 | 标准库每日 DB 快照；默认保留 14 天 |
| `inventory-db-backup.service.example` | `/etc/systemd/system/inventory-db-backup.service` | root:root / 644 | inventory 用户执行每日快照 |
| `inventory-db-backup.timer.example` | `/etc/systemd/system/inventory-db-backup.timer` | root:root / 644 | 每天 03:30 Asia/Shanghai，Persistent |
| `inventory-full-backup.service.example` | `/etc/systemd/system/inventory-full-backup.service` | root:root / 644 | 复用现有 full 备份 CLI；mtime +56 清理为 best-effort |
| `inventory-full-backup.timer.example` | `/etc/systemd/system/inventory-full-backup.timer` | root:root / 644 | 每周日 04:00 Asia/Shanghai，Persistent |
| `inventory-oss-upload.sh.example` | `/usr/local/sbin/inventory-oss-upload.sh` | root:inventory / 750 | db/full 最新已发布目录上传；无 OSS 删除 |
| `inventory-oss-upload@.service.example` | `/etc/systemd/system/inventory-oss-upload@.service` | root:root / 644 | 分离的 OSS 上传 oneshot |
| `inventory-db-backup.service.d/oss-upload.conf.example` | `/etc/systemd/system/inventory-db-backup.service.d/oss-upload.conf` | root:root / 644 | OnSuccess → uploader@db |
| `inventory-full-backup.service.d/oss-upload.conf.example` | `/etc/systemd/system/inventory-full-backup.service.d/oss-upload.conf` | root:root / 644 | OnSuccess → uploader@full |

运行版本和依赖安装见 [运行环境基线](../docs/runtime-baseline.md)，手动操作见 [备份与恢复](../docs/backup-and-restore.md)。从新 ECS 开始的安装命令、下载校验和恢复顺序见 [灾难恢复手册](../docs/disaster-recovery.md)。

## 目录用途

| 路径 | 用途 |
| --- | --- |
| `/srv/inventory-system` | Git 项目代码、API 虚拟环境；API 工作目录是其中的 `api/` |
| `/var/www/inventory-system/out` | Nginx 读取的 Next.js 静态导出产物 |
| `/var/lib/inventory-system` | 已确认生产数据根目录，包含 SQLite、uploads 和临时 import-previews；inventory:inventory / 750 |
| `/var/backups/inventory-system/db` | 每日 DB 备份；inventory:inventory / 750 |
| `/var/backups/inventory-system/full` | 每周完整备份；inventory:inventory / 750；只放备份集，不放其他数据 |
| `/var/backups/inventory-system/recovery` | 恢复时新下载的独立副本；不在 db/full 自动保留目录内 |
| `/var/lib/inventory-system-recovery` | 独立恢复演练 target 的父目录；inventory:inventory / 750 |
| `/var/tmp/inventory-ossutil` | 上传 output / checkpoint 工作目录；inventory:inventory / 750 |
| `/etc/inventory-system.env` | systemd 加载的生产环境文件，由管理员维护实际值 |

上述路径依据本次生产确认。`inventory` 用户须能读取 API 代码和虚拟环境，并能写入数据库所在目录、uploads 和 preview 目录；SQLite 的日志文件也需要目录写权限。静态目录 root:root / 755、静态文件 644；密码文件 root:www-data / 640，供 Ubuntu Nginx 用户读取。

## 备份与 OSS 串联

```text
daily timer → DB backup service → OnSuccess → OSS uploader@db
weekly timer → full backup service → OnSuccess → OSS uploader@full
```

- DB 模板是缺失生产脚本的等价实现，不是生产脚本逐字拷贝：sqlite3 backup API → integrity_check → SHA-256 manifest → 同目录临时目录 rename。字段与现场样例一致，时间使用服务器本地时间，无时区后缀。清理仅针对直接子目录中完整的每日 DB 备份；失败会记录 warning。
- full service 继续调用 `api/scripts/backup_inventory.py`，不复制业务实现。full manifest 的 created_at 和目录时间采用现有 UTC 行为，可能比北京时间少 8 小时。
- full `ExecStartPost` 保留清理命令和 `mtime +56` 规则，在可执行路径前使用 systemd 的 `-` 前缀。清理非零退出状态会被记录并忽略，find/rm 错误仍在 journal 中；不会把已生成并验证的 full 备份判为失败，也不会因此阻断 OnSuccess 上传。主备份 ExecStart 不忽略错误，备份真正失败时仍不触发上传。管理员已确认生产服务器同步采用该前缀，仓库模板与生产一致。
- uploader 按目录名排序选择最新时间戳目录，不处理 `.tmp`；缺少所需文件会失败。OSS 上传是独立 service，其失败不删除或修改成功的本地备份。不能仅凭本地 service 成功判定 OSS 备份完整，应检查上传 service 和远端文件。
- 两个 timer 为 `Persistent=true`，新启用时可能补执行错过的任务；应在正式数据恢复、校验完成后启用。

OSS 基线：ossutil **v1.7.19**；杭州 internal endpoint；Private Bucket `inventory-system-backup`、Block Public Access；角色 `InventorySystemBackupRole`、策略 `InventorySystemBackupOSS`。不在配置或脚本中保存长期 AccessKey。

角色允许 Bucket 的 ListObjects，以及对象的 GetObject / PutObject / ListParts / AbortMultipartUpload，**没有 DeleteObject**。控制台生命周期按对象最后修改时间：`db/` 90 天、`full/` 180 天。脚本不实现 OSS retention；重新 `-f` 上传相同对象会更新其最后修改时间。

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

Git 保存 `.example` 模板及说明；已确认的公开路径、Bucket / Role 名称可以记录。Basic Auth 密码文件和任何密码、AccessKey、token、secret 均由管理员在仓库外维护。

复制模板、启停服务与恢复均由管理员按灾难恢复手册执行。本地 Windows 语法检查不能替代新 Ubuntu 上的 systemd / Nginx 验证。

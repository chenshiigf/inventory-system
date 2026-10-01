# 备份与恢复：日常手动操作

整台 ECS 丢失时，按 [灾难恢复手册](disaster-recovery.md) 从 GitHub + OSS 恢复；模板用途和权限见 [deploy](../deploy/README.md)，版本见 [运行基线](runtime-baseline.md)。本页只保留现有 CLI 的日常用法。

## 两种备份

| 类型 | 本地目录 | 内容 | 本地 / OSS 保留 |
| --- | --- | --- | --- |
| db | `/var/backups/inventory-system/db` | `inventory.db`、`manifest.json` | 14 天 / 90 天 |
| full | `/var/backups/inventory-system/full` | `inventory.db`、`uploads.tar.gz`、`manifest.json` | 生产 mtime +56 / 180 天 |

full 使用 SQLite backup API，检查数据库 integrity、归档文件数与 SHA-256 后才发布目录。包含 uploads 下的正式图片；不包含 import-previews、代码、build、缓存或认证密码文件。db 只有数据库，不能单独用来完整恢复图片。

## 手动 full 备份

管理员执行下面的命令。Shell 不会自动继承 systemd 的 EnvironmentFile，必须显式加载，避免备份 `api/data` 开发目录：

```bash
sudo -u inventory -H bash -c '
set -euo pipefail
set -a
. /etc/inventory-system.env
set +a
cd /srv/inventory-system/api
test -s /var/lib/inventory-system/inventory.db
.venv/bin/python scripts/backup_inventory.py --destination /var/backups/inventory-system/full
'
```

或者 `sudo systemctl start inventory-full-backup.service`，它会执行本地清理并在成功后触发 OSS 上传。手动直接运行 CLI 不触发 OnSuccess；需要时另运行 uploader service，并确认远端文件完整。

## 先演练，再替换

选择实际存在的完整备份目录，替换下例时间戳。先恢复到**新的独立目录**（不使用 `--force`）：

```bash
sudo bash -c '
set -euo pipefail
set -a
. /etc/inventory-system.env
set +a
cd /srv/inventory-system/api
.venv/bin/python scripts/restore_inventory.py \
  --backup /var/backups/inventory-system/full/2026-10-01_111022 \
  --target /var/lib/inventory-system.restore-check-2026-10-01_111022
'
```

现有 restore 工具先检查 full manifest、大小、hash、DB integrity、tar 安全路径和文件数，再安装并验证 DB + uploads。返回 0 表示恢复成功（可能伴随 cleanup warning）；返回 1 表示恢复失败。warning 中的残留路径须保留记录并确认后处理，不应重新回滚成功的数据。

restore 在 target 父目录创建 staging/rollback，`/var/lib` 不应对 inventory 开放写权限，所以这类 target 使用 root 运行，完成后将正式 target 的所有权交还 inventory。

替换已有正式数据前，停止 API **和备份 timers**，确认没有正在执行的 backup/uploader jobs，留存当前数据的独立完整备份。然后按同一环境运行 restore，明确指定正式 target 并添加 `--force`。它只替换 `inventory.db` 和 `uploads/`，不会删除备份源。新 ECS 的空目标不需要 `--force`。

恢复完成后确认 inventory 所有权、数据库完整性、图片、商品与库存流水数量，再检查 Alembic current/head，必要时在 API 停止状态下 upgrade。不得运行 seed、编号 backfill 或 stamp 来替代恢复。最后启动 API、验证网页，再启用 timers。详细命令和失败处理见灾难恢复手册。

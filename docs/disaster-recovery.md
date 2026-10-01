# 灾难恢复：从 GitHub + OSS 重建新 ECS

适用场景：旧 ECS 完全不可用，只剩 GitHub 仓库和 OSS 备份。由管理员在**全新的 Ubuntu 24.04 x86_64 ECS** 上逐步执行。本轮没有连接生产服务器；命令须在新机演练。不要照用 `api/README.md` 的开发初始化流程。

版本见 [运行基线](runtime-baseline.md)，模板用途与权限见 [deploy](../deploy/README.md)，日常 CLI 用法见 [备份与恢复](backup-and-restore.md)。

## 1. 前提与新 ECS

准备 GitHub 访问权限、已验证的生产 commit/tag、阿里云账号、可用 OSS full 备份及 `InventorySystemBackupRole`。Git/SSH 凭证在仓库外配置，不写入 URL 或文档。新 ECS 优先华东 1 杭州（cn-hangzhou），可沿用杭州 OSS internal endpoint；其他地域不能直接照用此 endpoint。

在控制台确认：

- Bucket `inventory-system-backup` 为 Private、Block Public Access 开启。`db/` 90 天、`full/` 180 天的最后修改时间生命周期仍存在；过期对象不能凭 Git 找回。
- 新 ECS 挂 `InventorySystemBackupRole`，绑定 `InventorySystemBackupOSS`。Bucket 资源 `acs:oss:*:*:inventory-system-backup` 允许 ListObjects；对象资源 `acs:oss:*:*:inventory-system-backup/*` 允许 GetObject、PutObject、ListParts、AbortMultipartUpload，**不增加 DeleteObject**。
- Role 信任 ECS，实例能通过元数据取得临时凭证。不配置长期 AccessKey，也不用交互式 `ossutil config` 写入 AK。
- 网络能访问 GitHub、软件源、OSS；仅向所需使用者开放 Nginx 8082，API 8102 不开放公网；SSH 按管理员访问范围开放。
- 旧服务已隔离，避免两台机器同时接受业务写入。数据验收前不启动新 API / timers。

优先选**最新可靠 full**，它同时包含数据库和 uploads。db 只有数据库。full 目录和 created_at 使用 UTC（带 +00:00），daily db 使用服务器本地时间（不带时区）；不能直接比较两类目录名判断先后。每日 timer 03:30、每周日 timer 04:00 均明确为 Asia/Shanghai。full 目录比北京时间少 8 小时是现有行为。

## 2. 基础软件、用户与目录

先用 `sudo -i` 进入管理员 root Bash；下面的 `sudo -u inventory` 命令明确降权运行代码和上传。所有代码块逐步执行，任一步失败就停下，不是一键部署脚本：

```bash
set -euo pipefail
sudo apt-get update
sudo apt-get install -y git python3.12 python3.12-venv nginx apache2-utils sqlite3 curl ca-certificates xz-utils unzip
python3.12 --version
uname -m
sudo timedatectl set-timezone Asia/Shanghai
timedatectl status
getent group inventory >/dev/null || sudo groupadd --system inventory
id inventory >/dev/null 2>&1 || sudo useradd --system --gid inventory --create-home --home-dir /home/inventory --shell /usr/sbin/nologin inventory
sudo install -d -o inventory -g inventory -m 0750 \
  /srv/inventory-system /var/lib/inventory-system \
  /var/backups/inventory-system /var/backups/inventory-system/db \
  /var/backups/inventory-system/full /var/backups/inventory-system/recovery \
  /var/tmp/inventory-ossutil /var/tmp/inventory-ossutil/output /var/tmp/inventory-ossutil/checkpoint \
  /var/lib/inventory-system-recovery
sudo install -d -o root -g root -m 0755 /var/www/inventory-system /var/www/inventory-system/out
```

生产 Python 3.12.3；新 Ubuntu 源若提供不同版本，记录并验证差异，不能声称精确重建。pip 26.2.1 只是生产观察值。新机使用 Asia/Shanghai 本地时区，使 daily 无时区后缀的时间与现有生产含义一致，full 仍由现有代码使用 UTC。inventory 不直接登录，用 `sudo -u inventory -H ...` 执行命令。

### Node 24.20.0 / Corepack / pnpm

固定使用 [Node 官方发行目录](https://nodejs.org/download/release/v24.20.0/) 的 Linux x64 包和 SHA-256 清单：

```bash
INSTALL_DIR=$(mktemp -d)
cd "$INSTALL_DIR"
curl -fSLO https://nodejs.org/download/release/v24.20.0/node-v24.20.0-linux-x64.tar.xz
curl -fSLO https://nodejs.org/download/release/v24.20.0/SHASUMS256.txt
grep '  node-v24.20.0-linux-x64.tar.xz$' SHASUMS256.txt | sha256sum -c -
sudo tar -xJf node-v24.20.0-linux-x64.tar.xz -C /opt
for binary in node npm npx corepack; do
  test -e "/opt/node-v24.20.0-linux-x64/bin/$binary"
  sudo ln -s "/opt/node-v24.20.0-linux-x64/bin/$binary" "/usr/local/bin/$binary"
done
node --version
sudo corepack enable --install-directory /usr/local/bin
sudo -u inventory -H corepack prepare pnpm@10.34.5 --activate
sudo -u inventory -H pnpm --version
```

输出应为 v24.20.0 / 10.34.5。新机若存在同名 binary，停止检查，不用 `ln -sf` 覆盖未知安装。全局 pnpm 11.19.0 不作为项目基线。

### ossutil v1.7.19

按 [阿里云 1.x 安装说明](https://help.aliyun.com/zh/oss/developer-reference/install-ossutil) 固定 Linux x86_64 包及 SHA-256，不换成 2.x：

```bash
cd "$INSTALL_DIR"
curl -fSLO https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil-v1.7.19-linux-amd64.zip
printf '%s  %s\n' dcc512e4a893e16bbee63bc769339d8e56b21744fd83c8212a9d8baf28767343 ossutil-v1.7.19-linux-amd64.zip | sha256sum -c -
unzip -q ossutil-v1.7.19-linux-amd64.zip
OSS_BINARY=$(find "$INSTALL_DIR" -type f \( -name ossutil -o -name ossutil64 \) | head -n 1)
test -n "$OSS_BINARY"
sudo install -o root -g root -m 0755 "$OSS_BINARY" /usr/local/bin/ossutil
ossutil -v
```

OSS 命令显式指定 EcsRamRole、role 和 endpoint，不需要 AK 文件。角色/网络失败先修授权或网络，不临时写长期密钥。

## 3. Clone、锁定依赖、前端 build

替换两项占位值，并为 inventory 用户单独准备仓库读取权限（例如仓库外的只读 SSH 凭证）。full manifest 没有代码 commit，管理员根据发布记录选择匹配版本；不能默认 Git 最新 HEAD 兼容旧 DB。

```bash
REPOSITORY_URL='REPLACE_WITH_GITHUB_REPOSITORY_URL'
RESTORE_REF='REPLACE_WITH_VERIFIED_PRODUCTION_COMMIT_OR_TAG'
test "$REPOSITORY_URL" != REPLACE_WITH_GITHUB_REPOSITORY_URL
test "$RESTORE_REF" != REPLACE_WITH_VERIFIED_PRODUCTION_COMMIT_OR_TAG
sudo -u inventory -H git clone "$REPOSITORY_URL" /srv/inventory-system
sudo -u inventory -H git -C /srv/inventory-system checkout --detach "$RESTORE_REF"
sudo -u inventory -H git -C /srv/inventory-system rev-parse HEAD
sudo -u inventory -H bash -c '
set -euo pipefail
cd /srv/inventory-system/api
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip check
cd ../web
pnpm --version
pnpm install --frozen-lockfile
NEXT_PUBLIC_API_BASE_URL= pnpm build
'
test -f /srv/inventory-system/web/out/index.html
sudo cp -a /srv/inventory-system/web/out/. /var/www/inventory-system/out/
sudo chown -R root:root /var/www/inventory-system/out
sudo find /var/www/inventory-system/out -type d -exec chmod 0755 {} +
sudo find /var/www/inventory-system/out -type f -exec chmod 0644 {} +
```

只向新机空静态目录复制。生产前端固定同源 `/api`，不带开发 localhost API URL。Nginx 直接读 out，没有 Next.js 生产 service。不要重新生成 lockfile；后端不用浮动 requirements 或测试依赖。

## 4. 环境文件

```bash
sudo install -o root -g inventory -m 0640 \
  /srv/inventory-system/deploy/inventory-system.env.example /etc/inventory-system.env
```

确认文件内容（没有密码）：

```text
INVENTORY_DATA_DIR=/var/lib/inventory-system
DATABASE_URL=sqlite:////var/lib/inventory-system/inventory.db
```

手动 CLI 不会自动继承 systemd 环境，须加载并导出该文件。DATABASE_URL 决定 DB，INVENTORY_DATA_DIR 决定 uploads / import-previews；不能混用不同数据基线。`api/alembic.ini` 的 URL 占位由 env 中 URL 替代，若管理员改过 INI 的真实 URL，先检查指向。

## 5. 从 OSS 下载可靠 full

先列目录，选择三个文件齐全、时间/大小合理的 full。失败上传可能留下不完整 OSS 前缀，“最新”不等于可靠，最终由 manifest、hash 和 restore 校验决定：

```bash
OSS_ARGS=(--mode EcsRamRole --ecs-role-name InventorySystemBackupRole -e oss-cn-hangzhou-internal.aliyuncs.com)
sudo -u inventory -H ossutil ls oss://inventory-system-backup/full/ "${OSS_ARGS[@]}"
FULL_STAMP='REPLACE_WITH_FULL_TIMESTAMP'
[[ "$FULL_STAMP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$ ]]
FULL_DIR="/var/backups/inventory-system/recovery/full-$FULL_STAMP"
test ! -e "$FULL_DIR"
sudo install -d -o inventory -g inventory -m 0750 "$FULL_DIR"
for file in manifest.json inventory.db uploads.tar.gz; do
  sudo -u inventory -H ossutil cp "oss://inventory-system-backup/full/$FULL_STAMP/$file" "$FULL_DIR/$file" \
    "${OSS_ARGS[@]}" --output-dir /var/tmp/inventory-ossutil/output --checkpoint-dir /var/tmp/inventory-ossutil/checkpoint
done
```

单文件下载到明确文件名，避免递归 cp 目录歧义。失败时保留副本诊断，另建新目录重试；不覆盖唯一副本、不修改 OSS 源。恢复下载在 recovery 下，不进入 db/full 自动清理根目录。

## 6. 真实 manifest 校验与 restore 演练

full 字段：backup_version、created_at、database_filename、database_size、database_sha256、uploads_archive、uploads_file_count、uploads_archive_size、uploads_sha256。下面只读检查版本/文件名/大小/hash：

```bash
sudo -u inventory -H python3.12 - "$FULL_DIR" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
m = json.loads((root / 'manifest.json').read_text())
if m.get('backup_version') != 1 or m.get('database_filename') != 'inventory.db' or m.get('uploads_archive') != 'uploads.tar.gz':
    raise SystemExit('Unexpected full manifest format')
for name, size_key, hash_key in [('inventory.db', 'database_size', 'database_sha256'), ('uploads.tar.gz', 'uploads_archive_size', 'uploads_sha256')]:
    path = root / name
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if path.stat().st_size != m[size_key] or digest.hexdigest() != m[hash_key]:
        raise SystemExit('Size/hash mismatch: ' + name)
print('Full manifest size/hash OK; created_at:', m['created_at'], 'uploads:', m['uploads_file_count'])
PY
```

用现有 restore 工具继续完整校验/演练，不新造恢复实现：

```bash
REHEARSAL_DIR="/var/lib/inventory-system-recovery/full-$FULL_STAMP"
test ! -e "$REHEARSAL_DIR"
sudo -u inventory -H bash -c '
set -euo pipefail
set -a
. /etc/inventory-system.env
set +a
cd /srv/inventory-system/api
.venv/bin/python scripts/restore_inventory.py --backup "$1" --target "$2"
' bash "$FULL_DIR" "$REHEARSAL_DIR"
```

restore 核对 DB integrity、tar 安全路径、uploads_file_count，安装到独立 target 后再次验证。输出必须 integrity ok、数量与所选 manifest 一致；4226 / 约 122MB 是历史观察，不能作为固定验收值。返回 0 可能带 cleanup warning，记录残留路径；返回 1 就停止，不启动 API。

## 7. 正式恢复与数据核对

确认新数据目录没有旧 DB、WAL、SHM 或 uploads。任何一项存在都先调查，不加 `--force` 硬覆盖：

```bash
for name in inventory.db inventory.db-wal inventory.db-shm uploads; do
  test ! -e "/var/lib/inventory-system/$name"
done
sudo bash -c '
set -euo pipefail
set -a
. /etc/inventory-system.env
set +a
cd /srv/inventory-system/api
.venv/bin/python scripts/restore_inventory.py --backup "$1" --target /var/lib/inventory-system
' bash "$FULL_DIR"
sudo chown -R inventory:inventory /var/lib/inventory-system
```

正式恢复由 root 执行：现有 restore 的 staging/rollback 建在 target 的父目录 `/var/lib`，inventory 无权在这里创建目录，不能通过放宽 `/var/lib` 权限解决。完成后只将正式 target 交还 inventory；演练 target 在 inventory 可写的独立 recovery 父目录内。确认 inventory 能读写 DB、uploads 和数据目录。图片由 API 反代，Nginx 不需直接读取数据目录。

下例只读检查 rehearsal 与正式数据，结果应一致；商品计数包括停用商品，箱数以 packaging.carton_count 为准：

```bash
sudo -u inventory -H python3.12 - "$REHEARSAL_DIR" /var/lib/inventory-system <<'PY'
import sqlite3, sys
from pathlib import Path
for target in sys.argv[1:]:
    root = Path(target)
    db = sqlite3.connect((root / 'inventory.db').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise SystemExit('Integrity failed: ' + target)
        print(target, 'integrity: ok')
        for table in ['products', 'product_packagings', 'inventory_movements', 'categories', 'warehouses']:
            print(table, db.execute('SELECT count(*) FROM ' + table).fetchone()[0])
        print('cartons', db.execute('SELECT coalesce(sum(carton_count), 0) FROM product_packagings').fetchone()[0])
        print('revision', db.execute('SELECT version_num FROM alembic_version').fetchall())
    finally:
        db.close()
PY
```

必须恢复的是 SQLite + uploads。preview、node_modules、.venv、out、Git 代码/cache 不在正式数据备份中，由安装/构建/启动重新产生。

### 可选：评估比 full 新的 daily db，禁止自动叠加

先完成 full 基线校验。最新 db 可能引用 full 之后上传的图片，图片不能由 DB 还原；不同时间点不保证可以安全混合。缺图且无法补齐/确认业务一致时，保持 full 基线，明确恢复时间窗口。

确需评估，下载 db 两个文件到新的 recovery 目录，按每日真实字段 created_at、source、database、size、sha256、integrity_check 校验（与 full 不同）：

```bash
DB_STAMP='REPLACE_WITH_DB_TIMESTAMP'
[[ "$DB_STAMP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$ ]]
DB_DIR="/var/backups/inventory-system/recovery/db-$DB_STAMP"
test ! -e "$DB_DIR"
sudo install -d -o inventory -g inventory -m 0750 "$DB_DIR"
for file in manifest.json inventory.db; do
  sudo -u inventory -H ossutil cp "oss://inventory-system-backup/db/$DB_STAMP/$file" "$DB_DIR/$file" \
    "${OSS_ARGS[@]}" --output-dir /var/tmp/inventory-ossutil/output --checkpoint-dir /var/tmp/inventory-ossutil/checkpoint
done
sudo -u inventory -H python3.12 - "$DB_DIR" "$REHEARSAL_DIR/uploads" <<'PY'
import hashlib, json, sqlite3, sys
from pathlib import Path, PurePosixPath
root, uploads = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
m = json.loads((root / 'manifest.json').read_text())
path = root / 'inventory.db'
digest = hashlib.sha256()
with path.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
if m.get('database') != 'inventory.db' or m.get('integrity_check') != 'ok' or path.stat().st_size != m['size'] or digest.hexdigest() != m['sha256']:
    raise SystemExit('Daily DB manifest mismatch')
db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
try:
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise SystemExit('Daily DB integrity failed')
    missing = set()
    for row in db.execute('SELECT image_path, thumbnail_path FROM products'):
        for key in row:
            if not key:
                continue
            relative = PurePosixPath(key)
            candidate = (uploads / key).resolve()
            if relative.is_absolute() or '..' in relative.parts or not candidate.is_relative_to(uploads) or not candidate.is_file():
                missing.add(key)
    print('Daily DB created_at:', m['created_at'], 'source:', m['source'])
    if missing:
        print('Missing/invalid image keys:', *sorted(missing), sep='\n')
        raise SystemExit('Cannot combine this DB with the selected full uploads')
finally:
    db.close()
PY
```

全部引用可找到仍需在独立候选目录中配对 full uploads + daily DB，核对 revision、商品/规格/流水和实际图片并人工确认。现有 restore CLI **只接受 full 格式**，不能直接传 db 目录或修改 manifest 伪装 full。本手册不自动执行 DB 叠加；人工替换前保持 API/timers 停止，保留完整 full 基线副本与候选副本，确认配对正确后再安装，保留退回 full 的路径。

## 8. Alembic：检查恢复 DB，必要时升级

任何 Alembic 命令前确认正式 DB 非空。加载同一 env，工作目录 api。revision 不存在、领先代码或分叉时停止，选择匹配代码，不做 downgrade/stamp：

```bash
sudo -u inventory -H bash -c '
set -euo pipefail
set -a
. /etc/inventory-system.env
set +a
cd /srv/inventory-system/api
test -s /var/lib/inventory-system/inventory.db
.venv/bin/python -m alembic current
.venv/bin/python -m alembic heads
'
```

只有确认需要升级、API 未运行时，保存一套当前恢复数据 full 备份，执行已审查的 upgrade：

```bash
sudo -u inventory -H bash -c '
set -euo pipefail
set -a
. /etc/inventory-system.env
set +a
cd /srv/inventory-system/api
test -s /var/lib/inventory-system/inventory.db
.venv/bin/python scripts/backup_inventory.py --destination /var/backups/inventory-system/recovery/pre-migration
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current
'
```

不新建空 DB 覆盖恢复 DB，不执行 seed、编号 backfill、metadata.create_all、alembic stamp / downgrade。本轮没有新增 migration。

## 9. 安装 units、脚本、drop-ins（先不启动）

```bash
cd /srv/inventory-system
sudo install -o root -g inventory -m 0750 deploy/inventory-db-backup.py.example /usr/local/sbin/inventory-db-backup.py
sudo install -o root -g inventory -m 0750 deploy/inventory-oss-upload.sh.example /usr/local/sbin/inventory-oss-upload.sh
for unit in inventory-system-api.service inventory-db-backup.service inventory-db-backup.timer inventory-full-backup.service inventory-full-backup.timer inventory-oss-upload@.service; do
  sudo install -o root -g root -m 0644 "deploy/$unit.example" "/etc/systemd/system/$unit"
done
for service in inventory-db-backup inventory-full-backup; do
  sudo install -d -o root -g root -m 0755 "/etc/systemd/system/$service.service.d"
  sudo install -o root -g root -m 0644 "deploy/$service.service.d/oss-upload.conf.example" "/etc/systemd/system/$service.service.d/oss-upload.conf"
done
sudo bash -n /usr/local/sbin/inventory-oss-upload.sh
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/inventory-system-api.service /etc/systemd/system/inventory-db-backup.service /etc/systemd/system/inventory-db-backup.timer /etc/systemd/system/inventory-full-backup.service /etc/systemd/system/inventory-full-backup.timer /etc/systemd/system/inventory-oss-upload@.service
systemd-analyze calendar '*-*-* 03:30:00 Asia/Shanghai'
systemd-analyze calendar 'Sun *-*-* 04:00:00 Asia/Shanghai'
```

检查 Git 复制的脚本为 LF 换行；CRLF 会破坏 Bash/shebang。API 127.0.0.1:8102 单 worker，不设置 WEB_CONCURRENCY。systemd verify 失败先修，不直接启用 timers。

full service 的本地 56 天 retention 使用 `ExecStartPost=-/usr/bin/find ...`。这个 `-` 是 systemd 的忽略非零退出状态前缀：主备份成功后，清理失败仍留 journal 日志，但不会使备份 service 失败或阻断 OnSuccess OSS 上传。管理员已确认生产采用同一命令；恢复安装模板时须保留该前缀。主备份 ExecStart 的失败仍正常导致 service 失败。daily 模板的 14 天清理在 Python 内捕获文件系统错误、记录 warning，已发布备份仍返回成功。

## 10. Nginx 与 Basic Auth

```bash
sudo install -o root -g root -m 0644 deploy/nginx-inventory-system.conf.example /etc/nginx/sites-available/inventory-system
sudo ln -s /etc/nginx/sites-available/inventory-system /etc/nginx/sites-enabled/inventory-system
AUTH_USER='REPLACE_WITH_LOGIN_NAME'
test "$AUTH_USER" != REPLACE_WITH_LOGIN_NAME
if sudo test -e /etc/nginx/.tradecatalog_htpasswd; then
  sudo htpasswd /etc/nginx/.tradecatalog_htpasswd "$AUTH_USER"
else
  sudo htpasswd -c /etc/nginx/.tradecatalog_htpasswd "$AUTH_USER"
fi
sudo chown root:www-data /etc/nginx/.tradecatalog_htpasswd
sudo chmod 0640 /etc/nginx/.tradecatalog_htpasswd
sudo nginx -t
```

密码交互输入，不能用 `-b` 放进 shell history；htpasswd hash 不进 Git，已有文件不能 `-c` 覆盖其他账号。认证在 server 层覆盖网页、API、图片、导入预览、静态资源；未反代的 docs/redoc/openapi 未认证应 401，认证后按静态规则 404。

## 11. 启动、验收，再启用自动备份

```bash
sudo systemctl enable --now inventory-system-api.service
sudo systemctl enable --now nginx
sudo systemctl reload nginx
curl -f http://127.0.0.1:8102/api/dashboard/summary
curl -I http://127.0.0.1:8082/
curl -I http://127.0.0.1:8082/api/dashboard/summary
curl -I http://127.0.0.1:8082/docs
curl -I http://127.0.0.1:8082/openapi.json
curl -u "$AUTH_USER" -f http://127.0.0.1:8082/api/dashboard/summary
sudo journalctl -u inventory-system-api.service -n 80 --no-pager
```

API 本机 GET 应 200，8082 未认证应 401；最后一条 curl 交互输入密码。浏览器 `http://新ECS地址:8082` 验收六页，核对只读 SQL 基线、main/thumbs 图片、多规格库存和流水 before/after，不做真实库存变动测试。

通过后启用 timers，检查下一次触发：

```bash
sudo systemctl enable --now inventory-db-backup.timer inventory-full-backup.timer
sudo systemctl list-timers inventory-db-backup.timer inventory-full-backup.timer --all
sudo systemctl start inventory-db-backup.service
sudo systemctl start inventory-full-backup.service
sudo journalctl -u inventory-db-backup.service -u inventory-full-backup.service -u inventory-oss-upload@db.service -u inventory-oss-upload@full.service -n 150 --no-pager
sudo systemctl show inventory-oss-upload@db.service inventory-oss-upload@full.service -p ActiveState -p Result -p ExecMainStatus
sudo -u inventory -H ossutil ls oss://inventory-system-backup/db/ "${OSS_ARGS[@]}"
sudo -u inventory -H ossutil ls oss://inventory-system-backup/full/ "${OSS_ARGS[@]}"
```

Persistent timer 可能立即补执行；若 backup 正运行，等其完成再手动 start。OnSuccess uploader 异步执行，等待结束再看 Result/ExecMainStatus；成功 oneshot 通常 inactive，不能把 inactive 当失败。核对**新生成**时间戳所有远端文件及大小；必要时下载到另一临时目录重验 hash，不能用过去对象证明本次成功。上传失败不删除本地备份；排查后可 `sudo systemctl start inventory-oss-upload@db.service`（或 @full）重传最新本地集。

最终 checklist：

- [ ] Git commit、Python/Node/pnpm/ossutil 版本已记录，pip check 成功。
- [ ] full manifest 大小/hash、DB integrity、uploads 文件数通过；原 OSS 和下载副本保留。
- [ ] 商品、包装规格、总箱数、流水、分类、仓库与恢复基线相符；图片正常。
- [ ] Alembic current 与代码 head 一致，没有空 DB 初始化。
- [ ] API 仅 127.0.0.1:8102 单 worker；Nginx 8082 正常。
- [ ] 未认证网页、API、实际图片 URL、import-previews URL、docs/openapi 均 401；认证后业务正常；无公开 8102。
- [ ] daily/full timer 时间正确，本地快照成功，OnSuccess 上传成功。
- [ ] OSS 新时间戳：db 两个文件、full 三个文件存在，大小/hash 正确。
- [ ] Private / Block Public Access、无 DeleteObject、90/180 天生命周期已复核。
- [ ] 恢复时间点和故障时间差已记录，没有宣称恢复未备份的图片/数据。

## 12. 失败保护

失败时保持 API/timers 停止。不要删除 OSS 源或下载备份，不把唯一副本当工作目录，不对未知数据 `rm -rf`；新尝试放到新 target，保存错误日志及 rollback/staging 残留路径。

现有 restore 在 install/verify 失败时回滚 DB + uploads；rollback 失败会报告错误/路径，不能视为可运行。安装验证成功后的 cleanup 失败仅 warning + 成功状态，不应人工再次 rollback；残留目录确认后处理，不在本手册自动清理。

只有 daily DB、没有可靠 full 或图片副本时无法完整恢复图片；这是数据缺失，不能靠 migration 修复。最小保障是持续验证 full + OSS 上传，不增加 ECS 删除权限或新备份框架。

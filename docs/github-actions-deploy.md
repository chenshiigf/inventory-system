# 库存系统 GitHub Actions 自动部署

适用于仓库 `chenshiigf/inventory-system` 的 `master` 分支和 [现有生产部署](../deploy/README.md)。GitHub 使用 Ubuntu 24.04、Python 3.12.3、Node 24.20.0、pnpm 10.34.5；在 GitHub 上运行后端测试、TypeScript、ESLint、静态构建和依赖下载。**生产服务器不运行 pnpm / Next.js 构建**，后端依赖仅从上传的 wheel 离线安装，不在服务器编译。

## 发布行为

- PR 和 master 推送均运行检查。PR 不接触生产 SSH 密钥。
- 默认未开启推送部署。首次从 Actions → Inventory CI and deploy → Run workflow 手动运行，分支选择 master。
- 首次验收通过后，在仓库 Actions Variables 新建 `DEPLOY_ENABLED=true`，以后推送 master 就自动部署。删除或改成 false 可暂停自动部署，手动运行仍可使用。
- workflow 和服务器文件锁共同避免并发部署；不取消正在更新的部署。
- 服务器先准备新虚拟环境，检查生产数据库版本；暂停 API 写入后使用现有 CLI 备份 SQLite + uploads，再更新 API 和静态前端。
- 数据库、uploads、生产 env、Nginx、Basic Auth、systemd 配置不在上传包里，不运行 seed / backfill。
- 默认不迁移数据库。若新代码需要 migration，推送部署在停止 API 前失败；检查 migration 后手动运行并勾选 migrate。数据库版本领先、分叉或多个 head 也会拒绝更新。
- 无 migration 的更新失败会尝试恢复旧 API、旧虚拟环境和旧前端并检查后端；**开始过 migration 的更新若失败，会停止 API 和备份 timers，保留现场及 full 备份，不自动恢复数据库**。
- 检查后端 `127.0.0.1:8102/api/dashboard`；该检查不覆盖公网 Nginx、Basic Auth、实际浏览器和完整业务验收。首次必须自行打开网页验收。

## 1. Windows 上创建独立部署密钥

在 PowerShell 执行，若同名文件已存在，请用新文件名，避免覆盖原有密钥：

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\inventory_actions" -C "github-actions-inventory"
```

passphrase 两次直接回车，这把密钥专供非交互部署。查看并复制**公钥**：

```powershell
Get-Content "$env:USERPROFILE\.ssh\inventory_actions.pub"
```

私钥不发到聊天，不提交 Git；在第 3 步直接粘贴进 GitHub Secrets。

## 2. 服务器首次准备（现有管理员 SSH 连接）

服务器须已按现有文档部署、API 正常运行、数据库非空，Ubuntu 24.04 x86_64，具备 python3.12、python3.12-venv、sudo 和 runuser。生产 env 必须保持文档中的两个数据路径。以下操作不重建数据库，也不更新正在运行的业务代码。

下载新部署脚本并核对 SHA-256，再安装为 root 管理的独立命令：

```bash
set -euo pipefail
curl -fSL https://raw.githubusercontent.com/chenshiigf/inventory-system/master/deploy/deploy-actions.py -o /tmp/inventory-deploy-actions.py
printf '%s  %s\n' 2afc736b2540656d864a7590ff720450209a0d24814ff5d3ddb65933ef3bcf85 /tmp/inventory-deploy-actions.py | sha256sum -c -
python3.12 -m py_compile /tmp/inventory-deploy-actions.py
sudo install -o root -g root -m 0755 /tmp/inventory-deploy-actions.py /usr/local/sbin/inventory-deploy-actions
id inventory-deploy >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash inventory-deploy
sudo install -d -o inventory-deploy -g inventory-deploy -m 0700 /home/inventory-deploy/.ssh
sudo install -d -o inventory-deploy -g inventory-deploy -m 0750 /var/tmp/inventory-actions
```

把下面的占位行替换为第 1 步复制的整行公钥（以 ssh-ed25519 开头）：

```bash
PUBLIC_KEY='替换为整行ssh-ed25519公钥'
[[ "$PUBLIC_KEY" == ssh-ed25519\ * ]]
printf 'restrict %s\n' "$PUBLIC_KEY" | sudo tee -a /home/inventory-deploy/.ssh/authorized_keys >/dev/null
sudo chown inventory-deploy:inventory-deploy /home/inventory-deploy/.ssh/authorized_keys
sudo chmod 0600 /home/inventory-deploy/.ssh/authorized_keys
printf '%s\n' 'inventory-deploy ALL=(root) NOPASSWD: /usr/local/sbin/inventory-deploy-actions' | sudo tee /etc/sudoers.d/inventory-actions >/dev/null
sudo chmod 0440 /etc/sudoers.d/inventory-actions
sudo visudo -cf /etc/sudoers.d/inventory-actions
sudo systemctl is-active inventory-system-api.service
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

该账号没有通用免密 sudo，只能调用安装好的部署命令；部署代码作为 inventory 用户运行。部署密钥仍有发布生产代码的权限，应按生产凭证保管。脚本拒绝空生产数据库、路径不符、非法文件包和 SHA-256 不符的包。

## 3. GitHub 设置一次

仓库 Settings → Environments，新建 **production**。在 Deployment branches and tags 中只允许 **master**，不设置额外人工审批即可自动部署。Environment secrets 添加：

| Secret | 填写内容 |
| --- | --- |
| `SSH_HOST` | 云服务器公网 IPv4 或 SSH 域名，不加 http:// |
| `SSH_PORT` | SSH 端口，通常 22；留空也默认 22 |
| `SSH_USER` | `inventory-deploy` |
| `SSH_PRIVATE_KEY` | 第 1 步产生的私钥文件全文 |
| `SSH_KNOWN_HOSTS` | 经服务器指纹核对的 ssh-keyscan 输出 |

复制私钥到剪贴板（不会打印到终端）：

```powershell
Get-Content -Raw "$env:USERPROFILE\.ssh\inventory_actions" | Set-Clipboard
```

准备 known_hosts；将 SERVER_IP 和端口改成实际值：

```powershell
ssh-keyscan -t ed25519 -p 22 SERVER_IP 2>$null | Set-Content -Encoding ascii "$env:USERPROFILE\.ssh\inventory_known_hosts"
ssh-keygen -lf "$env:USERPROFILE\.ssh\inventory_known_hosts"
```

比较此输出与服务器第 2 步的 SHA256 指纹，一致后复制为 `SSH_KNOWN_HOSTS`：

```powershell
Get-Content -Raw "$env:USERPROFILE\.ssh\inventory_known_hosts" | Set-Clipboard
```

测试新账号能登录：

```powershell
ssh -i "$env:USERPROFILE\.ssh\inventory_actions" -p 22 inventory-deploy@SERVER_IP "id"
```

## 4. 首次发布与开启自动发布

1. 确保本地准备发布的修改已提交、推送到 master；首次先 `git status`，再 `git pull --ff-only` 同步新增配置，避免覆盖未提交修改。
2. GitHub Actions → Inventory CI and deploy → Run workflow，选择 master，migrate 保持不勾选。
3. backend / frontend / deploy 三项均成功后，打开库存网页，核对商品列表、统计、图片、新增或出库等需要的功能。
4. 仓库 Settings → Secrets and variables → Actions → Variables → New repository variable：`DEPLOY_ENABLED`，值 `true`。
5. 以后本地正常 commit + push master 即自动检查和部署；其他开发分支不发布生产。

此配置的默认部署分支是 master，不是 product-center-dev。仅 push 到 GitHub 才会触发，单纯本地保存文件或 git commit 不会发布。

## 5. 失败查看与保留文件

GitHub Actions 可查看失败步骤；服务器：

```bash
sudo systemctl status inventory-system-api.service --no-pager
sudo journalctl -u inventory-system-api.service -n 80 --no-pager
cat /srv/inventory-system/.actions-deployed.json
```

如果 SSH 连接超时，检查实际 SSH 端口、sshd 和阿里云安全组来源限制；GitHub 托管 runner 的出口与本地电脑不同。不要关闭 known_hosts 检查来绕过主机身份错误。

Actions 的 rerun 使用新的 attempt ID，可再次部署。网络断开或服务器重启后的失败必须先检查服务状态和版本，不能假定自动回退已完成。

保留目录：

| 路径 | 内容 |
| --- | --- |
| `/var/backups/inventory-system/actions/运行ID-次数-commit/` | 旧 API 源码、更新前完整数据备份 |
| `/srv/inventory-system/.actions-releases/运行ID-次数-commit/` | 发布包解压目录、新虚拟环境和旧虚拟环境 |
| `/var/www/inventory-system/.old-out-运行ID-次数-commit/` | 更新前静态前端 |
| `/var/tmp/inventory-actions/运行ID-次数-commit.tar.gz` | 上传的发布压缩包 |

这些部署备份不属于原来的 db/full retention 目录，本版不自动删除。首次验收后按磁盘情况人工保留需要的版本；**当前 `api/.venv` 是指向 `.actions-releases` 的符号链接，不要删除它指向的运行目录**。每次部署做 full 备份及短暂停服，大量图片会增加耗时和磁盘占用。

服务器 `/srv/inventory-system` 中的 .git 和 web 源码不随发布推进；实际运行版本以上述 `.actions-deployed.json` 为准。今后使用 Actions 更新，不在此目录继续执行 git pull 混合手动发布。若安装脚本需要升级，先检查变更，再以 root 重新安装独立命令；上传包不能覆盖它。

如果执行过数据库 migration 后失败，沿用 [备份与恢复](backup-and-restore.md) 与 [灾难恢复手册](disaster-recovery.md) 分析、选择匹配代码和 full 备份；恢复验收完成后再启动 API 及原先启用的 timers，不做自动 downgrade。

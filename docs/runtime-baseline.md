# 生产运行环境基线

本基线依据现场确认的生产环境和生产 `pip freeze` 记录，用于恢复已验证的运行环境，不执行依赖升级。

## 运行版本

| 项目 | 基线 |
| --- | --- |
| Production OS | Ubuntu 24.04 LTS x86_64（现场版本：24.04.4 LTS） |
| Python | 3.12.3 |
| Node | 24.20.0 |
| pnpm | 10.34.5，以 `web/package.json` 的 `packageManager` 为准 |
| API worker | 单 worker |

生产观察到的 pip 为 **26.2.1**，仅作参考，不要求固定该 pip 版本才能运行。服务器当前全局 pnpm **11.19.0** 不作为项目基线。

API 使用单 worker；当前导入预览任务状态保存在进程内，不使用多个 API worker。

## 后端恢复安装

预先准备上述 Python 版本。从仓库根目录进入 `api/`，创建新的虚拟环境并安装生产锁文件：

```sh
cd api
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
```

- `api/requirements.txt` 保留项目直接生产依赖的范围说明。
- `api/requirements.lock.txt` 精确固定现场生产 freeze 中的 22 个直接及传递依赖，恢复生产环境时使用此文件。
- 锁文件不含 pytest、httpx 等仅用于开发测试的依赖，也不固定 pip 本身。
- 本阶段不创建开发测试锁文件；现有 `requirements-dev.txt` 保持原样。

## 前端恢复安装

预先准备 Node **24.20.0**。从仓库根目录进入 `web/`，启用项目规定的 pnpm，并沿用现有锁文件：

```sh
cd web
corepack enable
corepack prepare pnpm@10.34.5 --activate
pnpm --version
pnpm install --frozen-lockfile
```

安装前确认 `pnpm --version` 输出 **10.34.5**，不使用服务器当前全局 11.19.0 作为恢复基线。不重新生成 `pnpm-lock.yaml`。

本文件仅记录运行环境及依赖安装。生产环境变量、数据恢复、数据库迁移和服务部署按对应部署与恢复文档执行。

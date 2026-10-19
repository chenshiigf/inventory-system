# 商品编号 V2.0：Phase 3B-1 受控切换机制

本文件说明本地实现，**不构成生产部署或正式换号授权**。本阶段测试仅生成合成数据库，没有操作 Phase 2B 的真实备份或副本。

## 1. 边界与状态

- Phase 2A 的 `product_code_offline.py` 和离线 CLI 路径保护保持原样。生产入口是独立的 `api/scripts/cutover_product_codes.py`。
- 维护状态：`<INVENTORY_DATA_DIR>/numbering-maintenance/state.json`。绑定操作 ID、发布 commit、目标文件的绝对路径/device/inode、冻结业务摘要、冻结月份计数器。状态持久化使用原子替换和 fsync。
- 开始维护首先落盘 `held` 意图，随后检查排空并冻结数据。冻结失败或中断时仍保持维护，只有同一操作 ID、发布 SHA 和数据库身份才能补完未完成的冻结；已冻结的操作不可被覆盖。
- 应用所有 `/api/` 非 GET/HEAD/OPTIONS 请求使用共享门禁，维护操作使用独占门禁；跨进程文件锁等待已准入请求退出。锁被占用、状态损坏或目录存在但状态缺失时，请求返回 503。AI 路由的 GET 也拦截，避免状态初始化等副作用。正常商品 GET 保持可读。
- 锁采用非阻塞准入：有在途请求时进入维护会拒绝，需待请求完成后恢复原操作。Windows 使用共享 LockFileEx，Linux 使用 flock；日常请求不会被维护机制互相串行化。
- `production_numbering_cutovers` 单例登记表和商品更新同一事务提交。Alembic `e3f4a5b6c7d8` 只增加空表，不执行换号；不能依据 revision 推断已经换号。表内有记录后禁止 downgrade 删除登记。
- 本地没有安装 systemd guard 或生产工具。未来需要单独批准安装独立于 API 目录的 root 所有的启动保护，以阻止维护中或换号后启动不兼容的旧代码。

## 2. 未来生产前必须单独审核、安装的内容

以下只列安装约定，本轮未执行。

1. 经审核的部署器安装至 `/usr/local/sbin/inventory-deploy-actions`。GitHub release 不会自动更新服务器已安装的部署脚本。
2. `deploy/numbering-service-guard.py` 安装至 `/usr/local/sbin/inventory-numbering-service-guard`（root 所有、755）；`deploy/numbering-guard.conf` 与 `deploy/numbering-nginx-guard.conf` 分别安装至 API 与 Nginx 的 systemd drop-in 目录（root 所有、644），重新加载 systemd 并核验两个 unit 的 DropInPaths / ExecStartPre。API guard 在维护仍保持或标记损坏时拒绝普通服务启动（包括重启机器后），防止 lifespan 初始化 AI 状态；中止恢复入口先停止现有 Nginx，Nginx guard 再拒绝其在 `held/abort_resuming` 状态下重启。维护已解除或正式切换已有登记时，API guard 还要求应用具有 `numbering-v2.json` 能力标记；不兼容旧代码不能启动。
3. 将以下审定版本的标准库模块独立安装至 `/usr/local/lib/inventory-numbering`，目录 root 所有、755，文件 root 所有、644，不可被 inventory 用户改写：
   - `app/__init__.py`、`app/services/__init__.py`（空包文件）
   - `app/numbering_maintenance.py`
   - `app/services/numbering_environment.py`
   - `app/services/product_code_cutover.py`
   - `app/services/product_code_offline.py`、`app/services/product_code_rehearsal.py`（只复用纯计算函数，离线路径权限不变）
   - `deploy/numbering_abort.py`、`deploy/numbering_abort_probe.py`
   - `scripts/cutover_product_codes.py`
   另将 `deploy/numbering-abort-cli.py` 安装为 `/usr/local/sbin/inventory-numbering-abort`（root 所有、755）。
   入口脚本按自身位置加载上面的独立模块；以 root 使用 Python 3.12 运行。部署器也只从该 root 所有的目录导入，不从 inventory 可写的 release 目录执行 root Python 代码。部署器、模块和迁移必须来自同一审定 commit，安装后校验 SHA256。
4. root 管理 `/etc/inventory-numbering.json`（建议 600，禁止组/其他用户写入）。必须先核实全部业务、备份、AI 和其他写任务，不能假定不存在额外任务。示意结构：

```json
{
  "format": "numbering-drain-v1",
  "all_writers_reviewed": true,
  "stopped_units": [
    "inventory-system-api.service",
    "inventory-db-backup.timer", "inventory-full-backup.timer",
    "inventory-db-backup.service", "inventory-full-backup.service"
  ],
  "ai_enable_markers": [
    "/var/lib/inventory-system/visual-search/runtime/ai-search.enabled"
  ]
}
```

此清单只是格式示例；若现场还有任务，必须补齐**真实存在**的 service/timer。工具拒绝未加载或未停止的清单单元，拒绝仍运行的 inventory/visual 相关单元，扫描 `/proc/*/fd` 排除其他进程占用数据目录；无法检查则停止。只检查 AI 启用标记是否存在和进程描述符，不读取模型索引、向量、图片内容或预算账本。cron、人工脚本、外部调度及目录外的 AI 任务须现场核查并纳入维护安排；这不是针对恶意 root 的隔离机制。

## 3. 部署器和 CI

- `workflow_dispatch` 新增 `deploy=false`，仅跑 CI 为默认。人工必须显式选 `deploy=true` 才可能进入部署 job。push 仍受 `DEPLOY_ENABLED` 控制，线上变量本轮未改变。
- `deployment_mode=numbering-hold` 必须提供合法 `operation_id`。workflow 先检查已安装部署器 `--capabilities`，旧脚本不兼容时停止。
- numbering hold 前必须确认已安装的 abort CLI、root library module 和 service guard；CI capability check 必须返回 `numbering_abort_protocol=1`。
- 首次部署带编号能力的新代码必须用 hold 模式。顺序为：准备依赖轮子 → 停备份 timer、等备份服务结束 → 停 API → 持久维护并排空/冻结 → 使用原有脚本生成完整业务备份 → 替换代码和虚拟环境 → 正常 Alembic upgrade → 替换已在 CI 构建的静态产物 → 写 `schema_staged_maintenance_held` 阶段回执。
- hold 成功后 API 和 timer 继续停止；不执行换号，不写 `.actions-deployed.json` 成功标记，不运行健康检查并开放业务。任意 hold 失败也不自动启动 API/timer。
- 应用创建与结构升级、历史换号、AI 协调、人工验收、解除维护是不同步骤。后续不得把 hold 回执误认为完成上线。
- 数据库已经正式换号后，部署器拒绝不带兼容能力标记的旧包；部署失败即使本次没有 Alembic 迁移，也不自动恢复旧代码。已有维护未解除时拒绝新的自动部署。
- 前端仍只在 CI/本地构建，生产仅安装预构建产物和 wheels。此阶段未修改前端代码。

## 4. 生产 CLI 的调用约定

**以下是后续另行授权后的示意，不要现在执行。** 生产入口没有 `--allow-any-db`、伪造测试身份或临时路径放行参数，仅允许 Linux root 和显式目标 `/var/lib/inventory-system/inventory.db`。业务数据和报告不放仓库或 CI artifact。

CLI action：

| action | 功能与限制 |
|---|---|
| `maintenance-enter` | 必须提供 `--release-sha`；持久维护、排空、冻结。独立运行使用部署全局锁；部署器在已有全局锁内调用相同实现 |
| `plan` | 默认只读，必须提供备份与操作 ID；`--export-reports` 才导出报告 |
| `plan --execute` | 还必须有 `--confirm-production-numbering` 和 `--expected-plan-sha256`；不满足任一条件不改号 |
| `status` | 只读检查是否已提交及全部不变量；未提交明确返回 `not_committed` |
| `recover-report` | 对已提交数据库只读复核后重新导出报告；绝不重新换号 |
| `recover-transaction` | 显式允许 SQLite 恢复热 rollback journal，检查还原冻结状态或已提交记录；不分配任何货号、不删除日志文件 |
| `maintenance-release` | 必须提供完整验收 JSON；只在已提交且当前不变量仍完全匹配时可解除维护 |

通用参数：`--database` 和 `--operation-id`，无数据库默认值。

预检的备份参数：`--backup-database /var/backups/inventory-system/.../inventory.db --backup-manifest /var/backups/inventory-system/.../manifest.json`。要求当前维护窗口内创建且不超过一小时的**完整业务备份**，包括 `uploads.tar.gz`；不接受只有每日数据库快照来授权正式切换。校验三者的文件身份、时间、大小和 SHA256，检查归档成员、数量和可完整读取，核对备份业务数据及计数器与冻结状态。原件始终只读。

预检摘要绑定维护状态、操作 ID、发布 SHA、目标文件身份、完整备份绑定、所有映射、结构、逐字段不变量和计数器。冻结后任何业务字段、新增商品、编号、导入批次或计数器变化都会停止；摘要过期/不匹配不能自动重新生成后执行。

报告目录默认 `/var/backups/inventory-system/numbering/report-<随机ID>/`，root 私有，包含 `mapping.csv`、`report.json`、`checksums.json`。CSV 全字段引号、UTF-8 BOM；人工使用 Excel 导入时将货号和原货号列明确设为文本。这是迁移核对输出，不是本阶段批准实际使用的贴标清单。控制台仅输出状态、摘要和报告路径，不输出商品明细。

## 5. 换号事务与故障恢复

1. 全局部署锁 + 独占业务准入锁；检查已冻结的操作及目标身份、服务/任务排空、备份；执行时 `BEGIN IMMEDIATE`。
2. 按当前全部商品的内部 ID 升序排名，分配 `25 + 四位序号`，包括停用、未分类、空号、异常旧号。当前工具范围 1～9999；超出立即停止，不写死 2523。
3. 在写入前，将批准映射和报告 fsync 落盘。先用唯一临时编号腾空全局唯一索引，再整体写入新编号，安全处理编号交换或目标已被另一商品占用的情况。
4. 同一事务提升月份计数器，保留既有高水位，并为新旧数字货号占用的月份序号保留空间，防止格式重叠导致之后重复使用。
5. 逐字段、SQLite 类型、表摘要比较全部非编号业务数据，包含商品 ID、原货号、分类、启停、包装身份与数量、每商品/仓库/总箱数、全部流水字段和包装所属关系、图片引用、导入批次和哈希；检查结构、实际最终映射、计数器、integrity_check/FK。
6. 单例完成登记与全部编号同一事务提交，完成摘要包含批准计划及结果。没有登记即没有成功标识；有登记就禁止再次执行换号。
7. 提交前错误或正常中断会 rollback，维护保持；进程被杀留下热日志时普通 plan/status 拒绝，使用明确的 `recover-transaction` 让 SQLite 自行恢复，禁止手工删除 journal/WAL/SHM。WAL/SHM 不自动恢复，必须先审核正常 checkpoint/排空步骤。
8. 提交后导出异常返回 `committed_report_failed`、退出码 3。**禁止重试 execute**，先 `status`，再 `recover-report`。提交后进程被杀也从登记判断，不能根据缺失报告推断回滚。
9. 数据库文件被替换、恢复后身份变化、非编号字段变化或登记损坏均拒绝自动继续。回滚生产必须另行批准完整恢复方案；不能只恢复代码、不能直接删维护标记、不能直接改 Alembic 版本。

## 6. 解除维护与 3B-2 依赖

解除维护的验收记录必须精确匹配：`operation_id`、`release_sha`、`database_identity`、`completion_sha256`，以及以下所有人工/后续阶段验收项：

```json
{"checks":{"inventory":true,"ai_metadata":true,"old_sessions":true,"web":true}}
```

工具不自动生成这些批准项。本阶段未实现 AI 元数据换号和旧会话失效，因此 **3B-1 完成不能解除生产维护或认定可以上线**。3B-2 需实现并验证零模型调用的元数据更新、原索引身份/图片向量和预算不变、旧结果不再复制旧号，再准备完整验收回执。

若要在维护中启动新 API 作只读网页验收，3B-2 必须先审查启动流程并提供受控只读验收方式；当前 guard 会拒绝 held 状态下的普通 systemd 启动，既有 lifespan 的 AI 服务初始化不属于本阶段更改范围，不能简单放行普通启动。迁移执行、状态复核和解除维护前仍必须停止 API 并满足排空检查。解除维护只改变准入状态，不自动启动服务或恢复定时器；后续人工分步恢复并健康检查。

## 7. 本地与 Linux 验证要求

本地测试只使用合成 SQL、一次性临时 DB、模拟 systemctl/proc，覆盖维护前后、重建 app、全部实际注册 API 写路由、正常并发、冻结集合变化、摘要拒绝、交换冲突、原件隔离、事务失败/进程终止/提交后中断、报告恢复、一次性登记、旧代码回退拒绝和 schema downgrade。

完整 Windows 测试使用现有 Python 3.14 环境；可用 Python 3.12 仅用于标准库测试（没有安装依赖）。Ubuntu/Python 3.12 完整测试由现有 CI 补齐，新增部署场景使用合成目录和模拟命令。上线前还需在非生产 Ubuntu 中验证真实 flock、root/inventory 权限、启动 drop-in、重启保持维护、systemctl/proc 排空、SQLite 热日志恢复、备份/恢复以及已安装脚本协议一致。禁止通过本地跳过 Unix 测试推断生产验收通过。

### 本阶段本地执行结果（2026-10-17）

- Windows / Python 3.14.6，显式收集 `api/tests` 全量：**815 passed，13 skipped，2 warnings**；共 828 项。12 个 Unix 部署测试和 1 个符号链接权限测试跳过；两条警告来自现有 FastAPI/Starlette/AnyIO 依赖。
- Windows / Python 3.12.14，`unittest tests.test_product_code_cutover tests.test_product_code_offline`：**50 项，48 通过，2 跳过**（缺少 Alembic、符号链接权限）。新控制入口不依赖 FastAPI/SQLAlchemy；完整 API 测试环境仍有缺口，未安装依赖。
- 7 个新入口/核心模块/部署脚本通过 Python 3.12 语法检查。
- WSL 只读枚举返回 `E_ACCESSDENIED`，没有取得可用 Ubuntu 测试环境；没有安装或更改系统依赖。
- 现有 README、docs 和压缩包共 9 个文件逐一 SHA256 核对一致，Phase 2A 预演实现未改动。真实备份、Phase 2B 副本、正式业务数据库、AI 索引和费用账本未用于本阶段测试。

## 8. Phase 4B-2A：编号换号前的发布中止恢复

本节只描述受控方案，不授权生产操作。服务端需先由管理员单独安装并校验与批准 commit 匹配的 root-owned `numbering_abort.py` 和 `/usr/local/sbin/inventory-numbering-abort`；部署器 capability 必须同时报告 maintenance 与 abort recovery protocol。GitHub release 不会更新服务器现有的部署器、guard 或 root helper。

部署器在进入 numbering hold 前，把 operation ID、release、目标数据库身份与结构摘要、旧 API/虚拟环境/静态产物摘要和旧版本记录写入该 release 的 `numbering-abort-baseline.json`。完整业务备份完成后才生成 `numbering-abort.json`。两份记录均由 root 管理并与发布目录绑定。

如果冻结或完整备份在生成恢复包前失败，业务服务保持停止或维护门禁保持关闭。修复导致失败的条件后，管理员可先查看中止状态；只有基线、旧产物和数据库仍与发布前相符时，才能显式准备恢复包：

```sh
sudo /usr/local/sbin/inventory-numbering-abort status \
  --database /var/lib/inventory-system/inventory.db \
  --operation-id '<operation-id>' --release '<run-attempt-commit>'

sudo /usr/local/sbin/inventory-numbering-abort prepare \
  --database /var/lib/inventory-system/inventory.db \
  --operation-id '<operation-id>' --release '<run-attempt-commit>' \
  --confirm-prepare-precommit-abort
```

`prepare` 会停止 API 和已知备份 timer，确认任务排空，再完成同一 operation 的冻结（仅当原冻结未完成），并为该冻结集合创建或核验完整业务备份及 `uploads`。它不替换应用代码、不升级数据库、不执行商品换号，也不解除维护。数据库身份、revision 或 schema 摘要变化，旧版本文件不匹配，已有换号登记、SQLite sidecar、备份缺失/冲突或排空不完整都会停止并保持维护。热 rollback journal、WAL/SHM 不手动删除；当前工具对此 fail closed，需按单独的 SQLite 恢复审查处理。

恢复包就绪后，必须重新查看只读计划，再把该计划的摘要作为独立确认：

```sh
sudo /usr/local/sbin/inventory-numbering-abort plan \
  --database /var/lib/inventory-system/inventory.db \
  --operation-id '<operation-id>' --release '<run-attempt-commit>'

sudo /usr/local/sbin/inventory-numbering-abort execute \
  --database /var/lib/inventory-system/inventory.db \
  --operation-id '<operation-id>' --release '<run-attempt-commit>' \
  --confirm-precommit-abort --expected-plan-sha256 '<plan-sha256>'
```

执行会从本次维护窗口的完整备份恢复数据库和 uploads，再恢复已摘要绑定的旧 API、虚拟环境及静态产物。进入持久的 `abort_resuming` 后，普通 API、Nginx 和备份 timer 仍全部停止。API 和 Nginx 的独立 `ExecStartPre` guard 在 `held/abort_resuming` 下无条件拒绝普通服务启动；旧的 probe/resume 标记不能作为启动普通 API 的例外。这也适用于重启机器和恢复进程中断，不依赖旧 API 含有维护中间件，不依赖 `/run` 中的临时 systemd 环境覆盖。

恢复验收使用 root 管理的 `numbering_abort_probe.py` 一次性子进程，以 inventory 用户和恢复后的 Python/依赖运行旧应用。该进程不启动 uvicorn、不创建 HTTP 监听端口，通过 ASGI 调用固定的 dashboard 和 products GET 路由；应用外层独立拒绝所有其他请求。SQLite 使用真正的 URI `mode=ro`，应用临时数据写入本次调用新建的隔离目录。生产适配器通过 `env -i` 清空环境，不提供模型密钥或生产 AI 目录；子进程结束后只清理本次创建的目录，之前中断留下的目录不复用、不自动清理。旧 API 的普通 loopback 服务在维护期间没有监听，因此直接访问原 API 端口也没有可绕过的普通业务服务。`--abort-probe` guard 只验证独立子进程的 operation/release/数据库身份/计划标记/旧 commit 和代理停止状态，不授权启动普通 API。

Nginx 状态必须查询成功且 `LoadState=loaded`，仅明确的 `ActiveState=inactive/failed` 可视为停止。活动、过渡、未知、缺失、重复或无法解析的状态、非零退出码、超时和执行异常均拒绝探测。探测成功后再次核验冻结数据库、完整备份恢复状态、AI 指纹和服务排空状态。receipt 的检查项为 `isolated_api_health`、`normal_api_stopped`、`timers_stopped` 等真实完成的检查，不再把维护期的探测描述成普通可写 API 已健康或 timer 已恢复。

完整验收登记及 receipt 先持久化，随后才原子切换为 `released`。这之后才启动普通 API、检查正常服务健康、恢复原有 timer 和 Nginx。若释放前失败或中断，维护继续保持，续做重新检查数据库和备份并重新执行独立只读探测。若释放已提交而服务激活或报告失败，必须按 released receipt 续做服务/报告恢复，不能再次恢复数据库或回退代码；普通 API 健康检查通过前不启动 Nginx。`released` 是正式开放条件，最后的 `phase=complete` 是服务激活和报告完成记录。

CLI 在释放前失败时返回 1 并明确维护继续保持；若持久状态已为 `released`，服务激活或报告失败返回 3 并明确禁止再次替换数据库。状态损坏或无法读取时不宣称仍受维护保护，停止并要求核查。

Phase 4B-2C 组件必须作为同一审定版本单独安装；部署器同时要求存在 root 管理的独立 probe 脚本和已加载的两个 guard。旧版 receipt/validation 若仍含 `normal_api_health/timers_restored`，新版 guard 会拒绝将其当作新的只读隔离验收，不自动转换标记。不得在已进行中的真实操作里混装不同版本；当前阶段没有授权生产安装或执行。

如发现旧版本残留的 `/run/systemd/system/inventory-system-api.service.d/numbering-abort-probe.conf`，新工具停止并保持维护，不自动删除或使用该覆盖文件。需人工核实旧操作状态，不能据此假定新版普通 API 会使用预期配置。

本阶段本地验证使用 Windows / Python 3.14.6、合成业务数据库和仓库外新临时目录。12 个相关测试文件回归为 216 通过、13 平台跳过；最后补充 CLI 失败状态区分后，3 个完整核心文件复验为 76 通过、12 平台跳过，当前相关集合收集到 231 项。实际子进程测试覆盖无维护中间件的 ASGI/FastAPI 应用、SQLite 只读写入拒绝、探测失败和中断续做；systemd 命令返回值使用 Mock。跳过项为 12 个 Unix 文件锁集成测试及 1 个系统不允许的符号链接测试，须在 Ubuntu 24.04 / Python 3.12 补验，不能作为 Linux 已通过的证据。没有修改或放宽离线工具路径保护。

`production_numbering_cutovers` 一旦存在已提交记录，prepare/plan/execute 普通中止路径都会拒绝操作；不能回退旧编号生成代码或删除维护标记。提交后恢复必须另行审批成套恢复或前向修复。没有发布前基线、恢复包损坏、数据库 sidecar 或旧产物不匹配时也不会自动解锁，需停留在维护状态等待人工处置。

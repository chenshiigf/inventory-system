# 商品编号 V2.0 Phase 3B-2：AI 元数据切换与只读验收

本阶段仅为本地代码与合成数据测试。没有读取真实生产镜像、索引、费用账本或 Phase 2B 副本，没有连接服务器。本说明不授权生产部署、正式换号或真实 AI 元数据更新。

已补读本会话的 Phase 3A 发布方案审查，并衔接 `product-code-production-cutover.md` 的维护、一次性登记和恢复约束。Phase 2A 离线工具及路径保护不变。

## 1. 已确认的代码契约与部署边界

现有资料记录业务库在 Ubuntu，而完整生产 AI 镜像/索引在本地运行；本轮没有打开真实数据核实当前物理路径或数量。正式窗口前必须再次核实实际部署位置。

| 对象 | 身份/关联规则 | 本次处理 |
|---|---|---|
| 业务商品 | 内部 `products.id` | 不变；完整业务映射来自已提交的正式切换结果 |
| 镜像商品 | `mirror_products(source_id,id)`，JSON 内有 `source_id/id/product_key` | 固定 `aliyun-inventory-production`，逐项核验 |
| 来源 | `source.json` 完整 identity，含 source_label | 原文件字节不变 |
| namespace | `production-` + identity fingerprint | 不变 |
| index_key | 模型、维度、版本、端点、预处理及来源身份摘要 | 必须与既有索引上下文匹配，不新建索引 |
| 查询目录版本 | 原 `catalog_fingerprint` 加可选 `catalog_revision` | 新 revision 绑定一次性换号提交，独立于向量身份 |

`visual_source_context` 是既有建索引时的历史上下文，不作为当前目录版本接口；本次连同整个 index.db 一起保持原字节。实时版本来自镜像 SQLite。

## 2. 实际允许修改的内容

AI 工具仅在已有 `catalog.sqlite` 中修改：

1. 已存在 `mirror_products.document` 的 `product_code`。
2. `mirror_meta.last_report` 的 `catalog_fingerprint`。
3. 新增 `mirror_meta.numbering_cutover` 登记，含操作、业务完成摘要、批准映射摘要、前后版本、规范快照、保护证据、事务完成状态及提交摘要。

不修改原货号、商品 ID、图片 identifier/路径/hash/状态、同步时间、分类、仓库或其他商品字段。不补建缺失商品、不清理旧向量。`source.json`、source binding、index.db、预算账本、原始同步 manifest 和批准输入文件保持原字节与文件身份。

原目录 fingerprint 仍按原同步 snapshot 的完整序列化规则计算；不是对旧 hash 或文本作替换。使用明确指定、与当前 last_report fingerprint 匹配的原始同步 manifest，复制 snapshot 后只改编号再计算。支持原有 full/sample 范围；sample 以其原始选择范围计算，并对所有已有镜像行应用完整业务映射。来源不符、已发布记录缺失、分类/仓库与快照不一致或旧号漂移时停止。

新增 `catalog_revision` 取镜像完成登记的提交摘要，避免 sample 中被选中的编号恰好未变化时，目录 fingerprint 不变却遗漏其他商品换号的会话失效。它不参与 namespace、index_key 或模型参数计算。

## 3. 独立 CLI 与安全边界

入口：`api/scripts/cutover_ai_catalog.py`。不加载 `.env`，不导入或构造 Provider、VisualService、图片下载器或索引执行器。

所有目标与批准材料必须明确指定，没有默认镜像路径：

| 参数 | 来源与含义 |
|---|---|
| `--visual-root`、`--catalog-root` | 经核实的既有 AI 根目录和商品镜像目录 |
| `--result` | 正式业务切换已提交结果的 report.json；不能使用离线预演或未提交计划 |
| `--completion-sha256` | 业务数据库登记中经 `status` 核验的完成摘要，不是 report.json 文件 SHA |
| `--mapping`、`--mapping-sha256` | 同一结果导出的完整 mapping.csv 及其获批文件 SHA |
| `--manifest` | 当前镜像 fingerprint 对应的原始同步 manifest |
| `--operation-id` | 与业务切换相同的操作 ID |
| `--namespace`、`--index-key` | 单独核实批准的既有值，不能让工具自动选择另一个库 |
| `--report-root` | 仓库及业务/AI 数据目录之外的私有报告目录 |

业务结果可通过批准的安全渠道从另一端提供。工具校验完成登记、原批准计划、操作、数据库身份绑定、映射全部列/ID 集合和排名，以及两个批准摘要。JSON 中自带的 hash 不是来源签名；必须用业务端正式登记中独立核实的摘要批准，不能自行对未知文件算 hash 后当作授权。

执行前必须关闭 AI（启用标记不存在）、排空索引任务，并获得现有 AI calls.lock 和镜像同步 calls.lock。它与现有 API/CLI 使用相同 OS 锁。普通生产反馈也复用 AI 操作锁，防止反馈写 index.db 与切换并发。路径拒绝符号链接、Windows junction/reparse 和硬链接；拒绝 WAL/SHM/journal，热 journal 仅允许显式事务恢复。

### action

| action | 行为 |
|---|---|
| `plan` | 默认只读预检；不创建镜像、锁文件、索引或报告 |
| `plan --export-reports` | 仅额外导出私有预检报告 |
| `plan --execute` | 必须再有 `--confirm-metadata-only`、`--expected-plan-sha256`，且摘要与当前状态完全一致 |
| `status` | 只读核验提交状态和全部不变量 |
| `recover-report` | 已提交后只恢复报告，不再次更新编号 |
| `recover-transaction` | 显式让 SQLite 恢复热 rollback journal，再核验未提交或已提交状态；不执行换号 |

禁止自动重算新摘要并继续执行；禁止转而运行普通 sync/index 来修复预检错误。原 manifest 缺失、来源材料不完整、未知镜像 ID 或保护文件变化时，保留暂停状态，交由人工核实。

### 事务与报告

`BEGIN IMMEDIATE` 内重新预检，先持久保存批准计划，再更新已有文档、fingerprint 和一次性完成登记，核对非编号字段/结构摘要、完整编号集合与所有保护文件后提交。登记和商品编号同一事务提交，重复 execute 拒绝。

提交后报告失败返回 `committed_report_failed`、退出码 3。数据库仍为已提交，实时目录版本已经更新；不得重试 execute，先 status 再 recover-report。进程在提交点附近中断，也必须以数据库登记判断。

报告包含业务/镜像实际数量、未镜像的业务 ID、逐商品旧新号、原始输入文件绑定、index.db/预算/source 标识 SHA、两类向量的行数和内容摘要。真实报告不能加入 Git 或 CI artifact。

## 4. 零费用测试证据的含义

测试先用合成数据建立业务库、3 款镜像、Mock 向量和已有预算预留记录，再记录基线。切换阶段把 Provider.embed、图片预处理、下载、普通同步和 rebuild 设为触发即失败，验证调用次数全为 0。

逐项比较：

- `visual_embeddings`、`visual_image_vectors` 的完整行集合及 BLOB 内容摘要；整个 index.db 字节一致，因此 jobs、calls、queries、feedback 与模型上下文也不变。
- 预算文件字节、已有记录及预算余额不变。
- source.json/source binding、namespace、index_key、图片文件集合及内容不变。
- 每个商品除 product_code 外的全部 JSON 字段不变，包括独立原货号。
- 4 款业务商品、3 款镜像数量不同仍可切换；另测镜像只有 ID 1/3 时，分配 250001/250003，ID 2/4 不补建。

另覆盖映射/来源/摘要不符、未知商品、旧号漂移、未确认、启用状态或运行中任务拒绝、事务中断回滚、进程强制退出后的热日志恢复、重复执行和提交后报告失败。真实数据从未用于这些测试。

## 5. 旧会话、反馈与复制

- 新增已登录用户可调用的 `GET /api/visual-search/catalog/version?source=production-catalog`，私有 no-store。只读 source 与镜像元数据，不经过 service()/index/status，不创建服务、任务或账本连接，不需要模型密钥。
- 新的生产查询在既有 `visual_queries.candidate_ids` TEXT 中保存 `{ids,catalog}`，绑定来源、namespace、fingerprint 和可选 revision；不修改视觉 schema，也不重写旧查询。开发来源继续使用旧列表格式。
- 生产反馈拒绝旧列表格式、其他 index_key、目录版本变化的查询，包括“无匹配”反馈。新查询仍保留原 Top5 和人工确认流程。
- 新页面在挂载恢复、focus、pageshow/BFCache、可见状态恢复、确认和复制前核验版本；变化或查询失败时清除候选、确认、旧货号及手动复制 fallback，不自动付费重新搜索。HTTP 环境的手动复制 fallback 仅在版本核验成功、Clipboard API 自身失败时保留。
- 既有搜索过程中普通镜像更新仍沿用批量重读与图片有效性复核，查询绑定复核后的版本；复核后又发生变化才拒绝返回旧资料。

### 无法远程撤销的边界

旧 JS 中的本地复制、浏览器文本选择、已复制剪贴板内容，以及历史 browse.html 离线快照，不能靠新后端可靠撤销。本工具保留原始 manifest 和静态浏览文件，不把它们改成当前业务号据。

正式流程必须在换号前让所有终端关闭 AI 找货和旧离线浏览页（含后台标签、手机页和浏览器后退恢复页），上线后重新打开并强制刷新新页面。验收需在每个使用端确认新版本检查请求存在、旧结果不会复制、失败时没有旧号 fallback，并核对商品管理页的新号。不能确认清退时，不得批准恢复业务；可先单独发布兼容补丁，再另行批准正式换号。

## 6. 独立受控只读验收

入口：`api/scripts/numbering_acceptance.py`。**不导入 app.main/app.database**，不运行主应用 lifespan，不初始化模型、索引、预览清理任务或预算。不修改 Phase 3B-1 的持久门禁、正式迁移路径保护或普通 systemd 启动拒绝规则。

补充修改 main.py 的原因：防止有人直接构建维护中的普通应用时，构造 VisualService 自动改写任务状态；维护存在/损坏时跳过视觉初始化，并禁止预览周期清理。受控验收仍使用独立入口，不能据此放行普通服务。

### 凭证与访问

1. `issue` 在明确指定的新私有文件中创建有时限的 capability，绑定本次操作、业务完成摘要和具体文件身份/目录版本。默认 30 分钟，允许 1～60 分钟；token 不打印到日志，不允许存放仓库、图片或业务/AI 数据目录。
2. `serve` 读取同一文件，必须指定已有 htpasswd 与管理员账号。复用现有管理员鉴权，还必须提供 `X-Numbering-Acceptance` 私有凭证；普通账号即使拿到 token 也不能访问。
3. 固定监听 `127.0.0.1`，默认 18114，可指定空闲高端口；不接受转发头，不开放 CORS，关闭 access log 与 OpenAPI。不是面向普通用户的管理后台。
4. 每次读取复核凭证时效/撤销、目标身份、已提交状态、维护仍 held，以及镜像变更结果。过期、健康检查失败或进程中断均不解除维护。普通写请求持续 503。
5. `--database` 只开放商品列表、详情和库存汇总；`--catalog-root` 只开放 AI 镜像元数据，两者可组合供合成模拟。真实分端部署分别启动，不要求复制业务数据库或把索引迁到服务器。

| 受控 GET | 内容 |
|---|---|
| `/acceptance/health` | 操作 ID、当前只读状态、已批准范围 |
| `/acceptance/products?page=1&page_size=50` | 商品列表，最多每页 100 |
| `/acceptance/products/123` | 商品详情与包装 |
| `/acceptance/stock` | 每商品/每仓库/总箱数 |
| `/acceptance/catalog` | AI 来源、namespace 与当前目录版本 |
| `/acceptance/catalog/products` | 已有镜像商品资料及图片引用；不提供图片下载或模型搜索 |

操作方式是管理员本机 HTTP 客户端读取 JSON，携带 Basic 管理员凭据与私有 header。凭证不得放 URL 或浏览器存储。这里没有新增普通业务页面，也没有提前开放写操作。网页渲染/真实终端验收与具体安全访问路径由 3B-3 组织，不能把 JSON 健康通过当作全部人工验收完成。

启动命令参数说明（后续另行授权后使用，本轮未启动真实入口）：

```text
numbering_acceptance.py issue --token-file <新私有文件> --operation-id <同一操作> --completion-sha256 <业务登记摘要> [--database <业务端数据库>] [--catalog-root <AI端目录>]
numbering_acceptance.py serve --token-file <同一私有文件> --operation-id <同一操作> --completion-sha256 <同一摘要> --htpasswd-file <既有凭据文件> --admin-user <既有管理员> [同一范围参数] [--port <空闲回环端口>]
```

不同端的健康回执必须由操作员核对相同操作 ID 和业务完成摘要；不建设跨主机自动协调。AI 端健康响应不能单独授权解除业务端维护。停止验收进程或删除凭证会停止受控访问，不会恢复公共业务。

## 7. Phase 3B-3 准备条件

- 审核独立本地提交，取得最新远端关系后按正常分支/PR 流程合并；本阶段不推送。
- Ubuntu 24.04/Python 3.12 完整 CI 跑全部后端测试，并将 `web/tests/*.test.mjs` 纳入前端 CI；保留类型、lint 和静态构建。生产不构建前端。
- 在非生产 Ubuntu 以真实 root/inventory 权限验证共享锁、维护/启动保护、专用只读进程、权限与重启、故障及恢复。仅本地 Mock 的 systemctl 不能替代此验收。
- Windows AI 端核实目录、ACL、禁用状态、在途任务和原始同步 manifest；不得为了补材料直接运行普通同步或重建现有向量。
- 用合成数据完成双端全流程：维护 → 备份 → 结构升级 → 业务换号 → AI plan/批准/执行 → 两端只读验收 → 旧页清退验收 → 完整回执 → 明确解除维护。覆盖每个中断点、报告失败与不兼容旧代码恢复拒绝。
- 核实最新版部署器、独立 root 工具库和受控验收运行时的安装及校验方式；不能从服务用户可写的 release 目录执行 root 工具。新增验收入口需要既有锁定依赖中的 FastAPI/Uvicorn/bcrypt/passlib，旧标准库工具包不会自动包含这些组件。
- 真实阶段前另行取得成套业务/镜像/向量/费用恢复点并演练恢复。编号失败不得覆盖较新的费用及预留记录。
- 本轮没有在真实 AI 数据上确认兼容性；实际原始 manifest 缺失、目录与发布记录不符或非当前固定端点时，工具会停止，需要先审查处理，不能绕过保护。

## 8. 本地验证记录（2026-10-18）

| 验证 | 结果 |
|---|---|
| Windows / Python 3.14.6，`pytest tests -q --tb=short -p no:cacheprovider` | 844 passed，13 skipped，2 条既有依赖弃用警告 |
| 本阶段合成 AI 换号、会话及只读验收测试 | 29 项包含在上述完整回归中 |
| `node --test web/tests/*.test.mjs` | 114 passed，0 failed |
| `tsc --noEmit --incremental false` | 通过 |
| 本阶段涉及的 7 个 TypeScript/TSX 文件 ESLint | 通过 |
| 既有 Python 3.12.14 标准库环境：offline/cutover unittest | 运行 50 项，OK，跳过 2 项（符号链接权限、缺少 Alembic/SQLAlchemy） |
| Python 3.12.14：6 个新增 Python 文件 AST、元数据工具冷导入 | 通过；未导入业务引擎、主应用、视觉服务、Provider 或 httpx |
| 原有 9 个未提交文件 SHA256、Phase 2A 工具及 Phase 3B-1 门禁差异 | 与开发前一致；本阶段未修改 workflow |

后端测试全部使用随机 OS 临时目录和合成数据库。普通执行沙箱在 Python 3.14 私有临时目录上出现 ACL 限制，因此测试改在获准的本地用户上下文运行；备份测试进程的 PATH 优先指定已有 Git Bash，避免误选 Windows WSL 代理。没有安装依赖、修改系统 PATH 或访问生产数据。

Windows 完整回归仍跳过平台/权限限制项目，不能替代 Ubuntu 的部署锁与服务集成验收。Python 3.12 环境缺少完整 Web 依赖，本轮未安装，完整 3.12 回归未执行。本轮未构建前端发布包、未启动真实验收服务、未进行真实浏览器人工验收。

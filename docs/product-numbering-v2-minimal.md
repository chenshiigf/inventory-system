# 商品编号 V2：最小人工切换

仅增加月度发号、一次性 `plan / execute / verify` 和旧 AI 结果保护。不新增部署器、维护状态机、guard、root helper 或后台任务。原 Phase 2A 离线工具及 PR #2 均未修改。

## 日常发号

- 结构迁移：`c8d9e0f1a2b3 -> d1e2f3a4b5c6`，只建立月份计数器，不自动换历史编号。
- API 和 Excel 正式导入共用创建服务，以北京时间建档月份生成 `YYMM + 至少两位流水`；预览不占号，事务失败不占号。
- 包含未分类及停用商品；分类、仓库、包装、出入库变更不改号。旧三段式货号仍可搜索、排序，旧回填工具在新结构下拒绝运行。
- 月计数器不会倒退，同时扫描已有数字货号，历史换号也保留旧、新数字货号可能占用的月份高水位，避免前缀冲突及号码回收。

## 执行前提

**本文件是未来经单独批准后的操作说明，不构成生产执行授权。**

1. 人工安排短维护窗口，关闭对外访问，停止 API、导入、库存写入、图片写入及所有同步/索引进程，排空请求；关闭现有 AI 开关。不要仅依赖页面隐藏或代理拦截。
2. 保存并核验现有完整业务备份（数据库、uploads）、独立 AI 备份（目录、source.json、原始同步 manifest、索引、预算账本）、旧代码及静态产物；验证能恢复。保留原件。
3. 安装批准的代码及**提前在本地/CI 构建**的静态产物，正常执行 Alembic 结构升级，暂不启动业务。
4. 结构升级后再制作当前业务数据库及 AI catalog.sqlite 的独立逐字节备份，作为下述工具的两个备份参数。它们不能是同一文件或硬链接。
5. 业务库和目录库必须已经处于 SQLite `DELETE` journal 模式，无 `-wal/-shm/-journal`。本工具拒绝其他模式/残留日志，不自动转换、不删除日志。若需检查点或故障恢复，由操作员用正常 SQLite 方式处理并重新核验备份。
6. AI 两个既有 `calls.lock` 必须存在且可独占；`runtime/ai-search.enabled` 不得存在。工具与现有调用/同步使用相同的 OS 锁，但**不能代替人工停止非协作写入程序**。

## 三条命令

在 API 目录运行，所有路径必须是绝对路径。以下均为占位符，不能直接用于生产：

```sh
python -B scripts/numbering_v2.py plan \
  --database /isolated/data/inventory.db \
  --backup-database /isolated/backups/post-schema-inventory.db \
  --backup-catalog /isolated/backups/catalog.sqlite \
  --visual-root /isolated/data/visual-search \
  --catalog-root /isolated/data/visual-search/production-catalog \
  --manifest /isolated/data/visual-search/production-catalog/manifests/CURRENT.json \
  --namespace production-EXISTING_NAMESPACE_HASH \
  --index-key EXISTING_INDEX_KEY \
  --output /isolated/reports/plan-UNIQUE

python -B scripts/numbering_v2.py execute \
  --plan /isolated/reports/plan-UNIQUE/report.json \
  --confirm-sha256 APPROVED_PLAN_SHA256 \
  --confirm-writers-stopped \
  --output /isolated/reports/execute-UNIQUE

python -B scripts/numbering_v2.py verify \
  --plan /isolated/reports/plan-UNIQUE/report.json
```

- `plan` 对数据库只读，不创建/初始化索引或锁文件。输出 `report.json`、完整 `mapping.csv` 和 `checksums.json`；报告必须位于仓库及业务/AI 数据目录外。CSV 全字段加引号，**用 Excel 导入时将货号列选为文本**；JSON 是执行依据，不从编辑后的 Excel 执行。
- 映射使用冻结时全部商品（含停用、未分类、空号/异常号），按 ID 排名分配 `250001…`，不按 ID 拼接，不固定2523款。超过9999款拒绝，需重新审批四位历史流水规则。
- 已有目标货号重叠会列入报告；全部商品同时换号，先用唯一临时编号再写最终编号，支持编号交换，不应将可解决的重叠误当作永久冲突。
- `execute` 必须提供审核过的**完整计划摘要**及已停止全部写入的明确确认；重新生成并逐项比较整个计划、文件身份、备份、全字段摘要和 AI 身份。任何变化即停止，不能自动重建计划继续。
- 业务库和 `catalog.sqlite` 使用 SQLite ATTACH + `BEGIN IMMEDIATE`、DELETE journal、FULL synchronous 的**同一提交事务**，同时写入一次性 `numbering_v2_runs` 登记。该表仅由明确执行命令在事务内建立；日常 ORM 及结构升级不依赖它。
- 仅修改业务 `products.product_code`、必要的月计数器；AI 仅修改已有 `mirror_products.document.product_code`、`last_report.catalog_fingerprint` 及 `mirror_meta.numbering_cutover` 完成/目录版本元数据。原货号、图片引用及其他镜像字段不变。
- 保留原 fingerprint 序列化规则，基于与当前目录匹配的**原始同步 manifest**重算，支持业务数量与镜像数量不同。缺少镜像商品只报告，不补录；未知镜像商品、旧号漂移或 manifest 不匹配即拒绝。
- 不导入/构造 provider，不下载或读取图片，不创建索引任务，不打开索引/预算的写连接；校验 source identity、namespace、index_key 和模型参数，前后比较索引与账本文件完整摘要。

## 验证与故障处理

`verify` 核验一次性登记、完整商品 ID 集合、全部非编号字段、全部其他业务表/结构、包装/库存/流水/导入批次、库存逐商品/仓库关联、计数器、AI 行集合/非编号字段/指纹及索引/账本不变量，并运行两个数据库完整性/外键检查。应在恢复业务写入**之前**执行；开放后正常业务变化将使冻结摘要不再相同。

- 提交前失败：两个库及登记一起回滚，保持服务停止；排查后重新核验，不自动开放业务。
- 进程被强制终止/断电：若存在 journal，工具拒绝继续。保持停止，保留全部两个库及日志，由 SQLite 完成合法的热日志恢复，然后使用 `verify` 判断提交状态；不得手工删日志或只替换一库。尚未刷盘产生的非热日志可能不会被一次读取移除，工具仍停止；必要时按既有备份恢复流程成套恢复，再核验新备份及重新审批计划。
- 已成功提交：重复执行会拒绝。提交后报告失败返回 `committed_report_failed`/退出码3；**不得重执行**，用保留的批准计划运行 `verify`，登记和原始映射仍在数据库及执行前报告中。
- 必须恢复时，保持业务和 AI 停止，使用现有恢复工具成套恢复匹配的数据库、AI catalog、代码及静态产物；预算账本以最新真实费用记录为准，不回退/覆盖为旧余额。本次工具不会改账本或向量。
- 开放写入后不得直接恢复旧库，否则丢失新增/库存业务；必须单独审批前向修复或保留新业务后的成套恢复。

## 最小人工验收与开放

1. `verify` 成功后，检查映射、商品详情、仓库库存汇总及 AI 元数据；模型调用必须为0，不用真实照片测试付费搜索。
2. **所有旧 AI 浏览器页面必须关闭并重新加载新前端。** 已加载的旧 JS 无法被服务器可靠阻止其本地剪贴板行为。
3. 新前端在焦点恢复、旧会话恢复及复制前检查免费目录版本；变化时清除候选/确认，不自动重搜，核验失败不复制。后端拒绝旧版本候选反馈。目录版本接口只读，不初始化视觉服务。
4. 人工确认后启动批准版本服务并恢复原有任务，检查新增/未分类商品得到 YYMM 号码。保留映射、摘要、备份及验收记录；不把任何业务报告提交 Git。

## 本地验证边界

测试只用临时合成业务库、镜像、图片、向量和预算记录；真实 SQLite 事务/摘要断言与系统命令 Mock 分开。本地 Windows/Python3.14结果不能替代未来 Ubuntu24.04/Python3.12的相同用例验证；本工具不需要新的 systemd 组件。

## 本次验证记录

- Windows，本地现有 Python 3.14.6 环境；后端完整回归 **760通过，6跳过，0失败**。6项为已有 Unix 文件锁部署集成用例，不将其视为 Linux 验收通过。
- 发号、导入、生命周期、迁移及最小工具定向回归 **91通过**；其中最小工具23项，覆盖真实 SQLite 双库回滚、真实子进程退出、独立 CLI、零库存、空装箱数、异常映射、重复执行、报告失败、硬链接/重解析路径拒绝及旧反馈拒绝。
- TypeScript通过；ESLint 0错误、1条原有 ProductImportWorkspace 导航警告；Node **114通过**；Next.js静态导出13页成功。
- provider构造/embedding/实际download_image/索引任务/网络探针调用次数均为0，AI索引、预算、source.json及source-binding.json保持文件摘要一致。
- 未运行 Ubuntu/Python3.12验证；未推送、部署或操作真实业务/AI数据。部署脚本、Actions工作流及依赖锁未改。

## 修改文件范围

- 发号与业务兼容：`api/app/product_codes.py`、`models.py`、`backfill_product_codes.py`、`services/products.py`、`routers/products.py`、`api/alembic/versions/d1e2f3a4b5c6_add_product_code_months.py`。
- 最小切换工具：`api/scripts/numbering_v2.py`、`api/app/services/numbering_v2.py`、`numbering_catalog.py`、`numbering_io.py`。
- AI目录与旧结果：`api/app/main.py`、`routers/visual_search.py`、`services/visual_search/catalog_metadata.py`、`service.py`、`sources.py`。
- 前端目录版本核对：`web/components/ai-search/AISearchSessionProvider.tsx`、`AISearchWorkspace.tsx`、`web/components/visual-search/VisualSearchWorkspace.tsx`、`web/lib/ai-catalog-version.ts`、`ai-search-session.ts`、`ai-search.ts`、`web/lib/api/visual-search.ts`。
- 后端测试：`api/tests/test_numbering_v2_minimal.py`、`test_product_code_months_migration.py`、`test_product_numbering_v2.py`、`test_product_codes.py`、`test_original_product_code.py`、`test_product_import_commit.py`、`test_product_lifecycle.py`；前端测试：`web/tests/ai-search-http.test.mjs`；说明：本文。

# 商品编号 V2.0：离线备份预演（Phase 2A）

## 边界与前提

本工具只建立离线预演能力。Phase 2A 开发测试全部使用合成数据库，未对真实备份换号。
真实备份副本上的换号须另行批准 Phase 2B；生产发布、生产换号不在本工具范围内。

入口：`api/scripts/rehearse_product_codes.py`。原 `create-fixture` / `plan` 合成入口的
临时目录、双重身份、确认参数等保护继续保留，并拒绝离线真实副本身份，不能靠补写合成标记进入。
新增模块只依赖标准库；需要升级副本结构时，独立子进程才导入现有 Alembic/SQLAlchemy。
不导入应用启动、AI、图片处理或网络服务。

请由业务负责人提供 **V1.2 上线后**生成的完整独立 SQLite 备份与配套 `manifest.json`，
优先 revision `c8d9e0f1a2b3`。支持以下两种现有摘要格式：

- db：`database`、`size`、`sha256`。
- full：`database_filename`、`database_size`、`database_sha256`。

摘要只能证明文件与清单一致，不能证明备份时间或业务来源；来源和时间仍须人工确认。
文件必须放在仓库外的私人本地目录，不能是正在运行的业务数据库。
不得把实际数据库、商品清单、报告、终端输出重定向进仓库、CI 日志或构建产物。
无需提供服务器凭据、`.env`、AI 索引或预算账本。

## 操作示例：只读核验与准备（不换号）

下面是将来收到并获准核验指定备份后的手工操作示例，本阶段未对真实备份运行。
在 PowerShell 中执行；替换来源路径，始终使用全新的工作目录。

```powershell
$python = 'D:\inventory-system\api\.venv\Scripts\python.exe'
$script = 'D:\inventory-system\api\scripts\rehearse_product_codes.py'
$source = 'D:\inventory-offline-private\v1.2-backup\inventory.db'
$manifest = 'D:\inventory-offline-private\v1.2-backup\manifest.json'
$work = Join-Path $env:TEMP ('inventory-offline-' + [guid]::NewGuid().ToString('N'))

# 仅输出来源摘要、revision、业务快照摘要；不写任何数据库或报告。
& $python $script offline-inspect --source-backup $source --source-manifest $manifest

# 只复制到新临时目录；正常迁移链只升级副本结构，不改货号。
& $python $script offline-prepare --source-backup $source --source-manifest $manifest --work-directory $work --upgrade-copy

# 默认完全只读。只有显式 --export-reports 才生成 CSV / JSON 报告。
& $python $script offline-plan --work-directory $work
& $python $script offline-plan --work-directory $work --export-reports
```

`offline-prepare` 会创建副本和专用身份状态；它不是零写入命令，但绝不换号。
源 revision 已是 `d1e2f3a4b5c6` 时可省略 `--upgrade-copy`。
旧 revision 必须明确加此参数，仅允许已审查的链：
`b2c3d4e5f6a7 → c8d9e0f1a2b3 → d1e2f3a4b5c6`，不支持 stamp、改版本表或跳过迁移。
其他 revision 直接拒绝，不猜测来源，也不自动修复。

准备失败时可能留下不完整副本目录；保留诊断证据，使用另一个新目录重新准备。
不复用半成品目录，不自动清理。工具要求来源备份一直可读，后续每次计划、执行、报告恢复都会核对它。

## 安全检查与数据规则

- 原始 SQLite 始终 `mode=ro&immutable=1` 加 `query_only`，拒绝 WAL、SHM、journal 旁文件。
  只接受独立快照，不能把活库直接称作备份。复制前后复核 SHA256 与大小。
- 路径逐级检查符号链接、junction、任意 Windows reparse 属性；文件要求普通文件、硬链接数为 1。
  拒绝 UNC、项目仓库、当前配置默认业务库及已知生产数据目录。
- 工作目录必须是系统临时目录内的新目录，位于仓库外，且不在来源备份目录内。
  数据库独立复制并核对 SHA256、`samefile`；副本单独转为 DELETE journal 模式。
  来源不 chmod、不改 journal 模式、不迁移、不修复。
- 工作副本外部标记与内部 `offline_numbering_run` 绑定来源路径、SHA256、清单摘要、run ID、
  绝对工作路径和准备摘要。确认摘要包含当前数据库全表快照、schema 和绑定信息，不能跨副本复用。
- 操作期间禁止其他程序编辑来源、工作目录、标记或报告。摘要/标记不是对抗具有本机写权限攻击者的签名；
  运行前后都要核对，不能把私人工作目录共享给其他写入进程。
- 将副本内**全部商品**按 ID 升序排名，分配 `25` + 四位序号；不采用 ID 拼接，也没有范围截断参数。
  包含停用、未分类、空货号、异常格式货号；空/异常格式在清单中标明，仍参加排名。
  原货号保持原值。历史商品数限 1～9999，超出须另行确认规则。
- 重复旧货号、无效 ID/启停状态、外键错误、损坏数据库、异常计数器、计数器溢出等直接停止。
  目标清单要求唯一、非空且顺序准确。旧编号互相占用目标时，事务内先使用随机临时编号再写最终值。
- 保留已有月份计数器高水位，并将释放的数字旧号、目标号可能占用的月份空间记入高水位，避免重用。
  不调整分类计数器，不生成商品 ID，不触碰任何图片文件。

## 核对文件与图片

`--export-reports` 或执行时生成全新的 `reports-<随机值>` 子目录，不覆盖旧报告：

- `mapping.csv`：UTF-8 BOM、所有单元格加引号；包含商品 ID、旧/新系统货号、原货号、分类路径、仓库、
  启停状态、主图/缩略图引用、图片可用性、包装全字段 JSON、库存箱数、旧号异常及 NULL 标志。
- `report.json`：原始文本值和 NULL、完整映射、逐表摘要、ID 集合、各商品/仓库库存、计数器、来源及确认摘要。
- `checksums.json`：上述两个报告的 SHA256。

**CSV 没有单元格类型信息。** 工具以原始文本写编号、不转整数、不丢前导零。
在 Excel 中使用“数据 → 从文本/CSV”，选择 UTF-8，关闭自动类型检测，所有列按“文本”导入；
不要双击直接打开，也不要把货号列设为数值。这样还能防止以 `=` 等开头的原货号变成公式。
需要保存 Excel 人工版本时，导入后另存为 `.xlsx`，货号列继续保持文本。
精确恢复 NULL 与空字符串以 JSON 和 `old_code_is_null` 为准。

当前工具不读取、复制、解包、下载或修改图片。主图/缩略图引用原样导出；有引用但未提供离线图时标明
“图片不可用：未提供离线图片”，无引用则标明“无主图引用”。
实体贴标前，另由人工将核对表中的引用与用户提供的离线 uploads 目录对应；无图行须单独人工辨认。
离线图片归档可以晚于数据库提供，但没有图片不能宣布贴标核对准备完成。

## 逐字段迁移校验

准备阶段校验来源与正常结构升级后的所有业务表完全一致；旧结构新增 `original_product_code=NULL`
是唯一允许的业务行结构差异。换号前后校验不只依赖总数：

1. 商品 ID 完整集合和所有非系统编号字段，包括原货号、状态、分类、仓库、时间戳、价格和图片引用。
2. 包装全部字段，包括 ID、商品 ID、装箱数、箱数、排序和时间戳；保留 `sqlite_sequence`。
3. 各商品、各仓库和总箱数；NULL 仓库独立归组。
4. 库存流水全部字段、商品/包装/仓库关联。
5. 导入批次全部字段及文件哈希；其他业务表也全部纳入摘要。
6. schema、索引、约束和触发器不变；SQLite 存储类型也参与非编号字段比较（BLOB 不等于同字面的 TEXT）。
7. `integrity_check=ok`、`foreign_key_check` 无结果、revision 精确匹配。
8. 全商品最终编号与冻结映射逐 ID 相等，计数器精确匹配预期高水位，执行状态正确。

校验失败在同一 `BEGIN IMMEDIATE` 事务内回滚编号、计数器及执行状态。原始备份完全独立，保持只读。

## Phase 2B：单独批准后的副本换号

批准前必须确认来源时间/版本、原始备份 SHA256、工作副本路径、`plan_sha256`、全部商品数量及异常清单。
必须先人工核对 CSV，确认全部现存商品都属于历史商品，原货号与库存无异常。
确认摘要不是授权的替代物；本阶段不能因工具打印了摘要就自动执行。

仅在用户另行批准对该副本换号后执行以下命令（这不是生产命令）：

```powershell
& $python $script offline-plan --work-directory $work --execute --confirm-offline-copy --expected-plan-sha256 '<该副本当前 dry-run 摘要>'
```

执行前必须先独占写入旧新对照报告并 fsync；业务更新与 `state=committed` 在同一事务中提交。
提交后再次导出核对结果。

退出码：0 成功；1 拒绝/错误；2 不可执行计划；3 **副本已提交但提交后报告写入失败**。
重复执行直接拒绝；不能把退出码 3 当作回滚并再次换号。

```powershell
# 同时用于执行后状态确认、进程退出后识别已提交状态、报告重建。
& $python $script offline-plan --work-directory $work --export-reports
```

若有 journal/WAL/SHM，默认只读命令拒绝继续。保留故障目录，重新从原始备份准备新目录，重新 dry-run 并取得
新的确认摘要；不自动删除 journal、不静默恢复。若旧副本已提交但报告失败，正常状态检查可读到 committed，
并用数据库内冻结的旧新映射重新导出，不会丢失旧编号。若故障副本确有损坏，不覆盖它；来源完整时可重新准备。
所有这些动作仅涉及副本；生产切换和生产恢复方案仍需之后单独审查。

## 开发测试方式

全套回归只收集 `api/tests`，避免扫描忽略目录中的历史代码。启动测试前设置新临时
`INVENTORY_DATA_DIR` / `DATABASE_URL`、空 `DASHSCOPE_API_KEY`；pytest 的 basetemp 也用新临时目录。
原有工作区文件不清理。标准库套件还可用 Python 3.12 运行：

```powershell
# 在 D:\inventory-system\api 下，使用已存在的 Python 环境。
python -m unittest discover -s tests -p test_product_code_offline.py -v
```

缺少 Alembic/SQLAlchemy 时只跳过真实迁移链兼容测试，不安装依赖、不修改系统环境。
Windows 无符号链接创建权限时该实际符号链接测试跳过；junction 与模拟 reparse 属性另有独立检查。
Linux/Ubuntu 兼容性须在已有的相应环境中补验，不能把 Windows 通过视为 Ubuntu 已通过。

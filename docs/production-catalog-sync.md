# Phase 1.6：Windows 生产商品图库只读同步工具

当前交付：本地工具、10 款真实同步及重复验收、2414 款全量资料 dry-run。**尚未授权或执行全量图片下载**；全量同步仍需用户单独确认。本阶段不调用百炼、不生成向量、不部署生产。

## 生产源与项目审查

生产为唯一权威来源。用户通过本人 SSH 密码登录完成只读预检，反馈：API active/running，商品 **2414** 款，启用 2414、停用 0；商品 GET 可读取 ID、货号、主图/缩略图、包装、分类、仓库。商品 ID 1 主图 HTTP 200、image/webp、83,868 字节。这是用户反馈的预检结果，不是本工具已经同步的统计。

代码审查确认产品没有独立品名列，包装为一对多，每箱装箱数是 `packing_qty`，`carton_count` 是库存箱数。主图为 uploads 相对 key，多个商品可共用图片。现有 `/api/products?status=all` 最大分页 100；分类和仓库有独立 GET。列表包含的价格/箱数在客户端白名单投影时丢弃，不保存原始 JSON。

部署文档记录数据根 `/var/lib/inventory-system`、API 回环 `127.0.0.1:8102`、Nginx 8082 Basic Auth。实际 SQLite DDL 未由工具直接读取；本方案使用用户已确认的运行 API 契约，不复制或打开在线 SQLite，不执行备份/恢复/迁移。首次代理检查仅为 3 次无凭据 HEAD，返回 401。后续生产检查命令及访问边界见 [用户执行的只读预检](production-catalog-readonly-check.md)。

保留原有 Phase 1.5 未提交的 `provider.py`、`test_visual_search.py`、`visual-search-v0.1.md`，以及未知归档 `web/inventory-web-fixed.tar.gz`，不纳入本阶段提交。开发库与 Phase 1.5 索引不作同步目标。

## 实现文件

- `api/scripts/sync_production_catalog.py`：Windows CLI；默认仅资料预检，无图片下载。真实图片下载须 `--execute-download`；小批量硬上限 10；全量另需 `--all --expected-total`，与 `--limit` 互斥。
- `api/app/services/visual_search/production_catalog.py`：只读 GET 客户端、字段投影、分页核对、图片缓存/校验、独立镜像存储、只读适配器及离线分页浏览页。
- `api/tests/test_production_catalog.py`、`test_production_catalog_cli.py`：合成商品 + MockTransport；不连接生产或 AI 服务。
- 本说明及 `production-catalog-readonly-check.md`。

没有新增或修改生产接口；没有改前端业务页面、生产服务或 Phase 1.5 功能。

## 数据与来源隔离

镜像白名单：原始生产 ID、货号、启用状态、分类/仓库 ID 与名称、尺寸、单位、主图/缩略图原始引用、包装 ID/装箱数/顺序、源更新时间及本地同步时间。主图保存下载的原字节，SHA256 内容寻址，不二次压缩。没有品名不造品名；自由备注不持久化，避免带入无关经营或客户信息。空装箱数保持空。

**不保存**价格、库存箱数、出入库流水、账号信息、凭据、API Key 或未过滤响应。包装 DTO 不补虚构的库存 0。内部 API 通过用户本人创建的 SSH 隧道访问，不需要收集 Nginx 密码，不读 htpasswd，不使用部署密钥。

固定来源 `source_id=aliyun-inventory-production`、`source_label=ecs-8.136.127.226-inventory-system` 写入镜像标记。镜像不可改为别的 source_id 混用；商品键为 `aliyun-inventory-production:<生产ID>`。只读适配器必须显式选择来源，例如：

```python
from pathlib import Path
from app.services.visual_search.production_catalog import ProductionCatalog
mirror = ProductionCatalog(Path("api/data/visual-search/production-catalog"), "aliyun-inventory-production")
product = mirror.get("aliyun-inventory-production", 1)
image_file = mirror.image_path("aliyun-inventory-production", 1)
namespace = mirror.index_namespace
```

适配器只打开镜像 SQLite `mode=ro`，不连接生产，拒绝开发或其他来源 ID。`index_namespace` 带独立生产来源身份，供以后新建向量/评测目录使用。本阶段没有把生产镜像接入原有业务 ORM 或付费视觉入口；原视觉页面继续使用开发源，不能拿生产 ID 打开开发商品详情。以后向量及标签需使用此生产命名空间和独立目录，不复用 Phase 1.5 索引。

## 下载目录

默认：`D:\inventory-system\api\data\visual-search\production-catalog\`

```text
production-catalog/
  source.json                       # 来源身份，无凭据
  catalog.sqlite                    # 白名单镜像，不是业务库或向量库
  images/<sha256>.webp|jpg|png        # 同图共文件，多商品 ID 关联独立
  download-cache.json                # 原始图片 key、hash、ETag、长度等
  manifests/<sync_id>.json           # 每轮稳定资料及关联，保留历史
  reports/<sync_id>.json             # 成功/失败统计，断点失败也记录
  browse.html, browse-2.html, ...     # 每页最多 100 款，本地离线浏览
  browse-pages.json                  # 当前分页文件清单
  calls.lock                        # 同步进程互斥，不与向量锁混用
```

已有 `api/.gitignore` 的 `data/visual-search/` 忽略整棵树，实际验证镜像库和图片均被忽略。目录不挂到公开 uploads，也不启动公网服务；离线 HTML 只引用本地文件，没有脚本、CDN 或远程请求。双击 `browse.html`，按页浏览；Ctrl+F 找本页 ID/货号，全库精确查询用 CLI `get`。

工具拒绝业务/向量目录、未标记的非空目录、来源不匹配及控制文件链接；不会把镜像写回生产。同步只改上述保留文件名，不写评测标签；建议人工标注存放 `api/data/visual-search/production-evaluation/<source_id>/`，与镜像和 Phase 1.5 分开。

## SSH 与 Windows 执行步骤（用户确认后再执行）

**目前仅提供命令，未执行。请用代码块复制按钮，不复制 Markdown 的三个反引号。**

### 1. PowerShell 窗口 A：本人建立隧道

```powershell
ssh -N -L 127.0.0.1:18102:127.0.0.1:8102 -o PubkeyAuthentication=no -o PreferredAuthentications=password -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 root@8.136.127.226
```

本人在终端输入 SSH 密码，保持此窗口开启。无输出通常表示转发已建立；该命令不执行服务器脚本、不修改配置。明确禁用公钥认证，不使用 `inventory-deploy` 或 SSH agent 的部署密钥。仅在 Windows 绑定回环 18102；远端连接已有 API 回环 8102。结束时在窗口 A 按 Ctrl+C，只关闭自己的隧道。

### 2. PowerShell 窗口 B：最多 10 款资料预检

```powershell
Set-Location D:\inventory-system
api/.venv/Scripts/python.exe api/scripts/sync_production_catalog.py sync --limit 10 --expected-total 2414
```

前后两遍读取最多 10 款商品及分类/仓库，比较白名单清单指纹，输出数量，不创建镜像、不下载图片。若总数已变、两遍不一致或隧道错误，则报告失败，不自动改预期数量。该命令仍涉及生产读取，请在获得小批量确认后执行。

### 3. 同一窗口 B：确认范围内的小批量下载

```powershell
api/.venv/Scripts/python.exe api/scripts/sync_production_catalog.py sync --limit 10 --expected-total 2414 --execute-download
```

资料清单前后各读取一次（只检查选中样本），最多为 10 个商品下载当前主图；同 key 的多个商品只下载一份。不会因为生产总数 2414 自动扩大取样。无主图保留空值，下载失败保留失败原因；已校验文件留下缓存。

### 4. 断开生产后验收本地镜像

```powershell
api/.venv/Scripts/python.exe api/scripts/sync_production_catalog.py status
api/.venv/Scripts/python.exe api/scripts/sync_production_catalog.py get --product-id 1
Invoke-Item api/data/visual-search/production-catalog/browse.html
```

`status` / `get` 不构造 HTTP 客户端、不需要隧道。示例 ID 1 须实际在此次样本中；没有该 ID 会明确报错，不回退到开发库。查询停用/失效商品资料仍可用，适配器的视觉主图读取会拒绝这些状态。

退出码：0 表示本轮操作完成，1 表示源/资料/路径等错误，2 表示已发布稳定资料但部分引用图片失败，或本地图片审计失败。退出 2 不能当作完整图库验收通过。

## 分页、增量、失败恢复与核验

- 请求只允许回环 HTTP 的指定端口，不接受公网地址、凭据 URL或跨域重定向；`trust_env=False`，不使用环境代理。所有远程请求是 GET，不调用写入接口。
- 下载顺序执行，并发为 1，请求间隔默认且最低 0.2 秒；超时 30 秒，网络异常及 429/500/502/503/504 最多重试 2 次并退避。图片最多 10MB/4000 万像素，JSON 有独立长度上限。
- 先分页读取清单，再下载主图，再读清单核对 ID/总数/白名单字段/包装/分类/仓库指纹。资料不一致则不发布本批，保留缓存，下一次人工运行可继续；不会无限自动重试整轮。
- 成功文件先验证图片格式与完整解码，计算 SHA256，原子写入并复读比较 hash；不能写入合法本地路径之外。源 API没有独立 SHA256，报告明确说明这不是“独立源端哈希一致性证明”。
- 增量以 ETag 优先、Last-Modified 次之做条件 GET，304 后复用已校验文件；无验证器时重新读取内容校验。发现本地缓存缺失、损坏或 hash 不一致会重新下载；修改图片引用也会下载新图。
- 新增/修改资料更新；停用照实同步。只有 `--all` 的完整稳定清单才标记消失 ID `source_present=false`，不删除历史商品和图片。小批量清单不能推断其余商品删除。
- 共图商品不合并。即使不同 key 下载到相同内容，仍保留每个商品 ID，只复用 SHA256 文件。旧清单和图片保留，不覆盖人工标签。
- 稳定资料可以带图片失败状态发布，以供重试定位；报告和退出码明确不完整。每轮记录选中/本地现存/历史失效商品数、无主图、404/损坏/其他失败、成功图片关联/独立文件数、重复分组、包装行/空装箱数/缺包装、分类/仓库缺失引用、缓存/hash异常、网络字节与重试数。分页冲突会在首个冲突处停止，失败报告不是所有冲突的完整普查。

## 预计数据量与全量许可

最多 10 款按首张 83,868 字节参考约 0.84MB，暂按 **0.5～3MB** 规划，实际因图片尺寸/共图不同；这不是下载实测。全量 2414 款约 2173～2414 条主图引用（90%依据任务背景），图片暂估 **50～200MB**，元数据通常小于 5MB。共图会减少独立文件数；以小批量实际字节和后续完整报告为准。

全量分页大小 100，2414 款每轮需 **25 页**，前后核对共 50 页，另外有分类/仓库 GET 和主图请求。本阶段无模型成本；可能产生正常公网传输费用，以云账单为准。

小批量验收后，再单独申请 2414 款全量许可。工具的全量开关为 `--all --expected-total 2414 --execute-download`，此处不授权执行。后续更新使用同一目录和 source_id，仍需确认当次范围及最新总数；不变文件通过条件请求复用，删除只标记。不要同时更换来源或拿开发库当权威源。

## 首次工具交付验证（d44a9d9，历史记录）

新增 36 项 Mock 测试及原有 34 项视觉回归全部通过，共 70 项（另有两项现有依赖弃用警告）。CLI 默认预检、显式下载、全量总数门槛、小批量硬上限/互斥参数均验证。Python语法、CLI帮助、TypeScript 和 `git diff --check` 通过。TypeScript 在 Windows 沙箱下曾出现依赖链接解析错误，沙箱外相同命令通过，没有修改前端绕过错误。

真实下载商品 **0**、真实图片 **0**、百炼调用 **0**。本轮未 SSH 登录、未访问生产 API、未改变生产代码/数据库/图片/配置/服务。交付前复核开发库和 Phase 1.5 索引的 SHA256，与开发前一致；来源不明的归档与 Phase 1.5 改动不纳入提交。开发 commit 仅含本阶段工具、测试和文档；真实小批量仍等待用户确认。

## 全量前最终检查（2026-10-11）

用户已完成并验收最初 10 款真实同步。代理只读本地审计确认 10 款、10 个有效主图文件、合计 460,420 字节，无本地图片校验失败，所有缓存均有 ETag 和 Last-Modified。复用用户已建立的 Windows SSH 回环隧道，没有代理 SSH 登录或使用部署密钥。

随后按用户授权重复同步原 10 款：10 次图片请求均返回 **304**，图片正文下载 **0**，失败 **0**、hash 异常 **0**、AI 调用 **0**。同步前后商品 ID、白名单业务字段、图片文件数量、SHA256 及修改时间均相同；仅本轮同步时间、清单、报告正常更新。证据在忽略目录 `production-catalog/reports/sample-repeat-verification.json`。主图实际修改、新增/停用、失败后恢复通过 Mock 验证，不为验证修改生产商品。

新增保护：dry-run 改为两遍完整资料核对；禁止间隔低于 0.2 秒；检查可用磁盘；Ctrl+C/本地 I/O/SQLite 异常使用受控错误及失败报告。新增与原有测试共 **84 项通过**，含 2414 款分页、限速/退避、资料变动、磁盘不足、Ctrl+C 后继续、sample→full 复用及本地 SQLite 损坏。两项现有依赖弃用警告；Windows 沙箱无法创建测试硬链接，沙箱外完整测试通过。没有改变前端业务或出入库逻辑。

### 已执行的全量资料 dry-run

```powershell
Set-Location D:\inventory-system
api/.venv/Scripts/python.exe api/scripts/sync_production_catalog.py sync --all --expected-total 2414 --interval 0.2
```

真实返回：

| 核对项 | 结果 |
| --- | ---: |
| 生产 / 选中商品 | 2414 / 2414 |
| 启用 / 停用 | 2414 / 0 |
| 扫描遍数 / 每遍页数 | 2 / 25 |
| HTTP 请求 / 正文传输 | 54 / 2,302,166 字节 |
| 两遍白名单清单一致 / ID 冲突 | 是 / 0 |
| 主图引用 / 不同主图路径 | 2414 / 2394 |
| 同路径多商品额外关联 | 20 |
| 空主图引用 | 0 |
| 包装规格行 / 空装箱数 | 2463 / 4 |
| 已有样本 / 全量清单中保留样本 | 10 / 10 |
| 将标记失效的已有商品 | 0 |
| 本地已校验图片缓存引用 | 10 |
| 图片正文下载 / AI 调用 | 0 / 0 |
| 发布镜像 | 否 |

运行前后核对原镜像所有文件 SHA256 与修改时间，均未变化，镜像仍只有 10 款。本次过滤后的报告保存于 `api/data/visual-search/phase16-preflight/full-dry-run.json`（Git 忽略，不保存原始响应）。20 个额外关联是同路径关联，不等于所有内容重复的普查；不同路径是否相同内容、未下载图片是否缺失或损坏，需全量图片下载后才能验证。4 条空装箱数原样保留，不推算或补值。

### 资源、异常与恢复

- dry-run 实测 D 盘可用 **77,026,873,344 字节（约 71.74 GiB）**。按 2394 个不同引用、每张上限 10MiB、临时文件及 256MiB 元数据余量，保守要求 **25,381,830,656 字节（约 23.64 GiB）**；空间足够。执行前仍重新查询；空间不足在图片请求前停止。其他进程中途占满磁盘则报告本地 I/O 失败，已成功缓存保留。
- 样本平均约 46KB，外推全量约 110MB，仅为样本估算，不能保证大图比例。当前 10 张已缓存，预计首次全量需获取最多 2384 个新引用。含条件请求和资料核对，约 2448 次请求，0.2 秒间隔自身约需 8.2 分钟，另加网络、解码和重试时间。
- 断点恢复粒度是**完整文件**：成功图片立即原子落盘并保存缓存。重新执行相同命令会重新扫描资料并校验/条件请求已完成文件；失败或未完成的图片重新获取。没有 HTTP Range 字节续传；单张中断会重新下载该张。失败的整轮资料核对不会替换已发布镜像。Ctrl+C 释放锁；进程强制终止由 OS 释放锁，来不及写最后报告时仍可校验已保存缓存。
- 每页必须匹配页号、页大小、预期行数及固定总数，拒绝重复 ID；两遍清单包含包装、分类、仓库及源更新时间。总数不再为 2414 或清单变化则失败，不擅自扩大范围或调整 `expected-total`。用户核对新总数后重新预检并确认。GET 分页并非服务器事务快照；两遍相等是观测一致性校验，不能证明期间从未发生过短暂变更。下载后的最终核对仍会再次执行。
- 稳定清单遇到部分图片失败仍可发布失败状态，退出 2 并报告未完整；不能当作全量验收通过。下一次运行重试失败图片。没有验证器时会再次读取正文，不能保证每个尚未测试的生产路径均支持 304。

### sample 扩充为 full 的数据影响

继续使用原 `production-catalog` 目录、同一 `source_id` 与来源标签，无需移动文件或合并 SQLite。完整稳定清单在镜像 SQLite 单个事务内新增预计 2404 款并刷新原 10 款当前资料；图片未变化时使用校验后的缓存，原样本 manifest 和旧图片保留。同图商品各自保留 ID。只有完整清单可将消失 ID 标记失效，不删除历史；本次 dry-run 没有待失效商品。

镜像当前元数据及离线浏览页会更新；不是“所有现有文件都不变”。本地开发库、原商品图片、出入库流水、Phase 1.5 索引、人工评测标签保持隔离。来源身份和 `index_namespace` 在 sample→full 中不变。

Phase 1.7 另行开发：显式选择生产图库；结果图片/货号/包装/详情只从对应镜像解析。新的独立向量索引须保存来源、图库清单指纹、原始生产 ID、图片哈希、模型名称/版本/维度及预处理配置，并保留共图的多商品映射。本阶段没有接入视觉查询、创建向量或合并任何数据库。

### 待单独确认的最终下载命令（尚未执行）

保持 SSH 窗口开启，确认后在 Windows 项目目录执行：

```powershell
api/.venv/Scripts/python.exe api/scripts/sync_production_catalog.py sync --all --expected-total 2414 --interval 0.2 --execute-download
```

结束后断开隧道，用 `status`、`get --product-id <真实ID>` 和本地 `browse.html` 验收。后续增量也使用此命令和同一来源；若生产总数变化，先重新核对并批准新预期总数。当前全量图片下载许可仍待用户确认。

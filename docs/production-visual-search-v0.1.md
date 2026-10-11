# Phase 1.7：独立生产图库视觉搜索

## 数据边界

复用 V0.1 的预处理、百炼 Provider、余弦 Top 5、人工确认、调用账本和批次任务。生产来源固定为 `production-catalog` / `aliyun-inventory-production`，API 不接受任意数据库路径或远程图库 URL。

- 开发数据仍来自原业务数据库；原 API 默认 `source=development`，兼容已有调用。
- 搜索页面默认选择“生产商品图库”，也可明确切换为“本地开发测试商品”。切换会清空候选、确认和索引预览，保留上传照片与框选。
- 生产资料只从 Phase 1.6 镜像 SQLite 的 `mode=ro` 连接读取。生产搜索接口和 CLI 不创建业务数据库 Session。
- 每个生产候选携带 `source_id` 和 `product_key=aliyun-inventory-production:<原始ID>`。图、货号、分类、仓库、尺寸和包装均来自镜像；详情在镜像专用弹窗展示。
- 生产 DTO 不提供价格、库存箱数、账户和流水；没有生产写入通道。图片通过管理员鉴权接口提供，禁止公开挂载镜像目录。
- 多个 ID 共图仍是多个独立候选，排序相同时按原始 ID 排序。

## 独立索引

默认布局（整个 `api/data/` 已被 Git 忽略）：

```text
api/data/
  inventory.db                         # 原开发业务数据库
  visual-search/
    index.db                           # 原 V0.1 开发索引
    phase15/index.db                   # 原 Phase 1.5 测试索引
    production-catalog/                # Phase 1.6 只读商品镜像
      source.json
      catalog.sqlite
      images/<sha256>.<format>
    production-indices/production-<来源身份指纹>/
      source-binding.json              # 拒绝来源不匹配、非空无标记目录、链接
      index.db                         # 独立 vs02 视觉索引
      jobs/<job_id>.json                # 来源、快照指纹、模型、ID 与图片哈希
      queries/<query_id>.jpg            # 私有查询裁剪图
    phase17/                           # 本地真实验收报告，不提交 Git
```

`source_identity` 加入生产 `index_key`；模型名称、维度、本地版本、北京地域、端点和预处理版本也参与 key。开发 key 的计算保持原样。

vs02 在既有独立视觉迁移历史上增加来源上下文和图片内容向量缓存。`init --source production-catalog` 仅迁移新建生产索引；旧 vs01 可继续读取，不会隐式迁移 Phase 1.5 或业务数据库。

商品向量按 `(product_id, index_key)` 保存，内容缓存按 `(index_key, 原图SHA256)` 保存。共图在当前模型配置下最多生成一次向量，跨批次复用。更换模型、维度、本地版本或预处理版本会形成新 key，不复用旧向量。

当前本地配置 `version=v1` 是人为管理的索引版本。百炼返回模型别名、请求 ID 和向量，未返回固定权重版本；不能宣称供应商权重永久不变。获知模型升级时，应提升 `VISUAL_EMBEDDING_VERSION` 并新建索引记录。

## 增量、续建与调用保护

每次计划重新读取镜像和主图 SHA256；启用且主图有效的商品才参与索引/检索。未变化记录跳过；更换引用但内容相同可直接复用；新内容重新调用；停用/失效 ID 排除。错误哈希拒绝检索。索引前后会再次检查状态与主图，避免调用期间变化的记录成为有效候选。

成功内容向量立即持久化，再写各商品关系。中断后的新批次只处理未完成或失败记录；同图 Provider 失败在同批内不会对其他 ID 重复付费尝试。重跑不会删除人工确认、查询或来源镜像历史。

默认 dry-run，实际调用需要 `--execute-paid`。每批上限 100 款；生产批次超过 10 款还需 `--confirm-large-batch`。API 对应 `confirm_paid` 和 `confirm_large_batch`，页面提供本批数量、预计新增图片调用与费用确认。

CLI 与 API 使用同一 OS 调用锁；API 内开发与生产来源共享串行锁。默认逐款间隔 1 秒。小批真实验收使用 `--no-retries`，最多 10 个不同内容调用，不自动全量循环。dry-run 不调用 AI，状态/计划只在 Windows 本地校验。

请求失败保存受控错误、请求 ID（如有）、时间和实际返回用量，不保存密钥、Base64 或远程响应中的私密错误文本。失败无用量时 `cost_complete=false`，不能把 Token 汇总的 0 当作账单确认。

## Windows 使用

在 `D:\inventory-system` PowerShell 中运行。密钥沿用 `api/.env` 的 `DASHSCOPE_API_KEY`；`--load-local-env` 只在本地进程加载，已有环境变量优先，绝不输出密钥。无需 SSH 连接生产服务器。

```powershell
# 仅首次初始化独立生产索引；已执行，通常无需重复
api/.venv/Scripts/python.exe api/scripts/visual_search.py init --source production-catalog --load-local-env

# 本地状态，不调用 AI
api/.venv/Scripts/python.exe api/scripts/visual_search.py status --source production-catalog --load-local-env

# 下一个 10 款批次预览，不调用 AI
api/.venv/Scripts/python.exe api/scripts/visual_search.py index --source production-catalog --max-items 10 --load-local-env --no-retries
```

实际受控批次命令如下，**重新运行会处理下一个待索引批次，不是重新验证已经完成的 10 款**：

```powershell
api/.venv/Scripts/python.exe api/scripts/visual_search.py index --source production-catalog --max-items 10 --load-local-env --no-retries --execute-paid --output api/data/visual-search/phase17/next-batch.json
```

本阶段已完成的 ID 1～10 不应再次发起上面命令作为验收。用 `status` 查看 `indexed=10` 和 SHA 验收报告即可。

API 搜索、状态、详情、查询图和人工确认必须携带 `?source=production-catalog`，例如：

```text
GET  /api/visual-search/index/status?source=production-catalog
POST /api/visual-search/search?source=production-catalog
GET  /api/visual-search/catalog/products/1?source=production-catalog
GET  /api/visual-search/catalog/products/1/image?source=production-catalog
POST /api/visual-search/feedback?source=production-catalog
```

更新 Phase 1.6 镜像后先查看 `status` / dry-run，再确认有限批次。不要将镜像路径配置成业务数据库，不要迁移或覆盖 Phase 1.5 索引。生产图库仍可离线浏览；实际查询向量生成需要访问百炼。

## 2026-10-11 实际验收

本地镜像：2414 款、2380 个不同图片内容。dry-run 全部原图 SHA 校验无异常，首批 10 款对应 10 个不同内容，上限 10 次 HTTP 尝试。

第一轮受限执行环境：原始生产 ID 1～10 全部连接失败。账本保留 10 条失败尝试，未返回向量、请求 ID 或 Token 用量。无密钥 HTTPS 连通性诊断后，在允许联网的环境只重试同一批 ID，没有扩大商品数量。

第二轮真实成功结果：

| 项目 | 结果 |
| --- | --- |
| 来源 | `aliyun-inventory-production` |
| 原始商品 ID | 1～10，保留 10 个独立身份 |
| 模型 / 维度 / 本地版本 | `qwen3-vl-embedding` / 2560 / `v1` |
| Endpoint | 北京原生 `https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding` |
| 成功 / 失败商品 | 10 / 0 |
| 成功 API 调用 / 内容缓存 | 10 / 10 |
| 实际 input / image / total Tokens | 220 / 6816 / 7036 |
| 成功调用费用估算 | ¥0.0124228 |
| 向量检查 | 全部 2560 维、有限非零数值、归一化范数约 1 |
| 图片映射检查 | 10 个向量记录 SHA256 全部与镜像原图一致 |
| 当前索引 / 剩余商品 | 10 / 2404 |
| 当前已索引 / 待索引不同内容 | 10 / 2370 |

费用按 image Tokens ¥0.0018/千、input Tokens ¥0.0007/千估算，来源：[阿里云多模态向量 API 官方文档](https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference)。实际费用以百炼账单和免费额度为准。账本总计 20 条尝试，其中 10 条无用量的连接失败；全账本 `cost_complete=false`，只有成功批次费用完整。

本地原始报告：`api/data/visual-search/phase17/plan.json`、`first10.json`（失败）、`first10-retry.json`（成功）、`acceptance.json`（不调用 AI 的向量及哈希验收）。含真实 ID/SHA 的逐商品证据留在被忽略目录。

保护检查：开发 `inventory.db` 和 Phase 1.5 `index.db` 的 SHA256 与本阶段开始时一致。适配器对生产镜像只用只读 SQLite 及图片读取；本阶段未连接生产服务器、未部署、未修改库存流水或生产配置。

测试：原 V0.1、生产镜像、镜像 CLI 与新增来源测试合计 **94 项通过**；覆盖业务 DB 同 ID 冲突、API 不创建业务 Session、跨源查询/确认隔离、同图复用、失败续建、版本/维度隔离、坏缓存重算、篡改主图排除、10 款调用门槛和无写入 dry-run。前端 `tsc --noEmit`、改动文件 ESLint、临时副本静态构建通过，`git diff --check` 通过。

隔离 Mock 浏览器验收：生产 3 款中红图 ID 1/2 均为独立候选，规格 24 件/箱；镜像详情和人工确认成功；切换开发后旧结果清空，开发结果为 12 件/箱；390px 宽度完成截图检查。临时 Python 静态服务器缺少 Next RSC 预取重写，出现预取 404；搜索和详情交互已成功，不作为生产部署验收。保留原用户服务，仅关闭本任务临时服务器。

**本轮仅验证真实建索引与映射，没有使用库存主图自查来宣称现场照片匹配准确率。** 现场多角度照片的真实 Top 5 命中率仍需另行测试。

## 首次 10 款验收后的全量授权申请（历史记录）

以下为首次验收时的申请说明。用户随后已授权全量执行；专用费用/失败保护及执行验收见 [全量索引文档](production-visual-search-full-index.md)。

剩余 2404 个商品关系，按当前镜像需新增约 2370 个图片向量；复用已完成的 10 个内容缓存。以本次 10 图平均用量推算，剩余首次调用费用约 **¥2.94**，完整 2380 图约 **¥2.96**；仅为小样本估算，其他图片和失败重试会改变用量。

获单独确认后，每批最多 100 款（约 25 批）串行执行，建议继续关闭自动重试，失败先检查再续建。未获确认不得执行以下付费命令或自动循环：

```powershell
# 仅表示获全量授权后的一个 100 款批次，目前未执行
api/.venv/Scripts/python.exe api/scripts/visual_search.py index --source production-catalog --max-items 100 --load-local-env --no-retries --execute-paid --confirm-large-batch --output api/data/visual-search/phase17/batch-001.json
```

每批返回非零或出现失败时先核对账本/状态；状态达到 2414 个有效关系、2380 个不同内容后停止。镜像更新、损坏图片或模型版本变化应先重新 dry-run，不按旧数量继续付费。

## Git 归属

本阶段提交来源适配、独立缓存迁移、受控 CLI、前端选择和 Mock 回归。Phase 1.5 已有的 Qwen `type=vl` Provider 兼容修复及合同测试是此次真实调用的直接依赖，一并保留并纳入；原 `docs/visual-search-v0.1.md` 的未提交 Phase 1.5 报告及来源不明压缩包保持原样，不纳入此次提交。真实镜像、向量、密钥、查询图与验收明细不提交。

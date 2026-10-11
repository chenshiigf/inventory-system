# Phase 1.9：独立人工标签与检索性能

基线 `dcd3b23`。本阶段没有真实 API 调用、新增 Token 或费用，没有访问生产服务器、部署、修改库存或重建图库向量。

## 第一轮标签收口

用户独立确认的 8 条答案已经与生产镜像货号、来源和原始候选核对，均为精确生产商品 ID 排名 1。
总查询对象 9 个，已审核 8 个；T01 未提供答案，保持待核实。
Top 1 / Top 3 / Top 5 均为 **8/8（100%）**，只针对已审核的 8 个对象，不代表全图库准确率。
T06、T07 是两个评测对象，虽然正确商品相同，分别计数。

标签使用 `source_id=aliyun-inventory-production` 和独立 `product_key`，只写入忽略目录：

```text
api/data/visual-search/field-evaluation/
  labels.json                                   追加人工审核修订，保留旧反馈
  reviews/round1-20261011-0nut83su/
    previous-labels.json                        原标签的完整字节备份
    review.json                                 标签、实际排名、精确/共图指标、原始记录哈希
    report.html                                 人工审核汇总
  runs/round1-20261011/                         原始查询、候选、分数、账本、报告全部保留
  phase19-acceptance.json                       数据保护验收
```

审核不重新搜索，不改变候选顺序或分数，不写商品业务数据库。
同图候选排名单独列出，不能替代精确商品 ID 命中。分类从对应镜像读取；未人工提供拍摄环境的标签记为 unknown，不猜测环境分组。
第一轮属于事后盲测审核，第二轮改为查询前独立确认答案，以降低事后判断偏差。

离线保存下一次人工审核的方式（正确答案文件与模型输入独立）：

```powershell
& api/.venv/Scripts/python.exe api/scripts/review_field_search.py `
  --run api/data/visual-search/field-evaluation/runs/<run-id> `
  --labels api/data/visual-search/field-evaluation/<human-label-input.json> `
  --catalog api/data/visual-search/production-catalog `
  --labels-store api/data/visual-search/field-evaluation/labels.json
```

输入是数组，每条包含 object_id、source_id、status，以及人工确认的 product_id/product_code。
status 支持 present / pending / ambiguous / absent；pending、ambiguous、absent 不进入精确 Top K 分母。
旧标签按修订保留；统计某一轮时必须选择明确的审核修订，不能将不同修订重复计数。

## 先测量得到的瓶颈

新增请求内的单调时钟 `timings_ms`，包含预处理、SQLite 向量读取、批量商品读取、主图及向量校验、查询 embedding、调用后复核、NumPy 相似度、查询保存和响应资料准备。
计时仅包含数值和查询 UUID，可返回给 API 调用者并写入日志，不记录照片、商品答案或密钥。
旧 elapsed_ms 字段保留原有含义（截至保存查询图片，早于 SQLite 查询记录插入）；完整服务耗时看 timings_ms.total 或外部 wall_ms。

第一轮没有保存查询向量。本阶段没有重新收费生成它们：以同一组 9 个真实现场对象和裁剪坐标，注入固定、与人工答案无关的图库向量，测量完整本地检索路径。
这些向量仅用于性能和排序一致性验证，**不是现场识别评测结果**。
所有查询及 Mock 账本写入只读在线备份生成的独立索引副本。

优化前同一 Windows 环境的 9 次热查询：

| 分段 | 平均毫秒 |
| --- | ---: |
| 图片预处理 | 57.540 |
| SQLite 全量向量读取（含连接） | 40.571 |
| 商品批量资料读取 | 23.984 |
| 调用前主图与向量复核 | 6085.378 |
| Mock embedding 及独立账本写入 | 8.990 |
| 调用后商品及主图复核 | 7395.280 |
| NumPy 相似度计算及排序 | 32.092 |
| 查询图片和 SQLite 记录保存 | 10.907 |
| 响应详情、来源及用量读取 | 2.863 |

调用后复核每次发生 2414 次单商品读取，每款分别连接商品和元数据 SQLite；每次查询共读取图片 4828 次。
cProfile 对 2414 款、137954844 字节主图的单轮复核测到约 5.72 秒，其中文件 read 约 3.96 秒、路径 resolve 约 0.99 秒，两个 SHA256 合计仅约 0.13 秒。
相同的 2380 个文件、135995198 字节对照：一次 read(10MB+1) 用 4.632 秒，64KB 分块有上限读取用 0.396 秒。
这是当前机器大块读取的实测差异；没有证据将原因具体归因到磁盘硬件或杀毒软件。
沙箱冷查询曾测到 32.03 秒，未纳入改善比率；正式前后基准均在相同的沙箱外本地环境运行。

## 实施的优化与结果

1. 图片改为 64KB 分块读取，保留 MAX_BYTES+1 上限、解析链接后的路径包含性检查和 SHA256 校验。
2. API 返回后重新批量读取商品资料，替代逐 ID 查询；删除、停用、主图变化和内容损坏仍复核，每个生产 ID 独立保留。
3. 索引 schema 检查的 SQLite 只读连接明确关闭，避免依赖垃圾回收释放句柄。

每次仍读当前模型/source index_key 对应向量，并在调用前后验证全部图片；没有引入跨请求图库缓存，因此不存在缓存失效或混用模型/来源的新增通道。
全量向量读取约 0.04 秒，排序约 0.03 秒，暂不为这两段新增内存索引、持久连接或 migration。

相同图片、相同合成向量、完整 2414 款图库，同一个常驻服务先 1 次冷查询、再 9 次热查询：

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 热查询平均 | 13.663 秒 | 3.195 秒 |
| 热查询 P50 | 13.676 秒 | 3.179 秒 |
| 热查询 P95（nearest-rank） | 14.045 秒 | 3.296 秒 |
| 进程内首次查询 | 12.977 秒 | 3.163 秒 |
| 脚本首语句至服务就绪，含依赖/快照/保护检查 | 1.313 秒 | 1.246 秒 |
| 脚本首语句至首次结果 | 14.289 秒 | 4.408 秒 |
| 本地 NumPy 检索平均 | 0.032 秒 | 0.029 秒 |
| 进程工作集（9 次后） | 140.21 MiB | 144.68 MiB |
| 进程峰值工作集 | 236.31 MiB | 240.27 MiB |

平均改善约 76.6%。10 个前后基准结果（首次冷查询及 9 次热查询）的 Top 5 来源、商品 ID、分数完全一致。
优化后仍有约 3.022 秒用于两轮全部主图路径、内容及向量校验，是当前主要剩余耗时。
现场原图文件读取单独测量，热查询平均约 0.384ms；表中的图片预处理另含 EXIF、解码、裁剪和 JPEG 规范化。
独立启动探针测到：启动至首条脚本语句约 50ms，NumPy 导入 82ms，其他服务依赖导入 670ms，启动至进程退出约 1.059 秒。
这些是冷进程成本，常驻 FastAPI 不会每个请求重新导入 Python/NumPy。

另外通过现有 FastAPI 路由、上传限制和鉴权执行 ASGI TestClient 基准；模型仍为 Mock，**显式模拟历史 API 平均 501.79ms 等待**：

- 9 次热请求平均 3.747 秒，P50 3.743 秒，P95 3.799 秒；首次请求 3.749 秒。
- 脚本首语句至首次结果 5.788 秒，包含建立索引副本及客户端初始化。
- 工作集 160.21 MiB，峰值 257.53 MiB。
- 包含真实请求处理代码，未包含浏览器、TCP 连接、真实百炼网络及服务波动。

**优化后的真实付费端到端耗时没有重测**。第一轮历史实测平均 13.503 秒、P95 13.627 秒、真实 API 平均 0.502 秒保持不变。
模拟路径尚未达到热启动端到端 3 秒目标，不能宣称目标已达成。
基准 JSON 报告写入约 3.6–4.6ms；原始 HTML 和历史报告未重写，其生成时间没有追溯测量。
各阶段、冷/热样本、来源调用数和内存原始记录见忽略的 benchmarks/before-hlu1y3z5、after-tjpvdj5w、after-qea6nc8v/report.json。

复测当前实现（零真实请求）：

```powershell
& api/.venv/Scripts/python.exe api/scripts/benchmark_field_search.py `
  --run api/data/visual-search/field-evaluation/runs/round1-20261011 --label after

& api/.venv/Scripts/python.exe api/scripts/benchmark_field_search.py `
  --run api/data/visual-search/field-evaluation/runs/round1-20261011 --label after `
  --http --provider-delay-ms 501.79
```

--label 只是报告前缀，不切换代码版本。前后比较使用同一数据模型身份及 --compare 指定的历史报告，任何候选/分数差异均失败并保存记录。
运行不加载 .env，注入 SyntheticProvider 且 API Key 为空，输出独立副本，不覆盖原索引或旧 run。

## 第二轮准备，不自动查询

建议进入 50～100 个对象的独立预标注评测，覆盖相似款、角度、尺寸、背景、多商品同框和图库外商品；目前没有依据更换模型或调整权重。
先由人工确认 present 或 absent；present 必须有明确生产 ID/货号，human_confirmed=true。
原图、区域、类别、环境、备注由人工提供；多对象保留各自 object_id，不按商品 ID 去重。
尚不能判定的对象先留在待核实集合，不混入本次正式预标注测试集。

```powershell
& api/.venv/Scripts/python.exe api/scripts/prepare_field_evaluation.py `
  --manifest api/data/visual-search/field-evaluation/round2/human-confirmed.json `
  --catalog api/data/visual-search/production-catalog `
  --output-parent api/data/visual-search/field-evaluation/round2/prepared
```

该准备工具只预检已有文件，不创建虚构照片，不调用模型，不意味着批准下一轮预算。
它冻结 human-labels.json 的原始字节和 SHA256，另输出只含对象、图片引用、crop、来源的 query-objects.json；正确 ID、货号、描述和标签状态不进入查询清单。
执行时模型只能接收图片区域字节，不能把对象 ID、文件名或清单文本当成提示词。付费执行仍需另行确认对象、请求数量和预算，关闭自动重试。
独立记录精确 ID 和同图排名；图库外商品按明确 no_match_decision 另算正确拒绝/错误接受率，不进入 Top K 分母。
无匹配判定策略必须在查询前单独确定并冻结；目前没有建立新的阈值或自动无匹配算法，没有显式决策的对象只记 undecided。

## 验收及边界

- 数据保护验收通过：原始现场图、生产镜像、完整索引、开发数据库、Phase 1.5 索引、原始第一轮报告及 API 记录哈希不变；原标签由授权追加修订，原件备份与 Phase 1.8 哈希一致。
- 原始索引 SQLite 完整性正常，2414 款商品关联和 2380 个不同图片向量保留；人工反馈未写入业务数据库。
- 最终相关回归 114 项通过（34.95 秒），Python 编译检查通过；两条既有 TestClient 依赖弃用警告。
- 前端 tsc --noEmit --incremental false 未通过，共 102 条类型错误。用 HEAD 的独立前端副本和相同 node_modules 重跑，错误内容完全一致；本阶段未修改前端或依赖，未将既存错误混入优化修复。
- Git diff --check 通过；生产照片、标签、查询结果、基准副本、类型检查日志均由现有 data/visual-search/ 忽略规则保护。
- 原有 docs/visual-search-v0.1.md 的未提交修改及 web/inventory-web-fixed.tar.gz 保留，不纳入本阶段提交。

当前交付可进入第二轮预标注评测准备。真实查询请求数和预算继续等待新的授权；没有智能出库、模型重排、库存操作或生产部署。

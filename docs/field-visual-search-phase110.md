# Phase 1.10：第二轮独立预标注准备

用户基线 bf401a1。随后按用户明确反馈补齐 T01，文档提交 d5229cb；第一轮当前审核为 9/9，精确 Top 1/3/5 均为 100%，待核实 0。
原始照片、候选、分数、报告、账本和旧审核修订保留，新的完整审核修订位于 field-evaluation/reviews/round1-20261011-qmfbzv1g。
这一轮小样本不能代表全图库准确率。

## 当前资料检查

- 本地仍是原来的 9 张原始现场照片，未发现新增第二轮照片或独立正确标签。
- 5 张照片、9 个已搜索对象属于第一轮，只可另列为回归对象，不能充当第二轮未见样本。
- 原有 4 张未搜索照片整理为 8 个临时待标注对象。房屋形调味瓶是否为套装、刺猬是否属于混款/多个商品，尚待人工确认；对象数量可能调整。
- 第二轮已确认正确标签 0、已确认裁剪 0、真实请求 0、Token 0、费用 0。
- 首批目标为 20～30 个独立对象；若这 8 个全部确认有效，仍需补足 12～22 个对象，不要求相同数量的不同照片。

真实资料全部保存在 Git 忽略的目录：

```text
api/data/visual-search/field-evaluation/round2/
  originals/                  4 张未测现场原图的字节一致副本；新照片可放这里
  pending-objects.json         8 个实际暂定对象，正确 ID/货号均为 null
  preparation-summary.json    资料缺口统计，execution_authorized=false
  draft-review/report.html    原图蓝框与实际预处理裁剪对照
  draft-review/crops/          8 个建议裁剪预览，不改原件
  human-confirmed.json         人工确认后另存；当前尚未创建
  prepared/                   标签/裁剪通过预检后的独立冻结包；当前尚未创建
```

建议对象编号为 R2-001～R2-008；实际照片、对象描述、区域和待补信息均在本地 JSON/HTML，不将真实商品资料或图片提交 Git。
按压瓶已包含泵头、手臂及底座，但两件商品局部相邻，矩形裁剪仍可能带邻商品边缘，需用户确认或补拍。
刺猬暂用整组预览，不擅自认定商品身份或拆分数量。手写内容、吊牌和视觉相似度不能代替人工确认的生产 ID。

## 人工需要补充的信息

每个对象需要：image、object_id、image_id、source_id、crop（EXIF 校正后的 x/y/width/height 比例坐标）、crop_confirmed，以及以下标签。

| 字段 | 要求 |
| --- | --- |
| status | present 或 absent；pending/ambiguous 继续待核实，不纳入已就绪首批 |
| human_confirmed | 用户独立确认正确商品或图库不存在后设为 true |
| product_id / product_code | present 必须是生产镜像中的准确 ID/货号；absent 均为 null |
| category | 商品类别；已有正确 ID 后从对应镜像复核，图库外商品由人工提供 |
| difficulty_tags | 至少一项：普通、相似外形、尺寸相近、角度变化、混款、多商品同框、复杂背景、图库外等 |
| multi_product_photo | 原图是否多商品同框 |
| mixed_product | 是否属于一个混款/套装商品身份；不清楚则继续待核实 |
| shared_image | 确认正确 ID 后按镜像 SHA256 核对是否有其他独立商品共图 |
| gallery_absent | 是否已人工确认图库不存在；必须与 absent 状态一致 |
| notes | 遮挡、裁剪缺口、场景和身份歧义等实际情况 |

没有正确标签时不会先调用 AI 来找答案。用户可以先离线浏览 production-catalog/browse.html 核对真实 ID。
同图商品保留独立来源及生产 ID；相似度不是正确概率，不能把所有共图商品当作精确成功。

## 已就绪首批的离线预检命令

```powershell
& api/.venv/Scripts/python.exe api/scripts/prepare_field_evaluation.py `
  --manifest api/data/visual-search/field-evaluation/round2/human-confirmed.json `
  --catalog api/data/visual-search/production-catalog `
  --output-parent api/data/visual-search/field-evaluation/round2/prepared `
  --first-batch
```

这是离线准备命令，没有付费开关，不会执行搜索。当前 pending-objects.json 只有 8 个且无标签，不能通过该预检。
新增 --first-batch 要求 20～30 个对象、独立人工标签、裁剪确认、类别/难度/混款/共图/图库外标记，验证真实图片和裁剪有效性。
图库原始主图不能冒充现场照片；完全相同原图哈希与裁剪的重复对象会被拒绝，不允许换编号凑数。
不同裁剪可来自同一张多商品原图，正确商品 ID 相同也不会被去重。
工具保存人工标签原始字节和 SHA256；query-objects.json 只保留对象/图片引用/区域/来源，过滤正确 ID、货号、类别、难度、描述及答案状态。
模型执行时只接收确认区域的图片字节，不把文件名、对象编号或清单文字发作提示词。

## 请求数量和预算草案

首批 20～30 个对象，每个确认裁剪请求一次，预计 20～30 次真实请求。整图/裁剪对照或重测会增加请求，需另行确认，不能悄悄翻倍。
当前授权真实调用数量为 0；8 个待标注对象只是准备资料，并没有批准任何查询。

北京 qwen3-vl-embedding 当前官方原价为图片 1.8 元/百万 Token、文本 0.7 元/百万 Token，已于 2026-10-11 核对[官方模型价格](https://help.aliyun.com/zh/model-studio/qwen3-vl-embedding)。
基于第一轮实际均价 0.0011106 元/对象，20～30 次约 0.0222～0.0333 元；按第一轮单次最高 0.001801 元外推约 0.0360～0.0540 元。
这是历史样本外推，不是下一轮实际 Token 或保证价格，尺寸、裁剪和服务用量可能不同。
建议提交用户确认的预算草案为 1 元人民币、本地实际用量累计估算停止阈值；它不是百炼账户账单硬上限。
执行前核对实际配置和单价，串行、间隔至少 1 秒、关闭自动重试，失败请求也计数；每次前预留费用、每次后保存实际用量。
失败、用量缺失、明显异常费用或来源/模型/维度变化时停止，不自动补调用。恢复成功对象不重新收费请求，待失败请求重新授权。

## 等待确认后的执行方案

1. 完成 20～30 个有效对象与人工独立标签，冻结文件及图片哈希，确认混款/套装对象身份和全部裁剪。
2. 提交明确对象清单、真实请求数量和预算，等待用户单独确认。预检或本次代码提交不构成付费授权。
3. 复用第一轮只读 SQLite 在线备份、独立 run、费用守卫、检查点和 HTML 报告方式；使用独立快照上的常驻 FastAPI，完整 2414 商品、qwen3-vl-embedding 2560 维，不重建主图向量。
4. 既有第一轮 Runner 固定 9 次/第一轮路径，不直接对它改参数后运行第二轮。实际执行前按明确批准的首批封装新 run，并离线核验数量/费用保护。
5. 每个对象只发一次真实图片向量请求，记录 API 账本实际 Token/费用/调用耗时，以及上传、鉴权、图片读取、预处理、SQLite/向量读取、商品资料、校验、NumPy、保存/响应等单调计时。
6. 首次实际请求单列冷启动，后续请求单列热启动；CLI 进程启动及客户端耗时独立计时，不重复付费调用同一对象来测 CLI。真实外部 API 与 Mock/模拟等待完全分开，不用 Phase 1.9 合成基准充当本轮真实性能。
7. 原始 Top 5、商品图、来源 ID/货号/规格/分数和每次计时保存为不可覆盖的新记录。人工标签只在结果统计阶段使用，不重排候选。
8. present 对象按精确 ID 算 Top 1/3/5、品类及难度分组；同系列、混款、共图单列错误/歧义。absent 对象单列非空候选返回率、最高相似分数及人工误推荐案例，不混入 Top K 分母。
9. 当前没有自动无匹配策略，不把推荐候选自动当匹配判定；显式 no_match_decision 才能算拒绝/接受指标，否则记 undecided，不自行调整阈值提高结果。
10. 首批结果及费用通过检查后，再单独确认扩大至 50～100 个对象。没有生产访问、部署、库存操作或索引重建。

## TypeScript 构建问题独立记录

Phase 1.9 tsc --noEmit --incremental false 有 102 条既存错误；当时基线独立前端副本使用同一 node_modules 复现了完全相同的错误，完整日志留在忽略的 field-evaluation/typecheck-phase19.txt 和 typecheck-baseline/baseline-types.txt。
当前 web/next.config.ts 只设置 output=export 和 trailingSlash，没有 typescript.ignoreBuildErrors。
已读取本机安装 Next.js 的 docs/01-app/03-api-reference/05-config/01-next-config-js/typescript.md 及 build/type-check.js：默认不跳过检查，类型错误会使 next build 的类型检查步骤失败。
因此，这些错误在当前本地依赖环境中会阻碍正式构建，不能称构建已通过；本阶段未执行完整 Next.js 构建，也没有修改配置、依赖或生产构建流程。
后续正式构建前应单独处理依赖/类型问题；不是 Phase 1.10 引入的错误，不通过关闭检查掩盖。

## 本次准备验收

待测对象/建议框和真实裁剪预览已生成；第二轮正确标签与执行授权均为 0，仍等待新增样本及人工补充。
10 项相关离线测试通过，覆盖标签隔离、原记录保护、首批数量、裁剪/标记校验、重复对象拒绝、图库主图拒绝、图库外对象单列；Python 编译检查和 Git diff --check 通过。
图库、原索引、开发数据库、Phase 1.5 索引、原始第一轮图片/报告及所有历史审核修订继续保持不变。
只有准备工具、离线测试及说明纳入提交，照片、人工标签、候选、原图副本和报告不提交 Git。已有无关修改保留。

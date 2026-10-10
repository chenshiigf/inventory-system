# 商品视觉搜索 V0.1

## 本次边界及项目审查

基于 `928422a`，当前分支为 `master`；开始时只有未跟踪的 `web/inventory-web-fixed.tar.gz`，本次不处理该文件。后端现有 499 个测试，商品及图片业务集中在 `api/app/models.py`、`routers/products.py`、`services/image_storage.py`。现有前端为 Next.js 16.3.5 静态导出，沿用 Ant Design、`PageHeading`、`ProductImage` 和 `/products/detail?id=...`。

- 商品有 `product_code`、`image_path`、`thumbnail_path`、`is_active`、分类和仓库关联；**没有独立品名字段**，页面显示货号和已有资料，不编造品名。
- `ProductPackaging` 独立记录装箱数及箱数。视觉模块只读商品资料，不更改库存、图片、货号或包装。
- 现有图片是 `/uploads/products/main/*.webp` 及配对缩略图；复用现有图片访问组件。商品录入、编辑、导入会保存图片标识；视觉模块以当前主图标识及 SHA256 校验变更。
- 停用商品不搜索；零库存但仍启用商品与现有商品列表一样可搜索。图片变化立即过滤旧向量，下一次增量索引更新。
- 生产鉴权在 Nginx server 层 Basic Auth，后端原来没有用户表或管理员模块。视觉接口额外核验同一 htpasswd 文件，使用 `VISUAL_ADMIN_USERS` 限定管理员。未配置鉴权时返回 503，未登录返回 401，非管理员不能调用付费接口。
- 本地只读核对发现 81 款商品，80 款启用、74 款有主图；这不是生产 2300 款的副本，实际全量成本及图库效果需用正式授权的数据另行验证。
- 业务数据库继续使用原有 Alembic。本次选独立 SQLite 索引：索引可重建，避免向真实业务数据库增加索引任务及查询图片记录；使用独立 Alembic 配置保留可回滚迁移机制。

本阶段仅本地开发测试，没有生产迁移、部署、2300 图付费建库、真实准确率评测，也没有新增出库操作、自动拆分、模型训练或自动认定商品。

## 数据结构及模块

数据目录：`$INVENTORY_DATA_DIR/visual-search/`，默认 `api/data/visual-search/`。

| 文件 / 表 | 用途 |
| --- | --- |
| `index.db` / `visual_embeddings` | product_id + index_key 联合主键，主图标识/哈希、模型、维度、版本、预处理版本、归一化 float32 BLOB、索引时间、ready/failed/skipped、失败原因 |
| `visual_jobs` | 数量上限、管理员、开始/完成时间、状态、逐款进度；中断后增量续建 |
| `visual_calls` | 每次实际 HTTP 尝试，包括重试；用途、任务 ID、用量、request_id、耗时、经过控制的错误文字 |
| `visual_queries` | 查询 ID、管理员、候选 ID、原图哈希、框选比例、模型版本、耗时、用量、私有图片引用 |
| `visual_feedback` | 每个查询最终确认商品 ID 或无匹配；再次确认覆盖该查询的确认记录，不修改索引或训练模型 |
| `queries/{uuid}.jpg` | 仅保存实际参与检索的标准化区域，不位于公开 uploads 下；原图不持久保存 |
| `calls.lock` | API 与本地 CLI 共用 OS 文件锁，进程退出自动释放；限制查询/批量索引并发 |

索引身份包含模型、维度、人工版本、地域、端点与预处理版本。切换这些配置会建立独立索引，旧向量不会混合比较。首版没有自动删除历史版本及反馈；备份/容量管理需同时考虑这个独立目录。现有 full 业务备份不会自动包含它，索引可重建，但反馈需单独备份。

服务处理：EXIF 校正 → RGB 白底 → 最大边 1024 → JPEG 质量 90。图库与查询走同一函数。上传支持 JPG/PNG/WEBP 静态图片，最多 10MB、4000 万像素；禁止远程 URL 检索及目录逃逸，拒绝动画。框选坐标为 EXIF 校正后原图的 `[x,y,width,height]` 比例，服务端验证并裁剪，返回的私有预览就是实际检索区域。

存储浮点向量预归一化；NumPy 余弦排序，分数相同按商品 ID 升序。返回最多 5 个候选，默认 `VISUAL_MIN_SCORE=0`，只是候选过滤参数，不是经过标定的准确率阈值。没有可用图库时不调用模型，返回空候选。

## 百炼原生 Provider

核对日期：2026-10-10。官方资料：[向量化](https://help.aliyun.com/zh/model-studio/embedding)、[多模态 Embedding API](https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference)。

默认北京 `qwen3-vl-embedding`、2560 维；文档支持 256 / 512 / 768 / 1024 / 1536 / 2048 / 2560，单图不超过 10MB。

Provider 使用 HTTPS POST：

```json
{
  "model": "qwen3-vl-embedding",
  "input": {"contents": [{"image": "data:image/jpeg;base64,<server-generated>"}]},
  "parameters": {"dimension": 2560}
}
```

读取 `output.embeddings[0].embedding`、`type`、`usage`、`request_id`。仅一个图像输入，不混入文本或其他模型向量。北京默认端点见 `.env.example`；可配置兼容的阿里云业务空间端点，切换端点自动隔离索引。首版仅启用北京地域，其他地域需先核实官方支持。低成本 vision-flash 可后续评测；当前未实测，不声称与默认模型等效。

默认超时 30 秒、最多重试 1 次，仅超时/网络异常及 429/500/502/503/504 重试；不重试鉴权或维度错误。不跟随重定向，不记录密钥、完整模型请求/错误体或 Base64。查询与索引共享调用锁，默认索引每款至少间隔 1 秒。每次实际 HTTP 尝试均计入 `api_calls`；失败尝试可能服务端已处理，不能从本地日志断言零费用。

qwen3 的 `input_tokens` 是文本 Token，`image_tokens` 是视觉 Token，`total_tokens` 是两者之和。当前文档图片每千 Token 0.0018 元、文本 0.0007 元，可通过环境变量修改；估算费用按两类分别计算。缺失用量/失败调用会标记成本不完整，其他模型成本显示未知；最终以百炼账单为准。

## API

所有入口复用 Basic Auth，只有名单内管理员可访问。POST 验证 Origin / Sec-Fetch-Site，防止浏览器跨站触发付费操作；前端仅允许显式配置的 Origin 携带凭据。

| 方法 / 路径 | 作用 |
| --- | --- |
| `POST /api/visual-search/search` | multipart file、可选 crop、top_k 1–5 |
| `GET /api/visual-search/index/status` | 配置、schema、已索引、待索引、失败、跳过、最近任务、实际用量 |
| `POST /api/visual-search/index/rebuild` | 默认 dry_run=true、max_items=10；真实索引另需 confirm_paid=true；每批最多 100 款 |
| `GET /api/visual-search/index/jobs/{id}` | 持久任务进度与该任务的实际调用统计 |
| `POST /api/visual-search/feedback` | query_id + selected_product_id 或 no_match=true |
| `GET /api/visual-search/queries/{id}/image` | 仅查询本人可访问的检索区域图片，private/no-store |

真实索引后台执行，单 worker；逐款提交，所以服务中断不会丢失已完成向量。重启时只有文件锁可取得，才将未完成任务标成 interrupted；CLI status 不会打断仍运行的 API 任务。索引的删除只涉及索引行，商品/原图永不删除。

## Windows 本地启动

使用 PowerShell，在项目根目录操作。默认不设置 `INVENTORY_DATA_DIR` 时使用本地现有数据；需完全隔离时先指定新本地目录，并按项目原说明创建/迁移本地业务库。不要使用生产数据库连接或把测试库上传生产。

```powershell
cd D:\inventory-system
api/.venv/Scripts/python.exe -m pip install -r api/requirements-dev.txt

# 仅初始化独立视觉索引；不执行业务表迁移
api/.venv/Scripts/python.exe -m alembic -c api/visual-alembic.ini upgrade head

# 本地首次测试时创建新的密码文件；隐藏提示输入密码，拒绝覆盖已有文件
api/.venv/Scripts/python.exe api/scripts/create_visual_admin.py --file api/data/visual-search/.htpasswd --user admin
$env:INVENTORY_HTPASSWD_FILE = 'D:\inventory-system\api\data\visual-search\.htpasswd'
$env:VISUAL_ADMIN_USERS = 'admin'
$env:VISUAL_ALLOWED_ORIGINS = 'http://127.0.0.1:3002,http://localhost:3002'

# 无 API Key 也可以启动和查看配置状态
api/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir api --host 127.0.0.1 --port 8002
```

另一终端：

```powershell
cd D:\inventory-system\web
$env:NEXT_PUBLIC_API_BASE_URL = 'http://127.0.0.1:8002'
pnpm dev --port 3002 --hostname 127.0.0.1
```

打开 `http://127.0.0.1:8002/api/visual-search/index/status`，使用本地管理员登录，然后打开 `http://127.0.0.1:3002/visual-search/`。本地跨端口使用同一主机名，不混用 localhost 与 127.0.0.1；真实生产走现有同源 Nginx 页面，不需要额外的登录页面。

前端可多图上传、鼠标/触摸拖框、数字框选、单区域/整图检索、依次检索多个区域、候选详情与确认、无匹配反馈、重试及受控索引。数字框选为键盘路径。候选详情在新页打开，保留当前搜索上下文。

### 安全配置 API Key

只设置后端进程环境，不设置任何 `NEXT_PUBLIC_*` Key。项目不会自动加载 `.env`；`.env.example` 只是示例。使用当前 PowerShell 的隐藏输入：

```powershell
$visualKeyInput = Read-Host '百炼北京 API Key' -AsSecureString
$env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new('', $visualKeyInput).Password
Remove-Variable visualKeyInput
# 在同一终端启动/重启本地 API，或执行下面的 CLI
```

不要输出环境变量，不把真实 Key 复制到文档、Git、浏览器或截图。本地环境变量只对当前进程及子进程生效；API 已启动时需停止自己的本地进程后重启，不影响其他系统。

将来经授权部署时，生产环境文件由管理员在 Git 外维护；`INVENTORY_HTPASSWD_FILE` 指向已有 Nginx 文件，API 运行用户需要只读权限（现有文件 root:www-data/640，inventory 默认无法读取）。通过受限 ACL 或受控同步的只读副本解决，不放开为所有人可读；后端支持 bcrypt、APR1、SHA1 格式，其他哈希格式会拒绝。`VISUAL_ADMIN_USERS` 填现有管理员用户名，`VISUAL_ALLOWED_ORIGINS` 填实际系统的完整 Origin（包括端口）。继续只监听回环、Nginx Basic Auth 保持启用。这里仅说明，尚未更改生产权限或配置。

### 先索引 10 款，再真实评测

以下 CLI 必须从设置了 Key 的后端 PowerShell 执行。默认 dry-run 不写索引、不调用模型。

```powershell
api/.venv/Scripts/python.exe api/scripts/visual_search.py status
api/.venv/Scripts/python.exe api/scripts/visual_search.py index --max-items 10
# 人工检查上面的预检数量及费用，再执行本批
api/.venv/Scripts/python.exe api/scripts/visual_search.py index --max-items 10 --execute-paid --output output/visual-first10.json
api/.venv/Scripts/python.exe api/scripts/visual_search.py status
```

按商品 ID 顺序处理前 10 个待索引的有效主图。检查结果、失败原因、实际调用次数和用量后，再在页面用真实照片检索，人工标注正确商品。

评测 manifest 示例（文件名和商品 ID 需要人工填写，不能使用未确认标签）：

```json
[
  {"image": "images/query-1.jpg", "product_id": 123},
  {"image": "images/multi.jpg", "product_id": 456, "crop": [0.1, 0.2, 0.4, 0.5]}
]
```

```powershell
api/.venv/Scripts/python.exe api/scripts/visual_search.py evaluate --manifest output/evaluation/labels.json --max-items 10
api/.venv/Scripts/python.exe api/scripts/visual_search.py evaluate --manifest output/evaluation/labels.json --max-items 10 --execute-paid --output output/evaluation/report.json
```

工具输出 Top 1/3/5 命中率、平均耗时、实际 Token 与估算费用。这里只评估人工标签对应的样本，失败则退出并保留调用日志；未运行真实评测前没有准确率报告。同款主图检索的 Mock 测试只能验证代码流程，不能表示真实商品识别率。

### 2300 款全量过程

不提供自动一次处理 2300 图的默认命令。先确认 10 款真实试验质量及账单，再由管理员分批执行 `index --max-items 100 --execute-paid`，检查每批报告后继续。无主图/无效图片跳过并留原因，停用商品过滤，已成功且未变的主图跳过；失败不会阻止之后商品，可以下批重试。

约 2300 条 2560 维 float32 向量的原始大小约 22.5MiB，SQLite 还有元信息开销；无需在 2 核 2GB 上运行视觉模型。默认每款 1 秒间隔，**仅速率等待**约 38 分钟，加上网络、预处理、推理和重试；先以 10 款实际耗时外推。若没有失败，初建模型尝试数等于有效待建图片数，有重试则增加；费用以实际 image/text Token 汇总外推，不能直接用商品数虚构金额。搜索前会读取当前主图哈希以排除旧索引，图库大时需要观察本地磁盘耗时。

所有 Next.js 构建及批量测试仍在本地；生产上线、迁移及正式批量调用需单独授权。不要上传本地测试数据库覆盖生产库。独立视觉目录含反馈，不应因“索引可重建”而随意清空它。

## 测试、构建与回滚

```powershell
New-Item -ItemType Directory -Force api/.pytest-tmp | Out-Null
# 为原有 shell 模板测试选择 Git Bash，避开 WindowsApps/bash.exe 占位程序
$env:PATH = 'D:\biancheng\Git\bin;' + $env:PATH
api/.venv/Scripts/python.exe -m pytest -q --basetemp=api/.pytest-tmp/visual-check -p no:cacheprovider
cd web
pnpm exec tsc --noEmit
pnpm lint
pnpm build
```

独立迁移 `vs01` 已覆盖 upgrade → downgrade → upgrade。若在确认保留反馈并备份后回滚，仅对独立索引配置执行 `alembic -c api/visual-alembic.ini downgrade base`；会移除视觉表，不动业务表，查询图片文件需管理员按保留策略另行处理。本阶段不在现有本地业务库或生产库执行回滚。

### 无 Key 浏览器 Mock 验收

自动化测试注入固定颜色特征 Provider，并禁止访问外部网络。生产服务没有 Mock 环境开关，不会因缺 Key 自动返回假向量。

仅手动验收时，可使用专门的 `api/tests/create_visual_manual_fixture.py --mock-only --directory <新临时目录> --port 8112`。它仅绑定本机回环，模型名明确为 `mock-colour-fixture-NOT-real-AI`，创建两款带 MOCK 货号的测试商品，不连接实际业务库。前端测试进程将 `NEXT_PUBLIC_API_BASE_URL` 设为 `http://127.0.0.1:8112`，账号为 `admin / local-mock-only`。完成后停止自己的两个测试进程，不把此服务或临时图库发布生产。

## 文件清单

新增：
- `api/app/routers/visual_search.py`
- `api/app/services/visual_search/{config,images,provider,auth,store,service,lock,upload_limit,__init__}.py`
- `api/visual-alembic.ini`、`api/visual_alembic/env.py`、`api/visual_alembic/versions/vs01_visual_search.py`
- `api/scripts/visual_search.py`、`api/scripts/create_visual_admin.py`
- `api/tests/test_visual_search.py`、`api/tests/create_visual_manual_fixture.py`
- `web/app/visual-search/page.tsx`
- `web/components/visual-search/{VisualSearchWorkspace,RegionSelector}.tsx`
- `web/lib/api/visual-search.ts`、本说明

修改：`.gitignore`（忽略本地浏览器验收产物）、`api/app/main.py`、`api/.env.example`、`api/.gitignore`、`api/requirements.txt`、`api/requirements.lock.txt`、`web/components/app-layout/AppLayout.tsx`、`web/lib/api/client.ts`、`web/app/globals.css`。依赖锁保留旧生产基线，只追加视觉所需依赖；NumPy 2.5.3 要求 Python ≥3.12，符合既有生产基线，安装及本地测试未升级原依赖。

验证记录：新增 32 项视觉测试通过；全量发现 531 项，运行 529 项通过，2 项既有恢复测试因 Windows 临时目录重命名被拒失败，随后两项单独复测通过。此前 11 项 shell 相关失败已通过只对测试进程调整 Git Bash PATH 解决。未修改既有备份代码。TypeScript 通过；完整 Lint 0 错误，原有导入页面 1 条导航警告；本地 Next.js 静态构建通过。浏览器 Mock 验收覆盖一次两图上传、两区域拖框、各区域独立检索、人工确认、390px 手机布局无横向溢出。截图为 `output/playwright/visual-desktop.png`、`visual-mobile.png`，其中 MOCK 商品及分数仅是测试数据。

最终验证结果及 Git commit 见本次交付消息；真实百炼联通和准确率仍需人工批准的小样本试验。

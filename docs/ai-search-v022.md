# AI 找货助手 V0.2.2：单图框选识别验收报告

日期：2026-10-13。基线 a8abcdb。本轮保留原 /ai-search 布局和整图流程，只增加单区域框选及确认后的货号复制。没有真实付费查询、生产访问或部署，没有修改业务数据库、商品镜像、视觉向量、模型及鉴权配置。V0.3 Mock 工作区继续保留，不接入正式页面。

## 使用流程

1. 在本地前端端口完成已有管理员登录，再打开 <http://localhost:3002/ai-search/>。登录入口为同主机、同端口的 /api/visual-search/index/status?source=production-catalog；不要把 8002 的登录当作前端登录。
2. 上传一张 JPG、PNG 或 WEBP。默认整图，直接点击开始找商品；不强制框选。
3. 如需局部识别，点击框选商品，在完整原图上拖动一个框。正向和反向拖动均支持。检查区域预览后点击识别框选商品。
4. 搜索仍返回最多五个候选。其他候选保持折叠；可以在只读资料弹窗中确认任何有效候选。
5. 人工确认反馈保存成功后，在已确认商品区域点击复制货号。复制对象是最终选择的候选，不固定为第一推荐。缺货号时按钮禁用；剪贴板失败时展示可手动选取的货号输入框。
6. 点击重新框选可以识别同图中的下一款；始终从完整原图重新选择，并立即清空旧结果、确认、详情和复制对象。清除框选返回整图方式，更换图片重新上传。上述操作均不自动搜索。

每次真实点击搜索可能产生百炼费用。本轮自动验收均使用隔离 Mock，真实网页有效照片验收由用户自行进行，不在此报告中冒称已经完成。

## 单区域与异步保护

正式会话只保存一个原始 File、一个 crop、一个当前结果和一个 confirmedId；不存在区域列表、确认清单或批量处理。图片版本和裁剪版本独立递增，每次查询生成前端请求标识。

start/complete/finish 操作核对 File 身份、图片版本、裁剪版本和请求标识；确认再核对当前 query_id 与候选ID。旧请求完成及清理不能覆盖或清除更新请求。界面卸载时取消前端等待并释放当前请求状态，不自动重发可能已经计费的请求。搜索及确认期间禁止更换照片、框选和重复提交。

完成响应还检查服务端返回的 crop 与提交范围一致。生产来源、独立命名空间、模型、候选身份、图片引用及详情图库版本继续沿用原校验。货号可以改变，关联身份始终采用生产商品ID与 source_id。

## 裁剪实现与验证

原始 File 不修改、不另存为商品主图。前端发送原始图片及 [x,y,宽,高] 比例坐标；后端既有 prepare_image 在 EXIF 方向校正后裁剪和缩放，再将裁剪 JPEG 字节交给 Provider。

RegionSelector 继续用于鼠标操作。SingleCropSelector 只传一个框，重画替换旧框。原有组件默认最小拖动比例不变，单区域包装按后端至少 10×10 原图像素校验，避免高分辨率照片的小区域被固定比例门槛静默拒绝。

共用 image-crop 函数验证显示缩放、反向拖动、边界夹取和原图像素换算。预览采用与 Python round 一致的半像素 ties-to-even 舍入规则，修正原 PreviewCrop 在 .5 像素边界处与后端可能相差一个像素的问题。V0.3 仅复用该通用预览改进，未增加任何正式多区域状态。

## 测试结果

- 前端：`cd web; node --test tests/ai-search.test.mjs tests/ai-search-preview.test.mjs`，24 项通过。包括原有来源隔离/代理保护，原始 File 与 crop 请求、缩放和反向拖动、像素边界、裁剪失效、图片替换、旧响应/旧清理拦截、复制非首选候选和 V0.3 回归。
- 后端：`tests/test_visual_search.py` 中按 crop、preprocessing_orientation、auth_all_endpoints、cross_site_paid、native_contract 筛选，27 项通过、16 项未选择。新增 EXIF 1～8 的 Provider 输入字节检查及原生百炼请求 MockTransport 的裁剪 payload 检查；只使用临时图库/数据库及 Mock，外部网络被测试夹具禁止。
- 后端验证保存的查询图片与 Provider 收到的 JPEG 字节完全一致，尺寸符合方向校正后的坐标，裁剪内容不混入另一颜色。Mock 向量用 256 维测试夹具，不能将它冒称真实 2560 维模型评测；真实模型及索引配置未改变。
- 专项 TypeScript：通过。全项目仍为基线 101 条诊断，归一化位置后逐条比较一致，新增 0，没有进行全项目类型修复或声称完整构建通过。
- ESLint：修改文件通过；全项目 0 错误，仅保留 ProductImportWorkspace 第625行既有导航警告。git diff --check 通过。

隔离网页实际使用 /ai-search 组件副本与 loopback-only Mock 服务，正式 3002 代理及真实服务配置不变。完成四次 Mock 搜索：整图、蓝色反向框选、同图重新框选红色、EXIF 6 旋转图框选。前3次上传原始文件 SHA256 一致；每次 top_k=5、source=production-catalog、不同 query_id。蓝色裁剪只含蓝色，旋转图裁剪为实际鼠标坐标对应的 451×320 像素且只含红色。鼠标输入落在屏幕像素上，须按实际采集坐标计算，不能把预定比例近似值当精确尺寸。

先确认第二候选，再复制，浏览器剪贴板读取到 DEMO-CROP-002。重新框选后旧推荐卡片与复制按钮数量均为0，同一时刻框数量为1。清除框选回到整图；上传 EXIF图后浏览器 naturalWidth/naturalHeight 为500×800，与后端方向校正一致。三份原有视觉索引/图库数据库文件 SHA256 在本轮 Mock 验收前后保持一致，付费调用为0。

浏览器资料、合成测试图、Mock ledger、截图、类型诊断、保护哈希和隔离工程仅保存在 Git 忽略的 api/data/visual-search/field-evaluation/v022-acceptance。截图中的 DEMO-CROP 商品及 Mock 示例仓库仅用于交互验收，不能作为商品匹配准确率。

## 修改文件清单

| 文件 | 内容 |
| --- | --- |
| web/components/ai-search/AISearchWorkspace.tsx | 单框操作、请求绑定、确认后的复制及错误提示 |
| web/components/ai-search/AISearchSessionProvider.tsx | 单图会话接入版本及请求 reducer，保留独立 Mock 预览 |
| web/lib/ai-search-session.ts | 单结果状态、过期响应/反馈保护 |
| web/lib/image-crop.ts | 坐标、范围、像素边界和裁剪比较 |
| web/components/ai-search/SingleCropSelector.tsx | 单区域包装，复用已有框选组件 |
| web/components/visual-search/RegionSelector.tsx | 复用坐标函数、可选比例门槛、禁用与绘制状态保护 |
| web/components/ai-search/PreviewCrop.tsx | 通用样式入口及一致像素换算 |
| web/components/ai-search/ai-search.css | 原布局内的框选与复制样式 |
| web/components/ai-search/SearchResultCard.tsx | 更换图片按钮文字 |
| web/lib/ai-search.ts | 空名称使用货号，提取真实已确认候选货号 |
| web/tests/ai-search.test.mjs | 新增单框及复制保护测试，保留原测试 |
| api/tests/test_visual_search.py | EXIF/裁剪/Provider 请求内容离线测试 |
| docs/ai-search-v022.md | 本报告 |

无后端算法或路由修改。待用户完成真实网页验收；本阶段到此暂停，不自动发起付费查询、部署或扩展多商品功能。

# AI 找货助手 V0.3-01：电脑端多区域 Mock 预览

日期：2026-10-13。基线 defa840。本阶段仅实现本地开发交互预览，没有付费查询、后端变更、索引重建、生产访问或库存业务写入。Phase 1.11 困难样本计划保持原样。

## 打开预览

```powershell
cd D:\inventory-system\web
pnpm dev --port 3002 --hostname localhost
```

开发地址：<http://localhost:3002/ai-search-preview/>。若已有同项目的开发服务运行，直接打开，不需要再启动一个进程。localhost 与现有开发服务主机名一致；不要为本阶段修改 Next 开发来源或视觉 API 鉴权规则。

正式 /ai-search 继续使用 V0.2 的真实搜索和反馈流程，未切换为 Mock。预览不加入正式导航的新入口；预览页面沿用 AI 找货菜单选中样式。开发模式之外，路由调用 notFound。导入 Mock 工作区放在明确的 development 分支内，使生产编译能够移除该导入。

通过隔离 Next.js 16.3.5 静态导出实测：该预览路径仅导出 404 内容，不渲染 Mock 页面，导出脚本不含 Mock 工作区标记。静态主机的 HTTP 状态码取决于其 404 配置，不能仅据 HTML 内的 404 字样断言 HTTP 状态；本阶段没有改动或部署生产静态主机。完整项目的正式构建仍受既有 TypeScript 问题影响，隔离导出检查不能冒充完整项目构建通过。

## 页面与操作

- 单款找货：上传原图，点击开始找商品，查看 Mock Top5，人工确认；不要求框选。
- 多款找货：切换模式，点击框选下一个商品，用鼠标拖动创建区域，检查裁剪预览，再点击找这个商品。每次只模拟当前区域，最多 10 个区域，不自动搜索全部。
- 左右双栏约 60% / 40%，左侧保留大幅原图与编号框，右侧为当前裁剪与结果。第一候选突出，其他四款默认折叠，通过只读资料弹窗也可确认。
- 确认后加入底部本次清单。不同区域选中同一商品仍保留独立条目，不推断箱数或合并商品身份。清单展示主图、货号、尺寸、包装规格、示例仓库及区域编号。
- 切换区域只读取已有结果；不会启动新搜索。重新框选会增加裁剪修订版本并清空当前有效结果，旧查询仍留在区域历史中。已确认区域需先从清单移除，才能重新框选或删除。
- 移除清单项需要确认，仅影响该区域的本地 Mock 清单状态。删除未确认区域不影响其他区域。更换有查询或区域记录的照片前，需要明确确认清空旧区域、结果和清单；取消保持旧照片。
- 根布局内的独立预览会话保留原始 File、区域和结果，页面内部导航返回可保留，刷新清空，不写浏览器持久存储。照片预览对象 URL 在更换或会话卸载时释放。
- 页面持续标识 Mock 开发预览，示例商品使用 mock 命名空间、DEMO 货号、内联 SVG 图片。它不识别真实上传照片，Mock 推荐只按区域编号轮换；不能作为准确率或真实 API 性能证据。
- 商品名为空时使用货号作为标题。无实时库存、价格、出库、编辑、自动检测或自动扣库存功能。

本阶段电脑端优先；仅保留窄屏基础换行，没有手机专项、触控或手势缩放设计及验收。

## 组件与状态

AISearchPreviewWorkspace 组织预览操作；PreviewCrop 按原图比例展示裁剪；ConfirmedProducts 展示清单。ImageUploader、SimilarProducts、ProductReadonlyModal、ProductImage 与 AppLayout 复用现有组件。

RegionSelector 增加可选 drawing、numbers、showManual 参数，旧页面默认行为保持；预览在非框选状态恢复普通滚动。区域显示编号与辅助标签使用同一稳定编号，删除后不重新编号。

AISearchSessionProvider 保留 V0.2 的原有状态与方法，只在 development 为子页面提供独立 AISearchPreviewSessionProvider。两套会话不共享 File、结果或确认ID；Mock 函数不会导入真实搜索方法。

ai-search-preview-session 定义纯 reducer：会话ID、单款整图区域、多区域稳定ID、坐标、修订版本、状态、逐次查询历史和清单。查询完成和确认必须核对 sessionId、regionId、revision；查询响应还需对应 requestId。当前选中区域不作为结果归属依据。

lib/mock/ai-search-preview 内的搜索/确认仅使用定时器，没有 HTTP。查询ID以 mock- 开头，候选身份以 mock: 开头，不能经过生产数据校验器或提交真实反馈。Mock 状态、资料、图片均与 production-catalog 隔离。

## 验收证据

自动化：`node --test tests/ai-search-preview.test.mjs tests/ai-search.test.mjs`，18 项通过。其中保留 V0.2 的请求与来源隔离测试，新增单款/多款、独立 Top5/query ID、裁剪失效、晚到响应、跨区域确认、重复商品、清单移除、10 区域限制、失败、串行及生产路由拒绝测试。Mock 函数在 fetch 被设为禁止调用时仍可完成测试。

桌面浏览器：已验证单款直接搜索、三个鼠标区域及各自结果、三项不同商品确认、模式/区域切换、重新框选、候选详情、同商品多区域独立关联、清单项移除、一次模拟失败不自动重试，以及删除失败区域仍保留原三项。预览页面真实 API 请求为零；正式 /ai-search 另作只读页面检查，没有点击真实搜索。

| 请求的浏览器视口 | 原图栏宽 | 结果栏宽 | 水平溢出 | 最终确认项 |
| --- | ---: | ---: | --- | ---: |
| 1920×1080 | 967.59px | 645.06px | 无 | 3 |
| 1366×768 | 637.59px | 425.06px | 无 | 3 |

左右宽度比例均为 1.5。照片区域会随可用宽度缩放，底部清单通过页面纵向滚动查看。截图工具的图片会裁去滚动条等边缘，图片像素不等于设置的完整视口；1920 视口输出 1905×1072，1366 视口输出 1351×760。

截图、布局数据、类型诊断与隔离导出工程保存在 Git 忽略的 api/data/visual-search/field-evaluation/v03-preview-acceptance：desktop-1920-viewport.jpg、desktop-1920-confirmed.jpg、desktop-1366-viewport.jpg、desktop-1366-confirmed.jpg、layout.json、types-before.txt、types-after.txt、tsconfig.json、production-guard/。

专项 TypeScript 检查通过。全项目基线 102 条错误，本阶段对复用 RegionSelector 的既有数字输入回调补齐 number|null 类型后剩余 101 条；归一化文件位置后对比没有新增诊断，其余诊断未变。不涉及全项目类型清理。全项目 ESLint 0 错误，仅保留 ProductImportWorkspace 中已有的一条导航警告；提交前 git diff --check 通过。

## V0.3-02 接口接入边界

1. 页面设计验收后再接真实 API，不在本阶段切换正式 /ai-search。
2. 每个区域调用一次现有 searchVisual(originalFile, crop, production-catalog)，单款 crop 为空；显式保留 aliyun-inventory-production、模型和索引来源校验。不能把 Mock 的ID、图片或返回值当作生产资料。
3. 每次真实查询绑定自己的 query_id、区域ID、修订版本与来源上下文；切换区域或更换照片后，晚到响应不能覆盖当前结果。详情和反馈必须绑定原查询上下文。
4. 真实反馈成功才加入清单；反馈重试不能触发搜索。现有反馈是每 query_id 的当前选择，不是清单同步接口。移除清单项不应伪造 no_match 或自动删除历史反馈。
5. 网络中断/取消不能证明服务端未收费；不得自动重发。保留请求状态与费用记录，真实验收的对象数量及预算另行确认。
6. 增补真实照片 EXIF 方向、裁剪像素一致性及小商品主体完整性验收；独立状态的 Mock 测试不能证明真实模型效果。

本阶段结束后停在 UI 审核，不自动接真实 API、不部署、不推进出库或下一阶段。

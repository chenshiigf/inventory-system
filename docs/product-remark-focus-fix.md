# 备注快捷编辑首次打开跳顶修复

日期：2026-10-13。基线：3025563。仅修复焦点行为，不修改布局、备注提交、业务数据、鉴权、AI 找货或生产环境。

## 确切根因

ProductRemarkEditor 的 Input.TextArea 使用 autoFocus。Ant Design 的 Popover 首次懒挂载时，内容先被放在屏幕外测量，随后定位。React 在首次挂载就调用原生 focus()，没有 preventScroll，浏览器于是滚到屏幕外输入框所在方向的页面边界。

使用原组件副本、50 行合成商品的隔离 Mock 页复现：点击前 scrollY 约2149.33px；首次 TEXTAREA focus.before 的 top 约-9306.67px、left 约-12637.33px、preventScroll=false；focus.after 的 scrollY 立即变成0。当前 URL 不变，DOM 观察未发现表格被移除。这不是根据 autoFocus 字样猜测原因，而是原生 focus 调用前后的实测记录。

其他排查结果：编辑按钮实际为 type=button，无 href、默认表单提交或详情跳转。打开仅更新当前备注组件状态，不调用父列表保存、分页或筛选处理，不改变选中商品。ProductTable 未设置 scroll 参数；安装版本的 Table 在分页/排序/筛选改变时才进入 scrollToFirstRowOnChange 分支，本次不触发该路径。InventoryWorkspace 的首次页面加载及详情返回滚动逻辑没有因备注打开而重挂载或重新触发，也未修改。

## 最小修复

- 移除 TextArea autoFocus，使用 Popover afterOpenChange：完成定位与打开动画后，调用 TextAreaRef.focus({preventScroll:true})。
- 关闭时，若焦点仍在编辑框内或因隐藏/禁用已回到 body，用 preventScroll 返回编辑按钮；若用户已点击外部控件，不抢回焦点。
- 保存失败后，禁用控件可能已失焦到 body。输入框重新启用后，用同样的 preventScroll 恢复输入焦点；不抢占外部控件焦点。
- 无 window.scrollTo、scrollIntoView、滚动位置缓存或每帧强制恢复。鼠标与键盘均保留正常操作路径。

不修改 saveProductRemark、编辑状态 reducer、列表更新策略、包装规格或后端。保存仍仅提交 remark，原有新增、修改、清空、取消及失败保留草稿逻辑继续沿用。

## 回归验收

1. 前端：`cd web; node --test tests/product-remark-focus.test.mjs tests/product-remarks.test.mjs tests/inventory-edit-navigation.test.mjs tests/ai-search.test.mjs tests/ai-search-preview.test.mjs`，39项通过。新增4项执行实际组件的焦点回调，覆盖首次挂载不聚焦、每次打开 preventScroll、关闭键盘返回/不抢外部焦点、失败后恢复输入焦点，以及无强制滚动、路由和默认提交。原有 remark-only、包装/库存不变、权限失败、清空及 AI 回归保留。
2. 新组件专项 TypeScript 通过；完整项目仍为原有101条诊断，除行号外逐条一致，新增0。修改文件 ESLint 与 git diff --check 通过。未声称全项目正式构建通过。
3. 隔离 Mock 网页量化验收共9个记录，所有被测操作前后 scrollY 完全一致：底部首次鼠标打开及键盘取消均为4309.33349609375；最终组件刷新后中部首次鼠标打开、重新打开、新增保存、修改保存、失败、清空保存及取消均为2038.6666259765625。
4. 底部 Tab→取消→Enter 后焦点返回该编辑按钮。中部保存成功/取消后也返回按钮；失败时输入仍为“失败保留输入不跳顶”，原列表备注仍为“修改备注不滚动”，焦点为TEXTAREA，scrollY不变；清空成功恢复“—”。全部操作只改变 Mock 本地状态，不发 HTTP 写入，不读写业务数据库、不调用模型。

量化操作采用原生鼠标/键盘；自动化定位器在点击/填值前可能自行滚动，因此没有将其产生的坐标变化冒称应用结果。焦点前后日志独立确认 preventScroll=true，输入框已经位于可见范围后才聚焦。

Mock 工程沿用 Git 忽略目录下的前端组件副本，没有增加正式预览路由。诊断、九项结果 JSON 和截图仅保存在 api/data/visual-search/field-evaluation/remark-focus/。网页审批服务曾因容量及超时暂时失败；操作没有绕过审批，服务恢复后完成剩余验收。

## 修改文件和用户验收

仅提交：

- web/components/inventory/ProductRemarkEditor.tsx：焦点时机、preventScroll及键盘焦点恢复。
- web/tests/product-remark-focus.test.mjs：新增焦点回归测试。
- docs/product-remark-focus-fix.md：本报告。

原有 api/README.md、docs/visual-search-v0.1.md 改动及 web/inventory-web-fixed.tar.gz 保留，不混入提交。

用户在本地 /products/ 刷新后先滚动到中部，再首次点击备注编辑；保持原位置且输入框可直接输入。保存、取消、再次打开、清空保存均应不跳顶。再刷新后滚动到底部，验证第一次打开和键盘 Tab/Enter 操作。可以仅在开发工具阻断商品 PATCH 来模拟失败，确认草稿和滚动保留，再解除阻断手动重试；不要为验收停用生产服务或修改真实库存。

完成后暂停等待用户真实网页验收，不部署。未来前端构建仍必须在 Windows 本地或资源充足环境执行，禁止在低配生产服务器运行 Next.js / pnpm build。

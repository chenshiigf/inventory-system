# 商品库存列表：备注快捷编辑

日期：2026-10-13。基线：ecbf878。仅修改表格备注列的交互；未改商品更新接口、数据库结构、包装删除保护、AI 找货功能或生产配置。没有付费模型调用或部署。

## 操作和数据边界

原有备注文字、长备注悬浮提示及空值“—”保留，旁边增加低干扰编辑图标。点击打开 Ant Design Popover，包含多行输入、2000 字上限、保存和取消。可新增、修改和清空。保存期间禁用输入及重复提交；保存成功关闭弹出框，只更新当前列表中相同商品 ID 的备注，不重置页码、筛选、顺序、选中项或详情入口。失败保留草稿和编辑框，并提示错误；用户可修正或手动重试，没有自动重试。取消不请求 API。

快捷编辑仅复用 PATCH /api/products/{id}，请求 JSON 只有 remark 一个字段，去除首尾空白后为空则发送 null。禁止把完整商品或编辑表单展开到请求中；不携带 packagings、库存、图片、价格、规格等字段。

列表即时更新只替换 remark，其他字段及包装对象保留。轻量版本记录防止已在途的旧分页/筛选 GET 覆盖刚保存的备注；新发起的 GET 仍使用后端最新值。页面内缓存不写浏览器持久存储，也不改服务器数据源。

权限继续复用普通商品编辑的同一 API 及请求封装。现有生产权限由 Nginx server 层 Basic Auth 保护，包含 /api；普通商品 PATCH 没有新建独立角色权限，本轮不改变它，也没有注入管理员密码或凭据。401/403 时不更新列表、保留输入并提示登录或无权限。权限失败由 Mock HTTP 测试覆盖，没有访问生产进行鉴权测试。

## 验收结果

- 前端命令：`cd web; node --test tests/product-remarks.test.mjs tests/inventory-edit-navigation.test.mjs tests/ai-search.test.mjs tests/ai-search-preview.test.mjs`，35 项通过，新增 6 项。验证新增/修改/清空只发送一次 remark-only PATCH；401、403、500、网络中断不自动重试且保留草稿；取消、保存锁、目标 ID 映射、包装和库存不变、旧 GET 防覆盖、长度及响应 ID 检查。
- 后端命令：`cd api; .venv/Scripts/python.exe -B -X utf8 -m pytest tests/test_product_packagings.py tests/test_inventory_movements.py tests/test_product_detail.py -p no:cacheprovider --basetemp D:/inventory-system/api/data/visual-search/field-evaluation/remark-quick-edit/pytest -q`，84 项通过，2 条既有依赖弃用警告。新增 7 项，所有写入均使用临时 SQLite。
- 新增后端成功测试从空备注开始，依次新增、修改、清空，覆盖 null、空串和空白串；商品有 121 箱库存和已有流水。完整包装及流水表逐字段相同，商品读取结果除 remark 和 updated_at 外全部相同。
- 失败覆盖超长、错误类型、不存在的商品及数据库提交失败。提交失败用例先 flush 真正执行商品 UPDATE 再抛异常，HTTP 500 后核对原备注、包装、箱数、历史流水和商品资料全部回滚。真正删除有库存包装仍被 409 拦截，ecbf878 的回归测试保留。
- 专项 TypeScript（新组件、辅助逻辑和 API 依赖）：通过。全项目修改前后仍为 101 条既有诊断，除行号外逐条一致，新增 0；本轮不修复这些全局类型问题，没有声称正式构建通过。
- 修改文件 ESLint、git diff --check：通过。
- 隔离纯 Mock 网页使用正式 ProductTable 和 ProductRemarkEditor 组件副本，验证新增、修改、失败保留输入、清空、取消及保存中禁用。合成样本保留 121 箱和另一行原备注；四次 Mock 保存（含一次失败），取消不新增请求。该回调不发 HTTP、不访问业务数据库，不作为真实生产验收结果。

Mock 网页、截图、临时数据库及类型诊断只存 Git 忽略目录 `api/data/visual-search/field-evaluation/remark-quick-edit/`，不添加正式预览路由。

## 修改文件

1. web/components/inventory/ProductRemarkEditor.tsx：备注展示及轻量编辑框。
2. web/lib/product-remarks.ts：remark-only API、编辑状态、错误提示和列表更新保护。
3. web/components/inventory/ProductTable.tsx：备注列接入编辑组件。
4. web/components/inventory/InventoryWorkspace.tsx：保存成功即时更新备注，防止旧列表响应覆盖。
5. web/app/globals.css：备注旁按钮及弹出编辑框局部样式。
6. web/tests/product-remarks.test.mjs：前端回归。
7. api/tests/test_product_packagings.py：成功及失败时数据库前后对比。
8. docs/product-remark-quick-edit.md：本说明。

原有 api/README.md、docs/visual-search-v0.1.md 和 web/inventory-web-fixed.tar.gz 保留，不纳入本次提交；不提交 .env、htpasswd、图片或真实数据。

## 用户网页验收

1. 打开本地 /products/ 表格视图，在一个测试商品的备注旁点击编辑图标。空备注填写后保存，确认当前列表立即显示；再次修改、保存并重新打开检查内容。
2. 清空输入保存，列表恢复“—”。输入其他内容后点击取消，原备注不变。
3. 保存时检查按钮和输入框禁用；Network 中 PATCH body 仅为 remark，不含 packagings。记录测试商品的原包装 ID、箱数和流水，更新后核对没有变化。
4. 可在浏览器开发工具只阻断商品 PATCH 来模拟网络失败，确认草稿与错误提示保留；解除阻断后手动保存。不要停止或改动生产服务来制造失败。
5. 检查原有筛选、分页、详情链接和编辑商品入口。原权限仍生效，未经网关认证的生产请求不会保存；不要在聊天或截图中展示认证凭据。

等待用户验收，不自动部署。未来前端仍只能在 Windows 本地或其他资源充足环境构建，禁止在低配生产服务器执行 Next.js / pnpm build。

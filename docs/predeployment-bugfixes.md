# 上线前两个 Bug 的修复与验收

日期：2026-10-13。基线：fdb9d97。仅本地开发及隔离 SQLite 测试，不访问生产服务器，不修改现有业务数据库、库存、流水、生产图库或视觉索引，不进行付费搜索及部署。

## BUG-01：普通商品资料保存误触发包装删除保护

ProductEditorModal 的 initialValues 含包装 ID，但 Form.List 内只注册 packingQty，没有注册 id。使用项目实际安装的 Ant Design 底层 FormStore 进行 validateFields：注册前返回 packingQty，丢失 id；注册隐藏 id 后返回两者。库存列表和详情编辑均使用该表单，并将包列表提交给 PATCH。

因此根因并非后端把省略 packagings 误判为删除，而是前端提交了缺少原包装 ID 的 packagings。后端将没有出现在请求中的原 ID 识别为删除；121 箱库存时，409 保护是正确行为。零库存时也存在误重建风险。

修复：每行注册隐藏 ID；两个编辑入口共用 packagingUpdateFields。ID、装箱数与顺序均未变化时省略 packagings，让 PATCH 完全不处理包装。真实规格新增、修改、排序或删除仍发送完整列表，保留已有 ID；新规格不猜测 ID，且不发送箱数。没有按位置或装箱数推断身份。后端代码、数据库结构及有库存不得删除的保护未改动。

后端新增 12 组普通资料测试（0/121 箱 × 备注新增、备注清空、尺寸、单位、价格、图片），每组先建立一条真实测试历史流水，再比较全部包装与流水表字段，同时捕获 SQL：没有这些表的 INSERT、UPDATE 或 DELETE。原商品 ID、货号、总箱数及包装列表一致，仅目标普通字段变化。

另新增旧错误 payload 的复现测试：包装丢失 ID 且仍有 121 箱时返回 409，备注与包装全部回滚。既有真正删除有库存规格的测试及盘点归零后删除的测试继续通过。所有写入只发生在 pytest 临时数据库。

## BUG-02：导航高亮回落到商品库存

AppLayout 已从 usePathname 取路由，不是点击状态未更新；问题是若干菜单使用精确无尾斜杠路径匹配。Next.js 生产静态导出设置 trailingSlash=true，/inventory-movements/ 等地址匹配失败，原逻辑默认高亮 inventory。

修复：统一去除尾斜杠，仍由实际 pathname 派生 selectedKeys；导入路由优先于 /products，商品详情归属商品库存，未知路由不误高亮。开发预览仍只在 development 对应 AI 找货。菜单顺序、图标、样式、Link 地址未变。

测试覆盖全部 8 个菜单的有/无尾斜杠路径、商品详情、导入优先级、来回切换、未知相近路径及开发预览边界。真实本地网页只读验证商品库存到库存流水、尾斜杠库存流水、返回商品库存、分类、仓库及批量导入；没有在现有业务库保存商品。

## 检查结果

- 前端：`cd web; node --test tests/inventory-edit-navigation.test.mjs tests/ai-search.test.mjs tests/ai-search-preview.test.mjs`，29 项通过，其中新增 5 项。保留 AI V0.2.2 及 V0.3 Mock 的原测试。
- 后端：`cd api; .venv/Scripts/python.exe -B -X utf8 -m pytest tests/test_product_packagings.py tests/test_inventory_movements.py tests/test_product_detail.py -p no:cacheprovider --basetemp D:/inventory-system/api/data/visual-search/field-evaluation/predeploy-bugfix/pytest -q`，77 项通过，2 条既有依赖弃用警告。
- 专项 TypeScript（新增辅助函数与 AppLayout）：通过。完整项目检查仍有 101 条既有错误；修改前后除位置外的所有诊断逐行一致，新增 0。受影响的旧商品组件已有类型问题也纳入完整比较。本轮不清理这些全局问题，没有声称正式构建通过。
- 修改文件 ESLint 通过；git diff --check 通过。
- 诊断文件和导航截图仅存 Git 忽略的 `api/data/visual-search/field-evaluation/predeploy-bugfix/`。

## 修改文件

1. web/components/inventory/ProductEditorModal.tsx：注册包装 ID。
2. web/lib/inventory-products.ts：未变化包装不进入 PATCH。
3. web/components/inventory/InventoryWorkspace.tsx：列表编辑接入辅助函数。
4. web/components/inventory/ProductDetailWorkspace.tsx：详情编辑接入辅助函数。
5. web/lib/navigation.ts：路由对应菜单键。
6. web/components/app-layout/AppLayout.tsx：selectedKeys 取当前路由键。
7. web/tests/inventory-edit-navigation.test.mjs：表单真实提交行为、payload 及导航回归。
8. api/tests/test_product_packagings.py：完整数据库前后对比及删除保护回归。
9. docs/predeployment-bugfixes.md：本报告。

本轮前已存在的 api/README.md、docs/visual-search-v0.1.md 改动及 web/inventory-web-fixed.tar.gz 保留，不纳入本次提交。

## 用户网页验收

在本地 /products/ 选一个已有库存、已关联分类和仓库的测试商品。先记录货号、包装规格、箱数和流水条数；只修改备注并保存，重新打开确认备注生效、包装和库存不变、没有新增流水。再从详情页编辑备注，验证相同结果。Network 中未改规格的 PATCH 不应出现 packagings。

在本地测试商品中确实尝试删除仍有库存的规格时，应被页面或后端拦截；不要为验收调整真实库存为 0。删除保护已经在隔离数据库完成自动测试。

来回点击商品库存和库存流水，并直接打开 /inventory-movements/、刷新或用浏览器返回。选中项应始终对应当前页面，再检查分类、仓库、批量导入等菜单。验收后再决定后续上线步骤。

不自动部署。未来前端构建必须在 Windows 本地或其他资源充足环境完成，禁止在现有低配阿里云生产服务器执行 Next.js / pnpm build。完整项目既有类型错误仍需在正式构建准备阶段处理。

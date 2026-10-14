# AI 找货上线准备第 3 步：运行开关、费用与权限保护

本步骤仅增加本地代码和 Mock 验证，不包含生产配置或部署操作。

## 运行开关

开关文件位于数据根目录下的 `visual-search/runtime/ai-search.enabled`。文件不存在、内容错误、不可读或路径为符号链接时均视为关闭；新环境默认关闭。FastAPI 每次处理搜索请求都会检查开关，因此创建或删除文件不需要重启 API 或重新部署前端。关闭时新搜索返回 HTTP 503；已经发往百炼的请求无法撤销，最多允许当前在途请求结束。

受信任的本机/服务器操作员可使用脚本切换：

```powershell
api/.venv/Scripts/python.exe api/scripts/set_visual_search.py enable
api/.venv/Scripts/python.exe api/scripts/set_visual_search.py disable
```

生产环境应由运行 FastAPI 的系统账号执行脚本，并显式传入与服务 `INVENTORY_DATA_DIR` 完全一致的数据目录：

```bash
sudo -u inventory /srv/inventory-system/api/.venv/bin/python \
  /srv/inventory-system/api/scripts/set_visual_search.py disable \
  --data-dir <服务使用的数据根目录>
```

网页没有开关或配置写入接口。家庭用户的 Basic Auth 账号只能搜索、读取商品资料、查看自己的查询图片及提交确认反馈。

## 付费调用保护

百炼 Provider 每次只发出一次 HTTPS 请求，不论 HTTP 429/5xx、超时还是传输错误都不自动重试。`VISUAL_EMBEDDING_RETRIES` 仅为兼容旧配置而保留，程序读取环境变量时强制设为 0。

每日费用预算和单次费用预留必须全部有效，缺失时拒绝新模型调用。每日调用次数是可选项，留空表示不设次数上限：

```dotenv
VISUAL_DAILY_MAX_CALLS=
VISUAL_DAILY_BUDGET_CNY=1.00
VISUAL_CALL_RESERVE_CNY=0.003
```

调用次数、保守占用金额、已知实际费用和未知用量次数保存在独立的 `visual-search/daily-budget.sqlite3`，不写入库存业务库或向量索引库。账本按北京时间自然日统计。发起外部请求前先原子写入一次调用和单次费用预留；失败、超时、进程中断或用量缺失不会退回预留。收到有效 Token 用量时记录实际金额，但保守预算仍至少扣除单次预留；实际费用高于预留时将超额补记，并阻止达到上限后的后续调用。账本损坏、权限不足或数据库异常均 fail closed。

本地生产索引库当前保存的 13 条成功搜索调用记录，按百炼官方北京单价重新估算，平均约 ¥0.001416/次，最高 ¥0.0018586/次（图像 Token 为 576–1024，文本 Token 为 22）。因此将单次预留建议为 ¥0.003，约为历史最高值的 1.6 倍；每日 ¥1 在历史用量范围内约可预留 333 次，不会再被固定次数限制为 20 次。每日调用次数留空即表示不限制，次数仍会记录。

该预留按已观察到的查询规模设计，不是单次账单硬上限。[阿里云模型信息](https://help.aliyun.com/zh/model-studio/qwen3-vl-embedding)给出 32,000 Token 最大输入和北京图片输入 ¥1.8/百万 Token；理论上单次图像费用可达 ¥0.0576。若请求实际费用超过 ¥0.003，系统在响应后补记实际超额并拒绝当日后续请求，但当前这一笔可能使每日实际费用短暂超过 ¥1。若必须把单次超额风险压到理论输入上限内，预留需提高到约 ¥0.06，按 ¥1 预算将只允许约 16 次预留；这会形成由成本预算导致的有效次数限制。

核对时发现，Phase 1.11 旧报告记载 13 次查询估算 ¥0.0130072；本地生产索引库中与 13 条查询逐一关联的 Provider usage，按当前官方价格重算为 ¥0.0184108。此次预留建议采用本地逐请求 Token 记录和当前官方价，旧报告与调用记录的差异仍需另行核对。实际以百炼账单为准。

## 权限和密钥

验证用户名和密码仍使用既有 htpasswd 文件。`VISUAL_ADMIN_USERS` 仅授权索引状态、重建和任务查询；有效 htpasswd 用户可使用搜索、商品只读资料、查询图片及反馈接口。运行开关和预算只能由本机/服务器操作系统权限管理，HTTP API 不提供修改入口。

`DASHSCOPE_API_KEY` 只由后端进程读取；配置对象隐藏其 repr，Provider 不记录请求头、请求体、Base64 图片或密钥。前端不需要密钥，也没有 `NEXT_PUBLIC_*` 密钥变量。部署密钥应继续放在仓库外的 systemd EnvironmentFile 中，不能作为脚本命令行参数。

当前本地仓库没有 GitHub Actions 工作流或生产 `inventory-deploy-actions` 脚本，因此无法从本地版本核实这些外部发布组件的日志和构建产物处理。统一部署前仍需只读审查当前线上工作流与服务器脚本，确认它们不回显环境值、不把环境文件打入构建产物。

## 启动隔离

AI 专属环境变量解析或本地视觉索引初始化失败时，应用记录不包含异常内容的通用错误，并将视觉搜索置为不可用；FastAPI 库存 API 继续启动。视觉搜索请求在开关、API Key、预算、索引或凭据配置无效时返回 503。图片和模型请求仍在现有单 worker、单搜索许可内运行。

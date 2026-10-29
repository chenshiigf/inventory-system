# C2 停机前私有排空门禁

默认关闭：未配置 `INVENTORY_MAINTENANCE_SOCKET` 时不创建控制 socket，不增加维护中间件，原业务行为保持不变。现有代码的 close 等待没有上界，单靠数据库无 running 行或 HTTP 安静不能证明可以停止。

启用需要**另行授权、独立 API 小版本部署**，不包含编号迁移、换号或本次生产操作。使用原单 worker 架构；先核验部署版本、Python、进程数、实际用户和私有控制目录。目录由受控运维准备，owner 为 root/API 用户、不可被组或其他用户写入，socket 为 0600；不改现有库、锁文件权限。控制 socket 拒绝覆盖已有路径，内核 SO_PEERCRED 只允许本机 root 请求，CLI 双向核对 MainPID 与随机 nonce。没有公网维护路由。

另行部署验收后，root 在已冻结全部外部 CLI、镜像同步、备份、timer、部署入口且无人操作的受控会话中执行以下命令。`PY`、`CURRENT_API`、`MAINTENANCE_SOCKET` 来自当次部署核验；不能沿用旧 PID。

```bash
PID=$(systemctl show inventory-system-api.service -p MainPID --value)
"$PY" -B "$CURRENT_API/scripts/maintenance_control.py" begin \
  --socket "$MAINTENANCE_SOCKET" --pid "$PID" --timeout-seconds 90
# begin 返回 BLOCKED 可为正在排空；API 保持运行。未到截止时可读取 status。
"$PY" -B "$CURRENT_API/scripts/maintenance_control.py" status \
  --socket "$MAINTENANCE_SOCKET" --pid "$PID"
```

begin 原子关闭所有新 HTTP/WS 入口（HTTP 503，包括新查询及取消请求），已有请求继续完成；已接纳请求可提交其必要任务。无在途请求时也拒绝后台任务、AI 调用和源初始化新进入。统计直到 ASGI 完成及 Future 的 finally 真正完成，周期清理同样纳入。内部预览取消仍为原协作机制，门禁不强杀、不删除锁、不更改 close 等待。

SAFE_TO_STOP 必须同时满足：本次维护未超过 90 秒期限；无 HTTP、queued/running/pending Future、AI/源初始化/清理活动；预览终态且会话保留与 heavy 锁均释放；取得并持有两个**已有** calls.lock；只读检查预算 v1 无 reserved/未知结算；现有索引无未结束任务。检查不初始化、恢复或修改数据库。没有预算/锁文件、日志状态异常、锁忙、检查超时/异常等均 BLOCKED，不能补造文件。锁持有至 resume 或进程正常关闭，防止已知 AI 调用路径重新进入；外部写入来源的冻结仍需单独签字。

begin 不能延长期限；超过期限即使工作随后结束仍 BLOCKED。拒绝、异常、超时、未知状态时保持 API 运行，**禁止调用 systemctl stop**。ASGI 异常/取消或审计异常会留下本进程故障标记，resume 不会把未知工作伪装为已安全；此时终止窗口申请并查明该项，不能通过重启绕过。审计在独立线程执行，不阻塞事件循环；审计线程未结束时拒绝 resume。

放弃本次排空时，人工确认后以同样身份/PID执行 `resume`，释放门禁持有的锁、重新开放请求（不修改原 AI 开关）。未得到 RUNNING 不宣称恢复完成。只有实时 status 成功退出 0 才可进入原清单 `c2_stop_api`，它再次检查状态与 PID；不复用缓存报告。安全结论仅覆盖该 API 及已知调用路径，不证明外部进程冻结、跨库一致性或生产磁盘事务能力。

Linux 测试只使用合成数据：真实私有 socket/CLI、实际预览协作取消与 finally、实际 VisualService.rebuild→Future→索引完成→费用结算；真实等待超过 90 秒验证停止入口不执行 stop，API可 resume。比对合成商品、逐包装/仓库库存与流水及 SHA256，不代替生产部署后的配置与停写验收。维护前仍需 SOURCE_SAFE 人工审核及原有 timer 独立恢复决定；绝不覆盖生产库。

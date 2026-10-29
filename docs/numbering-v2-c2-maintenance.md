# C2-2：一次性一致备份执行清单

**状态：仅本地准备；未授权执行。** 编号业务基线固定为 `d2873699032057717fbbad9c077275180883fb39`，本准备分支不改 B 逻辑。本清单只冻结、复制、验证、恢复原版经营，不迁移、不换号、不部署。完整部署器及日/周备份单元均不作为四库一致备份入口：其范围不足，且包含清理、上传或启动服务动作。

**维护执行仍须单独授权。** Linux 基础验证已由 PR #6 完成；后台等待没有固定上界，停止前必须使用[私有排空门禁](numbering-v2-c2-drain.md)。该门禁需要独立 API 小版本部署及运行时验收，本次不部署；生产未具备门禁前保持 BLOCKED。

## 开始门槛与人工签字单

以下全部填妥才可安排窗口；当前未填项为 **PENDING，禁止开始维护**。

| 项目 | 必须确认 |
| --- | --- |
| 授权与人员 | 另行明确授权停写、开关、备份和原状态恢复；店内无人操作，有现场联系人和原 SSH 维护终端 |
| 执行身份 | 使用现有、已验证的 root 维护会话；不 sudo、不更换 SSH 身份。只用于服务管理、读取和新建私有备份，不改生产 owner/mode/ACL |
| 写入者签字表 | API、AI 调用/索引/反馈、导入/清理线程、镜像同步、所有 CLI、cron/at/用户及系统 timer、备份/uploader、Actions/人工部署；每项写明实际入口、阻止再启动的方法、排空证据、恢复方法和负责人。未发现不等于不存在；无法映射即停止 |
| 路径与工具 | 重新核实四库、source/binding、两个调用锁、部署锁、uploads、必要 import-previews 及 visual 全树；确认范围内没有凭据或无关资料，发现即停，不能删除源文件或临时跳项。工具在独立位置逐文件校验，Python/SQLite 版本记录；先在独立 Linux 合成环境验证实际 flock/FD9 继承、cgroup 和目录 fsync，本地 Windows 模拟不能替代。传入/执行该准备工具仍需另行授权，本次不上传 |
| 原始状态 | API 应为 active/running；timer 可 active/waiting 或 inactive，逐项记录 ActiveState、UnitFileState、NextElapseUSecRealtime、LastTriggerUSec、Persistent；原 AI 标记有效或确实不存在。出现 failed/activating、非法标记等不开始 |
| 时间和容量 | 填排空时限、维护截止 UTC、两个 timer 的下次触发 UTC、空间/inode 安全余量、数据量/文件数与预估耗时。无实测不承诺固定分钟数；优先选择不跨定时触发点的窗口 |

历史 C2-1 的同设备 ext4/64771、约 26 GB 余量、无 sidecar 都须重查。`catalog.sqlite=root:inventory 640`、目录 `750` 意味着普通 inventory 无写库/建 journal 权限；root 可备份不代表未来换号权限、fsync、super-journal 或跨库原子性通过。未来换号身份单独验收，不在此阶段解决权限或事务问题。

## API 停止安全关口（必须在维护前完成）

- 已确认的 90 秒是 systemd 停止超时，**不是保证应用会安全退出的时限**。`control-group` 默认向整个 cgroup 发 SIGTERM；若进程仍在且 `SendSIGKILL=yes`，超时后会发送 `FinalKillSignal`（默认 SIGKILL）。默认值不是现场确认值；`SendSIGKILL`、`FinalKillSignal`、`KillSignal`、`TimeoutStopFailureMode` 及自定义 ExecStop/StopPost 是否存在仍须复核。仓库模板无自定义 ExecStop；不能假定现场完全一致。有自定义停止命令时须单独分析其超时，不能把 90 秒当整个流程的实测耗时。[systemd 255 停止超时](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html#TimeoutStopSec=)、[终止信号规则](https://www.freedesktop.org/software/systemd/man/255/systemd.kill.html)。
- 本地代码 `app/main.py` 在 lifespan 退出时依次等待导入任务、视觉服务和来源服务关闭；导入/视觉线程池使用 `shutdown(wait=True)`，代码没有给出统一的 90 秒内完成上界。不能因为模型单次请求有 timeout，就认定排队、重试、导入、清理和关闭总耗时有界。
- 维护前只读复核当前部署版本及上述非敏感属性，管理员审查实际停止命令的存在性/摘要（不公开完整命令行或凭据）。在**独立 Linux、同一当前生产版本与依赖、相同停止策略**下用合成库、假模型验证排空后的退出路径，记录请求/任务/结算归零证据、SIGTERM 到退出的耗时、子 cgroup 与日志状态、相对 90 秒的已批准余量。不得用 B 版本或一次空载成功代替当前生产版本验收；模拟 systemctl 仅验证解析，不能证明应用关闭。
- 正常退出验收：stop 成功；`ActiveState=inactive`、`MainPID=0`、`Result=success`；退出状态为已在隔离环境验证过的正常形式；ASGI 清理已完成；主/子 cgroup 无 PID、无逃逸写入者和 queued job；无 SIGKILL/超时/abort/OOM 证据。`ExecMainStatus=15` 若出现，须有同版本完整清理证据，不能仅凭 SIGTERM 被 systemd 视为 success 就接受。最后还须通过四库日志检查。
- 退出上界、排空标准、实际策略或余量任一无法说明，`API_STOP_POLICY=BLOCKED`，**在任何 timer/AI/API 状态改变之前终止维护申请**。禁止修改生产 unit/超时、添加强杀或通过重启试错。90 秒后发现被强杀属于失败现场，不能用来补做验证。

维护前复核命令仅查询非敏感属性，未来授权后由维护人执行并保存证据；本次未连接生产：

```bash
systemctl show inventory-system-api.service \
  -p TimeoutStopUSec -p KillMode -p KillSignal -p SendSIGKILL \
  -p FinalKillSignal -p TimeoutStopFailureMode -p FragmentPath -p DropInPaths
```

## 固定顺序（每步成功后才进入下一步）

命令均供未来获批窗口的同一个 Bash 会话使用；`TOOLS` 是审核后的独立工具目录，**不是替换生产 API**。不加载 `.env`，不打印完整进程命令行或业务响应。`PY` 使用已核实的现有解释器。

```bash
DATA=/var/lib/inventory-system
VISUAL="$DATA/visual-search"
API=inventory-system-api.service
CURRENT_API=/srv/inventory-system/api
PY="$CURRENT_API/.venv/bin/python"
test "$(id -u)" = 0 || exit 1
# 审核单必须填入 TOOLS、SESSION、EVIDENCE、SNAPSHOT 的绝对路径；
# SESSION 是批准的私有备份父目录下尚不存在的唯一目录，EVIDENCE/SNAPSHOT 是其子目录。
# 仅在审批后的现有父目录下创建，不覆盖旧备份：
test ! -e "$SESSION" && test ! -L "$SESSION" || exit 1
umask 077
mkdir -m 700 -- "$SESSION" || exit 1
mkdir -m 700 -- "$EVIDENCE" || exit 1
```

| 步骤 | 命令/动作、预期结果 | 失败出口 |
| --- | --- | --- |
| 1 无人操作、记录原态 | 现场确认；用下方只读命令及 `c2_capture_original_state` 保存状态并绑定变量，再保全 AI 标记。审核停止证据后显式填写 API_STOP_POLICY/EVIDENCE；开始门槛必须全满足 | 未记录、有人操作、策略未通过或漂移：不改变任何服务 |
| 2 冻结所有入口 | 负责人落实签字表；暂停 Actions/人工触发，确认没有正在部署，并在本终端持有部署锁 FD9；其他定时/人工入口按已批准的实际命令禁止再启动 | 未识别入口、部署进行中或锁忙：停止，不碰 API |
| 3 暂停 timer、排空任务 | 仅对原先 active 的库存 timer 执行 `systemctl stop <准确 timer>`；等待备份、清理、OnSuccess uploader、同步和其他任务自然完成，无 pending job。停止 timer 不会停止已经启动的 service | 超过签字单时限：不强杀，停止流程 |
| 4 关闭 AI 新请求并排空 | 若原标记有效，执行 `"$PY" -B "$CURRENT_API/scripts/set_visual_search.py" disable --data-dir "$DATA"`；原先关闭则跳过。等待已知调用、索引、结算完成；不调用可能初始化状态的 AI status 接口，不通过新搜索测试 | 不能证明排空：停止；不改任务行或费用账本 |
| 5 正常停 API | 使用下方 `c2_stop_api`，拒绝未审查策略；返回 0 仍须逐项满足上方正常退出验收，并由快照工具复核 cgroup/任务状态。开始记录实际冻结区间 | stop 失败/超时/强杀/仍有 PID：停止，源状态待确认，不能直接启动 |
| 6–10 锁、日志、复制、验证、留证 | 按下方快照命令只执行一次。工具在复制前后检查已审查服务与队列，取得两个已有 AI 锁并全程持有；检查普通文件、链接、四库日志/WAL 头；复制四库、visual 全树、uploads 和存在的 import-previews；只在副本执行 A2 和全 integrity/FK 检查，确认无 running 索引及 reserved 费用；核对全部源前/源后/副本 SHA256、文件集合和源权限元信息 | 任何失败：保留不完整目录，不重试覆盖、不清理日志、不自动恢复服务；按失败表退出 |
| 11 人工审查后恢复原态 | 检查工具退出 0 和 COMPLETE、冻结证据；标记 SOURCE_SAFE=yes，记录冻结结束。先原版 API、健康，再原 AI 状态，最后 timer/其他调度；不迁移、不复制数据库回生产 | 数据状态不明则不启动；启动/健康/开关恢复失败即停，不继续开启 timer |
| 12 经营验收和交接 | 原网页库存列表、既有图片可用，不新增测试流水、不调用付费 AI；核对原 API/AI/timer/启用状态和版本，部署/任务负责人按签字单恢复原入口；记录结束 UTC、待办和人工签字，关闭 FD9 | 任一原状态未恢复则维护未完成，明确交接，不报成功 |

状态记录（元信息和报告只放私有目录）：

```bash
date -u --iso-8601=seconds > "$EVIDENCE/start-utc.txt"
systemctl show "$API" inventory-db-backup.timer inventory-full-backup.timer \
  inventory-db-backup.service inventory-full-backup.service \
  -p Id -p ActiveState -p SubState -p UnitFileState -p MainPID -p ControlGroup \
  -p User -p Group -p Result -p ExecMainCode -p ExecMainStatus \
  -p KillMode -p SendSIGKILL -p TimeoutStopUSec \
  -p NextElapseUSecRealtime -p LastTriggerUSec -p Persistent \
  > "$EVIDENCE/original-units.txt"
findmnt -T "$DATA" > "$EVIDENCE/mount.txt"
df -h "$DATA" "$SESSION" > "$EVIDENCE/space.txt"
df -i "$DATA" "$SESSION" > "$EVIDENCE/inodes.txt"
# 对已核实的四库、父目录和三个锁执行 stat/namei/getfacl，保存到 EVIDENCE；
# getfacl 不存在、ACL 不可读或实际维护身份能力不明均不得跳过门槛。
```

### 原始变量来源与赋值（步骤 1，任何停服务之前）

以下函数原文也用于离线模拟。API/两个 timer 的原态只从当次 systemctl 只读结果赋值，成功后设为只读，不能手填 yes 或维护中重新采样成“原态”。分别保存到 EVIDENCE。决策初始均 pending，SOURCE_SAFE 初始 no；AI_WAS_ENABLED 只由后面的标记保全过程赋值。新会话不能凭记忆续跑，须人工审查原始证据并完成交接。

<!-- capture-functions -->
```bash
c2_read_timer_original() {
  local state substate
  state=$(systemctl show "$1" -p ActiveState --value) || return 11
  substate=$(systemctl show "$1" -p SubState --value) || return 11
  case "$state:$substate" in active:waiting) printf 'yes\n' ;; inactive:dead) printf 'no\n' ;; *) return 11 ;; esac
}
c2_capture_original_state() {
  [ "${C2_STATE_CAPTURED:-no}" != yes ] || return 15
  local state substate
  API_WAS_ACTIVE=unknown
  DB_TIMER_WAS_ACTIVE=unknown
  FULL_TIMER_WAS_ACTIVE=unknown
  SOURCE_SAFE=no
  API_HEALTHY=no
  AI_STATE_RESTORED=no
  API_STOP_POLICY=BLOCKED
  API_STOP_EVIDENCE=
  DB_TIMER_DECISION=pending
  FULL_TIMER_DECISION=pending
  DB_TIMER_RESTORE_RC=1
  FULL_TIMER_RESTORE_RC=1
  state=$(systemctl show inventory-system-api.service -p ActiveState --value) || return 10
  substate=$(systemctl show inventory-system-api.service -p SubState --value) || return 10
  [ "$state:$substate" = active:running ] || return 10
  API_WAS_ACTIVE=yes
  DB_TIMER_WAS_ACTIVE=$(c2_read_timer_original inventory-db-backup.timer) || return 11
  FULL_TIMER_WAS_ACTIVE=$(c2_read_timer_original inventory-full-backup.timer) || return 12
  printf '%s\n' "$API_WAS_ACTIVE" > "$EVIDENCE/original-api-active.txt" || return 14
  printf '%s\n' "$DB_TIMER_WAS_ACTIVE" > "$EVIDENCE/original-db-timer-active.txt" || return 14
  printf '%s\n' "$FULL_TIMER_WAS_ACTIVE" > "$EVIDENCE/original-full-timer-active.txt" || return 14
  readonly API_WAS_ACTIVE DB_TIMER_WAS_ACTIVE FULL_TIMER_WAS_ACTIVE
  C2_STATE_CAPTURED=yes
  readonly C2_STATE_CAPTURED
}
c2_require_stop_policy() {
  [ "${C2_STATE_CAPTURED:-no}" = yes ] && [ "${API_STOP_POLICY:-BLOCKED}" = PASS ] \
    && [ -n "${API_STOP_EVIDENCE:-}" ] && [ -s "$API_STOP_EVIDENCE" ] || return 60
}
c2_stop_api() {
  c2_require_stop_policy || return 60
  # 以下路径只能来自独立发布验收及当前私有 socket 配置，不能猜测。
  [ -n "${PY:-}" ] && [ -n "${CURRENT_API:-}" ] && [ -n "${MAINTENANCE_SOCKET:-}" ] \
    && [ -d "${EVIDENCE:-}" ] || return 62
  local pid
  pid=$(systemctl show inventory-system-api.service -p MainPID --value) || return 62
  case "$pid" in ''|*[!0-9]*|0) return 62;; esac
  "$PY" -B "$CURRENT_API/scripts/maintenance_control.py" status \
    --socket "$MAINTENANCE_SOCKET" --pid "$pid" > "$EVIDENCE/drain-last.json" || return 62
  [ "$(systemctl show inventory-system-api.service -p MainPID --value)" = "$pid" ] || return 62
  systemctl stop inventory-system-api.service || return 61
  # 仅停止命令返回成功；尚不等于正常退出验收或 SOURCE_SAFE 通过。
}
```

步骤 1 执行 `c2_capture_original_state`，非零即终止。签字人完成停止策略实测/复核后，才允许将 `API_STOP_EVIDENCE` 指向该次审核记录并设置 `API_STOP_POLICY=PASS`；两者必须在步骤 2 之前审查，步骤 5 再检查一次，不能在停机后补填。`PY`、`CURRENT_API` 为当次核验的 API Python 与目录；`MAINTENANCE_SOCKET` 为已部署 API 配置的私有绝对路径。API 新版本和门禁未单独验收则保持 BLOCKED。排空检查失败返回 62，API 继续运行，不进入 stop，也不自动重试或覆盖数据库；SOURCE_SAFE 仍须人工审核。

原 AI 标记若存在，须为单链接普通文件，内容精确为 `enabled\n`；用 `stat`、`sha256sum` 记录，`cp --preserve=all --reflink=never` 复制到新 EVIDENCE，再核验 hash。异常即停止；不读取凭据。此步骤只保全开关证据，不复制业务库。

步骤 1 的标记保全命令（目录与祖先路径已审查）：

```bash
FLAG="$VISUAL/runtime/ai-search.enabled"
AI_WAS_ENABLED=no
if [ -e "$FLAG" ] || [ -L "$FLAG" ]; then
  test -f "$FLAG" && test ! -L "$FLAG" && [ "$(stat -c %h "$FLAG")" = 1 ] || exit 1
  cmp -s "$FLAG" <(printf 'enabled\n') || exit 1
  cp --preserve=all --reflink=never -- "$FLAG" "$EVIDENCE/ai-search.enabled" || exit 1
  cmp -s "$FLAG" "$EVIDENCE/ai-search.enabled" || exit 1
  stat -c '%u:%g:%a %h %s %n' "$FLAG" > "$EVIDENCE/ai-marker-stat.txt"
  sha256sum "$FLAG" "$EVIDENCE/ai-search.enabled" > "$EVIDENCE/ai-marker-sha256.txt"
  AI_WAS_ENABLED=yes
fi
printf '%s\n' "$AI_WAS_ENABLED" > "$EVIDENCE/ai-was-enabled.txt"
```

部署锁：确认既有锁为普通文件、无链接及 inode 异常后，**在步骤 2 执行并一直持有到步骤 12**；只读打开不创建/截断锁。

```bash
c2_require_stop_policy || exit 1
test -f /run/inventory-actions.lock && test ! -L /run/inventory-actions.lock || exit 1
exec 9</run/inventory-actions.lock
flock -n -x 9 || exit 1
```

### 步骤 6–10：固定快照工具

`deploy/numbering-v2-c2-snapshot.py` 不管理服务，不修改源文件，不连接源 SQLite、不获取新建锁。它验证继承的 FD9，复用 B 的现有调用锁和 A2 检查；不会调用部分备份脚本、创建硬链接、覆盖备份、执行迁移或删除不完整目录。新副本文件为私有权限，原 uid/gid/mode/mtime 保存在清单；不是对生产权限的修改或未来恢复安装规则。

步骤 5 完成后，操作员在 EVIDENCE 中填写 `freeze-approval.json`（不能用示例当签字；截止时间必须是实际带时区的 ISO 时间）：

```json
{
  "operator": "填写维护人",
  "reference": "填写授权记录和写入者签字表路径",
  "maintenance_authorized": true,
  "writers_reviewed": true,
  "deploy_frozen": true,
  "inflight_settled": true,
  "original_state_recorded": true,
  "extra_units": ["inventory-oss-upload@db.service", "inventory-oss-upload@full.service"],
  "window_end_utc": "填写实际维护截止时间"
}
```

extra_units 必须加上盘点到的所有相关 systemd service/timer；不存在的 uploader 也须先核实记录，不能为通过检查临时删名单。cron/人工入口不能靠此字段冻结，证据由签字表单独提供。

```bash
"$PY" -B "$TOOLS/deploy/numbering-v2-c2-snapshot.py" \
  --source "$DATA" --output "$SNAPSHOT" \
  --approval "$EVIDENCE/freeze-approval.json" --deploy-lock-fd 9 \
  > "$EVIDENCE/snapshot.stdout" 2> "$EVIDENCE/snapshot.stderr"
# 非零立即停止；不靠只检查目录/COMPLETE 是否存在判断成功。
# 成功后将 COMPLETE.json 的 SHA256 另存到 EVIDENCE 并人工封存；
# COMPLETE 内也绑定其余五份证据 JSON 的 SHA256。
```

工具额外保留至少 1 GiB / 1024 inode；执行前人工余量应覆盖工具报告、正在增长的系统日志和实际存储开销。单文件哈希/SQLite 完整性检查不保证即时中断，须保守预留耗时；截止前不能完成则保留现场，不反复执行。

证据为 approval、source-before/after、copied、preflight、COMPLETE JSON，以及 EVIDENCE 中的原态、停写签字表、cgroup/进程及调度控制证据。COMPLETE 含冻结观察区间、四库 hash、前后 unit 状态、数量/大小；它始终标记 `REQUIRES_HUMAN_FREEZE_REVIEW`，不自动生成正式 `reviewed:true` 一致性凭据。哈希不变不能证明所有写入者被控制。零 manifests、镜像少于业务库并不自动失败；源损坏、来源冲突、未结算/运行任务等立即停止。

### 步骤 11：人工恢复关口（不是自动 finally）

以下函数只在人工确认源数据安全后调用；不得把 SOURCE_SAFE 默认置 yes。恢复失败不自动重启/重复尝试。原 API 非 active 不安排本维护窗口。AI 原态恢复完成后才置 AI_STATE_RESTORED=yes。

<!-- recovery-functions -->
```bash
c2_resume_api() {
  [ "${C2_STATE_CAPTURED:-no}" = yes ] || return 41
  [ "${SOURCE_SAFE:-no}" = yes ] || return 40
  [ "${API_WAS_ACTIVE:-no}" = yes ] || return 41
  API_HEALTHY=no
  systemctl start inventory-system-api.service || return 42
  systemctl is-active --quiet inventory-system-api.service || return 43
  curl --fail --silent --max-time 10 -o /dev/null \
    http://127.0.0.1:8102/api/dashboard/summary || return 44
  API_HEALTHY=yes
}
c2_restore_ai_marker() {
  [ "${API_HEALTHY:-no}" = yes ] || return 54
  test ! -e "$FLAG" && test ! -L "$FLAG" || return 55
  case "${AI_WAS_ENABLED:-unknown}" in
    yes)
      cmp -s "$EVIDENCE/ai-search.enabled" <(printf 'enabled\n') || return 56
      cp --preserve=all --reflink=never --no-clobber -- "$EVIDENCE/ai-search.enabled" "$FLAG" || return 57
      cmp -s "$EVIDENCE/ai-search.enabled" "$FLAG" || return 58
      test "$(stat -c '%u:%g:%a' "$EVIDENCE/ai-search.enabled")" = "$(stat -c '%u:%g:%a' "$FLAG")" || return 58
      sha256sum "$FLAG" > "$EVIDENCE/ai-restored-sha256.txt" || return 58 ;;
    no) ;;
    *) return 56 ;;
  esac
  # inventory 的实际读取能力仍须人工核验，之后再设 AI_STATE_RESTORED=yes。
}
c2_restore_timer() {
  local was_active decision
  case "$1" in
    inventory-db-backup.timer)
      was_active=${DB_TIMER_WAS_ACTIVE:-unknown}; decision=${DB_TIMER_DECISION:-pending} ;;
    inventory-full-backup.timer)
      was_active=${FULL_TIMER_WAS_ACTIVE:-unknown}; decision=${FULL_TIMER_DECISION:-pending} ;;
    *) return 49 ;;
  esac
  [ "${C2_STATE_CAPTURED:-no}" = yes ] && [ "${SOURCE_SAFE:-no}" = yes ] || return 40
  [ "${API_HEALTHY:-no}" = yes ] && [ "${AI_STATE_RESTORED:-no}" = yes ] || return 51
  case "$was_active" in no) return 0 ;; yes) ;; *) return 49 ;; esac
  case "$decision" in not-missed|catchup-approved) ;; *) return 50 ;; esac
  systemctl start "$1" || return 52
  systemctl is-active --quiet "$1" || return 53
}
```

调用 `c2_resume_api` 成功后，原 AI 关闭则保持关闭；原 AI 开启则确认 marker 仍不存在、父目录无链接且权限未漂移，再由同一受控维护身份将 EVIDENCE 中原标记用 `cp --preserve=all --reflink=never --no-clobber` 复制回原路径，核验字节 SHA256 及 uid/gid/mode。不得直接以 root 运行 enable 重建成 root:root/600，导致 inventory 无法读取。恢复复制不得覆盖异常现有文件，任何冲突停止。检查 inventory 对标记的读取能力；不触发模型调用。

步骤 11 逐项调用上述函数；恢复标记函数自动区分原开/关状态，不靠注释跳过复制：

```bash
c2_resume_api
# 非零停止；成功后重新审查 FLAG 父目录权限/链接，才执行：
c2_restore_ai_marker
# 非零停止；再完成人工 inventory 读取能力核验，才设 AI_STATE_RESTORED=yes 并逐个恢复 timer。
```

恢复变量绑定如下，禁止使用通用 `TIMER_WAS_ACTIVE` / `TIMER_DECISION` 在两者之间传递状态：

| 变量 | 唯一来源/赋值时点 |
| --- | --- |
| API_WAS_ACTIVE / DB_TIMER_WAS_ACTIVE / FULL_TIMER_WAS_ACTIVE | 步骤 1 的 `c2_capture_original_state`；分别对应 API、每日 timer、每周 timer；只读保存，不能在停后重采样 |
| AI_WAS_ENABLED | 步骤 1 的实际标记保全检查，写入 ai-was-enabled.txt，不从当前已关闭标记倒推 |
| SOURCE_SAFE | 始终默认 no；步骤 11 或安全退出失败流程中，人工审查源状态、日志及适用的前后清单并记录签字后才显式赋值 yes；不能只看 COMPLETE 存在 |
| API_HEALTHY | `c2_resume_api` 的服务和健康检查成功后为 yes；若 API 从未停止，使用下方既有运行检查，不执行 start |
| AI_STATE_RESTORED | 原开/关状态、标记内容/权限及 inventory 访问能力人工核验后才赋值 yes |
| DB_TIMER_DECISION / FULL_TIMER_DECISION | 分别来自每日/每周恢复审批，初始 pending；决策不能互相复制，依据单独保存到 EVIDENCE |
| DB_TIMER_RESTORE_RC / FULL_TIMER_RESTORE_RC | 分别保存对应恢复调用紧接着的 `$?`，初始 1；每日非零/未执行时每周段不能执行，不能手填成功 |

API 从未停止且需要中止维护时，不调用 `c2_resume_api`。仅在人工确认源状态安全、实际原态未变后执行只读健康检查，成功后设 API_HEALTHY=yes；AI 未改变时核对原标记后人工确认 AI_STATE_RESTORED，AI 已关闭但仍有未排空任务时则保持 no，不恢复 timer。

```bash
# 仅供失败表中明确允许保留原 API 的分支；不是未知数据库状态的启动出口：
API_HEALTHY=no
if [ "${SOURCE_SAFE:-no}" = yes ] && systemctl is-active --quiet inventory-system-api.service \
  && curl --fail --silent --max-time 10 -o /dev/null http://127.0.0.1:8102/api/dashboard/summary; then
  API_HEALTHY=yes
fi
```

对两个 timer **逐个**审核，只接受以下决策：

- `not-missed`：核对暂停区间未跨原触发点，无时钟跳变/旧待补执行；原 active 才启动。
- `catchup-approved`：确实错过，已明确批准一次预期补执行；在 API/AI 恢复并完成数据核验后逐个启动，观察该 backup 及 OnSuccess uploader 正常完成，再处理另一个。不要另行手动 start backup service，防止重复。
- 不能确认或未获补执行批准：保持 timer 停止，返回 50，明确“经营可用但维护未完成/定时备份待恢复”，交人工决定，不能报完全恢复。

每日和每周的原始变量不可重新赋值。各自审批结论通过下面的独立赋值步骤输入并留证；任一步返回非零立即停止，禁止整段忽略错误粘贴执行：

```bash
if [ "$DB_TIMER_WAS_ACTIVE" = yes ]; then
  read -r -p '每日 timer 已审核决策(not-missed/catchup-approved/pending): ' DB_TIMER_DECISION || DB_TIMER_DECISION=pending
fi
printf '%s\n' "$DB_TIMER_DECISION" > "$EVIDENCE/db-timer-decision.txt"
c2_restore_timer inventory-db-backup.timer
DB_TIMER_RESTORE_RC=$?
```

每日失败立即停在此处。每日成功后，若批准了补执行，先观察其 backup/uploader 完成并记录，才可单独执行每周段；不能把两段连成无人值守脚本。每周段也检查每日退出码，拒绝沿用未完成状态。

<!-- weekly-timer-restore -->
```bash
if [ "${DB_TIMER_RESTORE_RC:-1}" -eq 0 ]; then
  if [ "$FULL_TIMER_WAS_ACTIVE" = yes ]; then
    read -r -p '每周 timer 已审核决策(not-missed/catchup-approved/pending): ' FULL_TIMER_DECISION || FULL_TIMER_DECISION=pending
  fi
  printf '%s\n' "$FULL_TIMER_DECISION" > "$EVIDENCE/full-timer-decision.txt"
  c2_restore_timer inventory-full-backup.timer
  FULL_TIMER_RESTORE_RC=$?
else
  printf 'BLOCKED: 每日 timer 未完成，不恢复每周 timer\n'
fi
```

不修改 Persistent、不清理/触碰 timestamp、不更改时钟、不偷偷跳过备份。原 enable/disable 配置不变。官方说明：[Persistent 补执行](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)。恢复 API 后可能已有正常写入，禁止再用冻结清单判断其必须不变，更不能覆盖数据库。

## 停止及恢复表

| 首个异常/发生阶段 | API | AI / timer / 其他入口及恢复条件 |
| --- | --- | --- |
| 停止策略、原态或全部写入者未确认，尚未改变服务 | 保持原运行，不调用 start/stop | AI/timer 保持原态；维护 BLOCKED，不先停 timer 来等证据 |
| 入口已冻结、timer 已暂停，但 API/AI 均未停止 | 保持原 API，不重启 | 原 AI 核验未变；SOURCE_SAFE/只读健康审核通过后，才可按各自决策恢复已暂停 timer；其他入口按签字表交还 |
| AI 已关闭，但调用/索引/结算无法排空，API 尚未停止 | 不再尝试 stop，保留实际运行状态 | AI 新请求保持关闭，timer 保持暂停；不重开 AI 掩盖未结算任务，不杀进程/改 running 或 reserved；交人工处理 |
| API stop 超时、强杀或 cgroup 不空 | 不复制、不新启动、不循环重启；报告实际状态，不能假称已停干净 | AI/timer 保持停止，所有再启动入口继续冻结，源状态不明则 SOURCE_SAFE=no |
| API 正常停止后锁忙，或 sidecar/WAL/损坏/来源冲突/源漂移 | 保持停止，不自动启动 | AI/timer 保持停止；不删锁/日志、不恢复/checkpoint/转换模式；人工明确源状态前不得继续 |
| 正常停后空间/inode 不足、复制 I/O/目标 hash 失败 | 源清单一致、无异常日志、冻结仍受控且 SOURCE_SAFE 人工通过，才可恢复原版 API；证据不足保持停止 | 仅 API 健康后恢复原 AI，再按每日/每周独立决策恢复 timer；不覆盖或清理不完整集合 |
| 备份完整但 API 启动/健康失败 | 保留备份和实际 API 状态，不再 start/restart，不替换代码/数据库 | AI/timer 不恢复；其他部署/写入入口不放开，交维护人 |
| API 健康后 AI 标记/权限恢复失败 | 已核验 API 可保留，不重启 | 不宣称 AI 已开或已关，记录标记实际状态并暂停使用；timer 保持停止，不 chmod/chown 或换身份试修 |
| timer 补执行未批准或恢复失败 | 已恢复且健康的 API 保留 | 已核验原 AI 保留；未获批准/未恢复的 timer 保持停止，已按批准恢复的 timer 不自动回停；逐项交接，不改变 Persistent/timestamp，不报完全成功 |

root 备份能力与未来换号能力分别验收；同设备 ext4 只是必要事实，不证明断电原子提交。源状态不明的失败出口优先安全而非强行恢复；清单没有无条件 finally/start 或数据库回拷。

## 本地验收记录

离线测试入口：`api/tests/test_numbering_v2_c2_snapshot.py`。复用 A2 合成四库 fixture、现有路径/锁/预检机制；systemd、部署锁和 Windows 调用锁模拟，恢复函数从本文原文提取并由假 systemctl/curl 执行。覆盖复制前关口、源/目标 SHA 与集合、日志、空间、inode、链接、来源损坏、源漂移、任务重启、原状态及补执行决策。所有真实服务、生产锁、权限和事务行为仍待运行时验收；本地通过不证明真实停写或一致性。

上次准备提交 `291bb7e` 的本地 Windows 离线记录为 128 passed。此次收口增加原态采集、停止策略门槛、两 timer 交叉状态/决策隔离及 Linux 专用验证。2026-10-29 最终结果：**148 passed，5 skipped**（68 项准备检查通过、80 项既有回归通过；5 项 Linux 专用用例全部跳过）。正常快照仍证明源前/源后/副本 SHA256 一致、inode 独立；模拟通过不证明生产停止策略。

```text
python -B -m pytest -q --tb=short -p no:cacheprovider --basetemp <新建唯一临时目录> tests/test_numbering_v2_c2_snapshot.py tests/test_numbering_v2_c2_linux.py tests/test_numbering_v2_preflight.py tests/test_deploy_backups.py
```

Git Bash 仅运行合成文件操作与假 systemctl/curl；没有调用主机真实服务。未连接生产、未上传工具、不推送、不合并 PR，不自动进入下一阶段。

### 独立 Linux 验证：PENDING，不能标记 PASS

2026-10-29 只读盘点本机：WSL 没有已安装发行版；Docker 默认及 desktop-linux 上下文的本地引擎管道均不存在。没有启动引擎、安装 Linux 或寻找远程服务器来绕过限制。

`tests/test_numbering_v2_c2_linux.py` 准备了 Linux root 隔离环境专用用例：Bash FD9 真锁继承、调用实际快照 main、真实 AI flock 与目录 fsync、只对副本 SQLite 校验、日志/锁忙/排队任务/无 FD 的失败出口。仅在测试进程将部署锁路径映射到私有合成文件，保留真实 inode/FD/flock 检查；systemctl 全部使用本地假命令，不创建 /run 文件或真实服务。Windows 上这些用例全部 SKIP。

| 验证项 | 当前结论 |
| --- | --- |
| Bash FD9 / Linux 调用锁继承与互斥 | PENDING，未在 Linux 执行 |
| 快照实际 main、SQLite 副本校验、目录 fsync | Linux 验证 PENDING；Windows 合成检查不能替代 |
| systemd 状态解析、恢复函数、timer 失败决策 | Windows/Git Bash 模拟可测；Linux 验证 PENDING，不涉及真实 systemd 停服务 |
| 当前生产 API 同版本 90 秒安全退出 | BLOCKED：wait=True 无统一上界，缺排空/耗时/清理完整性实测和审核 |

只有取得独立 Linux 实际结果、当前生产版本的停止策略证据，以及全部写入者/权限/窗口的人工签字后，才具备申请一致备份维护窗口的前提；本次到此停止。

# C2-2：一次性一致备份执行清单

**状态：仅本地准备；未授权执行。** 编号业务基线固定为 `d2873699032057717fbbad9c077275180883fb39`，本准备分支不改 B 逻辑。本清单只冻结、复制、验证、恢复原版经营，不迁移、不换号、不部署。完整部署器及日/周备份单元均不作为四库一致备份入口：其范围不足，且包含清理、上传或启动服务动作。

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
| 1 无人操作、记录原态 | 现场确认；用下方只读命令保存状态、UTC、挂载/空间、必要权限；备份 AI 标记原字节及 uid/gid/mode/hash 到 EVIDENCE。检查部署版本未变化 | 未记录、有人操作或漂移：不开始 |
| 2 冻结所有入口 | 负责人落实签字表；暂停 Actions/人工触发，确认没有正在部署，并在本终端持有部署锁 FD9；其他定时/人工入口按已批准的实际命令禁止再启动 | 未识别入口、部署进行中或锁忙：停止，不碰 API |
| 3 暂停 timer、排空任务 | 仅对原先 active 的库存 timer 执行 `systemctl stop <准确 timer>`；等待备份、清理、OnSuccess uploader、同步和其他任务自然完成，无 pending job。停止 timer 不会停止已经启动的 service | 超过签字单时限：不强杀，停止流程 |
| 4 关闭 AI 新请求并排空 | 若原标记有效，执行 `"$PY" -B "$CURRENT_API/scripts/set_visual_search.py" disable --data-dir "$DATA"`；原先关闭则跳过。等待已知调用、索引、结算完成；不调用可能初始化状态的 AI status 接口，不通过新搜索测试 | 不能证明排空：停止；不改任务行或费用账本 |
| 5 正常停 API | 执行 `systemctl stop "$API"`；检查 inactive、MainPID=0、Result=success、非 SIGKILL，整个 cgroup 及子 cgroup 无 PID，且无逃逸写入进程。开始记录实际冻结区间 | stop 失败/超时/强杀/仍有 PID：停止，源状态待确认，不能直接启动 |
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

必须事先确认正常退出可在现有 stop 超时内完成；不临场修改 unit 来绕过 SIGKILL 风险。原 AI 标记若存在，须为单链接普通文件，内容精确为 `enabled\n`；用 `stat`、`sha256sum` 记录，`cp --preserve=all --reflink=never` 复制到新 EVIDENCE，再核验 hash。异常即停止；不读取凭据。此步骤只保全开关证据，不复制业务库。

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
  case "$1" in inventory-db-backup.timer|inventory-full-backup.timer) ;; *) return 49 ;; esac
  [ "${API_HEALTHY:-no}" = yes ] && [ "${AI_STATE_RESTORED:-no}" = yes ] || return 51
  case "${TIMER_WAS_ACTIVE:-unknown}" in no) return 0 ;; yes) ;; *) return 49 ;; esac
  case "${TIMER_DECISION:-pending}" in not-missed|catchup-approved) ;; *) return 50 ;; esac
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

对两个 timer **逐个**从原记录填写 TIMER_WAS_ACTIVE，并填写 TIMER_DECISION：

- `not-missed`：核对暂停区间未跨原触发点，无时钟跳变/旧待补执行；原 active 才启动。
- `catchup-approved`：确实错过，已明确批准一次预期补执行；在 API/AI 恢复并完成数据核验后逐个启动，观察该 backup 及 OnSuccess uploader 正常完成，再处理另一个。不要另行手动 start backup service，防止重复。
- 不能确认或未获补执行批准：保持 timer 停止，返回 50，明确“经营可用但维护未完成/定时备份待恢复”，交人工决定，不能报完全恢复。

不修改 Persistent、不清理/触碰 timestamp、不更改时钟、不偷偷跳过备份。原 enable/disable 配置不变。官方说明：[Persistent 补执行](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)。恢复 API 后可能已有正常写入，禁止再用冻结清单判断其必须不变，更不能覆盖数据库。

## 停止及恢复表

| 首个异常 | 固定出口 |
| --- | --- |
| 写入来源或冻结方式不明 | 不开始；只补齐实际任务、负责人和停写/恢复方法，不猜测名称或临时加屏蔽机制 |
| AI/后台/备份无法排空 | 到批准时限停止，不杀进程、不手改 running/reserved；保持新请求关闭，人工判定已改变状态的恢复 |
| API 非正常退出、锁忙 | 不复制；保留 Result/cgroup/锁状态。未知数据库状态不自动启动，不删锁 |
| sidecar、WAL 头、损坏、来源冲突、源漂移 | 保留文件及报告，禁止 SQLite 恢复/checkpoint/模式转换/删除日志；停止自动操作，人工审查前不启动 API/AI |
| 空间/inode 不足、复制 I/O/目标 hash 失败 | 不覆盖、不清理不完整集合；若源文件与前清单一致、无日志、冻结仍受控，经人工确认才能走恢复关口；无足够证据则保持停写并报告 |
| 备份完整但 API 启动/健康失败 | 备份原样保留，不恢复数据库、不替换代码；不继续开启 AI/timer，不循环重启，报告实际服务状态并交维护人 |
| AI 标记/原权限恢复失败 | 不更改目录权限，不尝试其他身份；timer 继续暂停，报告 API/AI 实际状态，人工处理 |
| timer 补执行未批准或恢复失败 | 不改 Persistent/timestamp，不把维护报成功；明确仍暂停的 timer 及人工接管人 |

root 备份能力与未来换号能力分别验收；同设备 ext4 只是必要事实，不证明断电原子提交。源状态不明的失败出口优先安全而非强行恢复；清单没有无条件 finally/start 或数据库回拷。

## 本地验收记录

离线测试入口：`api/tests/test_numbering_v2_c2_snapshot.py`。复用 A2 合成四库 fixture、现有路径/锁/预检机制；systemd、部署锁和 Windows 调用锁模拟，恢复函数从本文原文提取并由假 systemctl/curl 执行。覆盖复制前关口、源/目标 SHA 与集合、日志、空间、inode、链接、来源损坏、源漂移、任务重启、原状态及补执行决策。所有真实服务、生产锁、权限和事务行为仍待运行时验收；本地通过不证明真实停写或一致性。

2026-10-29 本地 Windows 离线结果：**128 passed**，其中新增准备检查 48 项，复用 A2/备份回归 80 项。正常快照逐文件证明源前/源后/副本 SHA256 一致且 inode 独立；外部写入、损坏、日志及服务恢复异常均按预期停止。实际执行命令如下，basetemp 使用本次新建唯一目录（未清理原 pytest 目录）：

```text
python -B -m pytest -q --tb=short -p no:cacheprovider --basetemp <新建唯一临时目录> tests/test_numbering_v2_c2_snapshot.py tests/test_numbering_v2_preflight.py tests/test_deploy_backups.py
```

Git Bash 仅运行合成文件操作与假 systemctl/curl；没有调用主机真实服务。未连接生产、未上传工具、不推送、不合并 PR，不自动进入下一阶段。

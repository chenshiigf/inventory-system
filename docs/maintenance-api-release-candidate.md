# Independent maintenance API candidate

Base: 42c7f99b2af3ba4938de83ba60d32f7c2cf082e1.
Production release evidence: Actions run 38857180677, deploy job 116831606887 completed successfully. Later recorded runs are PR-only. This is recorded release provenance, not a fresh production inspection.
Gate source: b8fc43104b1dd83eb965e879b688482a4192f73b (PR #7 unchanged).

No numbering modules, migrations, frontend or dependency changes are included. Only the gate's required path validation, read-only SQLite checks and platform locks are local to maintenance.py. No numbering_io import remains.

Default off. A separate authorized API-only release must retain the original database revision, data directories, environment and existing single worker. Verify the live API release still matches the recorded base before deployment; any drift blocks deployment. Prepare and verify the private socket parent and effective inventory permissions, then configure INVENTORY_MAINTENANCE_SOCKET. Do not reuse the full deployer or run migrations. API shutdown for that release still requires a separately approved safe drain procedure: the old API has no new gate.

Rollback requires the original API source and locked runtime, configuration and version evidence retained before release. After safely draining the candidate and human confirmation of source safety, restore only original API code/runtime and socket configuration. Never overwrite databases, uploads, ledger or index. If draining or source safety cannot be established, keep the API running and stop automatic actions. Rollback itself needs separate authorization.

The archive is a review-only API source candidate, not an automatic deployment package. It includes api/app, maintenance_control.py, unchanged runtime lock files and release.json with per-file hashes; excludes migrations, UI, tests, data and credentials. Independent Linux validation must use this candidate commit, not PR #7's numbering branch.

## Stop wrapper used by synthetic tests
The existing signed stop-policy and source-safety gates still apply. All paths and fresh PID must be verified by the authorized operator.

```bash
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

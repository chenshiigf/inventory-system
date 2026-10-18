"""Fail-closed recovery for a numbering-hold deployment before code commit.

The command is deliberately separate from the deployer and from the production
numbering transaction. Its only rollback source is the operation-bound full
backup created after maintenance was established.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request

from app.numbering_maintenance import (
    Maintenance,
    MaintenanceError,
    atomic_json,
    checked_path,
    database_identity,
    file_gate,
)
from app.services import product_code_cutover as cutover
from app.services.numbering_environment import ProductionEnvironment, file_binding


ABORT_BUNDLE = "numbering-abort-bundle-v1"
ABORT_BASELINE = "numbering-abort-baseline-v1"
ABORT_PROGRESS = "numbering-abort-progress-v1"
ABORT_RECEIPT = "numbering-abort-receipt-v1"
OPERATION_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{7,95}\Z")
RELEASE_RE = re.compile(r"[0-9]+-[0-9]+-[0-9a-f]{40}\Z")
API_ITEMS = ("app", "alembic", "scripts", "alembic.ini", "requirements.txt", "requirements.lock.txt")
SERVICE = "inventory-system-api.service"
PROXY = "nginx.service"
HEALTH_URL = "http://127.0.0.1:8102/api/dashboard/summary"
PRODUCTS_URL = "http://127.0.0.1:8102/api/products?page=1&page_size=1&status=all"
API_ROOT = Path("/srv/inventory-system/api")
PROJECT = Path("/srv/inventory-system")
STATIC = Path("/var/www/inventory-system/out")
BACKUPS = Path("/var/backups/inventory-system/actions")
DATA_DIR = Path("/var/lib/inventory-system")
ENV_FILE = Path("/etc/inventory-system.env")
RUNTIME_DROPIN_ROOT = Path("/run/systemd/system/inventory-system-api.service.d")
RUNTIME_PROBE_ROOT = Path("/run/inventory-numbering-abort")
PROBE_DATA_ROOT = Path("/var/tmp/inventory-numbering-abort")
TIMERS = ("inventory-db-backup.timer", "inventory-full-backup.timer")


class AbortError(MaintenanceError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf8")).hexdigest()


def _safe_tree_root(path):
    requested = Path(path).absolute()
    for part in (requested, *requested.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise AbortError("恢复产物路径不能包含符号链接或 junction")
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise AbortError("恢复产物目录缺失")
    return resolved


def tree_digest(path):
    """Hash a tree without following links; reject special and hard-linked files."""
    root = _safe_tree_root(path)
    digest = hashlib.sha256()
    stack = [(root, "")]
    while stack:
        directory, relative = stack.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name, reverse=True)
        except OSError as error:
            raise AbortError("无法读取恢复产物") from error
        for item in children:
            child = Path(item.path)
            name = f"{relative}/{item.name}" if relative else item.name
            info = item.stat(follow_symlinks=False)
            digest.update(name.encode("utf8") + b"\0")
            digest.update(f"{stat.S_IMODE(info.st_mode):04o}\0".encode("ascii"))
            if stat.S_ISLNK(info.st_mode):
                digest.update(b"L\0" + os.readlink(child).encode("utf8") + b"\0")
            elif stat.S_ISDIR(info.st_mode):
                digest.update(b"D\0")
                stack.append((child, name))
            elif stat.S_ISREG(info.st_mode):
                # Windows' os.scandir().stat() leaves st_nlink at zero; a
                # positive value greater than one is the cross-platform signal
                # that this file is hard-linked. Linux production always
                # provides a positive count.
                if info.st_nlink > 1:
                    raise AbortError("恢复产物含硬链接，拒绝使用")
                digest.update(b"F\0" + str(info.st_size).encode("ascii") + b"\0")
                with child.open("rb") as stream:
                    digest.update(hashlib.file_digest(stream, "sha256").digest())
            else:
                raise AbortError("恢复产物含特殊文件，拒绝使用")
    return digest.hexdigest()


def artifact_digest(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as error:
        raise AbortError("恢复产物缺失") from error
    if stat.S_ISDIR(info.st_mode):
        return tree_digest(path)
    if stat.S_ISLNK(info.st_mode):
        return _digest({"type": "symlink", "mode": stat.S_IMODE(info.st_mode), "target": os.readlink(path)})
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AbortError("恢复产物不是独立普通文件")
    return _digest({"type": "file", "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size, "sha256": file_binding(path)["sha256"]})


def api_items_digest(root, items=API_ITEMS):
    return _digest({item: artifact_digest(Path(root) / item) for item in items})


def database_schema_digest(path):
    database = checked_path(path, existing=True)
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            rows = db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
            ).fetchall()
    except sqlite3.Error as error:
        raise AbortError("无法核验切换前数据库结构") from error
    return _digest(rows)


def write_abort_bundle(context, rollback, release, operation_id, release_sha,
                       baseline, backup_directory, *, ensure_venv_copy=False):
    """Publish one complete abort bundle from independently verified inputs."""
    paths = _bundle_paths(context, release)
    if Path(rollback).resolve() != paths["release_dir"].resolve():
        raise AbortError("恢复包目录与受控发布目录不匹配")
    state = Maintenance(context.data_root).require(operation_id, context.database)
    if (state.get("release_sha") != release_sha or not state.get("frozen_business_sha256") or
            state.get("database_identity") != baseline.get("database_identity_before_deploy")):
        raise AbortError("维护冻结状态与旧版本恢复基线不匹配")
    if api_items_digest(context.api) != baseline.get("old_api_tree_sha256"):
        raise AbortError("当前 API 已不再是切换前旧版本")
    if tree_digest(context.api / ".venv") != baseline.get("old_venv_tree_sha256"):
        raise AbortError("当前虚拟环境已不再是切换前旧版本")
    if tree_digest(context.static) != baseline.get("old_static_tree_sha256"):
        raise AbortError("当前静态产物已不再是切换前旧版本")
    if api_items_digest(paths["api"]) != baseline.get("old_api_tree_sha256"):
        raise AbortError("旧 API 恢复副本摘要不匹配")
    if tree_digest(paths["static"]) != baseline.get("old_static_tree_sha256"):
        raise AbortError("旧静态产物恢复副本摘要不匹配")
    previous = _read_json(paths["previous_metadata"], "切换前部署版本记录")
    if (previous.get("commit") != baseline.get("previous_commit") or
            file_binding(paths["previous_metadata"])["sha256"] != baseline.get("previous_metadata_sha256")):
        raise AbortError("切换前部署版本记录与恢复基线不匹配")

    backup_directory = checked_path(backup_directory)
    if (backup_directory.parent != paths["backup"] or backup_directory.is_symlink() or
            not backup_directory.is_dir() or
            not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}", backup_directory.name)):
        raise AbortError("完整备份必须位于本次发布的受控时间戳目录")
    for name in ("manifest.json", "inventory.db", "uploads.tar.gz"):
        candidate = backup_directory / name
        if candidate.is_symlink() or not candidate.is_file():
            raise AbortError("完整业务备份不完整")

    venv_target = paths["venv"]
    if ensure_venv_copy:
        _restore_tree(context.api / ".venv", venv_target)
        if hasattr(context, "chown_recovery"):
            context.chown_recovery(venv_target)
        else:
            context.chown_inventory(venv_target)
    if os.path.lexists(venv_target) and (not venv_target.is_dir() or venv_target.is_symlink() or
                                         tree_digest(venv_target) != baseline.get("old_venv_tree_sha256")):
        raise AbortError("旧 Python 虚拟环境恢复副本缺失或摘要不匹配")
    if not ensure_venv_copy and not os.path.lexists(venv_target) and tree_digest(context.api / ".venv") != baseline.get("old_venv_tree_sha256"):
        raise AbortError("旧 Python 虚拟环境已变化，拒绝发布恢复包")

    manifest = backup_directory / "manifest.json"
    backup_db = backup_directory / "inventory.db"
    uploads = backup_directory / "uploads.tar.gz"
    verified_backup = cutover.verify_backup(context, backup_db, manifest, state)
    bundle = {
        "format": ABORT_BUNDLE,
        "state": "ready",
        "operation_id": operation_id,
        "release": release,
        "release_sha": release_sha,
        "database_identity_before_deploy": baseline["database_identity_before_deploy"],
        "frozen_business_sha256": state["frozen_business_sha256"],
        "frozen_counters": state.get("frozen_counters", {}),
        "previous_metadata": str(paths["previous_metadata"]),
        "previous_metadata_sha256": baseline["previous_metadata_sha256"],
        "previous_commit": baseline["previous_commit"],
        "old_api": str(paths["api"]),
        "old_api_tree_sha256": baseline["old_api_tree_sha256"],
        "old_venv": str(venv_target),
        "old_venv_tree_sha256": baseline["old_venv_tree_sha256"],
        "old_static": str(paths["static"]),
        "old_static_tree_sha256": baseline["old_static_tree_sha256"],
        "backup_directory": str(backup_directory),
        "backup_manifest_sha256": verified_backup["manifest"]["sha256"],
        "backup_database_sha256": verified_backup["database"]["sha256"],
        "backup_uploads_sha256": verified_backup["uploads"]["sha256"],
        "timers_were_active": baseline["timers_were_active"],
        "proxy_was_active": baseline["proxy_was_active"],
        "created_at_ns": time.time_ns(),
    }
    if os.path.lexists(paths["bundle"]):
        raise AbortError("受控中止恢复包已存在；拒绝覆盖")
    atomic_json(paths["bundle"], bundle, group=Path(context.database).stat().st_gid)
    return bundle


def atomic_text(path, content, mode=0o600):
    target = Path(path)
    checked_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + "-" + os.urandom(8).hex())
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, mode)
        with os.fdopen(fd, "w", encoding="utf8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path, label):
    candidate = checked_path(path, existing=True)
    try:
        value = json.loads(candidate.read_text(encoding="utf8"))
    except (OSError, ValueError) as error:
        raise AbortError(f"{label}缺失或损坏") from error
    if not isinstance(value, dict):
        raise AbortError(f"{label}格式无效")
    return value


def _database_facts_path(database):
    database = checked_path(database, existing=True)
    sidecars = {}
    for suffix in ("-wal", "-shm", "-journal"):
        path = Path(str(database) + suffix)
        if os.path.lexists(path):
            sidecars[suffix] = file_binding(checked_path(path, existing=True))
    if sidecars:
        raise AbortError("数据库存在 WAL/SHM/journal sidecar；先按 SQLite 事务恢复流程核验，禁止直接替换")
    with closing(cutover.connect(database)) as db:
        cutover.check(db, require_head=False)
        revision_rows = [row[0] for row in db.execute("SELECT version_num FROM alembic_version")]
        if len(revision_rows) != 1:
            raise AbortError("数据库 Alembic revision 不唯一")
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        committed_rows = []
        if cutover.CONTROL in names:
            committed_rows = db.execute(f"SELECT id,operation_id,state FROM {cutover.CONTROL}").fetchall()
        if committed_rows:
            raise AbortError("历史商品换号已提交；禁止使用普通中止恢复")
        business_sha = cutover.digest(cutover.snapshot(db))
        counters = {}
        if "product_code_months" in names:
            counters = dict(db.execute("SELECT period_ym,next_sequence FROM product_code_months"))
        facts = {
            "database_identity": database_identity(database),
            "database_binding": file_binding(database),
            "revision": revision_rows[0],
            "schema_sha256": database_schema_digest(database),
            "business_sha256": business_sha,
            "counters": counters,
            "cutover_registry": "absent" if cutover.CONTROL not in names else "empty",
            "integrity_check": "ok",
            "foreign_key_check": 0,
            "sidecars": sidecars,
            "product_count": db.execute("SELECT count(*) FROM products").fetchone()[0],
        }
    return facts


def _database_facts(context, *, expect_frozen=None):
    facts = _database_facts_path(context.database)
    if expect_frozen is not None and (facts["business_sha256"] != expect_frozen["business_sha256"] or
                                      facts["counters"] != expect_frozen["counters"]):
        raise AbortError("商品或库存数据不再匹配维护冻结摘要")
    return facts


def _uploads_content_digest_directory(directory):
    from app.inventory_backup import _walk_uploads

    entries = []
    for path, archive_name, is_directory in _walk_uploads(Path(directory)):
        relative = "/".join(archive_name.split("/")[1:])
        if is_directory:
            entries.append([relative, "directory"])
        else:
            binding = file_binding(path)
            entries.append([relative, "file", binding["size"], binding["sha256"]])
    return _digest(sorted(entries, key=lambda item: (item[0], item[1])))


def _uploads_content_digest_archive(archive_path):
    import tarfile
    from app.inventory_backup import _validate_archive_members

    entries = []
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member, parts, is_directory in _validate_archive_members(archive.getmembers()):
                relative = "/".join(parts[1:])
                if is_directory:
                    entries.append([relative, "directory"])
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise AbortError("完整备份图片归档无法读取文件")
                digest = hashlib.sha256()
                size = 0
                with stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                if size != member.size:
                    raise AbortError("完整备份图片归档文件长度不匹配")
                entries.append([relative, "file", size, digest.hexdigest()])
    except (OSError, EOFError, tarfile.TarError) as error:
        raise AbortError("完整备份图片归档无法安全核验") from error
    return _digest(sorted(entries, key=lambda item: (item[0], item[1])))


def _bundle_paths(context, release):
    if not RELEASE_RE.fullmatch(release):
        raise AbortError("发布 ID 格式无效")
    release_dir = context.backups / release
    return {
        "release_dir": release_dir,
        "bundle": release_dir / "numbering-abort.json",
        "baseline": release_dir / "numbering-abort-baseline.json",
        "previous_metadata": release_dir / "previous-deployed.json",
        "api": release_dir / "api",
        "venv": release_dir / "old-venv",
        "static": release_dir / "old-static",
        "backup": release_dir / "full",
        "progress": release_dir / "numbering-abort-progress.json",
    }


def _load_bundle(context, operation_id, release):
    if not OPERATION_RE.fullmatch(operation_id):
        raise AbortError("操作 ID 格式无效")
    paths = _bundle_paths(context, release)
    bundle = _read_json(paths["bundle"], "部署器生成的中止恢复包")
    if (bundle.get("format") != ABORT_BUNDLE or bundle.get("state") != "ready" or
            bundle.get("operation_id") != operation_id or bundle.get("release") != release or
            bundle.get("release_sha") != release.split("-", 2)[2]):
        raise AbortError("恢复包与操作 ID、发布 ID 或发布摘要不匹配")
    exact_paths = {
        "previous_metadata": paths["previous_metadata"],
        "old_api": paths["api"],
        "old_venv": paths["venv"],
        "old_static": paths["static"],
    }
    for key, expected in exact_paths.items():
        if bundle.get(key) != str(expected):
            raise AbortError("恢复包中的路径与本机受控路径不匹配")
    backup_directory = Path(bundle.get("backup_directory", ""))
    if (backup_directory.parent != paths["backup"] or
            not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}", backup_directory.name) or
            backup_directory.is_symlink() or not backup_directory.is_dir()):
        raise AbortError("恢复包中的完整备份目录不在本次发布的隔离备份集中")
    paths["backup"] = backup_directory
    return paths, bundle


def _load_baseline(context, operation_id, release):
    paths = _bundle_paths(context, release)
    baseline = _read_json(paths["baseline"], "部署前旧版本恢复基线")
    if (baseline.get("format") != ABORT_BASELINE or baseline.get("state") != "captured" or
            baseline.get("operation_id") != operation_id or baseline.get("release") != release or
            baseline.get("release_sha") != release.split("-", 2)[2]):
        raise AbortError("部署前恢复基线与本次 operation/release 不匹配")
    expected_paths = {
        "previous_metadata": paths["previous_metadata"],
        "old_api": paths["api"],
        "old_venv": paths["venv"],
        "old_static": paths["static"],
    }
    for key, expected in expected_paths.items():
        if baseline.get(key) != str(expected):
            raise AbortError("部署前恢复基线的旧产物路径不匹配")
    if not re.fullmatch(r"[0-9a-f]{40}", str(baseline.get("previous_commit", ""))):
        raise AbortError("部署前恢复基线缺少旧版本 commit")
    if not re.fullmatch(r"[0-9a-f]{64}", str(baseline.get("database_schema_sha256", ""))):
        raise AbortError("部署前恢复基线缺少数据库结构摘要")
    if not isinstance(baseline.get("timers_were_active"), list) or any(
            timer not in TIMERS for timer in baseline["timers_were_active"]):
        raise AbortError("部署前恢复基线的 timer 清单无效")
    if baseline.get("proxy_was_active") is not True:
        raise AbortError("部署前 Nginx 状态不符合恢复条件")
    return paths, baseline


def _old_metadata(context, bundle):
    previous = _read_json(bundle["previous_metadata"], "切换前部署版本记录")
    if (file_binding(bundle["previous_metadata"])["sha256"] != bundle.get("previous_metadata_sha256") or
            previous.get("commit") != bundle.get("previous_commit")):
        raise AbortError("切换前代码版本记录与恢复包不匹配")
    current_path = context.project / ".actions-deployed.json"
    current = _read_json(current_path, "当前部署版本记录")
    if (file_binding(current_path)["sha256"] != bundle.get("previous_metadata_sha256") or
            current != previous):
        raise AbortError("当前部署版本记录已变化，拒绝推断旧代码版本")
    if not re.fullmatch(r"[0-9a-f]{40}", str(previous.get("commit", ""))):
        raise AbortError("切换前部署记录缺少有效 commit")
    return previous


def _artifact_facts(context, bundle):
    paths = _bundle_paths(context, bundle["release"])
    if api_items_digest(paths["api"]) != bundle.get("old_api_tree_sha256"):
        raise AbortError("旧 API 恢复副本摘要不匹配")
    venv_sources = [p for p in (context.api / ".venv", paths["venv"]) if p.exists() or p.is_symlink()]
    venv_matches = [p for p in venv_sources if not p.is_symlink() and tree_digest(p) == bundle.get("old_venv_tree_sha256")]
    if not venv_matches:
        raise AbortError("旧 Python 虚拟环境恢复点缺失或摘要不匹配")
    static_sources = [p for p in (context.static, paths["static"]) if p.exists() or p.is_symlink()]
    static_matches = [p for p in static_sources if not p.is_symlink() and tree_digest(p) == bundle.get("old_static_tree_sha256")]
    if not static_matches:
        raise AbortError("旧静态产物恢复点缺失或摘要不匹配")
    previous = _old_metadata(context, bundle)
    return {
        "old_api_tree_sha256": bundle["old_api_tree_sha256"],
        "old_venv_tree_sha256": bundle["old_venv_tree_sha256"],
        "old_static_tree_sha256": bundle["old_static_tree_sha256"],
        "previous_commit": previous["commit"],
        "previous_release": previous.get("release", previous.get("commit")),
        "available_venv_path": str(venv_matches[0]),
        "available_static_path": str(static_matches[0]),
    }


def _read_progress(path):
    if not os.path.lexists(path):
        return None
    progress = _read_json(path, "中止恢复执行登记")
    if progress.get("format") != ABORT_PROGRESS:
        raise AbortError("中止恢复登记格式无效，保持维护")
    return progress


def _write_progress(context, paths, progress):
    atomic_json(paths["progress"], progress, group=Path(context.database).stat().st_gid)


def _build_plan(context, operation_id, release):
    paths, bundle = _load_bundle(context, operation_id, release)
    maintenance = Maintenance(context.data_root)
    state = maintenance.require(operation_id, context.database)
    if state.get("release_sha") != bundle["release_sha"] or not state.get("frozen_business_sha256"):
        raise AbortError("维护状态未完整冻结或与发布不匹配")
    if state.get("database_identity") != bundle.get("database_identity_before_deploy"):
        raise AbortError("数据库身份与部署前维护绑定不一致")
    if bundle.get("frozen_business_sha256") != state["frozen_business_sha256"] or bundle.get("frozen_counters") != state.get("frozen_counters"):
        raise AbortError("恢复包的冻结数据摘要与维护状态不一致")
    context.drained()
    if hasattr(context, "api_startup_safe"):
        context.api_startup_safe()
    db = _database_facts(context, expect_frozen={
        "business_sha256": state["frozen_business_sha256"],
        "counters": state.get("frozen_counters", {}),
    })
    db["uploads_content_sha256"] = _uploads_content_digest_directory(context.data_root / "uploads")
    previous = _old_metadata(context, bundle)
    artifact = _artifact_facts(context, bundle)
    backup = cutover.verify_backup(
        context, paths["backup"] / "inventory.db", paths["backup"] / "manifest.json", state
    )
    if (bundle.get("backup_manifest_sha256") != backup["manifest"]["sha256"] or
            bundle.get("backup_database_sha256") != backup["database"]["sha256"] or
            bundle.get("backup_uploads_sha256") != backup["uploads"]["sha256"]):
        raise AbortError("维护窗口完整备份摘要与恢复包不一致")
    if bundle.get("proxy_was_active") is not True:
        raise AbortError("部署前 Nginx 未处于预期服务状态，拒绝自动恢复服务")
    active_timers = bundle.get("timers_were_active")
    if not isinstance(active_timers, list) or any(timer not in TIMERS for timer in active_timers):
        raise AbortError("恢复包中的备份 timer 状态无效")
    body = {
        "format": "numbering-precommit-abort-plan-v1",
        "operation_id": operation_id,
        "release": release,
        "release_sha": bundle["release_sha"],
        "database": db,
        "maintenance": {
            "state": "held",
            "operation_id": state["operation_id"],
            "release_sha": state["release_sha"],
            "database_identity": state["database_identity"],
            "frozen_business_sha256": state["frozen_business_sha256"],
            "frozen_counters": state.get("frozen_counters", {}),
        },
        "backup": backup,
        "artifacts": artifact,
        "proxy_was_active": bundle["proxy_was_active"],
        "timers_were_active": active_timers,
        "decision": "restore full frozen backup and previous release; keep maintenance until both API probes pass",
    }
    return {**body, "plan_sha256": _digest(body), "state": "precommit_abort_ready", "dry_run": True}


def _verify_bound_backup(context, paths, state, expected):
    """Recheck an already approved backup by immutable file bindings on resume.

    The ordinary cutover verifier requires a backup younger than one hour.
    Once this abort plan has been durably approved, an interrupted recovery may
    resume later; the same content-addressed backup remains valid while every
    file binding and the frozen business snapshot still match.
    """
    database = paths["backup"] / "inventory.db"
    manifest = paths["backup"] / "manifest.json"
    uploads = paths["backup"] / "uploads.tar.gz"
    context.backup_paths(database, manifest)
    actual = {
        "database": file_binding(database),
        "manifest": file_binding(manifest),
        "uploads": file_binding(uploads),
    }
    if actual != expected:
        raise AbortError("已批准的完整备份文件身份或摘要发生变化")
    try:
        metadata = json.loads(manifest.read_text(encoding="utf8"))
    except (OSError, ValueError) as error:
        raise AbortError("已批准的完整备份 manifest 缺失或损坏") from error
    if (metadata.get("database_filename") != database.name or
            metadata.get("database_sha256") != actual["database"]["sha256"] or
            metadata.get("database_size") != actual["database"]["size"] or
            metadata.get("uploads_archive") != uploads.name or
            metadata.get("uploads_sha256") != actual["uploads"]["sha256"] or
            metadata.get("uploads_archive_size") != actual["uploads"]["size"]):
        raise AbortError("已批准的完整备份内容与 manifest 不匹配")
    with closing(cutover.connect(database)) as db:
        cutover.check(db, require_head=False)
        if (cutover.digest(cutover.snapshot(db)) != state["frozen_business_sha256"] or
                (dict(db.execute("SELECT period_ym,next_sequence FROM product_code_months"))
                 if "product_code_months" in {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                 else {}) != state.get("frozen_counters", {})):
            raise AbortError("已批准的完整备份不再匹配冻结业务状态")
    return actual


def plan(context, operation_id, release):
    with context.locked(), file_gate(Maintenance(context.data_root).gate):
        paths = _bundle_paths(context, release)
        progress = _read_progress(paths["progress"])
        if progress:
            if progress.get("operation_id") != operation_id or progress.get("release") != release:
                raise AbortError("现有中止执行登记属于其他操作")
            raise AbortError("中止已经开始；先使用 status 查看进度，不重新生成确认摘要")
        return _build_plan(context, operation_id, release)


def prepare(context, operation_id, release, *, confirm=False):
    """Complete the recovery bundle after an early hold/backup failure.

    This action never installs release code or migrates the database. It stops
    writers, completes the same operation's freeze if needed, and captures a
    full backup plus the old venv only while the pre-deploy schema and old
    artifacts still match the immutable baseline.
    """
    if not confirm:
        raise AbortError("准备恢复包会停止业务服务并创建完整备份；必须显式确认")
    with context.locked():
        paths, baseline = _load_baseline(context, operation_id, release)
        if os.path.lexists(paths["bundle"]):
            raise AbortError("完整恢复包已存在；直接使用只读 plan，不重复准备")
        if (database_identity(context.database) != baseline.get("database_identity_before_deploy") or
                database_schema_digest(context.database) != baseline.get("database_schema_sha256")):
            raise AbortError("数据库身份或结构已变化；拒绝从早期失败状态准备恢复包")
        current = _database_facts(context)
        if current["revision"] != baseline.get("database_revision_before_deploy"):
            raise AbortError("数据库 revision 已变化；可能已开始结构迁移，拒绝早期恢复包准备")
        previous_path = paths["previous_metadata"]
        previous = _read_json(previous_path, "切换前部署版本记录")
        marker = context.project / ".actions-deployed.json"
        current_marker = _read_json(marker, "当前部署版本记录")
        if (previous != current_marker or previous.get("commit") != baseline.get("previous_commit") or
                file_binding(previous_path)["sha256"] != baseline.get("previous_metadata_sha256") or
                file_binding(marker)["sha256"] != baseline.get("previous_metadata_sha256")):
            raise AbortError("当前运行标记不再匹配切换前旧版本")
        if (api_items_digest(paths["api"]) != baseline.get("old_api_tree_sha256") or
                api_items_digest(context.api) != baseline.get("old_api_tree_sha256") or
                tree_digest(context.api / ".venv") != baseline.get("old_venv_tree_sha256") or
                tree_digest(context.static) != baseline.get("old_static_tree_sha256") or
                tree_digest(paths["static"]) != baseline.get("old_static_tree_sha256")):
            raise AbortError("旧 API、虚拟环境或静态恢复点已变化")

        context.stop_timers()
        context.stop_api()
        context.drained()
        maintenance = Maintenance(context.data_root)
        state = maintenance.read()
        if state is None:
            maintenance.enter(operation_id, baseline["release_sha"], context.database, None)
            state = maintenance.read()
        if (not state or state.get("state") != "held" or state.get("operation_id") != operation_id or
                state.get("release_sha") != baseline["release_sha"] or
                state.get("database_identity") != database_identity(context.database)):
            raise AbortError("维护状态不是本次 operation/release 的未完成冻结")
        if state.get("frozen_business_sha256") is None:
            cutover.enter(context, operation_id, baseline["release_sha"])
        else:
            if state.get("drain_policy") != getattr(context, "policy", None):
                raise AbortError("已有维护冻结的排空清单与当前策略不符")
            _database_facts(context, expect_frozen={
                "business_sha256": state["frozen_business_sha256"],
                "counters": state.get("frozen_counters", {}),
            })

        state = maintenance.require(operation_id, context.database)
        current = _database_facts(context, expect_frozen={
            "business_sha256": state["frozen_business_sha256"],
            "counters": state.get("frozen_counters", {}),
        })
        if (current["revision"] != baseline.get("database_revision_before_deploy") or
                database_schema_digest(context.database) != baseline.get("database_schema_sha256")):
            raise AbortError("完成冻结后发现数据库结构/revision 已变化，拒绝准备恢复包")

        full_root = paths["backup"]
        checked_path(full_root)
        if os.path.lexists(full_root) and (full_root.is_symlink() or not full_root.is_dir()):
            raise AbortError("完整备份目录路径无效")
        full_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        if hasattr(context, "prepare_backup_root"):
            context.prepare_backup_root(full_root)
        manifests = list(full_root.glob("*/manifest.json"))
        if not manifests:
            context.create_abort_backup(full_root)
            manifests = list(full_root.glob("*/manifest.json"))
        candidates = sorted((manifest.parent for manifest in manifests), key=lambda p: p.name)
        if not candidates:
            raise AbortError("没有生成完整备份；维护保持冻结")
        backup_directory = candidates[-1]
        backup = cutover.verify_backup(
            context, backup_directory / "inventory.db", backup_directory / "manifest.json", state
        )
        if (file_binding(backup_directory / "inventory.db")["sha256"] != backup["database"]["sha256"] or
                file_binding(backup_directory / "uploads.tar.gz")["sha256"] != backup["uploads"]["sha256"]):
            raise AbortError("新生成的完整备份在核验后发生变化")
        write_abort_bundle(context, paths["release_dir"], release, operation_id,
                           baseline["release_sha"], baseline, backup_directory,
                           ensure_venv_copy=True)
        return {
            "state": "recovery_bundle_ready",
            "operation_id": operation_id,
            "release": release,
            "backup_directory": str(backup_directory),
            "database_revision": current["revision"],
            "database_identity": current["database_identity"],
            "backup_database_sha256": backup["database"]["sha256"],
            "backup_manifest_sha256": backup["manifest"]["sha256"],
            "maintenance": "held",
            "next_action": "plan",
        }


def _set_maintenance_state(context, state):
    maintenance = Maintenance(context.data_root)
    atomic_json(maintenance.path, state, group=Path(context.database).stat().st_gid)


def _rebind_after_restore(context, original_state, bundle, paths):
    facts = _database_facts(context, expect_frozen={
        "business_sha256": original_state["frozen_business_sha256"],
        "counters": original_state.get("frozen_counters", {}),
    })
    backup_db = paths["backup"] / "inventory.db"
    if file_binding(context.database)["sha256"] != file_binding(backup_db)["sha256"]:
        raise AbortError("恢复后的数据库与维护窗口原始数据库备份字节摘要不一致")
    with closing(cutover.connect(backup_db)) as source:
        cutover.check(source, require_head=False)
        source_revision = source.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if facts["revision"] != source_revision:
        raise AbortError("恢复后的 Alembic revision 与完整备份不一致")
    maintenance = Maintenance(context.data_root)
    state = maintenance.read()
    if not state or state.get("state") != "held" or state.get("operation_id") != original_state["operation_id"]:
        raise AbortError("恢复期间维护状态变化，拒绝重新绑定")
    current_identity = database_identity(context.database)
    if state.get("database_identity") != current_identity:
        state = {**state, "database_identity": current_identity,
                 "abort_database_restore": {
                     "release": bundle["release"],
                     "backup_database_sha256": file_binding(backup_db)["sha256"],
                     "database_revision": source_revision,
                     "verified_at_ns": time.time_ns(),
                 }}
        _set_maintenance_state(context, state)
    return state


def _safe_remove(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode):
        path.unlink()
    elif stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        raise AbortError("拒绝清除恢复目标中的特殊文件")


def _restore_tree(source, target, *, replace=True):
    source, target = Path(source), Path(target)
    source_digest = artifact_digest(source)
    if (target.exists() or target.is_symlink()) and artifact_digest(target) == source_digest:
        return
    temporary = target.with_name("." + target.name + ".abort-restore")
    _safe_remove(temporary)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, temporary, symlinks=True)
    elif source.is_symlink():
        temporary.symlink_to(os.readlink(source), target_is_directory=False)
    else:
        shutil.copy2(source, temporary, follow_symlinks=False)
    if artifact_digest(temporary) != source_digest:
        _safe_remove(temporary)
        raise AbortError("恢复临时产物摘要不匹配")
    _safe_remove(target)
    os.replace(temporary, target)
    if artifact_digest(target) != source_digest:
        raise AbortError("恢复产物安装后的摘要不匹配")


def _restore_old_artifacts(context, bundle, paths):
    if api_items_digest(paths["api"]) != bundle["old_api_tree_sha256"]:
        raise AbortError("旧 API 恢复副本摘要不匹配")
    if tree_digest(paths["venv"]) != bundle["old_venv_tree_sha256"]:
        raise AbortError("旧虚拟环境恢复副本摘要不匹配")
    if tree_digest(paths["static"]) != bundle["old_static_tree_sha256"]:
        raise AbortError("旧静态产物恢复副本摘要不匹配")
    for item in API_ITEMS:
        _restore_tree(paths["api"] / item, context.api / item)
        context.chown_inventory(context.api / item)
    _restore_tree(paths["venv"], context.api / ".venv")
    context.chown_inventory(context.api / ".venv")
    _restore_tree(paths["static"], context.static)
    if _old_metadata(context, bundle).get("commit") != bundle["previous_commit"]:
        raise AbortError("恢复旧版本后部署记录不匹配")
    result = _artifact_facts(context, bundle)
    if result["available_venv_path"] != str(context.api / ".venv") or result["available_static_path"] != str(context.static):
        raise AbortError("恢复后旧虚拟环境或静态产物未安装到服务路径")
    return result


def _write_probe_marker(context, maintenance_state, bundle, plan_sha256):
    marker = {
        "format": "numbering-abort-probe-v1",
        "state": "authorized",
        "operation_id": bundle["operation_id"],
        "release_sha": bundle["release_sha"],
        "previous_commit": bundle["previous_commit"],
        "database_identity": database_identity(context.database),
        "plan_sha256": plan_sha256,
        "expires_at_ns": time.time_ns() + 10 * 60 * 10**9,
    }
    _set_maintenance_state(context, {**maintenance_state, "abort_probe": marker})
    return marker


def _clear_probe_marker(context, operation_id):
    maintenance = Maintenance(context.data_root)
    state = maintenance.read()
    if state and state.get("operation_id") == operation_id and "abort_probe" in state:
        state = dict(state)
        state.pop("abort_probe", None)
        _set_maintenance_state(context, state)


def _verify_not_committed(context):
    facts = _database_facts(context)
    if facts["cutover_registry"] != "absent" and facts["cutover_registry"] != "empty":
        raise AbortError("编号换号登记状态不明，拒绝恢复")
    return facts


def _run_readonly_probe(context, state, bundle, plan_sha256):
    context.stop_proxy()
    _verify_not_committed(context)
    _write_probe_marker(context, state, bundle, plan_sha256)
    started = False
    try:
        context.configure_readonly_probe(bundle["operation_id"], context.database)
        context.start_api()
        started = True
        context.health_readonly()
    finally:
        if started or context.api_active():
            context.stop_api()
        context.clear_readonly_probe(bundle["operation_id"])
        _clear_probe_marker(context, bundle["operation_id"])
    return True


def _save_or_resume_plan(context, operation_id, release, expected_plan_sha256):
    paths, bundle = _load_bundle(context, operation_id, release)
    progress = _read_progress(paths["progress"])
    if progress is None:
        report = _build_plan(context, operation_id, release)
        if report["plan_sha256"] != expected_plan_sha256:
            raise AbortError("确认摘要与当前恢复预检不匹配")
        progress = {
            "format": ABORT_PROGRESS,
            "state": "in_progress",
            "phase": "authorized",
            "operation_id": operation_id,
            "release": release,
            "plan_sha256": report["plan_sha256"],
            "plan": report,
            "database_identity_before_restore": report["database"]["database_identity"],
            "started_at_ns": time.time_ns(),
        }
        _write_progress(context, paths, progress)
        return paths, bundle, progress, report
    plan = progress.get("plan")
    if (progress.get("operation_id") != operation_id or progress.get("release") != release or
            progress.get("plan_sha256") != expected_plan_sha256 or not isinstance(plan, dict) or
            _digest({k: v for k, v in plan.items() if k not in {"plan_sha256", "state", "dry_run"}}) != expected_plan_sha256):
        raise AbortError("中止恢复登记与确认摘要不匹配")
    return paths, bundle, progress, plan


def _source_database_matches_plan(context, progress):
    planned = progress.get("plan", {}).get("database", {})
    required = ("database_identity", "database_binding", "revision", "schema_sha256",
                "business_sha256", "counters", "cutover_registry", "integrity_check",
                "foreign_key_check", "product_count", "uploads_content_sha256")
    if any(key not in planned for key in required):
        raise AbortError("已保存计划缺少数据库/图片源状态摘要；拒绝续做")
    actual = _database_facts(context)
    actual["uploads_content_sha256"] = _uploads_content_digest_directory(context.data_root / "uploads")
    if any(actual.get(key) != planned.get(key) for key in required):
        raise AbortError("授权后的源数据库、结构、图片或冻结业务状态发生变化；拒绝覆盖")
    return actual


def _restored_database_matches(context, paths, progress, bundle, state, *, require_saved_identity):
    frozen = {
        "business_sha256": state["frozen_business_sha256"],
        "counters": state.get("frozen_counters", {}),
    }
    actual = _database_facts(context, expect_frozen=frozen)
    backup_path = paths["backup"] / "inventory.db"
    backup_facts = _database_facts_path(backup_path)
    target_binding = actual["database_binding"]
    backup_binding = backup_facts["database_binding"]
    comparisons = ("revision", "schema_sha256", "business_sha256", "counters",
                   "cutover_registry", "integrity_check", "foreign_key_check", "product_count")
    if (target_binding["sha256"] != bundle.get("backup_database_sha256") or
            target_binding["size"] != backup_binding["size"] or
            backup_binding["sha256"] != bundle.get("backup_database_sha256") or
            any(actual.get(key) != backup_facts.get(key) for key in comparisons)):
        raise AbortError("当前数据库不是完整备份对应的已验证恢复状态")
    actual_uploads = _uploads_content_digest_directory(context.data_root / "uploads")
    backup_uploads = _uploads_content_digest_archive(paths["backup"] / "uploads.tar.gz")
    if actual_uploads != backup_uploads:
        raise AbortError("当前图片目录不是完整备份对应的已验证恢复状态")
    saved_identity = progress.get("restored_database_identity")
    if require_saved_identity and (not saved_identity or actual["database_identity"] != saved_identity):
        raise AbortError("恢复后的数据库文件身份发生变化；拒绝续做")
    return actual


def _validate_resume_database_state(context, paths, progress, bundle, state):
    phase = progress.get("phase")
    if phase == "authorized":
        if (state.get("database_identity") != progress.get("database_identity_before_restore") or
                state.get("database_identity") != database_identity(context.database)):
            raise AbortError("授权阶段数据库身份已变化；拒绝开始破坏性恢复")
        return "source", _source_database_matches_plan(context, progress)
    if phase == "database_restore_started":
        # The backup restore helper atomically installs database and uploads.
        # Before mutation, the exact approved source snapshot is safe to retry;
        # after install, only the byte-for-byte backup pair is safe to advance.
        source_identity = progress.get("database_identity_before_restore")
        current_identity = database_identity(context.database)
        if state.get("database_identity") != source_identity and state.get("database_identity") != current_identity:
            raise AbortError("恢复中断阶段维护登记绑定了未知数据库身份；拒绝续做")
        try:
            source = _source_database_matches_plan(context, progress)
            if state.get("database_identity") != source_identity:
                raise AbortError("恢复前数据库状态与维护登记身份不匹配")
            return "source", source
        except (AbortError, MaintenanceError, OSError, sqlite3.Error):
            try:
                restored = _restored_database_matches(
                    context, paths, progress, bundle, state, require_saved_identity=False
                )
                return "restored", restored
            except (AbortError, MaintenanceError, OSError, sqlite3.Error) as error:
                raise AbortError(
                    "database_restore_started 階段狀態既不是授權源快照，也不是完整驗證備份；保持維護並人工檢查"
                ) from error
    if phase in {"database_restored", "code_restore_started", "artifacts_restored",
                 "probe_started", "probe_passed", "resume_starting", "ready_to_release"}:
        if state.get("database_identity") != database_identity(context.database):
            raise AbortError("恢复后数据库身份与维护登记不匹配；拒绝续做")
        return "restored", _restored_database_matches(
            context, paths, progress, bundle, state, require_saved_identity=True
        )
    raise AbortError("中止恢復階段無法識別；保持維護並人工檢查")


def _read_abort_resume_state(context, operation_id, bundle, progress):
    state = Maintenance(context.data_root).read()
    phase = progress.get("phase")
    if (not state or state.get("state") not in {"held", "abort_resuming"} or
            state.get("operation_id") != operation_id or
            state.get("release_sha") != bundle.get("release_sha")):
        raise AbortError("中止恢复维护状态、operation 或 release 不匹配")
    current_identity = database_identity(context.database)
    if phase == "database_restore_started":
        source_identity = progress.get("database_identity_before_restore")
        if state.get("database_identity") != source_identity and state.get("database_identity") != current_identity:
            raise AbortError("恢复中断阶段维护状态绑定未知数据库；拒绝续做")
    elif state.get("database_identity") != current_identity:
        raise AbortError("维护状态与当前数据库文件身份不匹配")
    return state


def _update_progress(context, paths, progress, *, phase, **changes):
    updated = {**progress, **changes, "phase": phase, "updated_at_ns": time.time_ns()}
    _write_progress(context, paths, updated)
    return updated


def _finish_released_resume(context, paths, bundle, progress):
    state = Maintenance(context.data_root).read()
    receipt = state.get("abort_receipt") if state else None
    if not (state and state.get("state") == "released" and state.get("release_reason") == "precommit_abort" and
            receipt and receipt.get("plan_sha256") == progress.get("plan_sha256") and
            state.get("database_identity") == database_identity(context.database)):
        raise AbortError("释放状态与已批准中止登记不匹配")
    checks = {
        "cutover_not_committed": True,
        "database_restored": True,
        "old_code_restored": True,
        "api_readonly_probe": True,
        "inventory_readonly_checks": True,
        "normal_api_health": True,
        "ai_unchanged": True,
        "timers_restored": True,
        "proxy_stopped_until_release": True,
    }
    validation_path = Maintenance(context.data_root).directory / "abort-validation.json"
    validation = _read_json(validation_path, "解除维护前最终恢复登记")
    if (receipt.get("format") != ABORT_RECEIPT or receipt.get("state") != "complete" or
            receipt.get("operation_id") != bundle.get("operation_id") or
            receipt.get("release_sha") != bundle.get("release_sha") or
            receipt.get("database_identity") != state.get("database_identity") or
            receipt.get("previous_commit") != bundle.get("previous_commit") or
            receipt.get("backup_manifest_sha256") != bundle.get("backup_manifest_sha256") or
            receipt.get("checks") != checks or
            validation.get("format") != "numbering-abort-validation-v1" or
            validation.get("state") != "ready_to_release" or
            validation.get("operation_id") != receipt.get("operation_id") or
            validation.get("release_sha") != receipt.get("release_sha") or
            validation.get("database_identity") != receipt.get("database_identity") or
            validation.get("plan_sha256") != receipt.get("plan_sha256") or
            validation.get("previous_commit") != receipt.get("previous_commit") or
            validation.get("checks") != checks or
            not isinstance(progress.get("plan"), dict) or
            progress.get("phase") not in {"ready_to_release", "released"} or
            receipt.get("plan_sha256") != progress.get("plan_sha256")):
        raise AbortError("释放状态缺少完整的最终恢复登记；拒绝推断恢复已完成")
    _verify_not_committed(context)
    _old_metadata(context, bundle)
    # Once the atomic release receipt is durable, regular old-version writes
    # may already have occurred. Recheck integrity/identity, but do not compare
    # against the pre-release frozen business digest or rewrite restored data.
    db_facts = _database_facts(context)
    if db_facts["database_identity"] != receipt["database_identity"]:
        raise AbortError("最终恢复后的数据库身份变化；拒绝完成报告")
    if hasattr(context, "api_active") and not context.api_active():
        if hasattr(context, "api_startup_safe"):
            context.api_startup_safe()
        context.start_api()
    context.health_readonly()
    context.start_timers(bundle.get("timers_were_active", []))
    if bundle.get("proxy_was_active"):
        if not context.proxy_active():
            context.start_proxy()
        if not context.proxy_active():
            raise AbortError("恢复登记已释放，但 Nginx 未恢复；可安全续做服务恢复")
    # The complete receipt and all pre-release gates precede atomic admission
    # release. This resume path can idempotently finish service activation and
    # repair a missing report without repeating any database/artifact restore.
    return _update_progress(context, paths, progress, phase="complete", state="complete",
                            abort_receipt=receipt, completed_at_ns=time.time_ns())


def _cleanup_held_resume(context, paths, progress, error):
    cleanup_errors = []
    for action in (context.stop_proxy, context.stop_timers, context.stop_api):
        try:
            action()
        except BaseException as cleanup_error:
            cleanup_errors.append(f"{type(cleanup_error).__name__}: {cleanup_error}")
    try:
        state = Maintenance(context.data_root).read()
        if state and state.get("state") == "released":
            return False, cleanup_errors
        if not state or state.get("state") not in {"held", "abort_resuming"}:
            cleanup_errors.append("维护状态无法确认仍处于冻结")
            return False, cleanup_errors
        _update_progress(context, paths, progress, phase=progress.get("phase", "in_progress"),
                         state="failed_maintenance_held",
                         last_error=f"{type(error).__name__}: {error}",
                         cleanup_errors=cleanup_errors)
    except BaseException as progress_error:
        cleanup_errors.append(f"恢复失败登记写入失败：{type(progress_error).__name__}")
    return True, cleanup_errors


def execute(context, operation_id, release, *, confirm=False, expected_plan_sha256=None):
    if not confirm or not isinstance(expected_plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256):
        raise AbortError("必须显式确认中止恢复，并提供本次预检摘要")
    with context.locked(), file_gate(Maintenance(context.data_root).gate):
        paths, bundle = _load_bundle(context, operation_id, release)
        progress = _read_progress(paths["progress"])
        if progress and progress.get("phase") == "complete":
            raise AbortError("该中止恢复已完成；禁止重复执行")
        state_now = Maintenance(context.data_root).read()
        if state_now and state_now.get("state") == "released" and state_now.get("release_reason") == "precommit_abort":
            if (not progress or progress.get("phase") not in {"ready_to_release", "released"} or
                    progress.get("plan_sha256") != expected_plan_sha256):
                raise AbortError("维护已经释放但中止执行登记缺失；不能自动推断恢复状态")
            paths, bundle, progress, _report = _save_or_resume_plan(
                context, operation_id, release, expected_plan_sha256
            )
            return _finish_released_resume(context, paths, bundle, progress)
        paths, bundle, progress, report = _save_or_resume_plan(context, operation_id, release, expected_plan_sha256)
        if hasattr(context, "api_startup_safe"):
            context.api_startup_safe()
        try:
            maintenance = Maintenance(context.data_root)
            initial_state = _read_abort_resume_state(context, operation_id, bundle, progress)
            if (initial_state.get("release_sha") != bundle.get("release_sha") or
                    initial_state.get("frozen_business_sha256") != report.get("maintenance", {}).get("frozen_business_sha256")):
                raise AbortError("维护 operation/release 或冻结摘要与批准计划不匹配")
            _verify_not_committed(context)
            backup = _verify_bound_backup(context, paths, initial_state, report["backup"])
            if (backup["manifest"]["sha256"] != bundle.get("backup_manifest_sha256") or
                    backup["database"]["sha256"] != bundle.get("backup_database_sha256") or
                    backup["uploads"]["sha256"] != bundle.get("backup_uploads_sha256")):
                raise AbortError("执行前完整备份摘要发生变化")

            # A restart may have reactivated Nginx, API, or a timer after a
            # previous process stopped. Reassert the private window before
            # checking drain state or touching database/artifact files.
            context.stop_proxy()
            context.stop_timers()
            context.stop_api()
            context.drained()

            initial_state = _read_abort_resume_state(context, operation_id, bundle, progress)
            _verify_not_committed(context)
            _verify_bound_backup(context, paths, initial_state, report["backup"])
            phase = progress.get("phase")
            db_state, _db_facts = _validate_resume_database_state(
                context, paths, progress, bundle, initial_state
            )

            if phase == "authorized":
                progress = _update_progress(context, paths, progress, phase="database_restore_started")
                phase = "database_restore_started"
                db_state = "source"
            if phase == "database_restore_started":
                if db_state == "source":
                    context.restore_database(bundle)
                # A crash after the restore helper returned but before the
                # progress checkpoint is resumable only when the installed DB
                # and uploads exactly match this approved backup.
                state_after_restore = _rebind_after_restore(context, initial_state, bundle, paths)
                db_facts = _restored_database_matches(
                    context, paths, progress, bundle, state_after_restore, require_saved_identity=False
                )
                progress = _update_progress(context, paths, progress, phase="database_restored",
                                            restored_database_identity=db_facts["database_identity"],
                                            restored_revision=db_facts["revision"])
                phase = "database_restored"
            else:
                db_facts = _restored_database_matches(
                    context, paths, progress, bundle, initial_state, require_saved_identity=True
                )
                if initial_state.get("state") == "held":
                    state_after_restore = _rebind_after_restore(context, initial_state, bundle, paths)
                else:
                    state_after_restore = initial_state

            if phase in {"database_restored", "code_restore_started"}:
                progress = _update_progress(context, paths, progress, phase="code_restore_started")
                _restore_old_artifacts(context, bundle, paths)
                db_facts = _restored_database_matches(
                    context, paths, progress, bundle, state_after_restore, require_saved_identity=True
                )
                progress = _update_progress(context, paths, progress, phase="artifacts_restored",
                                            restored_database_identity=db_facts["database_identity"])
                phase = "artifacts_restored"
            else:
                _restore_old_artifacts(context, bundle, paths)

            state_after_restore = maintenance.require_abort(operation_id, context.database)
            _verify_not_committed(context)
            if phase not in {"probe_passed", "resume_starting", "ready_to_release"}:
                progress = _update_progress(context, paths, progress, phase="probe_started")
                if not _run_readonly_probe(context, state_after_restore, bundle, expected_plan_sha256):
                    raise AbortError("旧服务只读探测失败")
                db_after_probe = _restored_database_matches(
                    context, paths, progress, bundle, state_after_restore, require_saved_identity=True
                )
                if db_after_probe["database_identity"] != db_facts["database_identity"]:
                    raise AbortError("只读服务探测期间数据库身份变化")
                progress = _update_progress(context, paths, progress, phase="probe_passed",
                                            probe_database_identity=db_after_probe["database_identity"])

            state_after_restore = maintenance.begin_abort_resume(
                operation_id, context.database, release_sha=bundle["release_sha"],
                plan_sha256=expected_plan_sha256, previous_commit=bundle["previous_commit"],
                expires_at_ns=time.time_ns() + 10 * 60 * 10**9,
            )
            progress = _update_progress(context, paths, progress, phase="resume_starting", state="in_progress")
            before_ai = context.ai_fingerprint() if hasattr(context, "ai_fingerprint") else None
            context.start_api()
            context.health_readonly()
            final_db = _database_facts(context, expect_frozen={
                "business_sha256": state_after_restore["frozen_business_sha256"],
                "counters": state_after_restore.get("frozen_counters", {}),
            })
            if final_db["database_identity"] != database_identity(context.database):
                raise AbortError("恢复期间数据库文件身份变化")
            if before_ai is not None and context.ai_fingerprint() != before_ai:
                raise AbortError("旧服务启动改变了 AI 本地文件；保持维护")
            context.start_timers(bundle.get("timers_were_active", []))
            if context.proxy_active():
                raise AbortError("Nginx 必须保持停止，直到中止恢复登记原子释放")
            context.health_readonly()
            _verify_not_committed(context)
            final_db = _database_facts(context, expect_frozen={
                "business_sha256": state_after_restore["frozen_business_sha256"],
                "counters": state_after_restore.get("frozen_counters", {}),
            })
            if before_ai is not None and context.ai_fingerprint() != before_ai:
                raise AbortError("服务恢复期间 AI 本地文件变化；保持维护")
            checks = {
                "cutover_not_committed": True,
                "database_restored": True,
                "old_code_restored": True,
                "api_readonly_probe": True,
                "inventory_readonly_checks": True,
                "normal_api_health": True,
                "ai_unchanged": True,
                "timers_restored": True,
                "proxy_stopped_until_release": True,
            }
            receipt = {
                "format": ABORT_RECEIPT,
                "state": "complete",
                "operation_id": operation_id,
                "release_sha": state_after_restore["release_sha"],
                "database_identity": final_db["database_identity"],
                "previous_commit": bundle["previous_commit"],
                "plan_sha256": expected_plan_sha256,
                "checks": checks,
                "backup_manifest_sha256": bundle["backup_manifest_sha256"],
                "ai_fingerprint_sha256": before_ai,
                "verified_at_ns": time.time_ns(),
            }
            validation = {
                "format": "numbering-abort-validation-v1",
                "state": "ready_to_release",
                "operation_id": operation_id,
                "release_sha": state_after_restore["release_sha"],
                "database_identity": final_db["database_identity"],
                "previous_commit": bundle["previous_commit"],
                "plan_sha256": expected_plan_sha256,
                "checks": checks,
                "verified_at_ns": time.time_ns(),
            }
            atomic_json(maintenance.directory / "abort-validation.json", validation,
                        group=Path(context.database).stat().st_gid)
            progress = _update_progress(context, paths, progress, phase="ready_to_release",
                                        state="ready_to_release", abort_receipt=receipt)
            maintenance.abort_release(operation_id, context.database, receipt)
        except Exception as error:
            held, cleanup_errors = _cleanup_held_resume(context, paths, progress, error)
            if not held:
                raise AbortError(
                    "恢复释放登记可能已经提交；请先读取状态和维护 receipt，禁止再次执行替换"
                ) from error
            if cleanup_errors:
                raise AbortError(
                    f"中止恢复失败并保持维护；清理未完全确认：{'；'.join(cleanup_errors)}"
                ) from error
            raise

        # The receipt and all successful gates are durable before admission is
        # opened. A report failure now is recoverable from this receipt and must
        # never restart/rollback an already released service.
        progress = _update_progress(context, paths, progress, phase="released", state="released",
                                    abort_receipt=receipt)
        return _finish_released_resume(context, paths, bundle, progress)


def status(context, operation_id, release):
    paths = _bundle_paths(context, release)
    progress = _read_progress(paths["progress"])
    maintenance = Maintenance(context.data_root).read()
    committed = None
    try:
        database = checked_path(context.database, existing=True)
        with closing(cutover.connect(database)) as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            committed = bool(db.execute(f"SELECT count(*) FROM {cutover.CONTROL}").fetchone()[0]) if cutover.CONTROL in names else False
        if committed:
            return {"state": "cutover_committed_abort_forbidden", "phase": progress.get("phase") if progress else "not_started",
                    "operation_id": operation_id, "release": release,
                    "maintenance": maintenance.get("state") if maintenance else "missing",
                    "database_identity_matches": bool(maintenance and maintenance.get("database_identity") == database_identity(context.database)),
                    "cutover_committed": True, "plan_sha256": progress.get("plan_sha256") if progress else None}
        facts = _database_facts(context)
    except Exception as error:
        return {"state": "needs_manual_inspection", "phase": progress.get("phase") if progress else "not_started",
                "maintenance": maintenance.get("state") if maintenance else "missing", "database_check": str(error)}
    if committed:
        state = "cutover_committed_abort_forbidden"
    elif progress:
        state = progress.get("state", "in_progress")
    elif maintenance and maintenance.get("state") == "held":
        if os.path.lexists(paths["baseline"]) and not os.path.lexists(paths["bundle"]):
            try:
                _load_baseline(context, operation_id, release)
                state = "held_abort_prepare_available"
            except Exception:
                state = "held_abort_baseline_invalid"
        else:
            state = "held_abort_not_started"
    else:
        state = "no_abort_operation"
    return {"state": state, "phase": progress.get("phase") if progress else "not_started",
            "operation_id": operation_id, "release": release,
            "maintenance": maintenance.get("state") if maintenance else "missing",
            "database_identity_matches": bool(maintenance and maintenance.get("database_identity") == database_identity(context.database)),
            "cutover_committed": committed,
            "plan_sha256": progress.get("plan_sha256") if progress else None}


class ProductionAbortContext:
    """Root-only production adapter. Unit tests use synthetic context objects."""

    project = PROJECT
    api = API_ROOT
    static = STATIC
    backups = BACKUPS
    data_root = DATA_DIR
    service = SERVICE
    timers = TIMERS
    proxy = PROXY

    def __init__(self, database):
        if sys.platform != "linux" or os.geteuid() != 0:
            raise AbortError("生产中止入口仅允许经审查的 Linux root 操作")
        self.production = ProductionEnvironment(database)
        self.database = self.production.database
        if self.data_root != self.database.parent:
            raise AbortError("目标数据库不属于固定生产数据目录")

    def locked(self):
        return self.production.locked()

    def drained(self):
        self.production.drained()

    def backup_paths(self, source, manifest):
        self.production.backup_paths(source, manifest)

    def _systemctl(self, *args, check=True):
        result = subprocess.run(["systemctl", *args], capture_output=True, text=True, check=False)
        if check and result.returncode:
            raise AbortError(f"systemctl {args[0]} {args[1] if len(args) > 1 else ''} 失败")
        return result

    def _active_state(self, unit):
        result = self._systemctl("show", unit, "--property=LoadState,ActiveState", check=False)
        if result.returncode != 0:
            raise AbortError(f"查询服务状态失败（ActiveState）：{unit}")
        properties = dict(
            line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line
        )
        if properties.get("LoadState") != "loaded":
            raise AbortError(f"服务单元未加载或状态不可确认（LoadState）：{unit}")
        state = properties.get("ActiveState", "")
        if state not in {"active", "activating", "deactivating", "reloading", "inactive", "failed"}:
            raise AbortError(f"服务状态无法确认（ActiveState）：{unit}")
        return state

    def api_active(self):
        return self._active_state(self.service) in {"active", "activating", "deactivating", "reloading"}

    def proxy_active(self):
        return self._active_state(self.proxy) in {"active", "activating", "deactivating", "reloading"}

    def stop_proxy(self):
        if self.proxy_active():
            self._systemctl("stop", self.proxy)
        if self.proxy_active():
            raise AbortError("Nginx 未停止，禁止启动只读 API 探测")

    def stop_api(self):
        if self.api_active():
            self._systemctl("stop", self.service)
        if self.api_active():
            raise AbortError("API 未停止")

    def start_api(self):
        self._systemctl("start", self.service)
        if not self.api_active():
            raise AbortError("API 未进入 active")

    def start_timers(self, timers):
        for timer in timers:
            if timer not in self.timers:
                raise AbortError("恢复 timer 名称不在固定清单")
            self._systemctl("start", timer)
            if self._systemctl("is-active", "--quiet", timer, check=False).returncode:
                raise AbortError("备份 timer 未恢复：" + timer)

    def stop_timers(self):
        for timer in self.timers:
            try:
                self._systemctl("stop", timer)
            except Exception as error:
                raise AbortError(f"停止 timer 失败（stop_timers）：{timer}") from error
            try:
                result = self._systemctl(
                    "show", timer, "--property=LoadState,ActiveState", check=False
                )
            except Exception as error:
                raise AbortError(f"查询 timer 状态失败（stop_timers）：{timer}") from error
            if result.returncode != 0:
                raise AbortError(f"查询 timer 状态失败（stop_timers）：{timer}")
            properties = dict(
                line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line
            )
            if properties.get("LoadState") != "loaded":
                raise AbortError(f"timer 单元未加载或状态不可确认（stop_timers）：{timer}")
            active_state = properties.get("ActiveState", "")
            if active_state not in {"inactive", "failed"}:
                raise AbortError(
                    f"未确认 timer 已停止（stop_timers）：{timer}（ActiveState={active_state or 'unknown'}）"
                )

    def start_proxy(self):
        self._systemctl("start", self.proxy)

    def chown_inventory(self, path):
        subprocess.run(["chown", "-R", "inventory:inventory", str(path)], check=True)

    def chown_recovery(self, path):
        subprocess.run(["chown", "-R", "root:inventory", str(path)], check=True)

    def prepare_backup_root(self, path):
        path = checked_path(path)
        if path != self.backups / Path(path).relative_to(self.backups):
            raise AbortError("恢复备份目录不在本次发布的隔离目录")
        os.chown(path, 0, __import__("grp").getgrnam("inventory").gr_gid)
        path.chmod(0o750)

    def create_abort_backup(self, destination):
        destination = checked_path(destination, existing=True)
        if not destination.is_relative_to(self.backups) or destination.is_symlink():
            raise AbortError("中止备份目标不在固定生产备份目录")
        old_python = self.api / ".venv/bin/python"
        backup_script = self.api / "scripts/backup_inventory.py"
        if not old_python.is_file() or old_python.is_symlink() or not backup_script.is_file() or backup_script.is_symlink():
            raise AbortError("旧 Python 或业务备份程序缺失/被别名")
        env = [f"DATABASE_URL=sqlite:///{self.database.as_posix()}",
               f"INVENTORY_DATA_DIR={self.data_root}"]
        command = ["runuser", "-u", "inventory", "--", "env", *env,
                   str(old_python), str(backup_script), "--destination", str(destination)]
        subprocess.run(command, check=True, capture_output=True, text=True)

    def restore_database(self, bundle):
        old_python = Path(bundle["old_venv"]) / "bin/python"
        restore_script = Path(bundle["old_api"]) / "scripts/restore_inventory.py"
        if not old_python.is_file() or not restore_script.is_file():
            raise AbortError("旧 Python 或恢复程序缺失")
        env = [f"DATABASE_URL=sqlite:///{self.database.as_posix()}", f"INVENTORY_DATA_DIR={self.data_root}"]
        command = ["runuser", "-u", "inventory", "--", "env", *env,
                   str(old_python), str(restore_script), "--backup", bundle["backup_directory"],
                   "--target", str(self.data_root), "--force"]
        subprocess.run(command, check=True, capture_output=True, text=True)

    def configure_readonly_probe(self, operation_id, database):
        if not OPERATION_RE.fullmatch(operation_id):
            raise AbortError("操作 ID 无效")
        self.stop_proxy()
        runtime_root = RUNTIME_PROBE_ROOT / operation_id
        data_parent = PROBE_DATA_ROOT / operation_id
        data_root = data_parent / "data"
        for root, target in ((RUNTIME_PROBE_ROOT, runtime_root), (PROBE_DATA_ROOT, data_parent)):
            checked_path(root)
            checked_path(target)
            if os.path.lexists(target):
                if target.is_symlink() or target.resolve().parent != root.resolve():
                    raise AbortError("旧 abort probe 临时目录存在路径别名")
                shutil.rmtree(target)
        runtime_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        data_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        subprocess.run(["chown", "-R", "inventory:inventory", str(PROBE_DATA_ROOT / operation_id)], check=True)
        runtime_root.chmod(0o700)
        if os.path.lexists(RUNTIME_DROPIN_ROOT) and RUNTIME_DROPIN_ROOT.is_symlink():
            raise AbortError("systemd runtime drop-in 目录是符号链接")
        RUNTIME_DROPIN_ROOT.mkdir(parents=True, exist_ok=True)
        dropin = RUNTIME_DROPIN_ROOT / "numbering-abort-probe.conf"
        if os.path.lexists(dropin):
            if dropin.is_symlink() or "numbering-abort-probe.conf" not in dropin.name:
                raise AbortError("abort probe drop-in 是链接或路径异常")
            _safe_remove(dropin)
        database_uri = f"sqlite:///file:{database.as_posix()}?mode=ro&uri=true"
        env_path = runtime_root / "probe.env"
        atomic_text(env_path, f"DATABASE_URL={database_uri}\nINVENTORY_DATA_DIR={data_root}\n")
        atomic_text(dropin,
                    "[Service]\nEnvironmentFile=\nEnvironmentFile=/etc/inventory-system.env\n"
                    f"EnvironmentFile={env_path}\nUnsetEnvironment=DASHSCOPE_API_KEY\n")
        self._systemctl("daemon-reload")

    def clear_readonly_probe(self, operation_id):
        self.stop_api()
        dropin = RUNTIME_DROPIN_ROOT / "numbering-abort-probe.conf"
        if dropin.exists() or dropin.is_symlink():
            _safe_remove(dropin)
        for root, target in ((RUNTIME_PROBE_ROOT, RUNTIME_PROBE_ROOT / operation_id),
                             (PROBE_DATA_ROOT, PROBE_DATA_ROOT / operation_id)):
            checked_path(root)
            checked_path(target)
            if os.path.lexists(target):
                if target.is_symlink() or target.resolve().parent != root.resolve():
                    raise AbortError("只读探测临时目录存在路径别名，拒绝清理")
                shutil.rmtree(target)
        self._systemctl("daemon-reload")

    def health_readonly(self):
        if not self.api_active():
            raise AbortError("只读探测 API 未运行")
        for url in (HEALTH_URL, PRODUCTS_URL):
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status != 200 or not isinstance(json.load(response), dict):
                        raise AbortError("旧服务只读 API 检查返回无效响应")
            except (OSError, ValueError) as error:
                raise AbortError("旧服务只读 API 检查失败") from error

    def api_startup_safe(self):
        visual_root = self.data_root / "visual-search"
        development_index = visual_root / "index.db"
        if development_index.is_file() and not development_index.is_symlink():
            with sqlite3.connect(f"{development_index.as_uri()}?mode=ro", uri=True) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "visual_jobs" in tables and db.execute("SELECT count(*) FROM visual_jobs WHERE status='running'").fetchone()[0]:
                    raise AbortError("AI 本地索引存在 running job；旧 API 启动会改写任务状态")
                if "alembic_version" in tables and db.execute("SELECT version_num FROM alembic_version").fetchone():
                    lock = visual_root / "calls.lock"
                    if not lock.is_file() or lock.is_symlink() or lock.stat().st_nlink != 1 or lock.stat().st_size < 1:
                        raise AbortError("AI 调用锁文件缺失或为空；旧 API 启动可能创建/修改 AI 状态")
        previews = self.data_root / "import-previews"
        if previews.is_dir() and not previews.is_symlink():
            now = time.time()
            for directory in previews.iterdir():
                if re.fullmatch(r"[0-9a-f]{32}", directory.name) and directory.is_dir() and not directory.is_symlink():
                    preview = directory / "preview.json"
                    source = directory / "source.xlsx"
                    modified = preview.stat().st_mtime if preview.is_file() else max(
                        directory.stat().st_mtime, source.stat().st_mtime if source.is_file() else 0)
                    if modified + 24 * 60 * 60 <= now:
                        raise AbortError("存在过期导入预览；旧 API 启动会清理该临时目录")

    def ai_fingerprint(self):
        root = self.data_root / "visual-search"
        if not os.path.lexists(root):
            return _digest({"visual_search": "absent"})
        if root.is_symlink():
            raise AbortError("AI 数据目录是符号链接")
        return tree_digest(root)

    def restore_old_artifacts(self, bundle, paths):
        return _restore_old_artifacts(self, bundle, paths)


def production_main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="受控恢复 numbering-hold 发布失败前的旧版本")
    parser.add_argument("action", choices=("prepare", "plan", "execute", "status"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--confirm-precommit-abort", action="store_true")
    parser.add_argument("--confirm-prepare-precommit-abort", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args(argv)
    try:
        context = ProductionAbortContext(args.database)
        if args.action == "prepare":
            result = prepare(context, args.operation_id, args.release,
                             confirm=args.confirm_prepare_precommit_abort)
        elif args.action == "plan":
            result = plan(context, args.operation_id, args.release)
        elif args.action == "status":
            result = status(context, args.operation_id, args.release)
        else:
            result = execute(context, args.operation_id, args.release,
                             confirm=args.confirm_precommit_abort,
                             expected_plan_sha256=args.expected_plan_sha256)
        public = {key: result[key] for key in ("state", "phase", "operation_id", "release", "plan_sha256",
                                                "database_revision", "database_identity", "backup_directory",
                                                "backup_database_sha256", "backup_manifest_sha256", "maintenance",
                                                "next_action") if key in result}
        if args.action == "plan":
            public["database_revision"] = result["database"]["revision"]
            public["product_count"] = result["database"]["product_count"]
            public["previous_commit"] = result["artifacts"]["previous_commit"]
            public["decision"] = result["decision"]
            public["dry_run"] = True
        print(json.dumps(public, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print("中止恢复已停止并保持维护：" + str(error), file=sys.stderr)
        return 1

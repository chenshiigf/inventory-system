"""Fixed Linux production boundary. Tests inject a separate context, never a CLI bypass."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from app.numbering_maintenance import MaintenanceError, checked_path, file_gate


def file_binding(path):
    path = checked_path(path, existing=True)
    before = path.stat()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    current = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
    if identity(before) != identity(opened) or identity(before) != identity(after) or identity(before) != identity(current):
        raise MaintenanceError('文件核验期间发生变化')
    return {'path': str(path), 'sha256': value, 'size': before.st_size,
            'mtime_ns': before.st_mtime_ns, 'device': before.st_dev, 'inode': before.st_ino}


class ProductionEnvironment:
    database = Path('/var/lib/inventory-system/inventory.db')
    data_root = database.parent
    report_root = Path('/var/backups/inventory-system/numbering')
    backup_root = Path('/var/backups/inventory-system')
    policy_path = Path('/etc/inventory-numbering.json')
    lock_path = Path('/run/inventory-actions.lock')
    proc_root = Path('/proc')

    def __init__(self, database):
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise MaintenanceError('生产入口仅允许 Linux 上经审查的 root 操作；本地测试使用合成测试上下文')
        if checked_path(database, existing=True) != self.database:
            raise MaintenanceError('必须显式指定文档约定的生产数据库路径')
        self.policy = self._policy()

    def _policy(self):
        path = checked_path(self.policy_path, existing=True)
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise MaintenanceError('排空清单必须由 root 管理且不可被组或其他用户修改')
        policy = json.loads(path.read_text(encoding='utf8'))
        units = policy.get('stopped_units')
        required = {'inventory-system-api.service', 'inventory-db-backup.timer',
                    'inventory-full-backup.timer', 'inventory-db-backup.service', 'inventory-full-backup.service'}
        if (policy.get('format') != 'numbering-drain-v1' or policy.get('all_writers_reviewed') is not True or
                not isinstance(units, list) or not required.issubset(units) or
                any(not isinstance(u, str) or not re.fullmatch(r'[a-zA-Z0-9_.@-]+\.(service|timer)', u) for u in units)):
            raise MaintenanceError('必须提供人工核实的 API、备份、业务和 AI 任务完整停止清单')
        if not isinstance(policy.get('ai_enable_markers'), list) or not policy['ai_enable_markers']:
            raise MaintenanceError('必须显式列出已核实的 AI 启用标记路径')
        return policy

    @contextmanager
    def locked(self):
        with file_gate(self.lock_path):
            yield

    def drained(self):
        # Policy changes invalidate the plan; no shell, network or environment secrets.
        if self._policy() != self.policy:
            raise MaintenanceError('排空清单变化')
        for unit in self.policy['stopped_units']:
            result = subprocess.run(['systemctl', 'show', unit, '--property=LoadState,ActiveState',
                                     '--no-pager'], capture_output=True, text=True, check=True)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            if values.get('LoadState') != 'loaded' or values.get('ActiveState') not in {'inactive', 'failed'}:
                raise MaintenanceError('服务或定时器未排空：' + unit)
        for value in self.policy['ai_enable_markers']:
            if not Path(value).is_absolute():
                raise MaintenanceError('AI 启用标记必须是绝对路径')
            path = checked_path(value)
            if not path.is_absolute() or path.exists():
                raise MaintenanceError('AI 仍启用，必须先按现有流程暂停并排空')
        running = subprocess.run(['systemctl', 'list-units', '--state=active,activating,reloading,deactivating',
                                  '--all', '--no-legend', '--plain', '--no-pager'],
                                 capture_output=True, text=True, check=True)
        for line in running.stdout.splitlines():
            unit = line.split()[0] if line.split() else ''
            if 'inventory' in unit.lower() or 'visual' in unit.lower() or unit in self.policy['stopped_units']:
                raise MaintenanceError('发现仍在运行的业务相关单元：' + unit)
        # Observe descriptors only, never read AI index, budget or image contents.
        # Reject any other process holding any file under the data directory.
        for process in self.proc_root.iterdir():
            if not process.name.isdigit() or int(process.name) == os.getpid():
                continue
            try:
                descriptors = list((process / 'fd').iterdir())
                for descriptor in descriptors:
                    try:
                        target = Path(os.readlink(descriptor))
                    except FileNotFoundError:
                        continue
                    if target.is_absolute() and target.is_relative_to(self.data_root):
                        raise MaintenanceError('数据目录仍被进程占用，PID=' + process.name)
            except FileNotFoundError:
                continue
            except PermissionError as error:
                raise MaintenanceError('无法完整检查在途进程') from error

    def backup_paths(self, database, manifest):
        for value in (database, manifest):
            path = checked_path(value, existing=True)
            if not path.is_relative_to(self.backup_root) or path.is_relative_to(self.report_root):
                raise MaintenanceError('备份必须在约定的独立备份目录')
        if Path(database).parent != Path(manifest).parent:
            raise MaintenanceError('数据库备份和 manifest 必须来自同一备份集')

    def reports(self):
        root = checked_path(self.report_root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.stat().st_uid != 0 or root.stat().st_mode & 0o077:
            raise MaintenanceError('报告目录必须为 root 私有目录')
        return root

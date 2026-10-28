"""Small filesystem primitives for the explicitly targeted one-off tool; no app startup."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def checked_path(path, *, existing=False):
    raw = Path(path).expanduser()
    if not raw.is_absolute() or '..' in raw.parts:
        raise ValueError('必须指定不含 .. 的绝对路径')
    for part in [*reversed(raw.parents), raw]:
        if not os.path.lexists(part):
            continue
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('拒绝符号链接、junction/reparse point')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('拒绝硬链接文件')
    if existing and not raw.is_file():
        raise ValueError('必需的普通文件不存在: ' + str(raw))
    return raw.resolve()


def binding(path):
    path = checked_path(path, existing=True)
    before = path.stat()
    with path.open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('读取期间文件发生变化')
    return {'path': str(path), 'device': after.st_dev, 'inode': after.st_ino,
            'size': after.st_size, 'mtime_ns': after.st_mtime_ns, 'sha256': value}


def no_sidecars(path):
    for suffix in ('-wal', '-shm', '-journal'):
        if os.path.lexists(str(path) + suffix):
            raise ValueError('数据库存在日志，停止；本工具不恢复、不 checkpoint、不删除: ' + str(path) + suffix)
    with Path(path).open('rb') as stream:
        header = stream.read(20)
    if header[:16] == b'SQLite format 3\0' and 2 in header[18:20]:
        raise ValueError('WAL 文件头不适合跨库原子切换；本工具不转换模式')


def connect(path, *, write=False):
    path = checked_path(path, existing=True)
    no_sidecars(path)
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if not write:
        db.execute('PRAGMA query_only=ON')
    return db


def integrity(db, schema='main'):
    if [tuple(r) for r in db.execute(f'PRAGMA {schema}.integrity_check')] != [('ok',)]:
        raise ValueError('SQLite integrity_check 异常: ' + schema)
    if db.execute(f'PRAGMA {schema}.foreign_key_check').fetchall():
        raise ValueError('SQLite foreign_key_check 异常: ' + schema)


@contextmanager
def existing_lock(path):
    # Same byte range/flock as visual_search.lock.CallLock; never create a file.
    path = checked_path(path, existing=True)
    with path.open('r+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('AI 调用或同步尚未排空') from error
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)

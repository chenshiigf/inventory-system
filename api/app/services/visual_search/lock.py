"""OS advisory lock shared by CLI and API; released automatically on process exit."""
import os


class CallLock:
    def __init__(self, root):
        self.path = root / "calls.lock"
        self.file = None

    def acquire(self):
        if self.file is not None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            return False
        self.file = file
        return True

    def release(self):
        file, self.file = self.file, None
        if file is None:
            return
        if os.name == "nt":
            import msvcrt
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_UN)
        file.close()

"""Private, offline Windows resource experiment. Never imports the app in the driver.

Run with the project venv from the repository root. Outputs stay in ignored
api/data/tmp/p2b1. No model traffic, production writes, installs or deployments.
Linux creation/measurements are deliberately outside this script's scope.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

MIB = 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / "api/data/tmp/p2b1"


def json_write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def unused_path(path):
    path = Path(path)
    candidate, attempt = path, 1
    while candidate.exists():
        attempt += 1
        candidate = path.with_name(f"{path.name}-attempt-{attempt}")
    return candidate


def private_destination(path, root=OUTPUT_ROOT):
    path, root = Path(path).absolute(), Path(root).resolve()
    if path == root or not path.resolve().is_relative_to(root):
        raise ValueError("output must be a fresh child of the ignored benchmark directory")
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("links are forbidden for writable benchmark data")
    return path


def block_external_network():
    # Audit connect/sendto, including code using libraries other than httpx.
    # Hostnames cannot cause DNS requests: only numeric loopback/AF_UNIX allowed.
    def audit(event, args):
        if event == "socket.getaddrinfo":
            host = args[0]
            if host not in (None, "127.0.0.1", "::1"):
                raise PermissionError("offline benchmark denies DNS")
        if event in {"socket.connect", "socket.sendto"}:
            sock, address = args[0], args[-1]
            if sock.family == getattr(socket, "AF_UNIX", object()):
                return
            try:
                permitted = ipaddress.ip_address(address[0]).is_loopback
            except (ValueError, TypeError, IndexError):
                permitted = False
            if not permitted:
                raise PermissionError("offline benchmark denies external connections")
    sys.addaudithook(audit)


def clean_environment(work):
    names = {"SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC",
             "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA"}
    env = {k: v for k, v in os.environ.items() if k.upper() in {n.upper() for n in names}}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               OPENBLAS_NUM_THREADS="2", OMP_NUM_THREADS="2", MKL_NUM_THREADS="2",
               DASHSCOPE_API_KEY="", INVENTORY_HTPASSWD_FILE="", VISUAL_ADMIN_USERS="",
               INVENTORY_DATA_DIR=str(work), DATABASE_URL=f"sqlite:///{(work / 'inventory.db').as_posix()}")
    return env


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
        "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


class MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
        (n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
        "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]


def host_memory():
    value = MemoryStatus()
    value.dwLength = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise ctypes.WinError()
    return {"total_physical_mib": value.ullTotalPhys / MIB,
            "available_physical_mib": value.ullAvailPhys / MIB,
            "available_commit_mib": value.ullAvailPageFile / MIB}


def process_memory(handle=None):
    value = MemoryCounters()
    value.cb = ctypes.sizeof(value)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    handle = handle or ctypes.windll.kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(value), value.cb):
        raise ctypes.WinError()
    return {"private_mib": value.PrivateUsage / MIB,
            "working_set_mib": value.WorkingSetSize / MIB,
            "os_peak_working_set_mib": value.PeakWorkingSetSize / MIB,
            # Commit charge, NOT physical paging or actual swap usage.
            "os_peak_commit_mib": value.PeakPagefileUsage / MIB}


def verify_worker_limits(cap_mib, job_name):
    # Verify the real interpreter, not just the Windows venv launcher. If a
    # launcher race escaped the job, abort BEFORE decoding or importing the app.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p]
    kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    limits, mask, system = ExtendedLimits(), ctypes.c_size_t(), ctypes.c_size_t()
    job = kernel.OpenJobObjectW(4, False, job_name)
    if not job:
        raise ctypes.WinError()
    inside = wintypes.BOOL()
    try:
        if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), job, ctypes.byref(inside)) or not inside.value:
            raise RuntimeError("real interpreter escaped the benchmark job")
        if not kernel.QueryInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
            raise ctypes.WinError()
    finally:
        kernel.CloseHandle(job)
    if limits.JobMemoryLimit != cap_mib * MIB or limits.ProcessMemoryLimit != cap_mib * MIB:
        raise RuntimeError("real interpreter did not inherit the benchmark memory limit")
    handle = kernel.GetCurrentProcess()
    if not kernel.GetProcessAffinityMask(handle, ctypes.byref(mask), ctypes.byref(system)):
        raise ctypes.WinError()
    bits = [1 << i for i in range(64) if mask.value & (1 << i)][:2]
    if len(bits) != 2 or not kernel.SetProcessAffinityMask(handle, sum(bits)):
        raise RuntimeError("cannot limit real interpreter to two logical CPUs")
    return {"verified_cap_mib": cap_mib, "inherited_logical_cpus": mask.value.bit_count(), "logical_cpus": 2}


class BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class WindowsJob:
    def __init__(self, process, cap_mib, name=None):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        self.kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p]
        self.handle = self.kernel.CreateJobObjectW(None, name)
        if not self.handle:
            raise ctypes.WinError()
        try:
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x200
            limits.ProcessMemoryLimit = cap_mib * MIB
            limits.JobMemoryLimit = cap_mib * MIB  # includes the Windows venv launcher
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError()
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError()
            mask, system = ctypes.c_size_t(), ctypes.c_size_t()
            if not self.kernel.GetProcessAffinityMask(int(process._handle), ctypes.byref(mask), ctypes.byref(system)):
                raise ctypes.WinError()
            bits = [1 << i for i in range(64) if mask.value & (1 << i)][:2]
            if len(bits) != 2 or not self.kernel.SetProcessAffinityMask(int(process._handle), sum(bits)):
                raise RuntimeError("cannot constrain test to two logical CPUs")
        except BaseException:
            self.close()
            raise

    def peak(self):
        limits = ExtendedLimits()
        if not self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
            raise ctypes.WinError()
        return limits.PeakJobMemoryUsed / MIB

    def memory(self):
        class ProcessIds(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD), ("ids", ctypes.c_size_t * 16)]
        ids = ProcessIds()
        if not self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(ids), ctypes.sizeof(ids), None):
            raise ctypes.WinError()
        total = {"private_mib": 0, "working_set_mib": 0, "processes": ids.count}
        for pid in ids.ids[:ids.count]:
            handle = self.kernel.OpenProcess(0x400 | 0x10, False, pid)
            if handle:
                try:
                    metrics = process_memory(handle)
                    total["private_mib"] += metrics["private_mib"]
                    total["working_set_mib"] += metrics["working_set_mib"]
                finally:
                    self.kernel.CloseHandle(handle)
        return total

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def sqlite_snapshot(source, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("refusing to overwrite a snapshot")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Official online backup from a read-only connection; never copy live DB bytes.
    with sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("invalid SQLite snapshot")


def protection_manifest():
    roots = [ROOT / "api/data/visual-search", ROOT / "api/data/uploads"]
    files = [ROOT / "api/data/inventory.db"]
    for root in roots:
        # Windows junctions are not symlinks: followlinks=False alone is insufficient.
        # Historical UI previews contain node_modules junctions to another tree.
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name not in {"node_modules", ".venv"}
                       and not Path(directory, name).is_symlink()
                       and not Path(directory, name).is_junction()]
            files += [Path(directory, name) for name in names
                      if not name.startswith((".env", ".htpasswd"))
                      and not Path(directory, name).is_symlink()]
    result = {}
    for path in files:
        if path.is_file():
            with path.open("rb") as file:
                result[str(path.relative_to(ROOT))] = hashlib.file_digest(file, "sha256").hexdigest()
    return result


def stats(values):
    values = sorted(values)
    if not values:
        return None
    return {"n": len(values), "mean_ms": statistics.mean(values), "p50_ms": statistics.median(values),
            "p95_ms": values[max(0, math.ceil(.95 * len(values)) - 1)], "max_ms": values[-1]}


def request(port, path, image=None, crop=None, authenticated=True):
    headers = {}
    if authenticated:
        headers["Authorization"] = "Basic " + base64.b64encode(b"benchmark:benchmark-only").decode()
    body = None
    if image is not None:
        boundary = "offline-resource-benchmark"
        prefix = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
        body = prefix + Path(image).read_bytes() + b"\r\n"
        for key, val in {"top_k": "5", **({"crop": json.dumps(crop)} if crop else {})}.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{val}\r\n'.encode()
        body += f"--{boundary}--\r\n".encode()
        headers.update({"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": f"http://127.0.0.1:{port}"})
    start = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request("POST" if body is not None else "GET", path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        return {"status": response.status, "elapsed_ms": (time.perf_counter() - start) * 1000,
                "body": json.loads(raw)}
    finally:
        conn.close()


SEARCH = "/api/visual-search/search?source=production-catalog"
STOCK = {"list": "/api/products?page=1&page_size=20", "detail": "/api/products/1",
         "movements": "/api/inventory-movements?page=1&page_size=20"}


def run_child(spec, output, driver=None, cap_mib=768, timeout=300, floor_mib=2048):
    if os.name != "nt":
        raise RuntimeError("this launcher requires Windows; Linux review is a separate approved step")
    if host_memory()["available_physical_mib"] < max(floor_mib + cap_mib, 3072 if floor_mib == 2048 else 0):
        raise RuntimeError("insufficient host reserve; did not launch the experiment")
    output = private_destination(output)
    output.mkdir(parents=True, exist_ok=False)
    spec = {**spec, "output": str(output), "cap_mib": cap_mib,
            "job_name": "inventory-p2b1-" + os.urandom(12).hex()}
    json_write(output / "spec.json", spec)
    start = time.perf_counter()
    samples, stopped = [], []
    with (output / "events.jsonl").open("w", encoding="utf8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(output / "spec.json")],
                                 stdin=subprocess.PIPE, stdout=log, stderr=log, text=True,
                                 env=clean_environment(output / "data"), creationflags=subprocess.CREATE_NO_WINDOW)
        job = None
        try:
            job = WindowsJob(child, cap_mib, spec["job_name"])
            def monitor():
                while child.poll() is None:
                    try:
                        # The venv executable is a small launcher; sample every
                        # process in the job, including the real Python worker.
                        sample = {"t": time.perf_counter(), **job.memory(), **host_memory()}
                        samples.append(sample)
                        if sample["available_physical_mib"] < floor_mib or sample["t"] - start > timeout:
                            stopped.append("host reserve or monotonic deadline reached")
                            child.kill()
                            return
                    except OSError:
                        return
                    time.sleep(.02)
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
            child.stdin.write("go\n")
            child.stdin.flush()
            driven = None
            if driver:
                ready = output / "ready.json"
                while not ready.exists():
                    if child.poll() is not None:
                        raise RuntimeError("worker exited before readiness; see private events log")
                    time.sleep(.05)
                readiness = json.loads(ready.read_text(encoding="utf8"))
                readiness["process_start_to_ready_ms"] = (readiness["events"][-1]["t"] - start) * 1000
                driven = driver(readiness)
                child.stdin.write("stop\n")
                child.stdin.flush()
            child.wait(timeout=timeout)
            watcher.join(timeout=2)
            result_path = output / "worker.json"
            result = {"worker": json.loads(result_path.read_text(encoding="utf8")) if result_path.exists() else None,
                      "driver": driven, "exit_code": child.returncode, "stopped": stopped,
                      "elapsed_s": time.perf_counter() - start, "cap_mib": cap_mib,
                      "job_peak_commit_mib": job.peak(),
                      "sample_peak_private_mib": max((x["private_mib"] for x in samples), default=0),
                      "sample_peak_working_set_mib": max((x["working_set_mib"] for x in samples), default=0),
                      "host_min_available_mib": min((x["available_physical_mib"] for x in samples), default=0)}
            json_write(output / "samples.json", samples)
            json_write(output / "result.json", result)
            return result
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            if job:
                job.close()
            child.stdin.close()


def worker(spec):
    # Called only after the parent assigned OS memory/CPU limits and sent 'go'.
    block_external_network()
    verified = verify_worker_limits(spec["cap_mib"], spec["job_name"])
    out = Path(spec["output"])
    events, lock = [], threading.Lock()
    def emit(stage, **extras):
        event = {"t": time.perf_counter(), "stage": stage, **process_memory(), **extras}
        with lock:
            events.append(event)
            print(json.dumps(event), flush=True)
    emit("stdlib_ready", **verified)
    if spec["mode"] == "fixtures":
        from PIL import Image, ImageDraw
        fixtures = Path(spec["fixtures"])
        fixtures.mkdir()
        for name, dims, fmt, exif, transparent in [
            ("3mp", (2000, 1500), "JPEG", 1, False),
            ("4mp", (2000, 2000), "JPEG", 1, False),
            ("12mp", (4000, 3000), "JPEG", 1, False),
            ("12mp-exif6", (4000, 3000), "JPEG", 6, False),
            ("24mp", (6000, 4000), "JPEG", 1, False),
            ("40mp", (8000, 5000), "JPEG", 1, False),
            ("over40mp", (8001, 5000), "JPEG", 1, False),
            ("transparent", (1600, 1200), "PNG", None, True),
            ("transparent-webp", (1600, 1200), "WEBP", None, True),
        ]:
            if spec.get("light_only") and dims[0] * dims[1] > 4_000_000:
                continue
            image = Image.new("RGBA" if transparent else "RGB", dims, (200, 50, 30, 120) if transparent else (200, 50, 30))
            ImageDraw.Draw(image).rectangle((0, 0, dims[0] // 2, dims[1] // 2), fill=(20, 50, 230))
            options = {}
            if exif:
                metadata = image.getexif()
                metadata[274] = exif
                options["exif"] = metadata
            image.save(fixtures / f"{name}.{fmt.lower()}", format=fmt, **options)
            image.close()
        if not spec.get("light_only"):
            jpeg = (fixtures / "12mp.jpeg").read_bytes()
            for name, length in (("10mib.jpeg", 10 * MIB), ("over10mib.jpeg", 10 * MIB + 1)):
                with (fixtures / name).open("wb") as f:
                    f.write(jpeg)
                    f.write(b"\0" * (length - len(jpeg)))
            (fixtures / "corrupt.jpeg").write_bytes(b"not an image")
        json_write(out / "worker.json", {"events": events})
        return

    sys.path.insert(0, spec["api_path"])
    if spec["mode"] == "image":
        from app.services.visual_search.images import prepare_image, VisualError
        from PIL import Image, ImageOps
        emit("image_dependencies_loaded")
        # Instrument original Pillow operations in the test child only.
        def wrap(obj, method):
            original = getattr(obj, method)
            def measured(*args, **kwargs):
                emit(method + ":start")
                try:
                    return original(*args, **kwargs)
                finally:
                    emit(method + ":end")
            setattr(obj, method, measured)
        for obj, method in ((ImageOps, "exif_transpose"), (Image.Image, "convert"),
                            (Image.Image, "crop"), (Image.Image, "thumbnail")):
            wrap(obj, method)
        raw = Path(spec["image"]).read_bytes()
        emit("input_read")
        try:
            prepared = prepare_image(raw, spec.get("crop"))
            from io import BytesIO
            with Image.open(BytesIO(prepared)) as check:
                result = {"status": 200, "size": check.size, "prepared_bytes": len(prepared)}
        except (VisualError, MemoryError) as error:
            result = {"status": getattr(error, "status_code", "MemoryError"), "error_type": type(error).__name__}
        emit("image_complete")
        json_write(out / "worker.json", {**result, "events": events})
        return

    from app.main import create_app
    from app import database, models
    emit("application_imported")
    database.Base.metadata.create_all(database.engine)
    with database.SessionLocal() as db:
        db.add(models.Category(id=1, name="Synthetic only"))
        db.add(models.Warehouse(id=1, name="Synthetic only"))
        db.flush()
        db.add_all([models.Product(id=i, category_id=1, warehouse_id=1, product_code=f"TEST-{i}", size="1x2x3", unit="pcs") for i in range(1, 2415)])
        db.flush()
        db.add_all([models.ProductPackaging(id=i, product_id=i, packing_qty=12, carton_count=7) for i in range(1, 2415)])
        db.flush()
        db.add_all([models.InventoryMovement(product_id=i, product_packaging_id=i, warehouse_id=1,
            movement_type="IN", quantity=7, before_carton_count=0, after_carton_count=7,
            packing_qty_snapshot=12, unit_snapshot="pcs") for i in range(1, 2415)])
        db.commit()
    emit("synthetic_stock_seeded")
    provider = None
    active, maximum, decodes = 0, 0, 0
    if spec["variant"] == "legacy":
        app = create_app()
    else:
        from app.services.visual_search.config import VisualConfig
        from app.services.visual_search import service as visual_module
        from app.services.visual_search.provider import Embedding, DashScopeProvider
        from passlib.hash import apr_md5_crypt
        import numpy as np
        def forbidden(*args, **kwargs):
            raise AssertionError("real provider forbidden in resource benchmark")
        DashScopeProvider.embed = forbidden
        class MockProvider:
            calls = 0
            delay = float(spec.get("delay", .5))
            inputs = []
            def embed(self, image, purpose, task_id):
                if purpose != "search":
                    raise AssertionError("index generation is not permitted in this benchmark")
                self.calls += 1
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(image)) as decoded:
                    self.inputs.append({"bytes": len(image), "size": decoded.size,
                                        "sha256": hashlib.sha256(image).hexdigest()})
                emit("mock_provider_wait:start")
                time.sleep(self.delay)
                emit("mock_provider_wait:end")
                return Embedding(vector, {"mock_only": True, "input_tokens": 0}, "offline")
        provider = MockProvider()
        vector = np.zeros(2560, dtype=np.float32)
        vector[0] = 1
        htpasswd = out / "test.htpasswd"
        htpasswd.write_text("benchmark:" + apr_md5_crypt.hash("benchmark-only") + "\n", encoding="utf8")
        config = VisualConfig(retries=0, htpasswd_file=str(htpasswd), admin_users=("benchmark",))
        if spec["variant"] == "unconfigured":
            config = VisualConfig(retries=0)
        app = create_app(visual_config=config, visual_provider=provider, visual_production_provider=provider)
        if spec["variant"] == "enabled":
            selected = app.state.visual_sources.get("production-catalog")
            # Assert snapshot source/model/ID mapping without decoding or indexing.
            rows = selected.store.rows()
            assert len(rows) == 2414 and all(r["embedding_dimension"] == 2560 for r in rows.values())
            assert selected.source.source_id == "aliyun-inventory-production"
            assert len({r["image_hash"] for r in rows.values()}) == 2380
            vector = np.frombuffer(next(iter(rows.values()))["embedding_vector"], dtype=np.float32).copy()
            del rows
            emit("production_source_loaded")
        original_prepare = visual_module.prepare_image
        def prepare(*args, **kwargs):
            nonlocal active, maximum, decodes
            with lock:
                active += 1
                decodes += 1
                maximum = max(active, maximum)
            emit("image_preprocessing:start")
            try:
                return original_prepare(*args, **kwargs)
            finally:
                emit("image_preprocessing:end")
                with lock:
                    active -= 1
        visual_module.prepare_image = prepare
        original_mark = visual_module.SearchTiming.mark
        def mark(self, name):
            result = original_mark(self, name)
            emit("search_stage:" + name)
            return result
        visual_module.SearchTiming.mark = mark
    emit("application_created")
    @app.get("/__benchmark/memory", include_in_schema=False)
    def diagnostic_memory():
        # Test-only route on an ephemeral loopback server, never shipped in app.
        return process_memory()
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("server failed to start")
        time.sleep(.01)
    emit("server_ready")
    json_write(out / "ready.json", {"port": port, "memory": process_memory(), "events": events})
    sys.stdin.readline()
    emit("before_shutdown")
    server.should_exit = True
    thread.join(timeout=20)
    if thread.is_alive():
        raise RuntimeError("test server did not stop")
    json_write(out / "worker.json", {"events": events, "mock_calls": provider.calls if provider else 0,
        "mock_inputs": provider.inputs if provider else [],
        "real_calls": 0, "preprocessing_entries": decodes, "max_parallel_preprocessing": maximum})


def prepare_run(run):
    run = private_destination(run)
    run.mkdir(parents=True, exist_ok=False)
    source = ROOT / "api/data/visual-search"
    copy = run / "snapshot/visual-search"
    catalog = copy / "production-catalog"
    catalog.mkdir(parents=True)
    shutil.copy2(source / "production-catalog/source.json", catalog / "source.json")
    sqlite_snapshot(source / "production-catalog/catalog.sqlite", catalog / "catalog.sqlite")
    shutil.copytree(source / "production-catalog/images", catalog / "images")
    indexes = list((source / "production-indices").glob("*/source-binding.json"))
    if len(indexes) != 1:
        raise ValueError("expected one unambiguous production namespace")
    target = copy / "production-indices" / indexes[0].parent.name
    target.mkdir(parents=True)
    shutil.copy2(indexes[0], target / "source-binding.json")
    sqlite_snapshot(indexes[0].parent / "index.db", target / "index.db")
    # Keep private source provenance in ignored outputs only.
    originals = source / "field-evaluation/originals"
    shutil.copy2(sorted(originals.glob("*.jpg"))[0], run / "field.jpg")
    archive = run / "legacy.tar"
    subprocess.run(["git", "archive", "2da620b", "api", "-o", str(archive)], cwd=ROOT, check=True)
    legacy = run / "legacy"
    legacy.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(legacy, filter="data")
    return run


def run_suite(run, resume=False):
    block_external_network()
    before = protection_manifest()
    if not resume:
        prepare_run(run)
    else:
        private_destination(run)
        if not (run / "snapshot/visual-search/production-catalog/catalog.sqlite").is_file():
            raise ValueError("resume requires an existing private snapshot")
    run = Path(run)
    results = {"host": host_memory(), "python": sys.version, "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "linux": "not performed; no installed WSL distribution/running Docker engine", "cases": {}}
    if resume:
        previous = json.loads((run / "summary.json").read_text(encoding="utf8"))
        if previous.get("harness_sha256") != results["harness_sha256"] or previous.get("code_head") != results["code_head"]:
            raise ValueError("resume requires the same harness and code version; start a fresh run instead")
        results["cases"] = previous["cases"]
    try:
        if "fixtures" not in results["cases"]:
            results["cases"]["fixtures"] = run_child({"mode": "fixtures", "fixtures": str(run / "fixtures")}, run / "fixtures-job", cap_mib=384)
        for name, crop in [("field", None), ("3mp", None), ("4mp", None), ("12mp", None), ("12mp-exif6", [0, 0, .5, .5]),
                           ("transparent", None), ("transparent-webp", None), ("24mp", None),
                           ("40mp", None), ("40mp-crop", [0, 0, .25, .25]), ("over40mp", None),
                           ("10mib", None), ("over10mib", None), ("corrupt", None)]:
            if "image-" + name in results["cases"]:
                continue
            image = run / "field.jpg" if name == "field" else next((run / "fixtures").glob(name.replace("-crop", "") + ".*"))
            result = run_child({"mode": "image", "api_path": str(ROOT / "api"), "image": str(image), "crop": crop}, unused_path(run / ("image-" + name)))
            results["cases"]["image-" + name] = result
            print(name, result["job_peak_commit_mib"], flush=True)
        def stock_driver(ready):
            data = {name: [request(ready["port"], path) for _ in range(40)] for name, path in STOCK.items()}
            return {"ready": ready, "stock": {n: stats([r["elapsed_ms"] for r in rows]) for n, rows in data.items()},
                    "statuses": {n: sorted({r["status"] for r in rows}) for n, rows in data.items()}}
        for variant in ("legacy", "unconfigured", "enabled"):
            if "server-" + variant in results["cases"]:
                continue
            target = unused_path(run / ("server-" + variant))
            target_data = target / "data"
            target_data.mkdir(parents=True, exist_ok=True)
            if variant != "legacy" and not (target_data / "visual-search").exists():
                shutil.copytree(run / "snapshot/visual-search", target_data / "visual-search")
            # run_child needs a fresh output; place pre-existing data beside it.
            spec = {"mode": "server", "variant": variant, "api_path": str(run / "legacy/api" if variant == "legacy" else ROOT / "api"), "data": str(target_data)}
            def driver(ready):
                stock = stock_driver(ready)
                if variant != "legacy":
                    stock["visual_status"] = request(ready["port"], "/api/visual-search/index/status?source=production-catalog")
                return stock
            result = run_child(spec, target / "job", driver, cap_mib=256)
            results["cases"]["server-" + variant] = result
            print(variant, result["job_peak_commit_mib"], flush=True)
        search_target = unused_path(run / "search")
        def search_driver(ready):
            port = ready["port"]
            baseline = stock_driver(ready)
            queries = []
            for i in range(30):
                q = request(port, SEARCH, run / "field.jpg")
                if q["status"] != 200 or len(q["body"].get("candidates", [])) != 5 or q["body"].get("source_id") != "aliyun-inventory-production":
                    raise RuntimeError("invalid offline search/source/mapping")
                queries.append(q)
                q["settled_memory"] = request(port, "/__benchmark/memory")["body"]
                json_write(search_target / "query-checkpoint.json", queries)
            extras = []
            for name, crop in [("3mp", None), ("4mp", None), ("12mp", None), ("12mp-exif6", [0, 0, .5, .5]), ("transparent", None),
                               ("transparent-webp", None), ("24mp", None), ("40mp", None),
                               ("over40mp", None), ("10mib", None), ("over10mib", None), ("corrupt", None)]:
                extras.append({"name": name, **request(port, SEARCH, next((run / "fixtures").glob(name + ".*")), crop)})
                json_write(search_target / "extra-checkpoint.json", extras)
            return {"ready": ready, "baseline": baseline, "queries": queries, "extras": extras,
                    "warm_search": stats([q["elapsed_ms"] for q in queries[1:]])}
        if "search" not in results["cases"]:
            search_target.mkdir()
            shutil.copytree(run / "snapshot/visual-search", search_target / "data/visual-search")
            results["cases"]["search"] = run_child({"mode": "server", "variant": "enabled", "api_path": str(ROOT / "api"), "data": str(search_target / "data")}, search_target / "job", search_driver, timeout=400)
        def concurrent_driver(ready):
            port = ready["port"]
            baseline = stock_driver(ready)
            gate = threading.Barrier(3)
            def search_one():
                gate.wait(timeout=5)
                return request(port, SEARCH, run / "fixtures/12mp.jpeg")
            mixed = {key: [] for key in STOCK}
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(search_one) for _ in range(3)]
                while not all(f.done() for f in futures):
                    for key, path in STOCK.items():
                        mixed[key].append(request(port, path))
                    time.sleep(.02)
                responses = [f.result() for f in futures]
            return {"ready": ready, "baseline": baseline, "responses": responses,
                    "mixed": {n: stats([q["elapsed_ms"] for q in rows]) for n, rows in mixed.items()},
                    "mixed_statuses": {n: sorted({q["status"] for q in rows}) for n, rows in mixed.items()}}
        if "concurrent" not in results["cases"]:
            target = unused_path(run / "concurrent")
            target.mkdir()
            shutil.copytree(run / "snapshot/visual-search", target / "data/visual-search")
            results["cases"]["concurrent"] = run_child({"mode": "server", "variant": "enabled", "api_path": str(ROOT / "api"), "data": str(target / "data"), "delay": 5}, target / "job", concurrent_driver)
    finally:
        after = protection_manifest()
        results["protection"] = {"files": len(before), "unchanged": before == after,
            "changed": [k for k in before.keys() | after.keys() if before.get(k) != after.get(k)]}
        json_write(run / "summary.json", results)
    if not results["protection"]["unchanged"]:
        raise RuntimeError("protected source changed; see private summary (no source was written by this script)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, help="fresh child directory under api/data/tmp/p2b1")
    parser.add_argument("--resume", action="store_true", help="reuse completed offline cases after a host-reserve stop")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if sys.stdin.readline().strip() != "go":
            raise RuntimeError("no parent resource-limit handshake")
        spec = json.loads(args.worker.read_text(encoding="utf8"))
        if spec.get("data"):
            os.environ.update(clean_environment(Path(spec["data"])))
        worker(spec)
    elif args.run:
        run_suite(args.run, args.resume)
    else:
        parser.error("provide --run; this never uses the production server")


if __name__ == "__main__":
    main()

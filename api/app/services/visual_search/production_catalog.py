"""One-way catalog mirror. GET only; no business DB, credentials or AI imports."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import time
from urllib.parse import quote, urlparse
import uuid
import warnings

import httpx
from PIL import Image

from .images import MAX_BYTES, MAX_PIXELS
from .lock import CallLock

MAGIC = "inventory-production-catalog-v1"
JSON_LIMIT = 8 * 1024 * 1024
MIN_INTERVAL = .2


class CatalogError(Exception):
    """Controlled errors only: never include remote bodies or credentials."""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path: Path, value):
    if path.is_symlink():
        raise CatalogError("拒绝写入链接文件")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def positive_id(value):
    if type(value) is not int or value <= 0:
        raise CatalogError("源数据的 ID 或装箱数无效")
    return value


def optional_id(value):
    return None if value is None else positive_id(value)


def text(value, limit=500, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise CatalogError("源数据的文字字段格式无效")
    return value


def image_identifier(value):
    value = text(value)
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or any(p in {".", ".."} for p in value.split("/"))
        or any(c in value for c in "\\:%?#") or any(ord(c) < 32 for c in value)):
        raise CatalogError("主图引用无效，未发送图片请求")
    return value


def product_projection(raw):
    """Never retain price/carton_count/remark or an unfiltered API response."""
    required = {"id", "product_code", "is_active", "category_id", "warehouse_id", "size", "image_path", "packagings"}
    if not isinstance(raw, dict) or not required <= raw.keys():
        raise CatalogError("商品接口缺少必要字段")
    pid = positive_id(raw.get("id"))
    if type(raw.get("is_active")) is not bool or not isinstance(raw.get("packagings"), list):
        raise CatalogError("源数据的状态或包装字段无效")
    packagings, seen = [], set()
    for packaging in raw["packagings"]:
        if not isinstance(packaging, dict) or not {"id", "packing_qty", "sort_order"} <= packaging.keys():
            raise CatalogError("包装接口缺少必要字段")
        pack_id = positive_id(packaging.get("id"))
        if pack_id in seen:
            raise CatalogError("包装 ID 冲突")
        seen.add(pack_id)
        order = packaging.get("sort_order")
        if type(order) is not int or order < 0:
            raise CatalogError("包装排序字段无效")
        packagings.append({"id": pack_id, "packing_qty": optional_id(packaging.get("packing_qty")), "sort_order": order})
    return {
        "id": pid, "product_code": text(raw.get("product_code"), 64, True),
        "original_product_code": text(raw.get("original_product_code"), 100, True),
        "is_active": raw["is_active"], "category_id": optional_id(raw.get("category_id")),
        "warehouse_id": optional_id(raw.get("warehouse_id")),
        "size": text(raw.get("size", ""), 200), "unit": text(raw.get("unit"), 20, True),
        "image_identifier": text(raw.get("image_path"), 500, True),
        "thumbnail_identifier": text(raw.get("thumbnail_path"), 500, True),
        "source_updated_at": text(raw.get("updated_at"), 100, True),
        "packagings": sorted(packagings, key=lambda p: (p["sort_order"], p["id"])),
    }


def categories_projection(nodes, seen=None, depth=0):
    if not isinstance(nodes, list) or depth > 20:
        raise CatalogError("分类树格式无效")
    seen = set() if seen is None else seen
    result = []
    for node in nodes:
        if not isinstance(node, dict) or type(node.get("sort_order")) is not int or node["sort_order"] < 0:
            raise CatalogError("分类字段格式无效")
        cid = positive_id(node.get("id"))
        if cid in seen:
            raise CatalogError("分类 ID 冲突")
        seen.add(cid)
        result.append({"id": cid, "name": text(node.get("name"), 100),
                       "parent_id": optional_id(node.get("parent_id")),
                       "code": text(node.get("code", ""), 64), "sort_order": node.get("sort_order")})
        result.extend(categories_projection(node.get("children", []), seen, depth + 1))
    return sorted(result, key=lambda row: row["id"])


def image_details(data):
    if not data or len(data) > MAX_BYTES:
        raise CatalogError("主图为空或超过 10MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                fmt = probe.format
                if fmt not in {"JPEG", "PNG", "WEBP"} or getattr(probe, "n_frames", 1) != 1:
                    raise CatalogError("主图不是受支持的静态图片")
                if probe.width * probe.height > MAX_PIXELS:
                    raise CatalogError("主图像素数量过大")
                probe.verify()
            with Image.open(BytesIO(data)) as decoded:
                decoded.load()
        return sha256(data).hexdigest(), {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[fmt]
    except CatalogError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise CatalogError("主图损坏或无法解码") from error


class CatalogClient:
    def __init__(self, base_url, *, transport=None, interval=.2, retries=2, timeout=30):
        parsed = urlparse(base_url)
        try:
            if parsed.port is None or not 1 <= parsed.port <= 65535:
                raise ValueError()
        except ValueError as error:
            raise CatalogError("隧道地址必须含有效端口") from error
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
            raise CatalogError("只允许 SSH 隧道的本机回环 HTTP 地址，不接受公网地址或凭据 URL")
        if not (MIN_INTERVAL <= interval <= 60 and 0 <= retries <= 2 and 1 <= timeout <= 60):
            raise CatalogError("超时、重试或请求间隔无效；间隔不得低于 0.2 秒")
        self.base_url = base_url.rstrip("/")
        self.interval, self.retries, self.timeout = interval, retries, timeout
        self.http = httpx.Client(transport=transport, timeout=timeout, trust_env=False, follow_redirects=False,
                                 headers={"Accept-Encoding": "identity"})
        self.stats = Counter()

    def close(self):
        self.http.close()

    def read(self, path, *, headers=None, limit=JSON_LIMIT):
        if not (path in {"/api/categories", "/api/warehouses"} or path.startswith(("/api/products?", "/uploads/"))):
            raise CatalogError("请求路径不在只读白名单")
        for attempt in range(self.retries + 1):
            time.sleep(self.interval)
            self.stats["http_requests"] += 1
            retry = False
            try:
                with self.http.stream("GET", self.base_url + path, headers=headers or {}) as response:
                    if response.status_code in {200, 304}:
                        if response.status_code == 304:
                            return 304, b"", dict(response.headers)
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise CatalogError("不接受压缩编码响应")
                        pieces, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            self.stats["transferred_bytes"] += len(chunk)
                            if size > limit:
                                raise CatalogError("响应超过本地读取上限")
                            pieces.append(chunk)
                        content = b"".join(pieces)
                        length = response.headers.get("content-length")
                        if length is not None and (not length.isdigit() or int(length) != len(content)):
                            raise CatalogError("响应长度不一致")
                        return 200, content, dict(response.headers)
                    retry = response.status_code in {429, 500, 502, 503, 504}
                    failure = CatalogError(f"只读请求失败：HTTP {response.status_code}")
            except (httpx.TimeoutException, httpx.TransportError):
                retry, failure = True, CatalogError("只读请求超时或网络不可用")
            if not retry or attempt == self.retries:
                raise failure
            self.stats["retries"] += 1
            time.sleep(min(2 ** attempt, 4))
        raise CatalogError("只读请求未完成")

    def json(self, path):
        status, data, _ = self.read(path)
        if status != 200:
            raise CatalogError("资料接口没有返回完整 JSON")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise CatalogError("资料接口返回无效 JSON") from error

    def snapshot(self, limit=10, expected_total=None):
        full = limit is None
        page_size = 100 if full else min(limit, 100)
        products, ids, total = [], set(), None
        page = 1
        while True:
            body = self.json(f"/api/products?status=all&page_size={page_size}&page={page}")
            if not isinstance(body, dict):
                raise CatalogError("分页响应格式无效")
            current_total = body.get("total")
            if type(current_total) is not int or current_total < 0 or not isinstance(body.get("items"), list):
                raise CatalogError("分页响应格式无效")
            total = current_total if total is None else total
            if current_total != total or (expected_total is not None and total != expected_total):
                raise CatalogError("生产总数发生变化或与批准的预期总数不符")
            if body.get("page") != page or body.get("page_size") != page_size:
                raise CatalogError("分页响应与请求不一致")
            target = total if full else min(limit, total)
            expected_rows = min(page_size, max(total - (page - 1) * page_size, 0))
            if len(body["items"]) != expected_rows:
                raise CatalogError("分页缺失或数量不一致")
            for raw in body["items"]:
                projected = product_projection(raw)
                if projected["id"] in ids:
                    raise CatalogError("商品 ID 冲突或分页期间清单变动")
                ids.add(projected["id"])
                products.append(projected)
            if len(products) >= target:
                products = products[:target]
                break
            page += 1
        categories = categories_projection(self.json("/api/categories"))
        warehouses = []
        warehouse_ids = set()
        warehouse_rows = self.json("/api/warehouses")
        if not isinstance(warehouse_rows, list):
            raise CatalogError("仓库响应格式无效")
        for raw in warehouse_rows:
            if not isinstance(raw, dict) or type(raw.get("sort_order")) is not int or raw["sort_order"] < 0:
                raise CatalogError("仓库字段格式无效")
            wid = positive_id(raw.get("id"))
            if wid in warehouse_ids:
                raise CatalogError("仓库 ID 冲突")
            warehouse_ids.add(wid)
            warehouses.append({"id": wid, "name": text(raw.get("name"), 100), "sort_order": raw.get("sort_order")})
        packaging_ids = [p["id"] for row in products for p in row["packagings"]]
        if len(packaging_ids) != len(set(packaging_ids)):
            raise CatalogError("不同商品之间包装 ID 冲突")
        return {"source_total": total, "products": sorted(products, key=lambda p: p["id"]),
                "categories": categories, "warehouses": sorted(warehouses, key=lambda w: w["id"])}


def initialize(root: Path, source_id: str, source_label: str):
    root = root.expanduser().resolve()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", source_id) or source_id in {"development", "default", "dev"}:
        raise CatalogError("必须使用独立的生产 source_id")
    text(source_label, 120)
    identity = {"format": MAGIC, "source_id": source_id, "source_label": source_label}
    marker = root / "source.json"
    if root.exists():
        if not root.is_dir() or any((root / n).exists() for n in ("inventory.db", "index.db", "uploads")):
            raise CatalogError("目标是业务/向量目录或文件，拒绝写入")
        for name in ("source.json", "catalog.sqlite", "download-cache.json", "calls.lock", "images", "reports", "manifests", "browse.html", "browse-pages.json"):
            candidate = root / name
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                raise CatalogError("镜像路径包含链接或越界，拒绝写入")
            if candidate.is_file() and candidate.stat().st_nlink > 1:
                raise CatalogError("镜像控制文件与其他文件共享硬链接，拒绝写入")
        if marker.exists():
            if json.loads(marker.read_text(encoding="utf8")) != identity:
                raise CatalogError("目标镜像来源不一致，拒绝混用 ID")
        elif any(root.iterdir()):
            raise CatalogError("非空目录不是本工具镜像，拒绝覆盖")
    root.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        atomic_json(marker, identity)
    return root, identity


def checked_file(root: Path, entry):
    digest = entry.get("sha256")
    suffix = entry.get("format")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) or suffix not in {"jpg", "png", "webp"}:
        raise CatalogError("本地图片记录无效")
    target = (root / "images" / f"{digest}.{suffix}").resolve()
    if not target.is_relative_to(root.resolve()):
        raise CatalogError("本地图片路径越界")
    try:
        with target.open("rb") as file:
            data = file.read(MAX_BYTES + 1)
    except OSError as error:
        raise CatalogError("本地主图文件缺失") from error
    actual, fmt = image_details(data)
    if actual != digest or fmt != suffix:
        raise CatalogError("本地图片 hash 不一致")
    return target


def download_image(client, root, identifier, cache, counts):
    image_identifier(identifier)
    old = cache.get(identifier)
    headers = {}
    if old:
        try:
            checked_file(root, old)
            if old.get("etag"):
                headers["If-None-Match"] = old["etag"]
            elif old.get("last_modified"):
                headers["If-Modified-Since"] = old["last_modified"]
        except CatalogError as error:
            counts["local_cache_invalid"] += 1
            if "hash" in str(error):
                counts["local_hash_mismatches"] += 1
            old = None
    status, content, response_headers = client.read("/uploads/" + quote(identifier, safe="/"), headers=headers, limit=MAX_BYTES)
    if status == 304:
        if not old or not headers:
            raise CatalogError("源端返回 304 但没有已校验的缓存")
        counts["files_not_modified"] += 1
        return old
    digest, fmt = image_details(content)
    directory = root / "images"
    if not directory.resolve().is_relative_to(root.resolve()):
        raise CatalogError("本地图片目录越界")
    directory.mkdir(exist_ok=True)
    target = directory / f"{digest}.{fmt}"
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise CatalogError("本地图片路径越界")
    entry = {"sha256": digest, "format": fmt, "etag": response_headers.get("etag"),
             "last_modified": response_headers.get("last-modified"), "bytes": len(content)}
    if target.exists():
        try:
            checked_file(root, entry)
            counts["files_content_reused"] += 1
        except CatalogError:
            counts["local_cache_invalid"] += 1
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
    else:
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
    checked_file(root, entry)  # Re-read disk and compare to downloaded content hash.
    cache[identifier] = entry
    atomic_json(root / "download-cache.json", cache)
    counts["files_downloaded"] += 1
    return entry


def publish(root, identity, snapshot, records, report):
    with closing(sqlite3.connect(root / "catalog.sqlite")) as db:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS mirror_products (source_id TEXT NOT NULL,id INTEGER NOT NULL,document TEXT NOT NULL,PRIMARY KEY(source_id,id))")
            db.execute("CREATE TABLE IF NOT EXISTS mirror_meta (key TEXT PRIMARY KEY,document TEXT NOT NULL)")
            if report["scope"] == "full":
                current_ids = {r["id"] for r in records}
                for pid, document in db.execute("SELECT id,document FROM mirror_products WHERE source_id=?", (identity["source_id"],)).fetchall():
                    if pid not in current_ids:
                        old = json.loads(document)
                        old["source_present"], old["synced_at"] = False, report["finished_at"]
                        db.execute("UPDATE mirror_products SET document=? WHERE source_id=? AND id=?", (json.dumps(old, ensure_ascii=False), identity["source_id"], pid))
            for row in records:
                db.execute("INSERT OR REPLACE INTO mirror_products VALUES (?,?,?)", (identity["source_id"], row["id"], json.dumps(row, ensure_ascii=False)))
            documents = [json.loads(r[0]) for r in db.execute("SELECT document FROM mirror_products WHERE source_id=?", (identity["source_id"],))]
            report["local_present_products"] = sum(r["source_present"] for r in documents)
            report["local_invalidated_products"] = sum(not r["source_present"] for r in documents)
            for key, value in {"identity": identity, "categories": snapshot["categories"], "warehouses": snapshot["warehouses"], "last_report": {**report, "published": True}}.items():
                db.execute("INSERT OR REPLACE INTO mirror_meta VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))


def disk_plan(directory, snapshot):
    """Read-only, conservative capacity plan; no source writes or image requests."""
    probe = Path(directory).expanduser().resolve()
    while not probe.exists():
        probe = probe.parent
    unique_images = len({p["image_identifier"] for p in snapshot["products"] if p["image_identifier"]})
    # Existing files are already deducted from free space. Allow all references to
    # produce new 10MB files, plus a temporary image and metadata/journal reserve.
    image_budget = unique_images * MAX_BYTES
    required = max(1024 ** 3, image_budget + MAX_BYTES + 256 * 1024 ** 2)
    free = shutil.disk_usage(probe).free
    return {"free_bytes": free, "conservative_image_budget_bytes": image_budget,
            "required_free_bytes": required, "sufficient": free >= required}


def dry_run_catalog(client, directory, source_id, source_label, limit, expected_total):
    initial = client.snapshot(limit, expected_total)
    final = client.snapshot(limit, expected_total)
    if fingerprint(initial) != fingerprint(final):
        raise CatalogError("dry-run 两遍商品清单不一致；未下载、未发布，请稍后重新预检")
    root = Path(directory).expanduser().resolve()
    existing = []
    if (root / "source.json").exists():
        mirror = ProductionCatalog(root, source_id)
        if mirror.identity["source_label"] != source_label:
            raise CatalogError("目标镜像来源不一致，拒绝混用 ID")
        existing = mirror.products()
    ids = {p["id"] for p in final["products"]}
    references = [p["image_identifier"] for p in final["products"] if p["image_identifier"]]
    cache = json.loads((root / "download-cache.json").read_text(encoding="utf8")) if (root / "download-cache.json").exists() else {}
    verified_cache = 0
    for identifier in set(references):
        entry = cache.get(identifier)
        if entry:
            try:
                checked_file(root, entry)
                verified_cache += 1
            except CatalogError:
                pass  # Download will retry invalid cache; dry-run never repairs it.
    packaging = [p for row in final["products"] for p in row["packagings"]]
    return {"dry_run": True, "source_id": source_id, "source_total": final["source_total"],
            "selected_products": len(final["products"]), "scope": "full" if limit is None else "sample",
            "snapshot_passes": 2, "pages_per_pass": max(1, math.ceil(final["source_total"] / 100)) if limit is None else 1,
            "pagination_consistent": True, "catalog_fingerprint": fingerprint(final), "id_conflicts": 0,
            "active_products": sum(p["is_active"] for p in final["products"]),
            "inactive_products": sum(not p["is_active"] for p in final["products"]),
            "image_references": len(references), "unique_image_references": len(set(references)),
            "duplicate_reference_links": len(references) - len(set(references)),
            "missing_main_images": len(final["products"]) - len(references),
            "packaging_rows": len(packaging), "unknown_packing_qty_rows": sum(p["packing_qty"] is None for p in packaging),
            "existing_mirror_products": len(existing), "existing_ids_in_selection": sum(p["id"] in ids for p in existing),
            "would_invalidate_products": sum(p["source_present"] and p["id"] not in ids for p in existing) if limit is None else 0,
            "verified_cached_image_references": verified_cache, "images_downloaded": 0, "published": False, "ai_calls": 0,
            "network": dict(client.stats), "disk": disk_plan(root, final),
            "limits": {"concurrency": 1, "interval_seconds": client.interval,
                       "timeout_seconds": client.timeout, "retries": client.retries, "max_image_bytes": MAX_BYTES}}


def sync_catalog(client, directory, source_id, source_label, *, limit=10, expected_total=None, execute=False):
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 10):
        raise CatalogError("小批量上限必须为 1～10；默认 10")
    if limit is None and (type(expected_total) is not int or expected_total <= 0):
        raise CatalogError("全量模式必须显式填写批准的预期商品总数")
    if not execute:
        return dry_run_catalog(client, directory, source_id, source_label, limit, expected_total)
    root, identity = initialize(Path(directory), source_id, source_label)
    lock = CallLock(root)
    if not lock.acquire():
        raise CatalogError("已有镜像同步运行中")
    run = uuid.uuid4().hex
    report = {"sync_id": run, "source_id": source_id, "scope": "full" if limit is None else "sample",
              "started_at": timestamp(), "published": False, "source_hash_verification": "源端未提供独立 SHA256；仅验证下载内容与落盘文件",
              "ai_calls": 0, "id_conflicts": 0}
    counts = Counter({name: 0 for name in ("local_cache_invalid", "local_hash_mismatches", "files_downloaded", "files_not_modified", "files_content_reused", "shared_reference_reused")})
    records = []
    try:
        initial = client.snapshot(limit, expected_total)
        report["source_total"] = initial["source_total"]
        report["disk"] = disk_plan(root, initial)
        if not report["disk"]["sufficient"]:
            raise CatalogError("本地磁盘空间不足，未开始图片下载、未发布；请释放空间后重试")
        cache_path = root / "download-cache.json"
        cache = json.loads(cache_path.read_text(encoding="utf8")) if cache_path.exists() else {}
        completed = {}
        for projected in initial["products"]:
            row = {**projected, "source_id": source_id, "product_key": f"{source_id}:{projected['id']}",
                   "source_present": True, "synced_at": timestamp(), "image_status": "missing", "image": None, "image_error": None}
            identifier = projected["image_identifier"]
            if identifier:
                if identifier in completed:
                    row.update(completed[identifier])
                    counts["shared_reference_reused"] += 1
                else:
                    try:
                        row["image"] = download_image(client, root, identifier, cache, counts)
                        row["image_status"] = "ready"
                    except CatalogError as error:
                        row["image_status"], row["image_error"] = "failed", str(error)
                    completed[identifier] = {k: row[k] for k in ("image", "image_status", "image_error")}
            records.append(row)
        final = client.snapshot(limit, expected_total)
        if fingerprint(initial) != fingerprint(final):
            raise CatalogError("同步前后商品清单不一致，未发布；下载缓存保留，下次可继续")
        hashes = Counter(r["image"]["sha256"] for r in records if r["image_status"] == "ready")
        category_ids = {r["id"] for r in final["categories"]}
        warehouse_ids = {r["id"] for r in final["warehouses"]}
        report.update({
            "finished_at": timestamp(), "catalog_fingerprint": fingerprint(final),
            "selected_products": len(records), "missing_main_images": sum(r["image_status"] == "missing" for r in records),
            "failed_main_images": sum(r["image_status"] == "failed" for r in records),
            "not_found_image_products": sum(r["image_error"] == "只读请求失败：HTTP 404" for r in records),
            "invalid_image_products": sum(r["image_error"] in {"主图损坏或无法解码", "主图不是受支持的静态图片", "主图像素数量过大"} for r in records),
            "ready_product_images": sum(r["image_status"] == "ready" for r in records), "distinct_image_files": len(hashes),
            "duplicate_content_groups": sum(n > 1 for n in hashes.values()), "duplicate_product_links": sum(n - 1 for n in hashes.values()),
            "packaging_rows": sum(len(r["packagings"]) for r in records),
            "missing_packaging_products": sum(not r["packagings"] for r in records),
            "unknown_packing_qty_rows": sum(p["packing_qty"] is None for r in records for p in r["packagings"]),
            "orphan_category_refs": sum(r["category_id"] is not None and r["category_id"] not in category_ids for r in records),
            "orphan_warehouse_refs": sum(r["warehouse_id"] is not None and r["warehouse_id"] not in warehouse_ids for r in records),
            "active_products": sum(r["is_active"] for r in records), "inactive_products": sum(not r["is_active"] for r in records),
            "image_failures": [{"product_id": r["id"], "reason": r["image_error"]} for r in records if r["image_status"] == "failed"],
            **dict(counts), "network": dict(client.stats),
        })
        report["images_complete"] = report["failed_main_images"] == 0
        (root / "manifests").mkdir(exist_ok=True)
        atomic_json(root / "manifests" / f"{run}.json", {"identity": identity, "snapshot": final, "records": records})
        publish(root, identity, final, records, report)
        report["published"] = True
        mirror = ProductionCatalog(root, source_id)
        render_browser(mirror)
        return report
    except CatalogError as error:
        report.update(finished_at=timestamp(), error=str(error), network=dict(client.stats))
        if "商品 ID 冲突" in str(error):
            report["id_conflicts"] = 1  # Stop at first conflict; not a complete conflict census.
        raise
    except KeyboardInterrupt as error:
        report.update(finished_at=timestamp(), error="同步已中断；已校验文件缓存保留，下次重新运行可继续", network=dict(client.stats))
        raise CatalogError(report["error"]) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        # Keep useful failure evidence without logging remote data or credentials.
        report.update(finished_at=timestamp(), error="本地同步失败：" + type(error).__name__, network=dict(client.stats))
        raise CatalogError(report["error"]) from error
    finally:
        try:
            (root / "reports").mkdir(exist_ok=True)
            atomic_json(root / "reports" / f"{run}.json", report)
        finally:
            lock.release()


class ProductionCatalog:
    """Explicit source selection. Never falls back to development or opens its DB."""
    def __init__(self, root, source_id):
        self.root = Path(root).expanduser().resolve()
        self.identity = json.loads((self.root / "source.json").read_text(encoding="utf8"))
        if self.identity.get("format") != MAGIC or self.identity.get("source_id") != source_id:
            raise CatalogError("生产图库 source_id 不匹配")
        self.source_id = source_id
        self.path = self.root / "catalog.sqlite"
        if not self.path.is_file():
            raise CatalogError("图库尚未发布")

    @property
    def index_namespace(self):
        return "production-" + fingerprint(self.identity)

    def _connect(self):
        return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)

    def products(self):
        with closing(self._connect()) as db:
            return [json.loads(r[0]) for r in db.execute("SELECT document FROM mirror_products WHERE source_id=? ORDER BY id", (self.source_id,))]

    def get(self, source_id, product_id):
        if source_id != self.source_id:
            raise CatalogError("不能将开发或其他来源 ID 用于生产图库")
        with closing(self._connect()) as db:
            row = db.execute("SELECT document FROM mirror_products WHERE source_id=? AND id=?", (source_id, positive_id(product_id))).fetchone()
        if row is None:
            raise CatalogError("镜像商品 ID 不存在")
        return json.loads(row[0])

    def image_path(self, source_id, product_id):
        row = self.get(source_id, product_id)
        if not row["source_present"] or not row["is_active"] or row["image_status"] != "ready":
            raise CatalogError("商品已失效、停用或没有有效主图")
        return checked_file(self.root, row["image"])

    def metadata(self):
        with closing(self._connect()) as db:
            return {k: json.loads(v) for k, v in db.execute("SELECT key,document FROM mirror_meta")}

    def audit(self):
        rows = self.products()
        failures = []
        for row in rows:
            if row["image_status"] == "ready":
                try:
                    checked_file(self.root, row["image"])
                except CatalogError as error:
                    failures.append({"product_id": row["id"], "reason": str(error)})
        return {"source_id": self.source_id, "index_namespace": self.index_namespace, "products": len(rows),
                "present_products": sum(r["source_present"] for r in rows),
                "invalidated_products": sum(not r["source_present"] for r in rows), "local_image_failures": failures,
                "last_sync": self.metadata().get("last_report")}


def render_browser(mirror):
    meta = mirror.metadata()
    categories = {r["id"]: r["name"] for r in meta["categories"]}
    warehouses = {r["id"]: r["name"] for r in meta["warehouses"]}
    pages = []
    rows = mirror.products()
    for page in range(max(1, math.ceil(len(rows) / 100))):
        cards = []
        for row in rows[page * 100:(page + 1) * 100]:
            image = row["image"]
            if image:
                try:
                    checked_file(mirror.root, image)
                except CatalogError:
                    image = None
            picture = (f'<img loading="lazy" src="images/{image["sha256"]}.{image["format"]}" alt="商品主图">'
                       if image and row["image_status"] == "ready" else '<p>主图缺失或下载失败</p>')
            packing = "、".join("未填装箱数" if p["packing_qty"] is None else str(p["packing_qty"]) for p in row["packagings"]) or "未填写"
            state = "已失效" if not row["source_present"] else "启用" if row["is_active"] else "停用"
            values = [f'ID {row["id"]} · {row["product_code"] or "未填货号"}',
                      f'状态：{state}', f'分类：{categories.get(row["category_id"], "未填写")}',
                      f'仓库：{warehouses.get(row["warehouse_id"], "未填写")}',
                      f'尺寸：{row["size"] or "未填写"} · 单位：{row["unit"] or "未填写"}', f'每箱装箱数：{packing}']
            cards.append('<article id="product-' + str(row["id"]) + '">' + picture + ''.join('<p>' + escape(v) + '</p>' for v in values) + '</article>')
        nav = ' '.join(f'<a href="{"browse.html" if i == 0 else "browse-" + str(i + 1) + ".html"}">第 {i + 1} 页</a>' for i in range(max(1, math.ceil(len(rows) / 100))))
        content = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                   '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' file:; style-src \'unsafe-inline\'; base-uri \'none\'">'
                   '<title>生产商品图库 · 本地只读</title><style>body{font:16px system-ui;margin:24px;background:#f4f5f7;color:#222}nav{line-height:2}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}article{background:white;padding:16px;border-radius:8px}img{width:100%;height:200px;object-fit:contain}p{overflow-wrap:anywhere}a{margin-right:12px}</style>'
                   '<h1>生产商品图库 · 本地只读</h1><p>来源：' + escape(mirror.source_id) + ' · ' + str(len(rows)) + ' 款（含历史失效商品）</p>'
                   '<p>按 Ctrl+F 查找本页 ID 或货号。仅使用本地图片；包装数字为每箱装箱数。</p><nav>' + nav + '</nav><main>' + ''.join(cards) + '</main></html>')
        target = mirror.root / ("browse.html" if page == 0 else f"browse-{page + 1}.html")
        temporary = target.with_suffix(".html.tmp")
        temporary.write_text(content, encoding="utf8")
        temporary.replace(target)
        pages.append(target.name)
    atomic_json(mirror.root / "browse-pages.json", pages)
    return mirror.root / "browse.html"

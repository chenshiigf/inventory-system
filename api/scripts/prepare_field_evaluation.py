"""Freeze independently confirmed Round 2 labels separately from query objects.

This has no Provider, no search execution and no paid-call option.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.visual_search.evaluation import query_objects, validate_label
from app.services.visual_search.images import MAX_BYTES, prepare_image
from app.services.visual_search.production_catalog import atomic_json, timestamp
from app.services.visual_search.sources import ProductionSource


def prepare(manifest, catalog, output_parent, *, first_batch=False):
    manifest, catalog, output_parent = map(lambda p: Path(p).resolve(), (manifest, catalog, output_parent))
    source = ProductionSource(catalog)
    original = manifest.read_bytes()
    labels = json.loads(original.decode("utf-8-sig"))
    if not isinstance(labels, list) or not 1 <= len(labels) <= 100:
        raise ValueError("Prepare 1..100 human-confirmed objects; no queries will run")
    if first_batch and not 20 <= len(labels) <= 30:
        raise ValueError("Round 2 first batch requires 20..30 confirmed objects")
    objects = query_objects(labels, source_id=source.source_id)
    image_digests, independent_inputs = {}, set()
    gallery_hashes = {p.detail["image_sha256"] for p in source.products()} if first_batch else set()
    for label in labels:
        validate_label(label, source.source_id)
        if label.get("status") not in {"present", "absent"} or label.get("human_confirmed") is not True:
            raise ValueError("Round 2 labels must be independently human-confirmed before queries")
        if first_batch:
            if label.get("crop_confirmed") is not True:
                raise ValueError("Each first-batch crop must be manually confirmed")
            flags = ("multi_product_photo", "mixed_product", "shared_image", "gallery_absent")
            if any(type(label.get(key)) is not bool for key in flags):
                raise ValueError("Confirm multi-product, mixed, shared-image and absent flags independently")
            if label["gallery_absent"] != (label["status"] == "absent"):
                raise ValueError("Absent flag conflicts with the independent label")
            if not isinstance(label.get("category"), str) or not label["category"].strip():
                raise ValueError("A first-batch object requires a category")
            difficulties = label.get("difficulty_tags")
            if not isinstance(difficulties, list) or not difficulties or any(not isinstance(t, str) or not t.strip() for t in difficulties):
                raise ValueError("Confirm at least one difficulty tag per object")
        if label["status"] == "present":
            detail = source.get(None, label["product_id"]).detail
            if detail["product_code"] != label.get("product_code"):
                raise ValueError("Human label does not match production mirror")
        relative = Path(label["image"])
        image = (manifest.parent / relative).resolve()
        if relative.is_absolute() or not image.is_relative_to(manifest.parent) or not image.is_file():
            raise ValueError("Images must exist inside the evaluation directory")
        with image.open("rb") as file:
            raw = file.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Evaluation image exceeds the 10MB query limit")
        digest = sha256(raw).hexdigest()
        image_digests[label["object_id"]] = digest
        if first_batch:
            prepare_image(raw, label.get("crop"))  # Validate actual EXIF-oriented crop before any query.
            if digest in gallery_hashes:
                raise ValueError("A gallery main image cannot masquerade as a field photo")
            identity = (digest, tuple(label.get("crop") or (0, 0, 1, 1)))
            if identity in independent_inputs:
                raise ValueError("Duplicate image and crop are not independent first-batch objects")
            independent_inputs.add(identity)
    output_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="prepared-", dir=output_parent))
    # Byte-identical evidence of answers confirmed before execution.
    (work / "human-labels.json").write_bytes(original)
    atomic_json(work / "query-objects.json", objects)
    atomic_json(work / "preflight.json", {**source.context(), "prepared_at": timestamp(),
        "label_sha256": sha256(original).hexdigest(), "objects": len(objects),
        "first_batch_metadata_verified": first_batch,
        "present_objects": sum(l["status"] == "present" for l in labels),
        "absent_objects": sum(l["status"] == "absent" for l in labels),
        "image_root": str(manifest.parent), "real_api_calls": 0,
        "image_sha256": image_digests,
        "execution_authorized": False, "budget_confirmed": False,
        "note": "Only image bytes and crop may enter the model. Freeze a no-match decision policy separately before execution."})
    return work


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="第二轮独立人工标签预检；不执行付费查询")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--output-parent", required=True)
    parser.add_argument("--first-batch", action="store_true", help="要求首批 20～30 个对象、完整人工标签和裁剪确认；仍不执行查询")
    args = parser.parse_args()
    print(prepare(args.manifest, args.catalog, args.output_parent, first_batch=args.first_batch))

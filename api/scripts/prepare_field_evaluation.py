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
from app.services.visual_search.production_catalog import atomic_json, timestamp
from app.services.visual_search.sources import ProductionSource


def prepare(manifest, catalog, output_parent):
    manifest, catalog, output_parent = map(lambda p: Path(p).resolve(), (manifest, catalog, output_parent))
    source = ProductionSource(catalog)
    original = manifest.read_bytes()
    labels = json.loads(original.decode("utf-8-sig"))
    if not isinstance(labels, list) or not 1 <= len(labels) <= 100:
        raise ValueError("Prepare 1..100 human-confirmed objects; no queries will run")
    objects = query_objects(labels, source_id=source.source_id)
    for label in labels:
        validate_label(label, source.source_id)
        if label.get("status") not in {"present", "absent"} or label.get("human_confirmed") is not True:
            raise ValueError("Round 2 labels must be independently human-confirmed before queries")
        if label["status"] == "present":
            detail = source.get(None, label["product_id"]).detail
            if detail["product_code"] != label.get("product_code"):
                raise ValueError("Human label does not match production mirror")
        relative = Path(label["image"])
        image = (manifest.parent / relative).resolve()
        if relative.is_absolute() or not image.is_relative_to(manifest.parent) or not image.is_file():
            raise ValueError("Images must exist inside the evaluation directory")
    output_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="prepared-", dir=output_parent))
    # Byte-identical evidence of answers confirmed before execution.
    (work / "human-labels.json").write_bytes(original)
    atomic_json(work / "query-objects.json", objects)
    atomic_json(work / "preflight.json", {**source.context(), "prepared_at": timestamp(),
        "label_sha256": sha256(original).hexdigest(), "objects": len(objects),
        "present_objects": sum(l["status"] == "present" for l in labels),
        "absent_objects": sum(l["status"] == "absent" for l in labels),
        "image_root": str(manifest.parent), "real_api_calls": 0,
        "image_sha256": {l["object_id"]: sha256((manifest.parent / l["image"]).read_bytes()).hexdigest() for l in labels},
        "execution_authorized": False, "budget_confirmed": False,
        "note": "Only image bytes and crop may enter the model. Freeze a no-match decision policy separately before execution."})
    return work


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="第二轮独立人工标签预检；不执行付费查询")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--output-parent", required=True)
    args = parser.parse_args()
    print(prepare(args.manifest, args.catalog, args.output_parent))

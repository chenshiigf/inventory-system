"""Independent human labels and exact-ID metrics; never a model input."""
import math
import statistics


def validate_label(label, source_id):
    if not isinstance(label, dict) or label.get("source_id") != source_id or not label.get("object_id"):
        raise ValueError("Human label must identify its object and independent source")
    status = label.get("status")
    if status not in {"pending", "present", "absent", "ambiguous"}:
        raise ValueError("Unknown human label status")
    pid = label.get("product_id")
    if status == "present" and (type(pid) is not int or pid <= 0):
        raise ValueError("Present labels require a positive production product ID")
    if status != "present" and pid is not None:
        raise ValueError("Unresolved/absent labels cannot supply a correct product ID")


def summarize(results, labels, source_id):
    """Count objects, not distinct product IDs. Unknowns are never successes."""
    by_object = {}
    for label in labels:
        validate_label(label, source_id)
        if label["object_id"] in by_object:
            raise ValueError("Duplicate object label")
        by_object[label["object_id"]] = label
    seen, rows, absent, pending = set(), [], [], []
    for result in results:
        oid = result["object_id"]
        if oid in seen:
            raise ValueError("Duplicate result object")
        seen.add(oid)
        candidates = result["candidates"]
        for candidate in candidates:
            product = candidate["product"]
            if product.get("source_id") != source_id or product.get("product_key") != f"{source_id}:{product['id']}":
                raise ValueError("Candidate namespace does not match human labels")
        label = by_object.get(oid, {"status": "pending"})
        status = label["status"]
        if status == "absent":
            # An explicit, independently frozen decision policy is required.
            # Candidate similarity alone is not a no-match decision.
            absent.append({"object_id": oid, "no_match_decision": result.get("no_match_decision")})
        elif status != "present":
            pending.append({"object_id": oid, "status": status})
        else:
            pid = label["product_id"]
            ids = [c["product"]["id"] for c in candidates]
            rank = ids.index(pid) + 1 if pid in ids else None
            digest = label.get("image_sha256")
            shared = [i + 1 for i, c in enumerate(candidates) if digest and c["product"].get("image_sha256") == digest]
            rows.append({"object_id": oid, "source_id": source_id, "product_id": pid,
                "product_key": f"{source_id}:{pid}", "product_code": label.get("product_code"),
                "rank": rank, "same_image_candidate_ranks": shared,
                "category": label.get("category") or "unknown",
                "environment": label.get("environment") or "unknown"})
    if set(by_object) - seen:
        raise ValueError("Labels contain objects missing from this result set")

    def rates(group):
        return {"objects": len(group), **{f"top{k}": {"hits": sum(r["rank"] is not None and r["rank"] <= k for r in group),
            "denominator": len(group), "rate": sum(r["rank"] is not None and r["rank"] <= k for r in group) / len(group) if group else None} for k in (1, 3, 5)}}

    latencies = [r["elapsed_ms"] for r in results if isinstance(r.get("elapsed_ms"), (int, float))]
    decided = [a for a in absent if type(a["no_match_decision"]) is bool]
    return {"total_query_objects": len(results), "reviewed_present_objects": len(rows),
        "pending_or_ambiguous": pending, "absent_objects": len(absent), **rates(rows), "rows": rows,
        "groups": {key: {value: rates([r for r in rows if r[key] == value]) for value in sorted({r[key] for r in rows})} for key in ("category", "environment")},
        "absent_metrics": {"decided": len(decided), "undecided": len(absent) - len(decided),
            "correct_rejections": sum(a["no_match_decision"] is True for a in decided),
            "false_accepts": sum(a["no_match_decision"] is False for a in decided),
            "correct_rejection_rate": sum(a["no_match_decision"] is True for a in decided) / len(decided) if decided else None},
        "mean_elapsed_ms": statistics.mean(latencies) if latencies else None,
        "p95_elapsed_ms": sorted(latencies)[math.ceil(len(latencies) * .95) - 1] if latencies else None,
        "note": "Exact source-qualified IDs only; shared images do not create exact-ID hits. Pending and absent objects excluded from Top K denominator."}


def query_objects(manifest, *, source_id):
    """Round 2 boundary: explicitly project image/crop fields, stripping answers."""
    allowed = ("object_id", "image_id", "image", "crop")
    ids = set()
    projected = []
    for item in manifest:
        if item.get("source_id") != source_id or not item.get("object_id") or not item.get("image"):
            raise ValueError("Evaluation objects require explicit source, object and image")
        if item["object_id"] in ids:
            raise ValueError("Duplicate evaluation object")
        ids.add(item["object_id"])
        projected.append({**{k: item[k] for k in allowed if k in item}, "source_id": source_id})
    return projected

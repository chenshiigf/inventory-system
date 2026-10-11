from copy import deepcopy

import pytest

from app.services.visual_search.evaluation import query_objects, summarize


SOURCE = "mock-production"


def candidate(pid, digest="shared"):
    return {"product": {"id": pid, "product_key": f"{SOURCE}:{pid}", "source_id": SOURCE,
            "image_sha256": digest}, "similarity": .9}


def test_exact_object_denominator_pending_and_shared_image_not_auto_correct():
    results = [{"object_id": "pending", "candidates": [candidate(1)]},
               {"object_id": "first", "candidates": [candidate(2), candidate(1)]},
               {"object_id": "second", "candidates": [candidate(1)]}]
    labels = [{"object_id": oid, "source_id": SOURCE, "status": "present", "product_id": 1,
               "image_sha256": "shared"} for oid in ("first", "second")]
    summary = summarize(results, labels, SOURCE)
    assert summary["total_query_objects"] == 3 and summary["reviewed_present_objects"] == 2
    assert summary["top1"] == {"hits": 1, "denominator": 2, "rate": .5}
    assert summary["top3"]["rate"] == summary["top5"]["rate"] == 1
    assert summary["rows"][0]["same_image_candidate_ranks"] == [1, 2]
    assert [r["product_id"] for r in summary["rows"]] == [1, 1]
    assert summary["pending_or_ambiguous"] == [{"object_id": "pending", "status": "pending"}]


def test_absent_requires_explicit_decision_and_is_outside_topk_denominator():
    results = [{"object_id": "accepted", "candidates": [candidate(1)], "no_match_decision": False},
               {"object_id": "rejected", "candidates": [], "no_match_decision": True},
               {"object_id": "undecided", "candidates": []}]
    labels = [{"object_id": r["object_id"], "source_id": SOURCE, "status": "absent"} for r in results]
    summary = summarize(results, labels, SOURCE)
    assert summary["top1"]["rate"] is None and summary["top1"]["denominator"] == 0
    assert summary["absent_metrics"] == {"decided": 2, "undecided": 1,
        "correct_rejections": 1, "false_accepts": 1, "correct_rejection_rate": .5}


def test_cross_source_and_duplicate_object_labels_rejected():
    result = {"object_id": "x", "candidates": [candidate(1)]}
    label = {"object_id": "x", "source_id": SOURCE, "status": "present", "product_id": 1}
    with pytest.raises(ValueError, match="Duplicate object"):
        summarize([result], [label, label], SOURCE)
    wrong = deepcopy(result)
    wrong["candidates"][0]["product"]["product_key"] = "development:1"
    with pytest.raises(ValueError, match="namespace"):
        summarize([wrong], [label], SOURCE)
    with pytest.raises(ValueError, match="source"):
        summarize([result], [{**label, "source_id": "development"}], SOURCE)


def test_round2_query_projection_contains_no_answers_or_descriptions():
    item = {"object_id": "x", "image_id": "photo", "image": "originals/photo.jpg", "crop": [0, 0, 1, 1],
            "source_id": SOURCE, "product_id": 123, "product_code": "HUMAN-ONLY", "description": "ANSWER", "status": "present"}
    assert query_objects([item], source_id=SOURCE) == [{k: item[k] for k in ("object_id", "image_id", "image", "crop", "source_id")}]
    with pytest.raises(ValueError, match="Duplicate"):
        query_objects([item, item], source_id=SOURCE)

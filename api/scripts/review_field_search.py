"""Offline human review of saved search results; no search/model/stock writes."""
import argparse
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.visual_search.evaluation import summarize, validate_label
from app.services.visual_search.production_catalog import atomic_json, timestamp
from app.services.visual_search.sources import ProductionSource


def save_review(run, label_input, catalog, labels_path):
    run, label_input, catalog, labels_path = map(lambda p: Path(p).resolve(), (run, label_input, catalog, labels_path))
    state = json.loads((run / "state.json").read_text(encoding="utf8"))
    source = ProductionSource(catalog)
    if state["source_id"] != source.source_id or state["index_namespace"] != source.identity_key:
        raise ValueError("Review source/index namespace mismatch")
    originals = {p: sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
    documents = [json.loads(p.read_text(encoding="utf8")) for p in sorted((run / "results").glob("*.json"))]
    results = [d for d in documents if "object_id" in d and "candidates" in d]
    if not results:
        raise ValueError("No object results to review")
    supplied = json.loads(label_input.read_text(encoding="utf-8-sig"))
    labels = []
    for label in supplied:
        validate_label(label, source.source_id)
        label = dict(label)
        if label["status"] == "present":
            product = source.get(None, label["product_id"]).detail
            if product["product_code"] != label.get("product_code"):
                raise ValueError("Human product code does not match mirror")
            label.update(product_key=product["product_key"], image_sha256=product["image_sha256"], category=product["category_name"])
        labels.append(label)
    summary = summarize(results, labels, source.source_id)
    old_bytes = labels_path.read_bytes() if labels_path.is_file() else b"[]"
    previous = json.loads(old_bytes.decode("utf-8-sig"))
    if not isinstance(previous, list):
        raise ValueError("Existing labels must be an array; no overwrite attempted")
    parent = labels_path.parent / "reviews"
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=run.name + "-", dir=parent))
    (work / "previous-labels.json").write_bytes(old_bytes)
    reviewed_at = timestamp()
    indexed_results = {r["object_id"]: r for r in results}
    complete = [{**label, "run_id": run.name, "query_id": indexed_results[label["object_id"]]["query_id"],
                 "reviewed_at": reviewed_at, "review_revision": work.name,
                 "image_id": indexed_results[label["object_id"]]["image_id"],
                 "image": indexed_results[label["object_id"]]["image"],
                 "crop": indexed_results[label["object_id"]]["crop"],
                 "original_sha256": indexed_results[label["object_id"]]["original_sha256"],
                 "label_origin": "independent-human-confirmation"} for label in labels]
    review = {"source_id": source.source_id, "run_id": run.name, "reviewed_at": reviewed_at,
              "labels": complete, "metrics": summary,
              "original_result_hashes": {str(p.relative_to(run)): h for p, h in originals.items()}}
    atomic_json(work / "review.json", review)
    rows = []
    ranked = {r["object_id"]: r for r in summary["rows"]}
    for obj in results:
        row = ranked.get(obj["object_id"])
        rows.append("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in
            (obj["object_id"], row["product_key"] if row else "待核实", row["product_code"] if row else "—",
             row["rank"] if row else "—", "人工确认" if row else "不计入准确率")) + "</tr>")
    report = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>第一轮人工审核</title>'
              '<style>body{font:16px system-ui;margin:32px;color:#182438}table{border-collapse:collapse}td,th{padding:12px;border:1px solid #ccd4dd}</style>'
              '<h1>第一轮人工审核</h1><p>总查询 ' + str(len(results)) + ' 个，已审核 ' + str(summary["reviewed_present_objects"]) + ' 个。</p>'
              '<p>Top 1 / 3 / 5：' + ' / '.join(str(summary[f"top{k}"]["hits"]) + '/' + str(summary[f"top{k}"]["denominator"]) for k in (1, 3, 5)) +
              '。同图关系单独记录，T06、T07 分别计数；相似度不是正确概率。</p>'
              '<table><tr><th>对象</th><th>来源及商品 ID</th><th>货号</th><th>真实排名</th><th>状态</th></tr>' + ''.join(rows) + '</table></html>')
    (work / "report.html").write_text(report, encoding="utf8")
    if any(sha256(p.read_bytes()).hexdigest() != h for p, h in originals.items()):
        raise ValueError("Original round files changed")
    # Append a revision; earlier feedback remains available and is never replaced.
    atomic_json(labels_path, previous + complete)
    return work


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="保存独立人工标签，不调用搜索或模型")
    parser.add_argument("--run", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--labels-store", required=True)
    args = parser.parse_args()
    print(save_review(args.run, args.labels, args.catalog, args.labels_store))

"""Human-review pilot for Phase 5M machine-extracted Knowledge.

This module prepares two deterministic review CSVs:

1. coverage_blind.csv
   Source paragraphs only. Review this FIRST, without opening knowledge_review.csv,
   to create a human gold annotation without seeing model output.

2. knowledge_review.csv
   Stratified sample of machine-extracted Knowledge records with their exact
   provenance paragraphs for faithfulness/type/qualification/hallucination review.

The source artifacts are read-only. Nothing in the Phase 5M directory is modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from .machine_knowledge_connector import MachineKnowledgeAdapter, DEFAULT_SOURCE

DEFAULT_SEED = "phase5r-pilot-v1"
DEFAULT_KNOWLEDGE_N = 30
DEFAULT_COVERAGE_N = 20

# Fixed before review. This exactly totals 30 for the current Phase 5M pilot.
KNOWLEDGE_QUOTAS = {
    "experimental_method": 8,
    "measurement_method": 4,
    "analysis_method": 5,
    "empirical_finding": 5,
    "limitation": 4,
    "study_design": 2,
    "review_statement": 1,
    "other": 1,
}

# Coverage is source-first. These are targets, not guaranteed counts: if a category
# lacks eligible paragraphs, the deficit is deterministically reallocated.
COVERAGE_QUOTAS = {
    "methods": 8,
    "results": 6,
    "discussion_limitations": 4,
    "other": 2,
}

FAITHFULNESS = {"Supported", "Partially supported", "Unsupported"}
TYPE_CORRECT = {"Yes", "No"}
QUALIFICATION = {"None", "Minor", "Major"}
HALLUCINATION = {"No", "Yes"}
SEMANTIC_PROVENANCE = {"Yes", "Partial", "No"}
COVERAGE_EXTRACTABLE = {"Yes", "No"}
REVIEW_STATUS = {"not_started", "in_progress", "completed"}

KNOWLEDGE_REVIEW_FIELDS = [
    "sample_id", "knowledge_id", "knowledge_type",
    "target_concepts", "sensors", "measurements",
    "experimental_methods", "analysis_methods", "conditions", "outcomes",
    "findings", "limitations", "applicability_notes", "population_json",
    "provenance_paragraph_ids", "provenance_section_titles",
    "provenance_source_texts",
    # Human labels
    "faithfulness", "type_correct", "qualification_preservation",
    "hallucination", "semantic_provenance", "reviewer_comment", "review_status",
]

COVERAGE_REVIEW_FIELDS = [
    "sample_id", "paragraph_id", "section_id", "section_title", "category",
    "kind", "source_order", "source_text_sha256", "source_text",
    # Human gold labels. No model output is exposed in this file.
    "gold_extractable", "gold_knowledge_types", "gold_knowledge_items",
    "reviewer_comment", "review_status",
]


def _stable_key(seed: str, identifier: str) -> str:
    return hashlib.sha256(f"{seed}:{identifier}".encode("utf-8")).hexdigest()


def _join(values: Iterable[str]) -> str:
    return "\n".join(str(v) for v in values)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    # utf-8-sig keeps Japanese text friendly when opened directly in Excel.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _allocate_sample(items: list[dict], group_key: str, quotas: dict[str, int],
                     total: int, seed: str, id_key: str) -> list[dict]:
    """Deterministic stratified sample with deterministic deficit reallocation."""
    by_group: dict[str, list[dict]] = {}
    for item in items:
        by_group.setdefault(str(item[group_key]), []).append(item)
    for group, rows in by_group.items():
        rows.sort(key=lambda r: _stable_key(seed + ":" + group, str(r[id_key])))

    selected: list[dict] = []
    selected_ids: set[str] = set()
    for group, quota in quotas.items():
        for item in by_group.get(group, [])[:quota]:
            selected.append(item)
            selected_ids.add(str(item[id_key]))

    # If a requested stratum was too small, fill from every remaining record.
    if len(selected) < total:
        remaining = [r for r in items if str(r[id_key]) not in selected_ids]
        remaining.sort(key=lambda r: _stable_key(seed + ":reallocate", str(r[id_key])))
        selected.extend(remaining[: total - len(selected)])

    if len(selected) < total:
        raise ValueError(f"Only {len(selected)} eligible rows available; {total} requested")
    selected = selected[:total]
    # Final ordering is deterministic but mixed across strata to reduce long runs.
    selected.sort(key=lambda r: _stable_key(seed + ":final", str(r[id_key])))
    return selected


def _knowledge_rows(adapter: MachineKnowledgeAdapter, n: int, seed: str) -> list[dict]:
    if n != DEFAULT_KNOWLEDGE_N:
        # The fixed quotas are part of the pilot protocol. A different n requires
        # an explicit future protocol rather than silently changing the design.
        raise ValueError(f"Pilot knowledge sample size is fixed at {DEFAULT_KNOWLEDGE_N}")
    sample = _allocate_sample(adapter.records, "knowledge_type", KNOWLEDGE_QUOTAS,
                              n, seed + ":knowledge", "knowledge_id")
    rows = []
    for i, record in enumerate(sample, 1):
        refs = record["provenance"]
        paragraphs = [adapter.lookup[ref["paragraph_id"]] for ref in refs]
        rows.append({
            "sample_id": f"K{i:03d}",
            "knowledge_id": record["knowledge_id"],
            "knowledge_type": record["knowledge_type"],
            "target_concepts": _join(record["target_concepts"]),
            "sensors": _join(record["sensors"]),
            "measurements": _join(record["measurements"]),
            "experimental_methods": _join(record["experimental_methods"]),
            "analysis_methods": _join(record["analysis_methods"]),
            "conditions": _join(record["conditions"]),
            "outcomes": _join(record["outcomes"]),
            "findings": _join(record["findings"]),
            "limitations": _join(record["limitations"]),
            "applicability_notes": _join(record["applicability_notes"]),
            "population_json": json.dumps(record["population"], ensure_ascii=False, sort_keys=True),
            "provenance_paragraph_ids": _join(ref["paragraph_id"] for ref in refs),
            "provenance_section_titles": _join(ref["section_title"] for ref in refs),
            "provenance_source_texts": "\n--- SOURCE PARAGRAPH ---\n".join(p["source_text"] for p in paragraphs),
            "faithfulness": "",
            "type_correct": "",
            "qualification_preservation": "",
            "hallucination": "",
            "semantic_provenance": "",
            "reviewer_comment": "",
            "review_status": "not_started",
        })
    return rows


def _section_category(title: str) -> str:
    """Reproduce Phase 5M's deterministic section-category rule.

    Phase 5M stores category on section metadata, not on source_segments.jsonl
    paragraph rows. Derive it from the preserved section_title instead of
    requiring a field that is absent from the real paragraph artifact.
    """
    title = title.lower()
    if any(word in title for word in ("method", "experiment")):
        return "methods"
    if "result" in title:
        return "results"
    if any(word in title for word in ("discussion", "limitation", "future work")):
        return "discussion_limitations"
    return "other"


def _coverage_rows(adapter: MachineKnowledgeAdapter, n: int, seed: str) -> list[dict]:
    if n != DEFAULT_COVERAGE_N:
        raise ValueError(f"Pilot coverage sample size is fixed at {DEFAULT_COVERAGE_N}")
    # Phase 5M paragraph artifacts do not carry section category. Reconstruct the
    # exact deterministic category from section_title without modifying source data.
    # Pilot scope: body paragraphs only. Short/empty artifacts and abstract text are
    # excluded so the first human pilot evaluates substantive source passages.
    eligible = [dict(p, category=_section_category(p["section_title"]))
                for p in adapter.paragraphs
                if p["role"] == "body" and len(p["source_text"].strip()) >= 80]
    sample = _allocate_sample(eligible, "category", COVERAGE_QUOTAS,
                              n, seed + ":coverage", "paragraph_id")
    rows = []
    for i, p in enumerate(sample, 1):
        rows.append({
            "sample_id": f"C{i:03d}",
            "paragraph_id": p["paragraph_id"],
            "section_id": p["section_id"],
            "section_title": p["section_title"],
            "category": p["category"],
            "kind": p["kind"],
            "source_order": p["source_order"],
            "source_text_sha256": p["source_text_sha256"],
            "source_text": p["source_text"],
            "gold_extractable": "",
            "gold_knowledge_types": "",
            "gold_knowledge_items": "",
            "reviewer_comment": "",
            "review_status": "not_started",
        })
    return rows


def _guide() -> str:
    return """# Phase 5R Knowledge Extraction Human Evaluation Pilot\n\n"
"This pilot evaluates Phase 5M machine-extracted Knowledge without assuming it is correct.\n\n"
"## Review order (important)\n"
"1. Review `coverage_blind.csv` FIRST. Do not open `knowledge_review.csv` until the blind Gold annotation is finished.\n"
"2. Freeze/save the completed coverage file.\n"
"3. Then review `knowledge_review.csv`.\n\n"
"## Knowledge review labels\n"
"- faithfulness: `Supported` / `Partially supported` / `Unsupported`\n"
"  - Supported: source alone directly supports the extracted Knowledge.\n"
"  - Partially supported: core claim is present, but a condition/scope/qualification is lost or mildly generalized.\n"
"  - Unsupported: source does not support the claim, or unsupported information is added.\n"
"- type_correct: `Yes` / `No`\n"
"- qualification_preservation: `None` / `Minor` / `Major`\n"
"- hallucination: `No` / `Yes` (Yes means content not present in the cited source was added.)\n"
"- semantic_provenance: `Yes` / `Partial` / `No`\n"
"- review_status: `not_started` / `in_progress` / `completed`\n\n"
"## Coverage blind labels\n"
"Read only the source paragraph and annotate what should be extracted, without consulting AI output.\n"
"- gold_extractable: `Yes` if the paragraph contains at least one useful structured Knowledge item; otherwise `No`.\n"
"- gold_knowledge_types: semicolon-separated expected types (e.g. `experimental_method;analysis_method`).\n"
"- gold_knowledge_items: concise human-authored items, one per line. Preserve conditions and scope.\n"
"- review_status: `not_started` / `in_progress` / `completed`.\n\n"
"Structural provenance (IDs/hashes/record-paragraph linkage) is already code-validated. This human pilot evaluates semantic faithfulness and coverage.\n"
"""


def prepare(source: Path, output: Path, seed: str = DEFAULT_SEED) -> dict:
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refuse overwrite: output directory is not empty")
    output.mkdir(parents=True, exist_ok=True)

    adapter = MachineKnowledgeAdapter(source)
    audit = adapter.audit()
    coverage = _coverage_rows(adapter, DEFAULT_COVERAGE_N, seed)
    knowledge = _knowledge_rows(adapter, DEFAULT_KNOWLEDGE_N, seed)

    _write_csv(output / "coverage_blind.csv", COVERAGE_REVIEW_FIELDS, coverage)
    _write_csv(output / "knowledge_review.csv", KNOWLEDGE_REVIEW_FIELDS, knowledge)
    (output / "EVALUATION_GUIDE.md").write_text(_guide(), encoding="utf-8")

    manifest = {
        "phase": "5R",
        "purpose": "knowledge_extraction_human_evaluation_pilot",
        "source": str(source),
        "seed": seed,
        "source_audit": audit,
        "knowledge_sample_size": len(knowledge),
        "knowledge_quotas": KNOWLEDGE_QUOTAS,
        "knowledge_sample_counts": dict(Counter(r["knowledge_type"] for r in knowledge)),
        "coverage_sample_size": len(coverage),
        "coverage_quotas": COVERAGE_QUOTAS,
        "coverage_sample_counts": dict(Counter(r["category"] for r in coverage)),
        "coverage_eligibility": "role=body and stripped source_text length >= 80",
        "blind_review_order": ["coverage_blind.csv", "knowledge_review.csv"],
        "files_sha256": {},
    }
    for name in ("coverage_blind.csv", "knowledge_review.csv", "EVALUATION_GUIDE.md"):
        manifest["files_sha256"][name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
    (output / "sampling_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _read_review(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def validate_reviews(output: Path) -> dict:
    """Validate entered labels; blank labels are allowed until a row is completed."""
    output = Path(output).expanduser().resolve()
    knowledge = _read_review(output / "knowledge_review.csv")
    coverage = _read_review(output / "coverage_blind.csv")
    if len(knowledge) != DEFAULT_KNOWLEDGE_N or len(coverage) != DEFAULT_COVERAGE_N:
        raise ValueError("Review row count changed")

    def check(row: dict, field: str, allowed: set[str], row_name: str) -> None:
        value = row[field].strip()
        if value and value not in allowed:
            raise ValueError(f"{row_name}: invalid {field}={value!r}")

    for row in knowledge:
        sid = row["sample_id"]
        check(row, "faithfulness", FAITHFULNESS, sid)
        check(row, "type_correct", TYPE_CORRECT, sid)
        check(row, "qualification_preservation", QUALIFICATION, sid)
        check(row, "hallucination", HALLUCINATION, sid)
        check(row, "semantic_provenance", SEMANTIC_PROVENANCE, sid)
        check(row, "review_status", REVIEW_STATUS, sid)
        if row["review_status"] == "completed":
            required = ["faithfulness", "type_correct", "qualification_preservation",
                        "hallucination", "semantic_provenance"]
            if any(not row[f].strip() for f in required):
                raise ValueError(f"{sid}: completed row has blank Knowledge labels")

    for row in coverage:
        sid = row["sample_id"]
        check(row, "gold_extractable", COVERAGE_EXTRACTABLE, sid)
        check(row, "review_status", REVIEW_STATUS, sid)
        if row["review_status"] == "completed":
            if not row["gold_extractable"].strip():
                raise ValueError(f"{sid}: completed row has blank gold_extractable")
            if row["gold_extractable"] == "Yes" and not row["gold_knowledge_items"].strip():
                raise ValueError(f"{sid}: extractable paragraph requires gold_knowledge_items")

    return {
        "knowledge_completed": sum(r["review_status"] == "completed" for r in knowledge),
        "knowledge_total": len(knowledge),
        "coverage_completed": sum(r["review_status"] == "completed" for r in coverage),
        "coverage_total": len(coverage),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="Create deterministic blind/review CSVs")
    p_prepare.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p_prepare.add_argument("--output", type=Path, required=True)
    p_prepare.add_argument("--seed", default=DEFAULT_SEED)

    p_validate = sub.add_parser("validate", help="Validate human-entered labels")
    p_validate.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.source, args.output, args.seed)
    else:
        result = validate_reviews(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

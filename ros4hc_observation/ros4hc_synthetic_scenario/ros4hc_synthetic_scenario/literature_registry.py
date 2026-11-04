"""Strict offline paper/evidence registry; metadata is always curator supplied."""
import copy
import json
import re
from pathlib import Path
from typing import TypedDict

STATUSES = ('candidate', 'metadata_checked', 'evidence_curated', 'verified', 'excluded')
FINDINGS = ('association', 'prediction', 'measurement', 'validation', 'comparison',
            'intervention', 'temporal_pattern', 'method', 'other')
RELATIONSHIPS = ('associated_with', 'predicts', 'measured_by', 'differs_between', 'changes_with', 'none')
DIRECTIONS = ('increase', 'decrease', 'positive', 'negative', 'mixed', 'not_applicable', 'unknown')
PUBLICATION_STATUSES = ('published', 'preprint', 'accepted', 'in_press', 'retracted', 'unknown')
PUBLICATION_TYPES = ('journal_article', 'conference_paper', 'review', 'preprint', 'thesis',
                     'book_chapter', 'report', 'other')


class PaperRecord(TypedDict):
    paper_id: str
    title: str
    authors: list | None
    year: int | None
    venue: str | None
    doi: str | None
    url: str | None
    publication_type: str | None
    publication_status: str | None
    peer_reviewed: bool | None
    study_design: str | None
    population: str | None
    sample_size: int | None
    sensor_modalities: list | None
    measured_variables: list | None
    tasks_or_states: list | None
    topics: list | None
    external_validation: str | None
    ground_truth_method: str | None
    notes: str | None
    synthetic_fixture: bool
    curation_status: str
    exclusion_reason: str | None


class EvidenceChunk(TypedDict):
    fall_risk: dict | None
    paper_id: str
    chunk_id: str
    section: str | None
    page: str | None
    figure_or_table: str | None
    evidence_summary: str
    short_quote: str | None
    sensor_modalities: list | None
    measured_variables: list | None
    topics: list | None
    population: str | None
    task_or_state: str | None
    finding_type: str | None
    relationship: str | None
    direction: str | None
    limitations: list | None
    curator_notes: str | None
    source_location_verified: bool
    synthetic_fixture: bool
    curation_status: str
    exclusion_reason: str | None


PAPER_LISTS = ('authors', 'sensor_modalities', 'measured_variables', 'tasks_or_states', 'topics')
CHUNK_LISTS = ('sensor_modalities', 'measured_variables', 'topics', 'limitations')


def normalize_doi(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError('DOI must be string/null')
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value.strip(), flags=re.I).lower()
    if not re.fullmatch(r'10\.\d{4,9}/[^\s]+', value):
        raise ValueError('Malformed DOI')
    return value


def _normalize(record, schema, required, lists, bools, enums, integers=()):
    if not isinstance(record, dict) or set(record) - set(schema.__annotations__):
        raise ValueError('Unsupported registry fields or record type')
    for key in required:
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError('Required nonempty string: ' + key)
    if type(record.get('synthetic_fixture')) is not bool:
        raise ValueError('Explicit synthetic_fixture boolean required')
    out = {key: copy.deepcopy(record.get(key)) for key in schema.__annotations__}
    out['curation_status'] = record.get('curation_status', 'candidate')
    if schema is EvidenceChunk:
        out['source_location_verified'] = record.get('source_location_verified', False)
    for key, value in out.items():
        if schema is EvidenceChunk and key == 'fall_risk':
            from .fall_risk_evidence import normalize_fall_risk
            out[key] = normalize_fall_risk(value) if value is not None else None
        elif key in bools:
            if value is not None and type(value) is not bool:
                raise ValueError('Expected boolean/null: ' + key)
        elif key in integers:
            if value is not None and (type(value) is not int or value < 1 or (key == 'year' and value > 9999)):
                raise ValueError('Invalid integer: ' + key)
        elif key in lists:
            if value is not None and (not isinstance(value, list) or
                    any(not isinstance(v, str) or not v.strip() for v in value)):
                raise ValueError('Expected nonempty-string list/null: ' + key)
        elif value is not None and not isinstance(value, str):
            raise ValueError('Expected string/null: ' + key)
    for key, choices in enums.items():
        if out[key] is not None and out[key] not in choices:
            raise ValueError('Invalid enum: ' + key)
    if out['curation_status'] not in STATUSES:
        raise ValueError('Invalid curation_status')
    marker = ' '.join(str(out.get(k) or '') for k in ('paper_id', 'title', 'evidence_summary')).casefold()
    if ('synthetic' in marker or 'fake test' in marker) and not out['synthetic_fixture']:
        raise ValueError('Synthetic test content cannot be marked real')
    return out


def validate_paper(record):
    out = _normalize(record, PaperRecord, ('paper_id', 'title'), PAPER_LISTS,
                     ('synthetic_fixture', 'peer_reviewed'),
                     {'publication_type': PUBLICATION_TYPES, 'publication_status': PUBLICATION_STATUSES},
                     ('year', 'sample_size'))
    pattern = r'(?:synthetic_)?paper_\d{4,}' if out['synthetic_fixture'] else r'paper_\d{4,}'
    if not re.fullmatch(pattern, out['paper_id']):
        raise ValueError('Use stable paper_NNNN IDs (synthetic_paper_NNNN for fixtures)')
    out['doi'] = normalize_doi(out['doi'])
    return out


def validate_chunk(record):
    out = _normalize(record, EvidenceChunk, ('paper_id', 'chunk_id', 'evidence_summary'), CHUNK_LISTS,
                     ('synthetic_fixture', 'source_location_verified'),
                     {'finding_type': FINDINGS, 'relationship': RELATIONSHIPS, 'direction': DIRECTIONS})
    if type(out['source_location_verified']) is not bool:
        raise ValueError('source_location_verified must be boolean')
    return out


def read_jsonl(path):
    def strict_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    records = []
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if line.strip():
            try:
                records.append(json.loads(line, object_pairs_hook=strict_object))
            except ValueError as exc:
                raise ValueError(f'{path}:{number}: {exc}') from exc
    return records


def validate_registry(papers, chunks, mode='real'):
    if mode not in ('real', 'synthetic'):
        raise ValueError('mode must be real or synthetic')
    papers = sorted([validate_paper(p) for p in papers], key=lambda p: p['paper_id'])
    chunks = sorted([validate_chunk(c) for c in chunks], key=lambda c: (c['paper_id'], c['chunk_id']))
    by_id, identities, warnings, summaries, locations, dois = {}, set(), [], {}, {}, {}
    for paper in papers:
        pid = paper['paper_id']
        if pid in by_id:
            raise ValueError('Duplicate paper ID: ' + pid)
        by_id[pid] = paper
        if paper['doi']:
            if paper['doi'] in dois:
                raise ValueError('Same normalized DOI assigned to multiple paper IDs')
            dois[paper['doi']] = pid
    for row in papers + chunks:
        if row['synthetic_fixture'] != (mode == 'synthetic'):
            raise ValueError('Synthetic/real mixing or wrong corpus mode')
        if row['curation_status'] == 'excluded' and not row['exclusion_reason']:
            warnings.append({'code': 'missing_exclusion_reason', 'paper_id': row['paper_id']})
    for chunk in chunks:
        identity = (chunk['paper_id'], chunk['chunk_id'])
        if identity in identities:
            raise ValueError('Duplicate paper/chunk ID: ' + repr(identity))
        identities.add(identity)
        if chunk['paper_id'] not in by_id:
            raise ValueError('Orphan evidence: ' + repr(identity))
        from .fall_risk_evidence import has_location, condition_warnings
        location = has_location(chunk)
        warnings.extend(condition_warnings(chunk))
        structured_doi = ((chunk.get('fall_risk') or {}).get('provenance') or {}).get('doi')
        paper_doi = by_id[chunk['paper_id']]['doi']
        if structured_doi and paper_doi and structured_doi != paper_doi:
            raise ValueError('Evidence DOI differs from parent paper DOI')
        if chunk['source_location_verified'] and not location:
            raise ValueError('Verified source location requires a reference')
        if not location:
            warnings.append({'code': 'missing_source_location', 'identity': list(identity)})
        if not chunk['source_location_verified']:
            warnings.append({'code': 'source_location_unverified', 'identity': list(identity)})
        summary = ' '.join(chunk['evidence_summary'].casefold().split())
        if summary in summaries:
            warnings.append({'code': 'identical_normalized_summary', 'identity': list(identity),
                             'other': list(summaries[summary])})
        summaries.setdefault(summary, identity)
        key = (chunk['paper_id'], chunk['page'] or (((chunk.get('fall_risk') or {}).get('provenance') or {}).get('page')), chunk['evidence_summary'])
        if key in locations:
            warnings.append({'code': 'same_paper_page_summary', 'identity': list(identity),
                             'other': list(locations[key])})
        locations.setdefault(key, identity)
    report = {'valid': True, 'mode': mode, 'paper_count': len(papers),
              'evidence_chunk_count': len(chunks), 'warnings': warnings}
    return papers, chunks, report


def load_registry(papers, evidence, mode='real'):
    return validate_registry(read_jsonl(papers), read_jsonl(evidence), mode)

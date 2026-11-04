"""Offline, short curated literature chunks; no downloading."""
import argparse
import copy
import json
import re
from pathlib import Path

REQUIRED = ('paper_id', 'chunk_id', 'title', 'evidence_text')
STRINGS = (*REQUIRED, 'doi', 'url', 'source_type', 'population', 'task_or_state',
           'evidence_type', 'notes')
LISTS = ('authors', 'sensor_modalities', 'measured_variables')
FIELDS = set(STRINGS + LISTS + ('year', 'synthetic_fixture'))


def validate_curation_provenance(value):
    from .literature_registry import STATUSES, RELATIONSHIPS, DIRECTIONS
    fields = {'section', 'page', 'figure_or_table', 'source_location_verified',
              'relationship', 'direction', 'limitations', 'curation_status',
              'paper_curation_status', 'diagnostic_unverified'}
    if not isinstance(value, dict) or not fields <= set(value) or set(value) - (fields | {'fall_risk'}):
        raise ValueError('Invalid curation provenance fields')
    for key, item in value.items():
        if key == 'fall_risk':
            from .fall_risk_evidence import normalize_fall_risk
            normalize_fall_risk(item)
        elif key in ('source_location_verified', 'diagnostic_unverified'):
            if type(item) is not bool:
                raise ValueError('Invalid provenance boolean')
        elif key == 'limitations':
            if item is not None and (not isinstance(item, list) or any(not isinstance(v, str) for v in item)):
                raise ValueError('Invalid limitations')
        elif item is not None and not isinstance(item, str):
            raise ValueError('Invalid provenance string')
    for key, choices in [('curation_status', STATUSES), ('paper_curation_status', STATUSES),
                         ('relationship', RELATIONSHIPS), ('direction', DIRECTIONS)]:
        if value[key] is not None and value[key] not in choices:
            raise ValueError('Invalid provenance enum')
    if any(value[k] not in STATUSES or value[k] == 'excluded' for k in ('curation_status', 'paper_curation_status')):
        raise ValueError('Invalid retrieval curation status')
    expected = any(value[k] != 'verified' for k in ('curation_status', 'paper_curation_status'))
    if value['diagnostic_unverified'] != expected:
        raise ValueError('Inconsistent diagnostic_unverified')
    from .fall_risk_evidence import has_location
    if value['source_location_verified'] and not has_location(value):
        raise ValueError('Verified location requires a reference')
    result = copy.deepcopy(value)
    if 'fall_risk' in result:
        result['fall_risk'] = normalize_fall_risk(result['fall_risk'])
    return result


def validate_record(record):
    if not isinstance(record, dict) or set(record) - (FIELDS | {'curation_provenance'}):
        raise ValueError('Unsupported corpus fields or record type')
    for key in REQUIRED:
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError('Required nonempty string: ' + key)
    for key in STRINGS:
        if record.get(key) is not None and not isinstance(record[key], str):
            raise ValueError('Expected string/null: ' + key)
    for key in LISTS:
        value = record.get(key)
        if value is not None and (not isinstance(value, list) or
                                  any(not isinstance(v, str) for v in value)):
            raise ValueError('Expected string list/null: ' + key)
    year = record.get('year')
    if year is not None and (type(year) is not int or not 1 <= year <= 9999):
        raise ValueError('Malformed year')
    doi = record.get('doi')
    if doi and not re.fullmatch(r'10\.\d{4,9}/[^\s]+', doi):
        raise ValueError('Malformed DOI (use bare DOI)')
    if 'synthetic_fixture' in record and type(record['synthetic_fixture']) is not bool:
        raise ValueError('synthetic_fixture must be boolean')
    if (record['paper_id'].startswith('synthetic_') or
            'synthetic test' in record['evidence_text'].casefold()) and not record.get('synthetic_fixture'):
        raise ValueError('Synthetic fixture must be explicitly marked')
    result = {key: copy.deepcopy(record.get(key)) for key in sorted(FIELDS)}
    if 'curation_provenance' in record:
        result['curation_provenance'] = validate_curation_provenance(record['curation_provenance'])
    return result


def validate_corpus(records):
    result, seen = [], set()
    for record in records:
        item = validate_record(record)
        identity = (item['paper_id'], item['chunk_id'])
        if identity in seen:
            raise ValueError('Duplicate paper/chunk ID: ' + repr(identity))
        seen.add(identity)
        result.append(item)
    return result


def load_corpus(path):
    with Path(path).open(encoding='utf-8') as stream:
        return validate_corpus([json.loads(line) for line in stream if line.strip()])


def main():
    parser = argparse.ArgumentParser(description='Validate local curated evidence JSONL offline')
    parser.add_argument('--corpus', required=True)
    args = parser.parse_args()
    try:
        records = load_corpus(args.corpus)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'Valid corpus: {len(records)} chunks')

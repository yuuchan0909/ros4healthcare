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


def validate_record(record):
    if not isinstance(record, dict) or set(record) - FIELDS:
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
    return {key: copy.deepcopy(record.get(key)) for key in sorted(FIELDS)}


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

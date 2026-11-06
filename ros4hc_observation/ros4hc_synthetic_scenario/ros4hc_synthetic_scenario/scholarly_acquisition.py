"""Phase 5L: small, reproducible official-API knowledge acquisition, not training."""
import argparse
import copy
from collections import Counter
from dataclasses import fields
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from .scholarly_models import KnowledgeCandidate, AUTOMATED_STATUSES, canonical_identity, normalize_id, normalize_license
from .scholarly_sources import (OpenAlexAdapter, EuropePMCAdapter, CrossrefAdapter, OfficialAPIClient,
    FetchError, canonical, sha, save, utc_now)

QUERIES = ('Parkinson disease wearable sensors', 'gait stability IMU experimental method',
           'fall risk older adults wearable sensors')
IDS = ('doi', 'pmid', 'pmcid', 'openalex_id')


def deduplicate(rows):
    """Connected components of exact normalized identifiers; no fuzzy title merging."""
    groups = []
    for row in sorted(copy.deepcopy(rows), key=canonical):
        identities = {(k, v) for k, v in row['identifiers'].items() if v}
        matching = [g for g in groups if identities & g['ids']]
        group = dict(ids=set(identities), rows=[row])
        for old in matching:
            group['ids'] |= old['ids']
            group['rows'] += old['rows']
            groups.remove(old)
        groups.append(group)
    candidates, decisions = [], []
    for group in groups:
        values = {k: sorted(v for kind, v in group['ids'] if kind == k) for k in IDS}
        ids = {k: values[k][0] if values[k] else None for k in IDS}
        candidate = KnowledgeCandidate(canonical_identity(ids), **ids).to_dict()
        candidate['identifier_conflicts'] = {k: v for k, v in values.items() if len(v) > 1}
        candidate['provenance'] = sorted(group['rows'], key=lambda r: ({'crossref': 0, 'europepmc': 1, 'openalex': 2}[r['source']], canonical(r)))
        for name in ('title', 'authors', 'year', 'venue', 'source_type'):
            candidate[name] = next((r[name] for r in candidate['provenance'] if r.get(name)), candidate[name])
        for name in ('publication_types', 'topics'):
            candidate[name] = sorted({s for r in group['rows'] for s in r[name]})
        candidate['access']['locations'] = sorted({canonical(loc): loc for r in group['rows'] for loc in r['locations']}.values(), key=canonical)
        candidate['acquisition'] = dict(
            discovery_sources=sorted({r['source'] for r in group['rows'] if r.get('discovery', True)}),
            queries=sorted({q for r in group['rows'] for q in r.get('queries', [])}),
            retrieved_at_utc=max(r['receipt']['retrieved_at_utc'] for r in group['rows']))
        signals = [dict(s, request_id=r['receipt'].get('request_id')) for r in group['rows'] for s in r['integrity_signals']]
        candidate['integrity']['status_sources'] = signals
        for key in ('retracted', 'correction_present'):
            states = [s[key] for s in signals if type(s.get(key)) is bool]
            candidate['integrity'][key] = True if True in states else (False if states and all(v is False for v in states) else None)
        if any(r['source'] == 'crossref' for r in group['rows']) or len({r['source'] for r in group['rows']}) > 1:
            candidate['knowledge_status'] = 'metadata_enriched'
        if candidate['identifier_conflicts'] or candidate['integrity']['retracted'] is True:
            candidate['knowledge_status'] = 'rejected'
        candidates.append(candidate)
        decisions.append(dict(canonical_id=candidate['canonical_id'], input_records=len(group['rows']),
            identifiers={k: v for k, v in values.items() if v}, identifier_conflicts=candidate['identifier_conflicts'],
            rule='Exact normalized identifier connected component; conflicts quarantined; no title-only merge'))
    candidates.sort(key=lambda c: c['canonical_id'])
    return candidates, sorted(decisions, key=lambda d: d['canonical_id'])


def rights_gate(candidate):
    access = candidate['access']
    for loc in access['locations']:
        loc['normalized_license'] = normalize_license(loc.get('license_raw'))
        loc['license_allowed'] = loc['normalized_license'] in ('CC-BY', 'CC0')
        loc['acquisition_allowed'] = bool(loc['license_allowed'] and loc.get('is_oa') is True and
            loc.get('source') == 'europepmc' and candidate['pmcid'] and
            loc.get('source_identifier') == candidate['pmcid'] and
            loc.get('api_fulltext_path') == '/' + candidate['pmcid'] + '/fullTextXML' and
            candidate['knowledge_status'] != 'rejected')
    ordered = sorted(access['locations'], key=lambda loc: (not loc['acquisition_allowed'],
        loc['source'] != 'europepmc', loc['normalized_license'] == 'unknown', canonical(loc)))
    selected = ordered[0] if ordered else None
    access.update(status='metadata_only', license=selected['normalized_license'] if selected else 'unknown',
        license_source=(selected['source'] + ':' + str(selected['source_identifier'])) if selected else None,
        fulltext_available=any(l.get('is_oa') is True and l.get('location') for l in ordered),
        fulltext_acquisition_allowed=any(l['acquisition_allowed'] for l in ordered))
    if access['fulltext_acquisition_allowed']:
        access['status'] = 'eligible_api_fulltext'
    return access


def validate_fulltext(body, candidate, location):
    if not location.get('acquisition_allowed'):
        raise ValueError('Rights gate denied')
    if b'<!ENTITY' in body.upper():
        raise ValueError('XML entity declaration rejected')
    root = ET.fromstring(body)
    if root.tag != 'article':
        raise ValueError('JATS article required')
    meta = root.find('./front/article-meta')
    if meta is None:
        raise ValueError('Article metadata missing')
    ids = {}
    for node in meta.findall('article-id'):
        kind, text = node.get('pub-id-type'), (node.text or '').strip()
        if kind == 'doi':
            ids['doi'] = normalize_id('doi', text)
        elif kind in ('pmc', 'pmcid'):
            ids['pmcid'] = normalize_id('pmcid', 'PMC' + text if text.isdigit() else text)
    if ids.get('pmcid') != candidate['pmcid'] or (candidate['doi'] and ids.get('doi') != candidate['doi']):
        raise ValueError('Full-text identity mismatch')
    licenses = meta.findall('./permissions/license')
    if not licenses:
        raise ValueError('XML license missing')
    declarations = []
    for node in licenses:
        links = [v for el in node.iter() for k, v in el.attrib.items() if k.endswith('href')]
        if not links:
            raise ValueError('Unambiguous XML license URI required')
        declarations.extend(normalize_license(v) for v in links)
        text = ' '.join(node.itertext()).lower()
        if any(t in text for t in ('non-commercial', 'noncommercial', 'no derivatives', 'no-derivatives', 'cc by-nc', 'cc by-nd')):
            raise ValueError('Restrictive XML license')
    if any(v not in ('CC-BY', 'CC0') for v in declarations) or location['normalized_license'] not in declarations:
        raise ValueError('XML license does not confirm metadata permission')
    return dict(license=location['normalized_license'], identifiers=ids, license_declarations=sorted(set(declarations)))


def jsonl(path, rows):
    save(path, ''.join(canonical(r) + '\n' for r in rows).encode(), binary=True)


def fixture_records(query, timestamp):
    """Invented transport-free records, never mixed with live discoveries."""
    from .scholarly_sources import record, identifiers
    rows = []
    for source in ('openalex', 'europepmc'):
        row = record(source, identifiers(doi='10.9999/phase5l.fixture', pmid='99999999', pmcid='PMC99999999'),
            'Synthetic acquisition fixture', ['Test Author'], 2026, 'Fixture only',
            dict(source=source, retrieved_at_utc=timestamp, request_id='fixture-' + source, response_sha256='fixture'))
        row.update(queries=[query], discovery=True)
        row['locations'] = [dict(source=source, source_identifier='PMC99999999', location='fixture-only',
            license_raw=None, is_oa=True, api_fulltext_path=None, version=None)]
        rows.append(row)
    return rows


def run(output, dry_run=False, client=None, cache=None, max_fulltext=5):
    output = Path(output).expanduser().resolve()
    existing = next(p for p in (output, *output.parents) if p.is_dir())
    if subprocess.run(['git', '-C', str(existing), 'rev-parse', '--show-toplevel'], capture_output=True).returncode == 0:
        raise ValueError('Artifacts must be outside Git')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty; prior runs are preserved')
    if type(max_fulltext) is not int or not 0 <= max_fulltext <= 5:
        raise ValueError('Full-text attempt limit must be 0..5')
    output.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now()
    manifest = dict(phase='5L', timestamp_utc=timestamp, git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        dry_run=dry_run, queries=list(QUERIES), sources=['openalex', 'europepmc', 'crossref'],
        limits=dict(per_source_per_query=10, per_query=20, global_unique=50, fulltext_attempts=max_fulltext,
                    request_attempts=2, timeout_seconds=20, interval_seconds=1.1, retry_backoff_seconds=[2], max_retry_after_seconds=30),
        status='running', errors=[], provenance_policy='No abstracts/reference lists cached; raw response hashes and projected metadata preserved')
    save(output/'manifest.json', manifest)
    save(output/'queries.json', list(QUERIES))
    client = client or (None if dry_run else OfficialAPIClient(cache or output/'cache'))
    rows, source_counts, invalid = [], Counter(), []
    for query in QUERIES:
        query_rows = []
        for adapter in (OpenAlexAdapter(), EuropePMCAdapter()):
            if dry_run:
                batch = [r for r in fixture_records(query, timestamp) if r['source'] == adapter.source]
                query_rows += batch
                source_counts[adapter.source] += len(batch)
                continue
            try:
                items, receipt, normalizer = adapter.discover(client, query)
                # Defense in depth: ignore any excess returned by a provider.
                for item in items[:10]:
                    try:
                        row = normalizer(item, receipt)
                        row.update(queries=[query], discovery=True)
                        query_rows.append(row)
                        source_counts[adapter.source] += 1
                    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                        invalid.append(dict(source=adapter.source, query=query, reason='malformed_record'))
            except (FetchError, ValueError, TypeError, KeyError, AttributeError) as exc:
                manifest['errors'].append(dict(stage='discovery', source=adapter.source, query=query,
                    error=exc.kind if isinstance(exc, FetchError) else 'malformed_response', http_status=getattr(exc, 'code', None)))
        rows += query_rows[:20]
        print('Discovery:', query, 'records=', len(query_rows[:20]), flush=True)
    candidates, decisions = deduplicate(rows)
    dedup = dict(valid_discovery_records=len(rows), unique_before_cap=len(candidates),
        duplicate_records_removed=len(rows) - len(candidates), malformed_records=invalid, decisions=decisions,
        excluded_by_global_cap=[c['canonical_id'] for c in candidates[50:]])
    candidates = candidates[:50]
    save(output/'dedup_report.json', dedup)
    jsonl(output/'candidates.jsonl', candidates)
    if not dry_run:
        adapter = CrossrefAdapter()
        for i, candidate in enumerate(candidates):
            if not candidate['doi']:
                continue
            doi = candidate['doi']
            try:
                row = adapter.enrich(client, doi)
                row.update(queries=candidate['acquisition']['queries'], discovery=False)
                merged, _ = deduplicate(candidate['provenance'] + [row])
                if len(merged) != 1 or merged[0]['canonical_id'] != candidate['canonical_id']:
                    raise ValueError('Enrichment identity conflict')
                candidate = candidates[i] = merged[0]
                source_counts['crossref'] += 1
            except (FetchError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
                manifest['errors'].append(dict(stage='enrichment', source='crossref', canonical_id=candidate['canonical_id'],
                    error=exc.kind if isinstance(exc, FetchError) else 'malformed_response', http_status=getattr(exc, 'code', None)))
            try:
                signals, receipt = adapter.updates(client, doi)
                candidate['integrity']['status_sources'].append(dict(source='crossref', field='updates query',
                    request_id=receipt['request_id'], result='signals_present' if signals else 'no_updates_reported_not_proof_of_absence',
                    retrieved_at_utc=receipt['retrieved_at_utc'], maximum_notices_checked=5))
                candidate['integrity']['status_sources'] += signals
                for signal in signals:
                    typ = str(signal.get('update_type') or '').lower()
                    if typ in ('retraction', 'withdrawal'):
                        candidate['integrity']['retracted'] = True
                    if typ in ('correction', 'erratum', 'corrigendum'):
                        candidate['integrity']['correction_present'] = True
            except (FetchError, ValueError, TypeError, KeyError, AttributeError) as exc:
                manifest['errors'].append(dict(stage='integrity', source='crossref', canonical_id=candidate['canonical_id'],
                    error=exc.kind if isinstance(exc, FetchError) else 'malformed_response', http_status=getattr(exc, 'code', None)))
            if candidate['integrity']['retracted'] is True:
                candidate['knowledge_status'] = 'rejected'
            jsonl(output/'candidates.jsonl', candidates)
            print('Crossref checked:', i + 1, '/', len(candidates), flush=True)
    attempts, acquired, fulltext_errors = 0, [], []
    for candidate in candidates:
        rights_gate(candidate)
        eligible = [l for l in candidate['access']['locations'] if l['acquisition_allowed']]
        if dry_run or not eligible or attempts >= max_fulltext:
            continue
        attempts += 1
        loc = eligible[0]
        try:
            body, receipt = client.get('europepmc', loc['api_fulltext_path'], xml=True)
            validation = validate_fulltext(body, candidate, loc)
            directory = output/'fulltext'
            directory.mkdir(exist_ok=True)
            filename = sha(candidate['canonical_id']) + '.xml'
            save(directory/filename, body, binary=True)
            client.cache_approved_xml(body, receipt)
            entry = dict(canonical_id=candidate['canonical_id'], source='europepmc', source_identifier=candidate['pmcid'],
                retrieved_at_utc=receipt['retrieved_at_utc'], license=validation['license'], license_source=loc['location'],
                content_sha256=sha(body), file='fulltext/' + filename, request_id=receipt['request_id'], validation=validation)
            acquired.append(entry)
            candidate['knowledge_status'] = 'fulltext_acquired'
            candidate['access']['status'] = 'fulltext_acquired'
        except (FetchError, ValueError, ET.ParseError, UnicodeError) as exc:
            fulltext_errors.append(dict(canonical_id=candidate['canonical_id'], error=exc.kind if isinstance(exc, FetchError) else 'content_identity_or_license_not_confirmed'))
        jsonl(output/'candidates.jsonl', candidates)
        jsonl(output/'fulltext_manifest.jsonl', acquired)
    assert all(c['knowledge_status'] in AUTOMATED_STATUSES for c in candidates)
    jsonl(output/'candidates.jsonl', candidates)
    jsonl(output/'fulltext_manifest.jsonl', acquired)
    rights = dict(policy='CC-BY or CC0 only, per location; Europe PMC API only; XML permissions and identity rechecked',
        candidate_license_counts=dict(Counter(c['access']['license'] for c in candidates)),
        location_license_counts=dict(Counter(l['normalized_license'] for c in candidates for l in c['access']['locations'])),
        metadata_only=sum(not c['access']['fulltext_acquisition_allowed'] for c in candidates),
        eligible_api_fulltext=sum(c['access']['fulltext_acquisition_allowed'] for c in candidates),
        decisions=[dict(canonical_id=c['canonical_id'], access=c['access']) for c in candidates])
    integrity = dict(no_single_quality_score=True, records=[dict(canonical_id=c['canonical_id'], **c['integrity']) for c in candidates],
        retracted_true=sum(c['integrity']['retracted'] is True for c in candidates),
        retracted_unknown=sum(c['integrity']['retracted'] is None for c in candidates),
        correction_true=sum(c['integrity']['correction_present'] is True for c in candidates),
        correction_unknown=sum(c['integrity']['correction_present'] is None for c in candidates))
    acquisition = dict(fulltext_attempts=attempts, fulltext_acquired=len(acquired), errors=fulltext_errors,
        not_acquired=len(candidates) - len(acquired), source_metadata_records=dict(source_counts),
        unique_per_query={q: sum(q in c['acquisition']['queries'] for c in candidates) for q in QUERIES})
    save(output/'rights_report.json', rights)
    save(output/'integrity_report.json', integrity)
    save(output/'acquisition_report.json', acquisition)
    manifest.update(status='dry_run' if dry_run else ('completed_with_source_errors' if manifest['errors'] else 'success'),
        candidate_count=len(candidates), source_counts=dict(source_counts), dedup_decisions=decisions,
        duplicate_records_removed=dedup['duplicate_records_removed'], license_decisions=rights['candidate_license_counts'],
        fulltext_acquired=len(acquired), fulltext_attempts=attempts,
        network_attempts=client.network_attempts if client else 0, requests=client.requests if client else [],
        fulltext_content_hashes={e['canonical_id']: e['content_sha256'] for e in acquired})
    report = ['# Phase 5L Scholarly Knowledge Acquisition MVP', '',
        'Knowledge取得基盤。LLM training・自動科学的検証・quality rankingではない。',
        'machine_extracted != human_verified。今回machine extraction / human verificationは未実装。',
        'OAは無制限再利用を意味しない。locationごとの権利を検査し、publisher scrapingは行わない。', '',
        'dry-run（架空fixturesのみ）' if dry_run else '公式API controlled live queries', '',
        '```json', json.dumps(dict(candidates=len(candidates), dedup=dedup['duplicate_records_removed'],
            acquisition=acquisition, licenses=rights['candidate_license_counts'], metadata_only=rights['metadata_only'],
            integrity={k: v for k, v in integrity.items() if k != 'records'}, errors=manifest['errors']), ensure_ascii=False, indent=2),
        '```', '', '不明な研究設計・集団・sample size等はnull。撤回・訂正の未検出は安全性や科学的妥当性の保証ではない。',
        'full text取得は最大5 attempts。未取得・権利不明の本文を補完せず、既存verified corpusへ自動登録しない。']
    save(output/'report_ja.md', ('\n'.join(report) + '\n').encode(), binary=True)
    manifest['artifact_sha256'] = {p.name: sha(p.read_bytes()) for p in sorted(output.iterdir()) if p.is_file() and p.name != 'manifest.json'}
    save(output/'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--max-fulltext', type=int, default=5)
    args = parser.parse_args()
    manifest = run(args.output, args.dry_run, cache=args.cache, max_fulltext=args.max_fulltext)
    print(json.dumps({k: manifest[k] for k in ('status', 'candidate_count', 'network_attempts', 'fulltext_acquired')}, ensure_ascii=False))


if __name__ == '__main__':
    main()

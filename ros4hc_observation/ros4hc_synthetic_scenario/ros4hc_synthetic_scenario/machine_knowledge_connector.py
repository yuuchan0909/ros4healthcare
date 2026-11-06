"""Read-only, deterministic adapter for one unverified Phase 5M paper."""
import copy
import json
import re
from pathlib import Path

from . import machine_knowledge as mk

DEFAULT_SOURCE = Path.home() / 'ros4hc_experiments/phase5m/machine_knowledge_extraction_mvp_20261106'
TYPE_MAP = {'experimental_methods': {'experimental_method', 'study_design'},
            'measurement_methods': {'measurement_method'}, 'analysis_methods': {'analysis_method'}}
STOP = set('a an the of for and or to in on with research experiment experimental method methods sensor sensors study control robot robots'.split())
WARNING = '以下は機械抽出Knowledgeに基づき、人手による学術的検証は未実施'


def tokens(text):
    text = text.casefold()
    # Explicit bilingual lexical aliases, not model-generated domain expansion.
    for source, target in [('四足', ' quadruped '), ('中央パターン生成器', ' cpg '),
                           ('central pattern generators', ' cpg '), ('multi-agent', ' multiagent ')]:
        text = text.replace(source, target)
    return set(re.findall(r'[a-z0-9]+', text)) - STOP


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class MachineKnowledgeAdapter:
    def __init__(self, source=DEFAULT_SOURCE):
        self.source = Path(source)
        self.document = json.loads((self.source / 'source_document.json').read_text())
        d = self.document
        if d['doi'] != mk.DOI or d['pmcid'] != mk.PMCID or d.get('integrity_verified') is not True:
            raise ValueError('Source identity mismatch')
        if mk.digest(Path(d['source_path']).read_bytes()) != d['source_sha256']:
            raise ValueError('Source hash mismatch')
        self.paragraphs = read_jsonl(self.source / 'source_segments.jsonl')
        self.lookup = {p['paragraph_id']: p for p in self.paragraphs}
        if len(self.lookup) != len(self.paragraphs):
            raise ValueError('Duplicate paragraph ID')
        for p in self.paragraphs:
            if mk.digest(p['source_text'].encode()) != p['source_text_sha256']:
                raise ValueError('Paragraph hash mismatch')
        # Reconstruct segmentation from the original, hash-checked local XML.
        # Segment hashes alone would not detect an edited text/hash pair.
        import xml.etree.ElementTree as ET
        raw = Path(d['source_path']).read_bytes()
        if b'<!ENTITY' in raw:
            raise ValueError('Entity declaration forbidden')
        _, original, _ = mk.segment(ET.fromstring(raw))
        if original != self.paragraphs:
            raise ValueError('Segments differ from source XML')
        self.records = read_jsonl(self.source / 'machine_knowledge.jsonl')
        ids = set()
        for r in self.records:
            if set(r) != set(mk.LISTS) | {'knowledge_type', 'population', 'provenance', 'knowledge_id', 'verification_status', 'extraction'}:
                raise ValueError('Invalid knowledge fields')
            mk.validate_record({k: v for k, v in r.items() if k not in ('knowledge_id', 'verification_status', 'extraction')}, self.paragraphs)
            if not isinstance(r['knowledge_id'], str) or not r['knowledge_id'] or r['knowledge_id'] in ids:
                raise ValueError('Invalid/duplicate knowledge ID')
            ids.add(r['knowledge_id'])
            if (r['extraction']['status'] != 'machine_extracted' or r['extraction']['human_verified'] is not False
                    or r['verification_status'] != 'pending'):
                raise ValueError('Unverified status boundary violated')
        self.by_id = {r['knowledge_id']: r for r in self.records}
        self.artifact_hashes = {n: mk.digest((self.source / n).read_bytes()) for n in
                               ('source_document.json', 'source_segments.jsonl', 'machine_knowledge.jsonl')}

    def audit(self):
        if any(mk.digest((self.source / n).read_bytes()) != h for n, h in self.artifact_hashes.items()):
            raise ValueError('Source artifact changed')
        if mk.digest(Path(self.document['source_path']).read_bytes()) != self.document['source_sha256']:
            raise ValueError('Source hash mismatch')
        return dict(unchanged=True, hashes=copy.deepcopy(self.artifact_hashes), records=len(self.records),
                    source_sha256=self.document['source_sha256'])

    def match(self, request, user_request):
        text = user_request + ' ' + ' '.join(request['target_concepts'])
        dois = re.findall(r'10\.\d{4,9}/[-._;()/A-Za-z0-9]+', text, re.I)
        pmcs = re.findall(r'\bPMC\d+\b', text, re.I)
        if dois or pmcs:
            matched = (all(x.rstrip('.,;').casefold() == mk.DOI for x in dois)
                       and all(x.upper() == mk.PMCID for x in pmcs))
            return dict(matched=matched, mode='exact_identifier', overlap=[], reason='Exact identifiers required')
        # Mixed/unrelated medical requests fail closed even if incidental CPG terms occur.
        if re.search(r'parkinson|cardiovascular|\bfalls?\b|パーキンソン|転倒|心血管', text, re.I):
            return dict(matched=False, mode='domain_guard', overlap=[], reason='Unsupported or mixed medical domain')
        query = tokens(text)
        paper = tokens(self.document['title'] + ' ' + ' '.join(t for r in self.records for t in r['target_concepts']))
        overlap = sorted(query & paper)
        # One broad term never suffices. Require two explicit domain anchors.
        anchors = query & {'quadruped', 'cpg', 'multiagent', 'locomotion', 'hopf'}
        matched = len(anchors) >= 2 and len(overlap) >= 2
        return dict(matched=matched, mode='conservative_lexical', overlap=overlap,
                    reason='Two specific paper-domain anchors required')

    def fetch(self, information, request, user_request, connector='ExperimentalMethodConnector', top_k=8):
        if type(top_k) is not int or not 1 <= top_k <= 8:
            raise ValueError('top_k must be 1..8')
        self.audit()
        match = self.match(request, user_request)
        types = set().union(*(TYPE_MAP.get(i, set()) for i in information))
        if connector == 'ScholarlyEvidenceConnector':
            types = set(mk.TYPES) - {'other', 'review_statement'}
        result = dict(connector=connector, source_category='machine_extracted_knowledge',
                      knowledge_status='machine_extracted', human_verified=False, verification_status='pending',
                      target_match=match, target_mismatch_detected=not match['matched'],
                      requested_information=list(information), retrieved_knowledge_count=0,
                      missing_information=list(information), status='unavailable', data=None)
        if not match['matched']:
            return dict(result, reason='Current machine KB target mismatch; no domain substitution')
        query = tokens(user_request + ' ' + ' '.join(request['target_concepts']))
        candidates = [r for r in self.records if r['knowledge_type'] in types]
        def rank(r):
            concept = len(query & tokens(' '.join(r['target_concepts'])))
            exact = len(query & tokens(json.dumps({k: r[k] for k in mk.LISTS}, ensure_ascii=False)))
            return (-concept, -exact, r['knowledge_id'])
        ranked = sorted(candidates, key=rank)
        # Reserve one best candidate per requested information group so lexical
        # popularity cannot crowd analysis/measurement methods out of the context.
        selected = []
        for info in sorted(information):
            best = next((r for r in ranked if r['knowledge_type'] in TYPE_MAP.get(info, set())), None)
            if best is not None and best not in selected and len(selected) < top_k:
                selected.append(best)
        for r in ranked:
            if len(selected) >= top_k:
                break
            if r not in selected:
                selected.append(r)
        selected.sort(key=rank)
        records = [dict(copy.deepcopy(r), knowledge_status='machine_extracted', human_verified=False,
                        doi=self.document['doi'], pmcid=self.document['pmcid'],
                        source_sha256=self.document['source_sha256']) for r in selected]
        covered = {i for i in information if any(r['knowledge_type'] in TYPE_MAP.get(i, set()) for r in selected)}
        if connector == 'ScholarlyEvidenceConnector' and selected:
            covered = set(information)
        return dict(result, status='available' if records else 'unavailable', reason='Unverified local machine knowledge only',
                    retrieved_knowledge_count=len(records), missing_information=sorted(set(information)-covered),
                    data=dict(knowledge=records, warning=WARNING, requested_types=sorted(types)))

"""Allowlisted deterministic knowledge routing; missing knowledge stays missing."""
import copy
from pathlib import Path
import re

from . import evidence_grounded_interpretation as base
from .condition_aware_retrieval import ConditionAwareRetrievalBaseline
from .literature_retrieval import LexicalRetrievalBaseline
from .task_router import validate_task

INFORMATION_CONNECTORS = {
    'current_observation': 'SensorHistoryConnector', 'sensor_history': 'SensorHistoryConnector',
    'scholarly_evidence': 'ScholarlyEvidenceConnector',
    'experimental_methods': 'ExperimentalMethodConnector',
    'measurement_methods': 'ExperimentalMethodConnector', 'analysis_methods': 'ExperimentalMethodConnector',
    'sensor_capabilities': 'SensorCapabilityConnector', 'action_policy': 'ActionPolicyConnector'}
CONNECTOR_NAMES = ('SensorHistoryConnector', 'ScholarlyEvidenceConnector', 'ExperimentalMethodConnector',
                   'SensorCapabilityConnector', 'ActionPolicyConnector')


def route(task):
    task = validate_task(task)
    required = set(task['required_information'])
    return [dict(connector=name, information=[k for k, v in INFORMATION_CONNECTORS.items()
        if v == name and k in required]) for name in CONNECTOR_NAMES
        if any(INFORMATION_CONNECTORS[k] == name for k in required)]


def generic_request(task, sensor_facts=None):
    task = validate_task(task)
    query = base.generate_query(sensor_facts) if sensor_facts is not None else None
    return dict(target_concepts=copy.deepcopy(task['target_concepts']), task_type=task['task_type'],
        measurements=copy.deepcopy(query['modalities']) if query else [], population=None, outcomes=[],
        knowledge_types=[k for k in task['required_information'] if k not in ('current_observation', 'sensor_history')])


def unavailable(name, information, reason, **extra):
    return dict(connector=name, status='unavailable', reason=reason,
                missing_information=list(information), data=None, **extra)


class SensorHistoryConnector:
    def fetch(self, information, request, sensor_facts, user_request):
        if sensor_facts is None:
            return unavailable(type(self).__name__, information, 'No Sensor Facts/history supplied')
        query = base.generate_query(sensor_facts)  # Recursive allowlist; no metadata envelopes.
        if not query['modalities']:
            return unavailable(type(self).__name__, information, 'No measured observations supplied')
        windows = sensor_facts.get('windows', [])
        if 'sensor_history' in information and (not windows or len(windows) < 2):
            return unavailable(type(self).__name__, information, 'Temporal history not supplied')
        return dict(connector=type(self).__name__, status='available', reason='Provided observations only',
                    missing_information=[], data=copy.deepcopy(sensor_facts))


class FallCorpusAdapter:
    """Narrow opt-in bridge, not a general disease/domain classifier.

    Entire user text must fit an authored fall-only query grammar AND every target
    must be an explicit fall alias. Unknown/mixed domains fail closed. This limited
    grammar is deliberately separate from the generic router and registry.
    """
    targets = {'falls', 'fall', 'fall risk', 'fall risk assessment', 'fall research', '転倒', '転倒リスク', '転倒研究'}

    @classmethod
    def accepts(cls, request, user_request):
        targets = request['target_concepts']
        valid_targets = bool(targets) and all(t.casefold().strip() in cls.targets for t in targets)
        text = re.sub(r'\s+', ' ', user_request).strip().rstrip('。？！?! .').casefold()
        japanese = r'転倒(?:リスク)?(?:に関連する|に関する|の)?(?:研究|文献|論文|エビデンス)(?:を)?(?:調べたい|調べて|探したい|検索したい|検索して|教えて)(?:です|ください)?'
        english = r'(?:find|search|look up) (?:research|papers|literature|evidence) (?:on|about|related to) (?:falls|fall risk)'
        return valid_targets and bool(re.fullmatch(japanese, text) or re.fullmatch(english, text))

    @staticmethod
    def query(sensor_facts):
        if sensor_facts is not None:
            query = base.generate_query(sensor_facts)
        else:
            query = dict(modalities=[], variables=[], temporal_features=[], keywords=[],
                         population=None, clinical_condition=None, observed_values={})
        query['keywords'] = sorted(set(query['keywords']) | {'fall'})
        return query


class ScholarlyEvidenceConnector:
    def __init__(self, corpus_dir=None):
        self.corpus_dir = Path(corpus_dir) if corpus_dir else Path(__file__).resolve().parents[1]/'literature/real'

    def fetch(self, information, request, sensor_facts, user_request):
        name = type(self).__name__
        if not FallCorpusAdapter.accepts(request, user_request):
            return unavailable(name, information,
                'Current KB is fall-related only; target mismatch or unsupported user-request grammar. No domain substitution.',
                knowledge_request=copy.deepcopy(request), target_mismatch_detected=True, kb_target=['falls'])
        # Domain guard runs BEFORE any corpus lookup. No disease fallback.
        try:
            papers, chunks, _ = base.load_registry(self.corpus_dir/'papers.jsonl', self.corpus_dir/'evidence_chunks.jsonl')
            corpus = base.convert_registry(papers, chunks)
        except (OSError, ValueError, TypeError, KeyError):
            return unavailable(name, information, 'Local curated corpus missing or invalid')
        if not corpus:
            return unavailable(name, information, 'No verified knowledge records')
        allowed_documents = {
            'paper_0001': '10.1186/s12877-026-07329-6',
            'paper_0002': '10.3389/fdgth.2022.921506',
            'paper_0003': '10.2196/77591',
        }
        if any(r['paper_id'] not in allowed_documents or
               r['doi'] != allowed_documents[r['paper_id']] or
               not re.search(r'\bfalls?\b', r['title'], re.I) for r in corpus):
            return unavailable(name, information, 'Configured corpus target/provenance mismatch',
                knowledge_request=copy.deepcopy(request), target_mismatch_detected=True)
        query = FallCorpusAdapter.query(sensor_facts)
        if query['modalities']:
            evidence = ConditionAwareRetrievalBaseline(corpus).retrieve(query, top_k=6)
            mode = 'condition_aware_final_evidence'
        else:
            # A literature lookup has no observed modalities: never fabricate them.
            evidence = LexicalRetrievalBaseline(corpus).retrieve(query, top_k=6)
            mode = 'topic_lookup_only'
            for record in evidence:
                record['applicability'] = dict(overall_status='unknown', population_status='unknown',
                    reason='No measurement context; topic match does not establish applicability')
        if not evidence:
            return unavailable(name, information, 'No final Evidence matching supplied measurements',
                knowledge_request=copy.deepcopy(request), target_mismatch_detected=False)
        for record in evidence:
            record['adapter_target'] = ['falls']
        supported = {m for e in evidence for m in e.get('applicability', {}).get('measurement', {}).get('supported_query_modalities', [])}
        gaps = sorted(set(query['modalities']) - supported) if query['modalities'] else []
        return dict(connector=name, status='available', reason='Verified local corpus only; no clinical validity claim',
            missing_information=[], knowledge_request=copy.deepcopy(request), target_mismatch_detected=False,
            data=dict(evidence=evidence, retrieval_mode=mode, measurement_coverage_gaps=gaps,
                      applicability_unassessed=(mode == 'topic_lookup_only'), corpus_papers=len(papers), corpus_chunks=len(chunks)))


class ExperimentalMethodConnector:
    def fetch(self, information, request, sensor_facts, user_request):
        return unavailable(type(self).__name__, information, 'not_implemented: no curated method knowledge source')


class SensorCapabilityConnector:
    def fetch(self, information, request, sensor_facts, user_request):
        return unavailable(type(self).__name__, information, 'not_implemented: no curated sensor capability source')


class ActionPolicyConnector:
    def fetch(self, information, request, sensor_facts, user_request):
        return unavailable(type(self).__name__, information, 'not_implemented: no approved action policy source')


def build_context(user_request, task, sensor_facts=None, corpus_dir=None):
    task = validate_task(task)
    # Validate even when not routed to a sensor connector, before any data can cross boundaries.
    request = generic_request(task, sensor_facts)
    connectors = {'SensorHistoryConnector': SensorHistoryConnector(),
        'ScholarlyEvidenceConnector': ScholarlyEvidenceConnector(corpus_dir),
        'ExperimentalMethodConnector': ExperimentalMethodConnector(),
        'SensorCapabilityConnector': SensorCapabilityConnector(), 'ActionPolicyConnector': ActionPolicyConnector()}
    results, gaps = [], []
    for entry in route(task):
        result = connectors[entry['connector']].fetch(entry['information'], request, sensor_facts, user_request)
        results.append(result)
        if result['status'] == 'unavailable':
            gaps.extend(result['missing_information'])
        elif result['connector'] == 'ScholarlyEvidenceConnector':
            gaps.extend('measurement:' + m for m in result['data']['measurement_coverage_gaps'])
            if result['data']['applicability_unassessed']:
                gaps.append('evidence_applicability_unassessed')
    if task['task_type'] == 'unknown':
        gaps.append('task_clarification')
    return dict(user_request=user_request, task_spec=copy.deepcopy(task), connector_results=results,
        coverage_gaps=sorted(set(gaps)), unavailable_sources=[r['connector'] for r in results if r['status'] == 'unavailable'])

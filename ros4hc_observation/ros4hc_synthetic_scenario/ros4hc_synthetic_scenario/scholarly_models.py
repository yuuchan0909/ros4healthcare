"""Domain-neutral acquisition candidates; never scientific verification."""
from dataclasses import asdict, dataclass, field
import re
from urllib.parse import unquote, urlsplit

KNOWLEDGE_STATUSES = ('discovered', 'metadata_enriched', 'fulltext_acquired', 'machine_extracted', 'human_verified', 'rejected')
AUTOMATED_STATUSES = {'discovered', 'metadata_enriched', 'fulltext_acquired', 'rejected'}
KNOWLEDGE_TYPES = ('empirical_finding', 'experimental_method', 'measurement_method', 'analysis_method',
                   'guideline', 'review', 'device_validation', 'other')


def normalize_doi(value):
    if not isinstance(value, str):
        return None
    value = unquote(value.strip()).lower()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)', '', value).strip()
    return value if re.fullmatch(r'10\.\d{4,9}/[^\s]+', value) and not any(c in value for c in '?#[<>') else None


def normalize_id(kind, value):
    if kind == 'doi':
        return normalize_doi(value)
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = str(value).strip()
    if kind == 'pmid':
        value = re.sub(r'^https?://(?:www\.)?pubmed\.ncbi\.nlm\.nih\.gov/', '', value).strip('/')
        return value if re.fullmatch(r'[1-9]\d*', value) else None
    if kind == 'pmcid':
        value = value.upper()
        return value if re.fullmatch(r'PMC[1-9]\d*', value) else None
    if kind == 'openalex_id':
        value = re.sub(r'^https?://openalex\.org/', '', value, flags=re.I).upper()
        return value if re.fullmatch(r'W[1-9]\d*', value) else None
    raise ValueError('Unknown identifier kind')


def canonical_identity(ids):
    for kind in ('doi', 'pmid', 'pmcid', 'openalex_id'):
        value = normalize_id(kind, ids.get(kind))
        if value:
            return kind + ':' + value
    raise ValueError('No supported identity')


def normalize_license(value):
    if not isinstance(value, str) or not value.strip():
        return 'unknown'
    text = value.strip().lower().replace('_', '-').replace(' ', '-')
    if text.startswith(('http://', 'https://')):
        url = urlsplit(text)
        if url.hostname not in ('creativecommons.org', 'www.creativecommons.org') or url.query:
            return 'other'
        path = url.path.rstrip('/')
        if re.fullmatch(r'/licenses/by/(?:1\.0|2\.0|2\.5|3\.0|4\.0)(?:/legalcode)?', path):
            return 'CC-BY'
        if path in ('/publicdomain/zero/1.0', '/publicdomain/mark/1.0'):
            return 'CC0'
        for code in ('by-nc-nd', 'by-nc-sa', 'by-nc', 'by-nd', 'by-sa'):
            if path.startswith('/licenses/' + code + '/'):
                return 'CC-' + code.upper()
        return 'other'
    if re.fullmatch(r'cc-?by(?:-?(?:1\.0|2\.0|2\.5|3\.0|4\.0))?', text):
        return 'CC-BY'
    if text in ('cc0', 'cc-0', 'cc0-1.0', 'public-domain'):
        return 'CC0'
    if text.startswith('cc-by-'):
        return next(('CC-' + c.upper() for c in ('by-nc-nd', 'by-nc-sa', 'by-nc', 'by-nd', 'by-sa')
                     if text == 'cc-' + c or re.fullmatch('cc-' + c + r'-(?:1\.0|2\.0|2\.5|3\.0|4\.0)', text)), 'other')
    return 'other'


@dataclass
class KnowledgeCandidate:
    canonical_id: str
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    title: str = ''
    authors: list = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    source_type: str | None = None
    publication_types: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    access: dict = field(default_factory=lambda: dict(status='metadata_only', license=None, license_source=None,
        fulltext_available=False, fulltext_acquisition_allowed=False, locations=[]))
    integrity: dict = field(default_factory=lambda: dict(retracted=None, correction_present=None, status_sources=[]))
    acquisition: dict = field(default_factory=lambda: dict(discovery_sources=[], queries=[], retrieved_at_utc=None))
    knowledge_status: str = 'discovered'
    quality_signals: dict = field(default_factory=lambda: dict(study_design=None, sample_size=None, population=None,
        prospective_or_retrospective=None, external_validation=None, peer_review_status='unknown', limitations=[]))
    provenance: list = field(default_factory=list)
    identifier_conflicts: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class KnowledgeExtractionCandidate:
    """Interface only. An extraction is not a human-verified literature record."""
    candidate_id: str
    knowledge_type: str = 'other'
    target_concepts: list = field(default_factory=list)
    population: object = None
    measurements: list = field(default_factory=list)
    sensors: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    conditions: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    provenance: list = field(default_factory=list)

    def __post_init__(self):
        if self.knowledge_type not in KNOWLEDGE_TYPES:
            raise ValueError('Unknown knowledge type')


def extract_knowledge(candidate):
    raise NotImplementedError('Machine extraction and human verification are outside Phase 5L')

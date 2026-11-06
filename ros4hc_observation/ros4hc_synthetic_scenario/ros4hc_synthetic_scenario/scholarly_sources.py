"""Official API-only transport and metadata adapters. No publisher URL retrieval."""
import copy
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import math
from email.utils import parsedate_to_datetime
import time
from datetime import datetime, timezone
from urllib import error, request
from urllib.parse import quote, urlencode

from .scholarly_models import normalize_id

BASES = {'openalex': 'https://api.openalex.org', 'europepmc': 'https://www.ebi.ac.uk/europepmc/webservices/rest',
         'crossref': 'https://api.crossref.org'}
DROP_FIELDS = {'abstract', 'abstractText', 'abstract_inverted_index', 'reference', 'references'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def save(path, value, binary=False):
    content = value if binary else (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    for name, secret in os.environ.items():
        if secret and (name.endswith(('API_KEY', 'TOKEN', 'PASSWORD', 'SECRET'))) and secret.encode() in content:
            raise ValueError('Credential rejected from artifact')
    Path(path).write_bytes(content)


def projection(value):
    if isinstance(value, dict):
        return {k: projection(v) for k, v in value.items() if k not in DROP_FIELDS}
    if isinstance(value, list):
        return [projection(v) for v in value]
    return value


class FetchError(Exception):
    def __init__(self, kind, code=None):
        self.kind, self.code = kind, code
        super().__init__(kind)  # Never include server body, URL credentials or transport message.


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OfficialAPIClient:
    def __init__(self, cache, opener=None, sleep=time.sleep, clock=time.monotonic, interval=1.1):
        self.cache = Path(cache)
        existing = next(p for p in (self.cache, *self.cache.parents) if p.is_dir())
        if subprocess.run(['git', '-C', str(existing), 'rev-parse', '--show-toplevel'], capture_output=True).returncode == 0:
            raise ValueError('Cache, including approved raw full text, must be outside Git')
        self.cache.mkdir(parents=True, exist_ok=True)
        self.opener = opener or request.build_opener(NoRedirect())
        self.sleep, self.clock, self.interval = sleep, clock, interval
        self.last = None
        self.requests = []
        self.network_attempts = 0

    @staticmethod
    def validate_endpoint(source, path, params, xml):
        if source not in BASES or not isinstance(params, dict):
            raise ValueError('Official sources only')
        if any(k.lower() in ('api_key', 'apikey', 'token', 'access_token', 'authorization') for k in params):
            raise ValueError('Credentials forbidden in request parameters')
        allowed = ((source == 'openalex' and path == '/works' and not xml) or
                   (source == 'crossref' and (path == '/works' or re.fullmatch(r'/works/10\.\d{4,9}%2F[^?#]+', path, re.I)) and not xml) or
                   (source == 'europepmc' and ((path == '/search' and not xml) or
                    (re.fullmatch(r'/PMC[1-9]\d*/fullTextXML', path) and xml and not params))))
        if not allowed:
            raise ValueError('Endpoint is outside allowlist')

    def get(self, source, path, params=None, xml=False):
        params = params or {}
        self.validate_endpoint(source, path, params, xml)
        identity = dict(source=source, path=path, parameters=params, representation='xml' if xml else 'json')
        key = sha(identity)
        cached = self.cache/(key + '.json')
        if cached.exists():
            payload = json.loads(cached.read_text())
            if sha(payload['data']) != payload['receipt']['cached_projection_sha256']:
                raise FetchError('cache_integrity_failure')
            if xml:
                data = base64.b64decode(payload['data'], validate=True)
                if sha(data) != payload['receipt']['response_sha256']:
                    raise FetchError('cache_content_hash_failure')
            else:
                data = payload['data']
            receipt = dict(payload['receipt'], cache_hit=True)
            self.requests.append(receipt)
            return data, receipt
        url = BASES[source] + path + ('?' + urlencode(sorted(params.items())) if params else '')
        headers = {'User-Agent': 'ROS4HC-ScholarlyAcquisition/0.1 (small research MVP)',
                   'Accept': 'application/xml' if xml else 'application/json'}
        if source == 'openalex' and os.environ.get('OPENALEX_API_KEY'):
            headers['Authorization'] = 'Bearer ' + os.environ['OPENALEX_API_KEY']
        receipt = dict(identity, request_id=key, endpoint=BASES[source] + path, retrieved_at_utc=utc_now(), cache_hit=False)
        for attempt in range(2):
            if self.last is not None:
                self.sleep(max(0, self.interval - (self.clock() - self.last)))
            self.last = self.clock()
            self.network_attempts += 1
            try:
                with self.opener.open(request.Request(url, headers=headers), timeout=20) as response:
                    limit = 20_000_000 if xml else 6_000_000
                    body = response.read(limit + 1)
                    if len(body) > limit:
                        raise FetchError('response_too_large')
                receipt.update(attempts=attempt + 1, response_sha256=sha(body), status='success')
                if xml:
                    # Caller must validate identity and location-specific permissions BEFORE saving/cache.
                    self.requests.append(receipt)
                    return body, receipt
                try:
                    data = projection(json.loads(body))
                except (ValueError, UnicodeError):
                    raise FetchError('malformed_response') from None
                receipt['cached_projection_sha256'] = sha(data)
                save(cached, dict(data=data, receipt=receipt))
                self.requests.append(receipt)
                return data, receipt
            except error.HTTPError as exc:
                code = exc.code
                retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
                exc.close()
                delay = int(retry_after) if retry_after.isdigit() else 0
                if retry_after and not retry_after.isdigit():
                    try:
                        delay = max(0, math.ceil((parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()))
                    except (ValueError, TypeError, OverflowError):
                        delay = 0
                wait = max(2 ** (attempt + 1), delay)
                if code in (408, 429, 500, 502, 503, 504) and attempt == 0 and wait <= 30:
                    self.sleep(wait)
                    continue
                receipt.update(attempts=attempt + 1, status='failed', error='http_error', http_status=code)
                self.requests.append(receipt)
                raise FetchError('http_error', code) from None
            except (TimeoutError, ConnectionError, error.URLError, OSError):
                if attempt == 0:
                    self.sleep(2)
                    continue
                receipt.update(attempts=attempt + 1, status='failed', error='transport_error')
                self.requests.append(receipt)
                raise FetchError('transport_error') from None
            except FetchError as exc:
                receipt.update(attempts=attempt + 1, status='failed', error=exc.kind)
                self.requests.append(receipt)
                raise

    def cache_approved_xml(self, body, receipt):
        if receipt['source'] != 'europepmc':
            raise ValueError('Only Europe PMC fullTextXML is supported')
        data = base64.b64encode(body).decode('ascii')
        receipt = dict(receipt, cached_projection_sha256=sha(data))
        save(self.cache/(receipt['request_id'] + '.json'), dict(data=data, receipt=receipt))


def object_list(value):
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise ValueError('Malformed API list')
    return value


def identifiers(**kwargs):
    return {k: normalize_id(k, kwargs.get(k)) for k in ('doi', 'pmid', 'pmcid', 'openalex_id')}


def record(source, ids, title, authors, year, venue, receipt, **extra):
    if not any(ids.values()) or not isinstance(title, str) or not title.strip():
        raise ValueError('Metadata identity/title missing')
    if type(year) is not int:
        try:
            year = int(year) if year else None
        except (ValueError, TypeError):
            year = None
    return dict(source=source, identifiers=ids, title=title.strip(), authors=authors, year=year, venue=venue,
                source_type=None, publication_types=[], topics=[], locations=[], integrity_signals=[],
                receipt=copy.deepcopy(receipt), **extra)


def openalex_record(item, receipt):
    ids = item.get('ids') or {}
    row = record('openalex', identifiers(doi=item.get('doi') or ids.get('doi'), pmid=ids.get('pmid'),
        pmcid=(ids.get('pmcid') or '').rstrip('/').split('/')[-1], openalex_id=item.get('id')),
        item.get('title'), [a['author']['display_name'] for a in item.get('authorships') or []],
        item.get('publication_year'), ((item.get('primary_location') or {}).get('source') or {}).get('display_name'), receipt)
    row.update(source_type=item.get('type'), publication_types=[item['type']] if item.get('type') else [],
        topics=[t['display_name'] for t in item.get('topics') or []])
    for loc in item.get('locations') or []:
        row['locations'].append(dict(source='openalex', source_identifier=row['identifiers']['openalex_id'],
            location=loc.get('landing_page_url') or loc.get('pdf_url'), license_raw=loc.get('license'),
            is_oa=loc.get('is_oa') is True, api_fulltext_path=None, version=loc.get('version')))
    if type(item.get('is_retracted')) is bool:
        row['integrity_signals'].append(dict(source='openalex', field='is_retracted', retracted=item['is_retracted']))
    return row


def europepmc_record(item, receipt):
    row = record('europepmc', identifiers(doi=item.get('doi'), pmid=item.get('pmid') or
        (item.get('id') if item.get('source') == 'MED' else None), pmcid=item.get('pmcid')),
        item.get('title'), [a.get('fullName', '') for a in (item.get('authorList') or {}).get('author', [])],
        item.get('pubYear'), ((item.get('journalInfo') or {}).get('journal') or {}).get('title'), receipt)
    row['publication_types'] = (item.get('pubTypeList') or {}).get('pubType') or []
    row['source_type'] = 'publication'
    pmcid = row['identifiers']['pmcid']
    row['locations'].append(dict(source='europepmc', source_identifier=pmcid or item.get('id'),
        location=('https://www.ebi.ac.uk/europepmc/webservices/rest/' + pmcid + '/fullTextXML') if pmcid else None,
        license_raw=item.get('license'), is_oa=item.get('isOpenAccess') == 'Y',
        api_fulltext_path='/' + pmcid + '/fullTextXML' if pmcid and item.get('isOpenAccess') == 'Y' else None,
        version='Europe PMC deposited full text'))
    if any(t.lower() == 'retracted publication' for t in row['publication_types']):
        row['integrity_signals'].append(dict(source='europepmc', field='pubTypeList', retracted=True))
    return row


def crossref_record(item, receipt):
    ids = identifiers(doi=item.get('DOI'))
    parts = ((item.get('published') or item.get('issued') or {}).get('date-parts') or [[]])[0]
    row = record('crossref', ids, (item.get('title') or [''])[0],
        [' '.join(filter(None, (a.get('given'), a.get('family')))) or a.get('name', '') for a in item.get('author') or []],
        parts[0] if parts else None, (item.get('container-title') or [None])[0], receipt)
    row.update(source_type=item.get('type'), publication_types=[item['type']] if item.get('type') else [])
    for loc in item.get('license') or []:
        row['locations'].append(dict(source='crossref', source_identifier=ids['doi'], location=None,
            license_raw=loc.get('URL'), is_oa=False, api_fulltext_path=None, version=loc.get('content-version'),
            start=loc.get('start')))
    for update in item.get('update-to') or []:
        row['integrity_signals'].append(dict(source='crossref', role='update_notice',
            target_doi=normalize_id('doi', update.get('DOI')), update_type=update.get('type')))
    relations = item.get('relation') or {}
    if relations.get('is-retracted-by'):
        row['integrity_signals'].append(dict(source='crossref', field='relation.is-retracted-by', retracted=True))
    if relations.get('is-corrected-by'):
        row['integrity_signals'].append(dict(source='crossref', field='relation.is-corrected-by', correction_present=True))
    return row


class OpenAlexAdapter:
    source = 'openalex'
    def discover(self, client, query):
        data, receipt = client.get(self.source, '/works', dict(search=query, per_page=10))
        return object_list(data['results']), receipt, openalex_record


class EuropePMCAdapter:
    source = 'europepmc'
    def discover(self, client, query):
        data, receipt = client.get(self.source, '/search', dict(query=query, resultType='core', format='json', pageSize=10))
        return object_list(data['resultList']['result']), receipt, europepmc_record


class CrossrefAdapter:
    source = 'crossref'
    def enrich(self, client, doi):
        data, receipt = client.get(self.source, '/works/' + quote(doi, safe=''))
        item = data['message']
        if normalize_id('doi', item.get('DOI')) != doi:
            raise ValueError('Crossref DOI mismatch')
        return crossref_record(item, receipt)

    def updates(self, client, doi):
        data, receipt = client.get(self.source, '/works', dict(filter='updates:' + doi, rows=5))
        signals = []
        for item in object_list(data['message']['items']):
            for update in item.get('update-to') or []:
                if normalize_id('doi', update.get('DOI')) == doi:
                    signals.append(dict(source='crossref', field='updates filter / update-to',
                        notice_doi=normalize_id('doi', item.get('DOI')), target_doi=doi,
                        update_type=update.get('type'), request_id=receipt['request_id']))
        return signals, receipt

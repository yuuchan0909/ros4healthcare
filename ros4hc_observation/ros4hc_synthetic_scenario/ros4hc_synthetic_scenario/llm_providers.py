"""小さなprovider境界。認証情報は実験コードに渡さない。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from http.client import IncompleteRead
import copy
import json
import math
import os
from urllib import error, request as http


@dataclass(frozen=True)
class LLMResponse:
    raw_response: str
    usage: dict = field(default_factory=dict)


class TransientProviderError(Exception):
    """限定的再試行が可能な通信障害。メッセージはログに保存しない。"""


class PermanentProviderError(Exception):
    """自動再試行しないprovider障害。メッセージはログに保存しない。"""


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, request, decoding):
        """requestはprompt/output_schemaのみ。sample IDや正解を含めない。"""

    @abstractmethod
    def model_identifier(self):
        pass

    @abstractmethod
    def provider_identifier(self):
        pass


class MockProvider(LLMProvider):
    """固定応答の配線確認専用。分類性能の研究結果には使わない。"""
    def __init__(self, model='mock-constant-v1'):
        self.model = model
        self.calls = 0

    def generate(self, request, decoding):
        self.calls += 1
        return LLMResponse('{"predicted_state":"indeterminate"}')

    def model_identifier(self):
        return self.model

    def provider_identifier(self):
        return 'mock'


class ExternalProvider(LLMProvider):
    """外部応答取り込み用の識別子。通信は行わない。"""
    def __init__(self, provider, model):
        self.provider, self.model = provider, model

    def generate(self, request, decoding):
        raise PermanentProviderError('外部応答がありません')

    def model_identifier(self):
        return self.model

    def provider_identifier(self):
        return self.provider


class _NoRedirect(http.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward Authorization to a redirect target.


class OpenAIProvider(LLMProvider):
    """Responses API adapter; no SDK retries, response envelopes or credentials persisted."""
    def __init__(self, model, timeout=60):
        if not isinstance(model, str) or not model.strip():
            raise ValueError('An exact model ID is required')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('A positive finite timeout is required')
        self.model = model
        self.timeout = timeout

    def model_identifier(self):
        return self.model

    def provider_identifier(self):
        return 'openai'

    @staticmethod
    def _payload(model, request, decoding):
        if (not isinstance(request, dict) or set(request) != {'prompt', 'output_schema'} or
                not isinstance(request['prompt'], str) or not isinstance(request['output_schema'], dict)):
            raise PermanentProviderError('Invalid provider request')
        schema = copy.deepcopy(request['output_schema'])
        # Phase 5B's oneOf adds display labels to the same enum. Responses strict
        # schemas do not support oneOf; remove it only when demonstrably redundant.
        state = schema.get('properties', {}).get('predicted_state', {})
        if 'oneOf' in state:
            choices = state['oneOf']
            if (not isinstance(choices, list) or
                    any(not isinstance(c, dict) or set(c) != {'const', 'title'} for c in choices) or
                    [c['const'] for c in choices] != state.get('enum')):
                raise PermanentProviderError('Unsupported output schema')
            del state['oneOf']
        if not isinstance(decoding, dict) or set(decoding) - {
                'temperature', 'reasoning_effort', 'max_output_tokens'}:
            raise PermanentProviderError('Unsupported decoding setting')
        payload = {'model': model, 'store': False, 'input': request['prompt'],
                   'text': {'format': {'type': 'json_schema', 'name': 'phase5c_classification',
                                       'strict': True, 'schema': schema}}}
        for key, value in decoding.items():
            if key == 'temperature':
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
                    raise PermanentProviderError('Invalid temperature')
            elif key == 'max_output_tokens':
                if type(value) is not int or value <= 0:
                    raise PermanentProviderError('Invalid output token limit')
            elif key == 'reasoning_effort':
                if value not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
                    raise PermanentProviderError('Invalid reasoning effort')
                payload['reasoning'] = {'effort': value}
                continue
            payload[key] = value
        if ('temperature' in decoding and 'reasoning_effort' in decoding and
                decoding['reasoning_effort'] != 'none'):
            raise PermanentProviderError('Temperature requires reasoning effort none')
        return payload

    def generate(self, request, decoding):
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key or not api_key.strip():
            raise PermanentProviderError('OPENAI_API_KEY is required')
        payload = self._payload(self.model, request, decoding)
        # The key exists only in this call's local transport state, never on self.
        try:
            wire_request = http.Request('https://api.openai.com/v1/responses',
                                        data=json.dumps(payload, allow_nan=False).encode('utf-8'),
                                        headers={'Authorization': 'Bearer ' + api_key,
                                                 'Content-Type': 'application/json'}, method='POST')
            with http.build_opener(_NoRedirect()).open(wire_request, timeout=self.timeout) as response:
                envelope = json.loads(response.read())
        except error.HTTPError as exc:
            transient = exc.code in (408, 409, 429) or 500 <= exc.code < 600
            exc.close()  # Do not read or report the API error body/headers.
            category = TransientProviderError if transient else PermanentProviderError
            raise category('OpenAI HTTP request failed') from None
        except (IncompleteRead, TimeoutError, ConnectionError, error.URLError, OSError):
            raise TransientProviderError('OpenAI transport failed') from None
        except Exception:
            raise PermanentProviderError('Invalid OpenAI response') from None
        try:
            if envelope.get('status') != 'completed':
                raise ValueError()
            texts = []
            for item in envelope['output']:
                if item.get('type') == 'message' and item.get('role') == 'assistant':
                    for content in item['content']:
                        if content.get('type') == 'refusal':
                            raise ValueError()
                        if content.get('type') == 'output_text':
                            texts.append(content['text'])
            if not texts or any(not isinstance(t, str) for t in texts):
                raise ValueError()
            raw = ''.join(texts)
            if api_key in raw:
                raise ValueError()  # Fail closed if a response unexpectedly echoes the credential.
            usage = envelope.get('usage') or {}
            usage = {k: v for k, v in usage.items()
                     if k in ('input_tokens', 'output_tokens', 'total_tokens') and type(v) is int and v >= 0}
            return LLMResponse(raw, usage)
        except Exception:
            raise PermanentProviderError('Invalid OpenAI response') from None

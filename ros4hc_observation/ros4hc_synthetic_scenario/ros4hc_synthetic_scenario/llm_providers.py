"""小さなprovider境界。認証情報は実験コードに渡さない。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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

"""Process-local reference contract fixture, not production storage.

Payloads must support faithful deepcopy (ordinary native message objects do).
The caller supplies the canonical message type; this fixture does not discover
ROS schemas. Copies preserve Python type, without serialization to byte blobs.
"""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from .model_v02 import ReferenceDescriptor, validate_reference_descriptor

SCHEME = 'ros4hc/reference/in-memory/v1'
NATIVE_PROFILE = 'ros4hc/profile/native-message/v1'


class ResolutionFailure(str, Enum):
    INVALID_REFERENCE = 'invalid_reference'
    UNSUPPORTED_SCHEME = 'unsupported_scheme'
    NOT_FOUND = 'not_found'
    TYPE_MISMATCH = 'type_mismatch'
    PROFILE_MISMATCH = 'profile_mismatch'


class ReferenceResolutionError(LookupError):
    """Resolution status, independent of sensor measurement quality."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True)
class _StoredSample:
    payload: object
    message_type: str
    profile: str


class InMemoryReferenceResolver:
    """Append-only snapshots; IDs resolve only in this live resolver instance.

    No persistence, expiry, cross-process lookup or thread-safety is promised.
    Explicit IDs support recorder-assigned identities; any reuse is rejected.
    """

    def __init__(self):
        self._samples = {}

    def register(self, payload, message_type, *, profile=NATIVE_PROFILE, reference_id=None):
        """Store an isolated native snapshot and return its descriptor.

        message_type is a caller-asserted canonical package/msg/Type identifier.
        All IDs are opaque to consumers. No field is stripped or normalized.
        """
        identifier = str(uuid4()) if reference_id is None else reference_id
        reference = ReferenceDescriptor(SCHEME, identifier, profile)
        errors = validate_reference_descriptor(reference)
        if not isinstance(message_type, str) or not message_type.strip():
            errors.append('message_type must be nonempty')
        if errors:
            raise ValueError('; '.join(errors))
        if identifier in self._samples:
            raise ValueError('reference ID is already registered; overwrite prohibited')
        snapshot = deepcopy(payload)
        if type(snapshot) is not type(payload):
            raise TypeError('deepcopy must preserve native Python type')
        self._samples[identifier] = _StoredSample(snapshot, message_type, profile)
        return reference

    def resolve(self, reference, expected_message_type):
        """Return an isolated native copy, checking source.message_type.

        Pass observation.metadata.source.message_type as expected_message_type.
        This method never reads or modifies ObservationQuality.
        """
        errors = validate_reference_descriptor(reference)
        if errors:
            raise ReferenceResolutionError(ResolutionFailure.INVALID_REFERENCE, '; '.join(errors))
        if reference.scheme != SCHEME:
            raise ReferenceResolutionError(ResolutionFailure.UNSUPPORTED_SCHEME, reference.scheme)
        if reference.id not in self._samples:
            raise ReferenceResolutionError(ResolutionFailure.NOT_FOUND, reference.id)
        stored = self._samples[reference.id]
        if expected_message_type != stored.message_type:
            raise ReferenceResolutionError(ResolutionFailure.TYPE_MISMATCH,
                                           'expected type differs from registered native type')
        if reference.profile != stored.profile:
            raise ReferenceResolutionError(ResolutionFailure.PROFILE_MISMATCH,
                                           'profile differs from registration')
        return deepcopy(stored.payload)

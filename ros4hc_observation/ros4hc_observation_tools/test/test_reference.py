"""Reference identity, immutable native snapshots and structural contracts."""

from copy import deepcopy
from dataclasses import dataclass, fields, replace
from pathlib import Path
import unittest

from ros4hc_observation_tools.in_memory_reference import (
    InMemoryReferenceResolver, NATIVE_PROFILE, ReferenceResolutionError,
    ResolutionFailure, SCHEME,
)
from ros4hc_observation_tools.model_v02 import (
    DataLevel, ObservationMetadata, ObservationSource, ReferenceDescriptor,
    ReferenceObservation, Validity, validate_reference, validate_reference_descriptor,
)


@dataclass
class NativeSample:
    values: list
    ordering: str = 'unresolved'


def observation(reference, identifier='observation-1'):
    return ReferenceObservation(ObservationMetadata(
        observation_id=identifier, subject_id='subject', session_id='session',
        observation_type='native_sample', source=ObservationSource(
            topic='/native', message_type='example_msgs/msg/NativeSample', field_path='$',
        ),
    ), reference)


class ReferenceContractTest(unittest.TestCase):
    def test_valid_descriptor_and_unknown_contracts(self):
        for reference in (ReferenceDescriptor(SCHEME, 'opaque', NATIVE_PROFILE),
                          ReferenceDescriptor('future scheme', 'opaque id', 'unknown profile')):
            self.assertEqual(validate_reference_descriptor(reference), [])
            self.assertEqual(validate_reference(observation(reference)), [])

    def test_required_strings(self):
        reference = ReferenceDescriptor(SCHEME, 'opaque', NATIVE_PROFILE)
        for name in ('scheme', 'id', 'profile'):
            for value in ('', ' \t\n', None, 123, False):
                with self.subTest(name=name, value=value):
                    candidate = replace(reference, **{name: value})
                    self.assertTrue(validate_reference_descriptor(candidate))
                    self.assertTrue(validate_reference(observation(candidate)))

    def test_metadata_invalid_and_validator_does_not_mutate(self):
        candidate = observation(ReferenceDescriptor(' future ', ' id ', ' profile '))
        before = deepcopy(candidate)
        self.assertEqual(validate_reference(candidate), [])
        self.assertEqual(candidate.reference, before.reference)
        self.assertEqual(candidate.metadata.source, before.metadata.source)
        self.assertEqual(candidate.metadata.observation_id, before.metadata.observation_id)
        self.assertTrue(validate_reference(replace(candidate, metadata=replace(
            candidate.metadata, subject_id='',
        ))))
        self.assertEqual(candidate.reference.id, ' id ')

    def test_whole_message_and_existing_field_paths(self):
        candidate = observation(ReferenceDescriptor(SCHEME, 'opaque', NATIVE_PROFILE))
        for path in ('$', 'hr', 'vector', 'payload'):
            self.assertEqual(validate_reference(replace(candidate, metadata=replace(
                candidate.metadata, source=replace(candidate.metadata.source, field_path=path),
            ))), [])
        self.assertTrue(validate_reference(replace(candidate, metadata=replace(
            candidate.metadata, source=replace(candidate.metadata.source, field_path=''),
        ))))

    def test_reference_does_not_imply_raw_or_measurement_valid(self):
        candidate = observation(ReferenceDescriptor(SCHEME, 'absent', NATIVE_PROFILE))
        for level in DataLevel:
            for validity in Validity:
                metadata = replace(candidate.metadata, data_level=level, quality=replace(
                    candidate.metadata.quality, validity=validity,
                ))
                self.assertEqual(validate_reference(replace(candidate, metadata=metadata)), [])

    def test_message_definitions_and_build_registration(self):
        root = Path(__file__).resolve().parents[2]
        directory = root / 'ros4hc_observation_msgs'
        for model, expected in (
            (ReferenceDescriptor, [('string', 'scheme'), ('string', 'id'), ('string', 'profile')]),
            (ReferenceObservation, [('ObservationMetadata', 'metadata'),
                                    ('ReferenceDescriptor', 'reference')]),
        ):
            declarations = [line.split('#')[0].split() for line in
                            (directory / 'msg' / (model.__name__ + '.msg')).read_text().splitlines()]
            self.assertEqual([tuple(line) for line in declarations if line], expected)
            self.assertEqual([field.name for field in fields(model)], [name for _, name in expected])
            self.assertIn('"msg/' + model.__name__ + '.msg"',
                          (directory / 'CMakeLists.txt').read_text())


class InMemoryReferenceTest(unittest.TestCase):
    def setUp(self):
        self.resolver = InMemoryReferenceResolver()
        self.sample = NativeSample([0, 255, 17])
        self.message_type = 'example_msgs/msg/NativeSample'
        self.reference = self.resolver.register(self.sample, self.message_type)

    def assert_failure(self, code, reference, message_type=None):
        with self.assertRaises(ReferenceResolutionError) as raised:
            self.resolver.resolve(reference, self.message_type if message_type is None else message_type)
        self.assertEqual(raised.exception.code, code)

    def test_registration_opaque_fresh_ids_and_same_sample_recovery(self):
        self.assertIsInstance(self.reference.id, str)
        self.assertTrue(self.reference.id.strip())
        second = self.resolver.register(self.sample, self.message_type)
        self.assertNotEqual(self.reference.id, second.id)
        for _ in range(2):
            restored = self.resolver.resolve(self.reference, self.message_type)
            self.assertIsInstance(restored, NativeSample)
            self.assertEqual(restored, self.sample)
            self.assertIsNot(restored, self.sample)

    def test_registration_and_resolution_are_isolated_snapshots(self):
        self.sample.values[0] = 999
        restored = self.resolver.resolve(self.reference, self.message_type)
        self.assertEqual(restored.values, [0, 255, 17])
        restored.values.clear()
        self.assertEqual(self.resolver.resolve(self.reference, self.message_type).values, [0, 255, 17])

    def test_overwrite_prohibited_even_for_same_payload(self):
        for payload in (self.sample, NativeSample([999])):
            with self.assertRaises(ValueError):
                self.resolver.register(payload, self.message_type, reference_id=self.reference.id)
        self.assertEqual(self.resolver.resolve(self.reference, self.message_type), self.sample)

    def test_explicit_failure_categories(self):
        self.assert_failure(ResolutionFailure.NOT_FOUND, replace(self.reference, id='absent'))
        self.assert_failure(ResolutionFailure.UNSUPPORTED_SCHEME,
                            replace(self.reference, scheme='other/scheme/v1'))
        self.assert_failure(ResolutionFailure.INVALID_REFERENCE, replace(self.reference, id=''))
        self.assert_failure(ResolutionFailure.PROFILE_MISMATCH,
                            replace(self.reference, profile='other/profile/v1'))
        self.assert_failure(ResolutionFailure.TYPE_MISMATCH, self.reference, 'other_msgs/msg/Other')

    def test_two_observations_share_native_reference_and_quality_is_unchanged(self):
        first = observation(self.reference)
        second = observation(self.reference, 'observation-2')
        self.assertNotEqual(first.metadata.observation_id, second.metadata.observation_id)
        for candidate in (first, second):
            self.assertEqual(validate_reference(candidate), [])
            self.assertEqual(self.resolver.resolve(candidate.reference,
                             candidate.metadata.source.message_type), self.sample)
            self.assert_failure(ResolutionFailure.TYPE_MISMATCH, candidate.reference, 'wrong/type')
            self.assertEqual(candidate.metadata.quality.validity, Validity.UNKNOWN)
            self.assertFalse(candidate.metadata.quality.quality_available)
        self.assertEqual(first.reference, second.reference)

    def test_lifetime_is_resolver_local(self):
        with self.assertRaises(ReferenceResolutionError) as raised:
            InMemoryReferenceResolver().resolve(self.reference, self.message_type)
        self.assertEqual(raised.exception.code, ResolutionFailure.NOT_FOUND)

    def test_registration_rejects_invalid_contract_without_reserving_id(self):
        for changes in ({'message_type': ''}, {'profile': ' '}, {'reference_id': ''}):
            arguments = dict(message_type=self.message_type, reference_id='new-id')
            arguments.update(changes)
            with self.assertRaises(ValueError):
                self.resolver.register(self.sample, **arguments)
        reference = self.resolver.register(self.sample, self.message_type, reference_id='new-id')
        self.assertEqual(self.resolver.resolve(reference, self.message_type), self.sample)


if __name__ == '__main__':
    unittest.main()

"""Phase 4Bの集計・計測境界・日本語出力を実機なしで確認する。"""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rclpy.serialization import serialize_message
from ros4hc_observation_eval.benchmark import (
    BenchmarkConfig, SCOPE, SIZE_DISCLAIMER, main, performance, preservation,
    run_benchmark, serialized_sizes, timing_statistics,
)
from ros4hc_observation_eval.benchmark_cases import MappingCase, make_sources
from ros4hc_observation_eval.benchmark_output import japanese_summary, write_outputs


class StatisticsTest(unittest.TestCase):
    def test_nearest_rank_statistics(self):
        result = timing_statistics(list(range(1, 101)), 20, 5)
        self.assertEqual(result['p95_mapping_time_ns'], 95)
        self.assertEqual(result['mean_mapping_time_ns'], 50.5)
        self.assertEqual(result['median_mapping_time_ns'], 50.5)
        self.assertEqual(result['min_mapping_time_ns'], 1)
        self.assertEqual(result['max_mapping_time_ns'], 100)
        self.assertEqual(result['total_mapping_time_ns'], 5050)
        self.assertEqual(result['measured_mapping_count'], 100)
        self.assertAlmostEqual(result['mappings_per_second'], 100 * 1e9 / 5050)

    def test_small_and_zero_durations(self):
        self.assertEqual(timing_statistics([7], 1, 1)['p95_mapping_time_ns'], 7)
        self.assertIsNone(timing_statistics([0], 1, 1)['mappings_per_second'])
        for values in ([], [-1]):
            with self.assertRaises(ValueError):
                timing_statistics(values, 1, 1)

    def test_invalid_configuration(self):
        for changes in ({'samples': 0}, {'samples': -1}, {'samples': 1.5},
                        {'samples': True}, {'repeats': 0}, {'repeats': False},
                        {'warmup': -1}, {'warmup': 0.1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                BenchmarkConfig(**changes)
        self.assertEqual(BenchmarkConfig(1, 1, 0).warmup, 0)


class CaseTest(unittest.TestCase):
    def test_scalar_counts(self):
        p = preservation('polar_hr', make_sources('polar_hr', 12))
        self.assertEqual(p['value_preserved_count'], 12)
        self.assertEqual(p['value_preservation_rate'], 1.0)
        self.assertEqual(p['validator_pass_rate'], 1.0)
        self.assertEqual(p['generated_count'], 12)
        self.assertEqual(p['mapped_count'], 12)
        self.assertEqual(p['mapping_error_count'], 0)

    def test_vector_counts_nan_and_frames(self):
        p = preservation('polar_acc', make_sources('polar_acc', 12))
        self.assertEqual(p['xyz_preserved_count'], 12)
        self.assertEqual(p['frame_id_preserved_count'], 12)
        self.assertEqual(p['xyz_preservation_rate'], 1.0)
        self.assertEqual(p['frame_id_preservation_rate'], 1.0)
        self.assertEqual(p['semantic_checks']['empty_frame_preserved']['applicable_count'], 6)
        self.assertEqual(p['validator_pass_count'], 12)

    def test_reference_counts_and_scope(self):
        p = preservation('sensomative_raw', make_sources('sensomative_raw', 8))
        for key in ('reference_registered_count', 'same_resolver_resolved_count',
                    'native_full_field_preserved_count', 'validator_pass_count'):
            self.assertEqual(p[key], 8)
        self.assertEqual(p['same_resolver_recovery_rate'], 1.0)
        self.assertEqual(p['native_full_field_preservation_rate'], 1.0)
        self.assertEqual(p['scope'], SCOPE)
        self.assertFalse(p['cross_process_evaluated'])
        for check in p['semantic_checks'].values():
            self.assertEqual(check['rate'], 1.0)

    def test_mapping_failure_counts_and_denominator(self):
        original = MappingCase.map
        def faulty(mapping, source):
            if source.hr == 40:
                raise ValueError('テスト用の失敗')
            return original(mapping, source)
        with patch.object(MappingCase, 'map', faulty):
            p = preservation('polar_hr', make_sources('polar_hr', 4))
        self.assertEqual(p['mapping_error_count'], 1)
        self.assertEqual(p['mapped_count'], 3)
        self.assertEqual(p['validator_pass_rate'], 0.75)
        self.assertEqual(p['value_preservation_rate'], 0.75)

    def test_timing_count_and_nonnegative(self):
        config = BenchmarkConfig(3, 2, 1)
        for name in ('polar_hr', 'polar_acc', 'sensomative_raw'):
            p = performance(name, make_sources(name, 3), config)
            self.assertEqual(p['measured_mapping_count'], 6)
            self.assertEqual(p['mapping_error_count'], 0)
            self.assertEqual(len(p['repeat_total_mapping_time_ns']), 2)
            self.assertGreaterEqual(p['min_mapping_time_ns'], 0)
            self.assertGreaterEqual(p['max_mapping_time_ns'], p['p95_mapping_time_ns'])

    def test_timing_does_not_resolve_or_serialize(self):
        with patch('ros4hc_observation_eval.benchmark.evaluate_reference', side_effect=AssertionError), \
             patch('ros4hc_observation_eval.benchmark.serialized_sizes', side_effect=AssertionError):
            p = performance('sensomative_raw', make_sources('sensomative_raw', 2), BenchmarkConfig(2, 1, 0))
        self.assertEqual(p['measured_mapping_count'], 2)

    def test_serialization_uses_standard_ros_api(self):
        for name in ('polar_hr', 'polar_acc', 'sensomative_raw'):
            mapping = MappingCase(name)
            source = make_sources(name, 1)[0]
            observation = mapping.map(source)
            size = serialized_sizes(mapping, source, observation)
            self.assertEqual(size['source_serialized_bytes'], len(serialize_message(source)))
            self.assertEqual(size['observation_serialized_bytes'], len(serialize_message(mapping.generated(observation))))
            self.assertFalse(size['native_storage_cost_included'])
            if name == 'sensomative_raw':
                self.assertEqual(size['payload_length'], 13)
                self.assertEqual(size['disclaimer'], SIZE_DISCLAIMER)

    def test_source_generation_deterministic(self):
        for name in ('polar_hr', 'polar_acc', 'sensomative_raw'):
            a, b = make_sources(name, 8), make_sources(name, 8)
            self.assertEqual([serialize_message(x) for x in a], [serialize_message(x) for x in b])


class OutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_benchmark(BenchmarkConfig(12, 2, 2))

    def test_schema_and_coverage(self):
        self.assertEqual(self.report['report_version'], 'phase4b/v1')
        self.assertEqual(len(self.report['cases']), 3)
        for case in self.report['cases']:
            self.assertEqual(set(case), {'case_name', 'representation_summary', 'preservation_summary',
                                         'performance_summary', 'serialized_size_summary'})
            coverage = case['representation_summary']
            for key in ('native_message_type', 'representation', 'observation_type', 'unit',
                        'unit_status', 'primary_time_semantics', 'data_level', 'validity',
                        'quality_available', 'source'):
                self.assertIn(key, coverage)
            self.assertEqual(case['preservation_summary']['validator_pass_count'], 12)
        raw = self.report['cases'][2]
        self.assertEqual(raw['representation_summary']['unit_status'], 0)
        self.assertEqual(raw['representation_summary']['unit'], '')
        self.assertEqual(raw['preservation_summary']['scope'], SCOPE)

    def test_json_csv_and_japanese_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            write_outputs(self.report, directory)
            paths = list(Path(directory).iterdir())
            self.assertEqual(len(paths), 6)
            restored = json.loads((Path(directory) / 'benchmark_results.json').read_text())
            self.assertEqual(restored, self.report)
            for name in ('representation_summary', 'preservation_summary', 'performance_summary', 'serialized_size_summary'):
                with (Path(directory) / (name + '.csv')).open(encoding='utf-8-sig', newline='') as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 3)
                self.assertIn('評価ケース', rows[0])
            self.assertIn('構造検証通過率', (Path(directory) / 'preservation_summary.csv').read_text(encoding='utf-8-sig'))
            self.assertIn('不明（UNKNOWN）', (Path(directory) / 'representation_summary.csv').read_text(encoding='utf-8-sig'))
            summary = (Path(directory) / 'academic_summary_ja.md').read_text()
            for phrase in ('表1', '表2', '表3', 'synthetic inputを用いたマッピング処理時間評価',
                           '同一プロセス・同一Resolverインスタンス内のみ', SIZE_DISCLAIMER):
                self.assertIn(phrase, summary)

    def test_cli_help_japanese(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            main(['--help'])
        self.assertEqual(error.exception.code, 0)
        self.assertIn('合成入力', output.getvalue())
        self.assertIn('事前実行件数', output.getvalue())

    def test_schema_keys_stable_across_runs(self):
        other = run_benchmark(BenchmarkConfig(1, 1, 0))
        self.assertEqual(set(other), set(self.report))
        for a, b in zip(other['cases'], self.report['cases']):
            for section in a:
                if isinstance(a[section], dict):
                    self.assertEqual(set(a[section]), set(b[section]))

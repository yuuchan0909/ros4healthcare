"""Phase 4B: 合成入力による記述的評価。実機・通信・臨床評価を含まない。"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import platform
import statistics
import time

from .benchmark_cases import CASE_NAMES, SOURCE_TYPES, MappingCase, make_sources
from .evaluation import compare_numeric, evaluate_reference, metadata_facts

SCOPE = 'same_process_same_resolver_instance_only'
SIZE_DISCLAIMER = ('ReferenceObservationのシリアライズサイズには、'
                   'in-memory resolver側で保持される元のRawNotificationの保存コストは含まれない。')
NOTES = [
    'synthetic inputを用いたマッピング処理時間評価',
    'マッピング処理時間はend-to-end latency、sensor latency、BLE latencyではない。',
    'synthetic結果は生理学的・臨床的妥当性の検証ではない。実機評価は別Phaseで行う。',
    'Reference recoveryは同一プロセス・同一Resolverインスタンス内のみ。永続化・別プロセス復元は未評価。',
    SIZE_DISCLAIMER,
    'ReferenceObservation単体のサイズから総システムメモリの削減を主張しない。',
    '単一実行の記述統計であり、信頼区間・有意差・科学的結論を示さない。',
]


@dataclass(frozen=True)
class BenchmarkConfig:
    samples: int = 10000
    repeats: int = 5
    warmup: int = 1000

    def __post_init__(self):
        for name, minimum in (('samples', 1), ('repeats', 1), ('warmup', 0)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f'{name} は整数かつ {minimum} 以上で指定してください')


def timing_statistics(durations, samples, repeats):
    """p95はnearest-rank法: 昇順のceil(0.95*n)番目（1始まり）。"""
    if not durations or any(value < 0 for value in durations):
        raise ValueError('計測値は空でなく、非負である必要があります')
    ordered = sorted(durations)
    total = sum(ordered)
    return dict(samples=samples, repeats=repeats, measured_mapping_count=len(ordered),
                total_mapping_time_ns=total, mean_mapping_time_ns=statistics.mean(ordered),
                median_mapping_time_ns=statistics.median(ordered),
                p95_mapping_time_ns=ordered[math.ceil(0.95 * len(ordered)) - 1],
                min_mapping_time_ns=ordered[0], max_mapping_time_ns=ordered[-1],
                mappings_per_second=len(ordered) * 1e9 / total if total else None,
                percentile_method='nearest_rank_ceil_0.95_n')


def _rate(count, denominator):
    return count / denominator if denominator else None


def preservation(case, sources):
    mapping = MappingCase(case)
    result = dict(generated_count=len(sources), mapped_count=0, mapping_error_count=0,
                  validator_pass_count=0, mapping_error_examples=[], semantic_checks={})
    counters = {'polar_hr': ('value_preserved_count',),
                'polar_acc': ('xyz_preserved_count', 'frame_id_preserved_count'),
                'sensomative_raw': ('reference_registered_count', 'same_resolver_resolved_count',
                                    'native_full_field_preserved_count')}[case]
    result.update({name: 0 for name in counters})
    for source in sources:
        try:
            observation = mapping.map(source)
        except Exception as error:
            result['mapping_error_count'] += 1
            if len(result['mapping_error_examples']) < 3:
                result['mapping_error_examples'].append(type(error).__name__ + ': ' + str(error))
            continue
        result['mapped_count'] += 1
        result['validator_pass_count'] += not mapping.validator(observation)
        m, q = observation.metadata, observation.metadata.quality
        checks = dict(validity_unknown=q.validity == 0,
                      quality_unavailable=not q.quality_available,
                      unavailable_quality_nan=not q.quality_available and math.isnan(q.quality_value),
                      unavailable_quality_scale_empty=not q.quality_available and q.quality_scale == '')
        if case == 'polar_hr':
            result['value_preserved_count'] += compare_numeric(source.hr, observation.value)['exact']
        elif case == 'polar_acc':
            result['xyz_preserved_count'] += all(compare_numeric(getattr(source.vector, axis),
                                                  getattr(observation.value, axis))['exact'] for axis in 'xyz')
            result['frame_id_preserved_count'] += source.header.frame_id == observation.frame_id
            if source.header.frame_id == '':
                checks['empty_frame_preserved'] = observation.frame_id == ''
        else:
            result['reference_registered_count'] += bool(observation.reference.id)
            recovered = evaluate_reference(observation, source, mapping.resolver)
            result['same_resolver_resolved_count'] += recovered['resolved']
            result['native_full_field_preserved_count'] += recovered.get('exact', False)
            checks.update(unit_unknown=m.unit_status == 0, unit_empty=m.unit == '')
        for key, passed in checks.items():
            check = result['semantic_checks'].setdefault(key, {'applicable_count': 0, 'satisfied_count': 0})
            check['applicable_count'] += 1
            check['satisfied_count'] += bool(passed)
    for check in result['semantic_checks'].values():
        check['rate'] = _rate(check['satisfied_count'], check['applicable_count'])
    rates = {'validator_pass_count': 'validator_pass_rate',
             'value_preserved_count': 'value_preservation_rate',
             'xyz_preserved_count': 'xyz_preservation_rate',
             'frame_id_preserved_count': 'frame_id_preservation_rate',
             'same_resolver_resolved_count': 'same_resolver_recovery_rate',
             'native_full_field_preserved_count': 'native_full_field_preservation_rate'}
    for count, rate in rates.items():
        if count in result:
            result[rate] = _rate(result[count], len(sources))
    if case == 'sensomative_raw':
        result.update(scope=SCOPE, cross_process_evaluated=False)
    return result


def performance(case, sources, config):
    warm = MappingCase(case)
    for index in range(config.warmup):
        warm.map(sources[index % len(sources)])
    del warm
    durations, errors = [], 0
    repeat_totals = []
    for _ in range(config.repeats):
        mapping = MappingCase(case)
        total = 0
        for source in sources:
            # 入力生成、resolver構築、検証集計、resolve、JSON出力は計測外。
            start = time.perf_counter_ns()
            try:
                observation = mapping.map(source)
            except Exception:
                errors += 1
                continue
            elapsed = time.perf_counter_ns() - start
            durations.append(elapsed)
            total += elapsed
            del observation
        repeat_totals.append(total)
        del mapping
    if durations:
        result = timing_statistics(durations, len(sources), config.repeats)
    else:
        result = dict(samples=len(sources), repeats=config.repeats, measured_mapping_count=0,
                      **{key: None for key in ('total_mapping_time_ns', 'mean_mapping_time_ns',
                         'median_mapping_time_ns', 'p95_mapping_time_ns', 'min_mapping_time_ns',
                         'max_mapping_time_ns', 'mappings_per_second')})
    return {**result, 'warmup': config.warmup, 'mapping_error_count': errors,
            'repeat_total_mapping_time_ns': repeat_totals,
            'title': NOTES[0],
            'mapping_boundary': {
                'polar_hr': 'map_polar_hr -> map_health_observation（Python model）',
                'polar_acc': 'map_polar_acc（Python model）',
                'sensomative_raw': 'map_reference（登録・deepcopy・内部validator・ROS message生成を含む）',
            }[case]}


def serialized_sizes(mapping, source, observation):
    try:
        from rclpy.serialization import serialize_message
    except ImportError:
        return dict(available=False, source_serialized_bytes=None, observation_serialized_bytes=None,
                    note='ROS 2標準シリアライズAPIを利用できないため未取得',
                    native_storage_cost_included=False, disclaimer=SIZE_DISCLAIMER if mapping.resolver else '')
    source_size = len(serialize_message(source))
    observation_size = len(serialize_message(mapping.generated(observation)))
    return dict(available=True, source_serialized_bytes=source_size,
                observation_serialized_bytes=observation_size,
                native_storage_cost_included=False,
                source_sample_index=0,
                payload_length=getattr(source, 'payload_length', None),
                disclaimer=SIZE_DISCLAIMER if mapping.resolver else '')


def run_benchmark(config):
    cases = []
    for name, source_type in zip(CASE_NAMES, SOURCE_TYPES):
        sources = make_sources(name, config.samples)
        mapping = MappingCase(name)
        example = mapping.map(sources[0])
        facts = metadata_facts(example)
        coverage = {key: facts[key] for key in ('representation', 'observation_type', 'unit',
                    'unit_status', 'primary_time_semantics', 'data_level', 'validity', 'quality_available', 'source')}
        coverage.update(case_name=name, native_message_type=source_type)
        sizes = serialized_sizes(mapping, sources[0], example)
        del mapping, example
        cases.append(dict(case_name=name, representation_summary=coverage,
                          preservation_summary=preservation(name, sources),
                          performance_summary=performance(name, sources, config),
                          serialized_size_summary=sizes))
    return dict(report_version='phase4b/v1', synthetic_only=True,
                config=dict(samples=config.samples, repeats=config.repeats, warmup=config.warmup),
                environment=dict(python=platform.python_version(), platform=platform.platform(),
                                 processor=platform.processor(), ros_distro=os.environ.get('ROS_DISTRO', ''),
                                 clock=time.get_clock_info('perf_counter').implementation,
                                 generated_utc=datetime.now(timezone.utc).isoformat()),
                methodology=dict(rate_denominator='generated_count（意味チェックのみapplicable_count）',
                    timing='成功したmapping呼出し単位の分布。外側検証・resolve・シリアライズは計測外。内部処理は含む。',
                    source_pattern='HR=40+i%141; ACC yはi%11==10でNaN、frameは空/既知文字列を交互; Raw長=13,0,5,32',
                    size_sample='各caseのindex=0。Raw payload長13の一例。文字列長・payload長に依存し固定サイズではない。',
                    uuid='既存mappingのUUID生成を変更しないためIDと実行時刻は非決定的',
                    memory='入力配列は計測前に生成。Reference storeはwarmupと各repeatで新規作成し終了時に解放。'),
                notes=NOTES, cases=cases)


def main(args=None):
    from .benchmark_output import write_outputs, console_summary
    parser = argparse.ArgumentParser(description='Observation Layerの合成入力を用いた定量評価',
                                     add_help=False)
    parser.add_argument('-h', '--help', action='help', help='この説明を表示して終了')
    parser.add_argument('--samples', type=int, default=10000, help='ケースごとの合成入力件数（既定: 10000）')
    parser.add_argument('--repeats', type=int, default=5, help='計測反復回数（既定: 5）')
    parser.add_argument('--warmup', type=int, default=1000, help='ケースごとの事前実行件数（既定: 1000）')
    parser.add_argument('--output', type=Path, required=True, help='JSON・CSV・日本語要約の出力先ディレクトリ')
    parser._optionals.title = 'オプション'
    values = parser.parse_args(args)
    try:
        config = BenchmarkConfig(values.samples, values.repeats, values.warmup)
    except ValueError as error:
        parser.error(str(error))
    report = run_benchmark(config)
    write_outputs(report, values.output)
    print(console_summary(report))
    print('出力先: ' + str(values.output))

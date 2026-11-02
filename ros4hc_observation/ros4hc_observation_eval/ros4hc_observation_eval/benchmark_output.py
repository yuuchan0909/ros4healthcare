"""安定したJSONキーと日本語のCSV・学会向け要約。"""
import csv
from pathlib import Path
from .evaluation import report_json

CASE_LABELS = {'polar_hr': 'Polar HR', 'polar_acc': 'Polar ACC', 'sensomative_raw': 'Sensomative raw'}
LABELS = {
    'case_name': '評価ケース', 'native_message_type': '入力メッセージ型',
    'representation': 'Observation表現', 'observation_type': '観測種別', 'unit': '単位',
    'unit_status': '単位の確認状態', 'primary_time_semantics': '主時刻の意味',
    'data_level': 'データレベル', 'validity': '妥当性状態', 'quality_available': '品質情報の利用可否',
    'source.device_type': '入力デバイス種別', 'source.device_id': '入力デバイス識別子',
    'source.topic': '入力トピック', 'source.message_type': '出典メッセージ型',
    'source.field_path': '出典フィールド', 'source.node': '出典ノード',
    'generated_count': '生成件数', 'mapped_count': 'マッピング成功件数',
    'mapping_error_count': 'マッピング例外件数',
    'validator_pass_count': '構造検証通過件数', 'validator_pass_rate': '構造検証通過率',
    'value_preserved_count': '値保持件数', 'value_preservation_rate': '値保持率',
    'xyz_preserved_count': 'XYZ保持件数', 'xyz_preservation_rate': 'XYZ保持率',
    'frame_id_preserved_count': 'frame_id保持件数', 'frame_id_preservation_rate': 'frame_id保持率',
    'reference_registered_count': 'Reference登録件数',
    'same_resolver_resolved_count': '同一Resolver復元件数',
    'same_resolver_recovery_rate': '同一Resolver復元率',
    'native_full_field_preserved_count': 'native全フィールド保持件数',
    'native_full_field_preservation_rate': 'native全フィールド保持率',
    'scope': '復元範囲', 'cross_process_evaluated': '別プロセス復元の評価有無',
    'samples': '入力件数', 'repeats': '計測反復回数', 'warmup': '事前実行件数',
    'measured_mapping_count': '計測成功件数',
    'total_mapping_time_ns': '合計マッピング処理時間_ns',
    'mean_mapping_time_ns': '平均マッピング処理時間_ns',
    'median_mapping_time_ns': '中央値マッピング処理時間_ns',
    'p95_mapping_time_ns': '95パーセンタイルマッピング処理時間_ns',
    'min_mapping_time_ns': '最小マッピング処理時間_ns',
    'max_mapping_time_ns': '最大マッピング処理時間_ns',
    'mappings_per_second': '処理時間から算出した毎秒マッピング件数',
    'percentile_method': 'パーセンタイル算出法', 'mapping_boundary': '計測範囲',
    'title': '評価名称', 'available': 'サイズ取得可否',
    'source_serialized_bytes': '入力シリアライズサイズ_バイト',
    'observation_serialized_bytes': 'Observationシリアライズサイズ_バイト',
    'native_storage_cost_included': 'resolver側native保存コストの算入有無',
    'source_sample_index': 'サイズ計測入力インデックス', 'payload_length': 'payloadバイト数',
    'disclaimer': '解釈上の注意', 'note': '補足',
}
CHECK_LABELS = {
    'validity_unknown': '妥当性不明の維持', 'quality_unavailable': '品質情報利用不可の維持',
    'unavailable_quality_nan': '利用不可品質値NaNの維持',
    'unavailable_quality_scale_empty': '利用不可品質scale空文字の維持',
    'empty_frame_preserved': '未知frame_id空文字の保持',
    'unit_unknown': '単位不明の維持', 'unit_empty': '単位空文字の維持',
}
ENUM_LABELS = {
    'unit_status': {0: '不明（UNKNOWN）', 1: '確認済み（VERIFIED）', 2: '推定（INFERRED）'},
    'primary_time_semantics': {0: '不明（UNKNOWN）', 1: 'デバイス計測時刻（DEVICE_MEASUREMENT）',
                             2: 'ホスト受信時刻（HOST_RECEIVE）', 3: 'ROS送信時刻（ROS_PUBLISH）',
                             4: '合成時刻（SYNTHETIC）'},
    'data_level': {0: '不明（UNKNOWN）', 1: '生サンプル（RAW_SAMPLE）',
                   2: 'デバイス側算出値（DEVICE_DERIVED）', 3: 'ROS側算出値（ROS_DERIVED）',
                   4: '合成サンプル（SYNTHETIC_SAMPLE）'},
    'validity': {0: '不明（UNKNOWN）', 1: '有効（VALID）', 2: '無効（INVALID）',
                 3: '欠測（MISSING）', 4: '期限超過（STALE）', 5: '重複（DUPLICATE）'},
}


def display(key, value):
    if key in ENUM_LABELS:
        return ENUM_LABELS[key].get(value, '未認識（' + str(value) + '）')
    if key == 'case_name':
        return CASE_LABELS.get(value, value)
    if key == 'scope' and value == 'same_process_same_resolver_instance_only':
        return '同一プロセス・同一Resolverインスタンス内のみ'
    if key == 'percentile_method':
        return 'nearest-rank法（ceil(0.95×件数)番目、1始まり）'
    if value is None:
        return '未取得・対象外'
    if isinstance(value, bool):
        return 'はい' if value else 'いいえ'
    if value == '':
        return '空文字'
    return value


def _rows(report, section):
    rows = []
    for case in report['cases']:
        row = {'case_name': case['case_name']}
        for key, value in case[section].items():
            if key == 'source':
                row.update({'source.' + name: item for name, item in value.items()})
            elif key == 'semantic_checks':
                for name, check in value.items():
                    for metric, item in check.items():
                        row[f'semantic_checks.{name}.{metric}'] = item
            elif not isinstance(value, (list, dict)):
                row[key] = value
        rows.append(row)
    return rows


def _header(key):
    if key.startswith('semantic_checks.'):
        _, check, metric = key.split('.')
        return CHECK_LABELS[check] + {'applicable_count': '_対象件数', 'satisfied_count': '_成立件数', 'rate': '_成立率'}[metric]
    return LABELS[key]


def _csv(report, section, path):
    rows = _rows(report, section)
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow([_header(key) for key in keys])
        for row in rows:
            writer.writerow([display(key, row[key]) if key in row else '' for key in keys])


def _table(title, headers, rows):
    return '\n'.join([title, '', '| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(str(item) for item in row) + ' |' for row in rows])


def japanese_summary(report):
    coverage_keys = ('case_name', 'native_message_type', 'representation', 'observation_type',
                     'unit', 'unit_status', 'primary_time_semantics', 'data_level', 'validity',
                     'quality_available', 'source.device_type', 'source.message_type', 'source.field_path')
    coverage = _rows(report, 'representation_summary')
    text = ['# Observation Layer Phase 4B 合成入力の評価結果',
            '設定: 入力件数={samples}、計測反復回数={repeats}、事前実行件数={warmup}'.format(**report['config']),
            _table('表1：表現・意味情報の範囲（記述結果）', [LABELS[key] for key in coverage_keys],
                   [[display(key, row[key]) for key in coverage_keys] for row in coverage])]
    rows = []
    for case in report['cases']:
        p = case['preservation_summary']
        for key, value in p.items():
            if key in LABELS:
                rows.append([CASE_LABELS[case['case_name']], LABELS[key], display(key, value)])
        for name, check in p['semantic_checks'].items():
            rows.append([CASE_LABELS[case['case_name']], CHECK_LABELS[name],
                         f"{check['satisfied_count']}/{check['applicable_count']}（成立率={check['rate']:.6f}）"])
    text.append(_table('表2：保持・構造検証結果', ['評価ケース', '評価指標', '結果'], rows))
    perf_keys = ('case_name', 'samples', 'repeats', 'measured_mapping_count', 'mean_mapping_time_ns',
                 'median_mapping_time_ns', 'p95_mapping_time_ns', 'min_mapping_time_ns',
                 'max_mapping_time_ns', 'total_mapping_time_ns', 'mappings_per_second')
    text.append(_table('表3：synthetic inputを用いたマッピング処理時間評価',
                       [LABELS[key] for key in perf_keys],
                       [[display(key, row[key]) for key in perf_keys]
                        for row in _rows(report, 'performance_summary')]))
    text.append(_table('補助表：ROS 2シリアライズサイズ（代表入力1件）',
                       ['評価ケース', '入力_バイト', 'Observation_バイト'],
                       [[CASE_LABELS[c['case_name']], c['serialized_size_summary']['source_serialized_bytes'],
                         c['serialized_size_summary']['observation_serialized_bytes']] for c in report['cases']]))
    text.extend(['## 解釈上の注意', *report['notes']])
    for case in report['cases']:
        text.append(CASE_LABELS[case['case_name']] + ' 計測範囲: ' + case['performance_summary']['mapping_boundary'])
    text.append('率の分母は生成件数。意味チェックの分母は適用件数。時刻統計は成功呼出しを反復間でプール。')
    text.append('p95はnearest-rank法。毎秒件数は計測時間合計の逆数から算出し、実システムのスループットではない。')
    text.append('サイズは先頭入力1件のみ。文字列長・payload長に依存する。総メモリ量の評価ではない。')
    return '\n\n'.join(text) + '\n'


def write_outputs(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'benchmark_results.json').write_text(report_json(report) + '\n', encoding='utf-8')
    for section in ('representation_summary', 'preservation_summary', 'performance_summary', 'serialized_size_summary'):
        _csv(report, section, output / (section + '.csv'))
    (output / 'academic_summary_ja.md').write_text(japanese_summary(report), encoding='utf-8')


def console_summary(report):
    lines = ['synthetic inputを用いたマッピング処理時間評価']
    for case in report['cases']:
        p, t = case['preservation_summary'], case['performance_summary']
        lines.append(f"{CASE_LABELS[case['case_name']]}: 生成={p['generated_count']}、"
                     f"構造検証通過={p['validator_pass_count']}、平均={t['mean_mapping_time_ns']} ns、"
                     f"中央値={t['median_mapping_time_ns']} ns、p95={t['p95_mapping_time_ns']} ns")
    lines.extend(report['notes'][1:])
    return '\n'.join(lines)

"""Evaluator-side coverage counts; never an automatic quality score."""
import hashlib
import json
from collections import Counter

CORPUS_VERSION = 'phase5g-v1'
TOPICS = ('heart_rate', 'heart_rate_variability', 'accelerometry', 'imu', 'physical_activity',
          'gait', 'balance', 'posture', 'postural_transition', 'fall_related_movement',
          'recovery_physiological_response', 'multimodal_sensing', 'wearable_sensing',
          'human_activity_recognition', 'sensor_fusion')


def eligible(paper, chunk, include_unverified=False):
    if 'excluded' in (paper['curation_status'], chunk['curation_status']):
        return False
    return include_unverified or (paper['curation_status'] == chunk['curation_status'] == 'verified')


def _counts(rows, key, multiple=False):
    counter = Counter()
    for row in rows:
        value = row[key]
        values = sorted(set(value)) if multiple and value else [value] if not multiple and value is not None else []
        counter.update(str(v) for v in values)
        if not values:
            counter['<unknown>'] += 1
    return dict(sorted(counter.items()))


def coverage_report(papers, chunks, mode):
    by_id = {p['paper_id']: p for p in papers}
    selected = [c for c in chunks if eligible(by_id[c['paper_id']], c)]
    selected_ids = {c['paper_id'] for c in selected}
    def counts(ps, cs):
        return {'papers': {key: _counts(ps, key, multiple) for key, multiple in (
                    ('sensor_modalities', True), ('measured_variables', True), ('population', False),
                    ('tasks_or_states', True), ('year', False), ('curation_status', False), ('topics', True))},
                'chunks': {key: _counts(cs, key, multiple) for key, multiple in (
                    ('sensor_modalities', True), ('measured_variables', True), ('population', False),
                    ('task_or_state', False), ('finding_type', False), ('curation_status', False), ('topics', True))}}
    matrix = []
    for p in papers:
        own = [c for c in selected if c['paper_id'] == p['paper_id']]
        matrix.append({'paper_id': p['paper_id'], 'curation_status': p['curation_status'],
                       'declared_modalities': sorted(set(p['sensor_modalities'] or [])),
                       'declared_topics': sorted(set(p['topics'] or [])),
                       'verified_chunk_modalities': sorted({m for c in own for m in c['sensor_modalities'] or []}),
                       'verified_chunk_topics': sorted({t for c in own for t in c['topics'] or []})})
    multimodal_papers = [{'paper_id':p['paper_id'], 'modalities':sorted(set(p['sensor_modalities']))}
                        for p in papers if len(set(p['sensor_modalities'] or [])) > 1]
    multimodal_chunks = [{'paper_id':c['paper_id'], 'chunk_id':c['chunk_id'],
                          'modalities':sorted(set(c['sensor_modalities']))}
                         for c in chunks if len(set(c['sensor_modalities'] or [])) > 1]
    gaps = []
    for topic in TOPICS:
        ids = {c['paper_id'] for c in selected if topic in (c['topics'] or [])}
        if len(ids) < 2:
            gaps.append({'topic':topic, 'verified_evidence_paper_count':len(ids),
                         'message':f'現在のコーパスでは {topic} の確認済み証拠のcoverageが限定的です。'})
    combos = sorted({tuple(sorted(set(c['sensor_modalities'] or []))) for c in selected
                     if len(set(c['sensor_modalities'] or [])) > 1})
    hr_activity = any('hr' in c['sensor_modalities'] and 'activity' in c['sensor_modalities']
                      for c in selected if c['sensor_modalities'])
    from .fall_risk_evidence import condition_coverage
    return {'fall_risk_coverage':{'registry':condition_coverage(chunks), 'verified_retrieval':condition_coverage(selected)},
            'mode':mode, 'synthetic_notice': '合成テスト資料・研究上の実証的根拠ではない' if mode == 'synthetic' else None,
            'registry_counts':counts(papers, chunks),
            'verified_retrieval_counts':counts([p for p in papers if p['paper_id'] in selected_ids], selected),
            'matrix':matrix, 'multimodal_papers':multimodal_papers, 'multimodal_chunks':multimodal_chunks,
            'verified_multimodal_combinations':[list(c) for c in combos],
            'verified_hr_activity_chunk_present':hr_activity, 'coverage_gaps':gaps}


def manifest(papers, chunks, mode, coverage, converted_count, include_unverified=False):
    payload = json.dumps({'papers':papers, 'chunks':chunks}, sort_keys=True, ensure_ascii=False, allow_nan=False)
    all_rows = papers + chunks
    return {'corpus_version':CORPUS_VERSION, 'schema_version':CORPUS_VERSION, 'mode':mode,
            'build_metadata':{'registry_sha256':hashlib.sha256(payload.encode()).hexdigest(),
                              'include_unverified':include_unverified, 'conversion_method':'manual_curation_v1'},
            'paper_count':len(papers), 'evidence_chunk_count':len(chunks),
            'verified_paper_count':sum(p['curation_status'] == 'verified' for p in papers),
            'verified_chunk_count':sum(c['curation_status'] == 'verified' for c in chunks),
            'retrieval_chunk_count':converted_count,
            'synthetic_fixture_count':sum(r['synthetic_fixture'] for r in all_rows),
            'real_record_count':sum(not r['synthetic_fixture'] for r in all_rows),
            'coverage_summary':coverage['verified_retrieval_counts'],
            'fall_risk_schema_version':'phase5g-conditions-v1',
            'fall_risk_coverage':coverage['fall_risk_coverage']['verified_retrieval']}


def render_ja(report):
    lines = ['# 文献coverage診断', '', 'モード: ' + report['mode'], '',
             report['synthetic_notice'] or '手動登録された実文献。医学的妥当性の自動評価ではありません。', '',
             '以下は記述的な件数です。研究品質・効果の強さを採点しません。', '',
             '| Paper ID | status | 論文の明示modality | 確認済みchunkのmodality集合 |',
             '|---|---|---|---|']
    for row in report['matrix']:
        lines.append('| ' + ' | '.join([row['paper_id'], row['curation_status'],
                     ', '.join(row['declared_modalities']), ', '.join(row['verified_chunk_modalities'])]) + ' |')
    lines += ['', 'chunk集合のunionは、同一chunkでの異種センサ間の関連を意味しません。', '',
              '論文・chunkの各modality/変数/集団/task/年/finding/status件数:', '',
              '```json', json.dumps(report['registry_counts'], ensure_ascii=False, indent=2), '```', '',
              '複数modalityを明示したchunk:', '',
              '```json', json.dumps(report['multimodal_chunks'], ensure_ascii=False, indent=2), '```', '',
              '確認済みchunkにHRとactivityが同時に明示されている: ' + str(report['verified_hr_activity_chunk_present']), '']
    lines += ['', '適用条件のcoverage（登録全体／確認済み検索対象）:', '',
              '```json', json.dumps(report['fall_risk_coverage'], ensure_ascii=False, indent=2), '```', '',
              '閾値は集団・測定法・転倒定義・追跡期間・性能と一体で扱います。条件一致や個人リスクは判定しません。', '']
    lines.extend('- ' + g['message'] for g in report['coverage_gaps'])
    return '\n'.join(lines) + '\n'

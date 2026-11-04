"""Manual registry validation/conversion and an explicitly synthetic offline demo."""
import argparse
import copy
import json
from pathlib import Path
from .evidence import build_bundle, write_jsonl
from .literature_registry import load_registry, validate_registry
from .literature_corpus import validate_corpus
from .literature_retrieval import LexicalRetrievalBaseline
from .literature_coverage import coverage_report, eligible, manifest, render_ja


def convert_registry(papers, chunks, mode='real', include_unverified=False):
    papers, chunks, _ = validate_registry(papers, chunks, mode)
    by_id = {p['paper_id']:p for p in papers}
    records = []
    for chunk in chunks:
        paper = by_id[chunk['paper_id']]
        if not eligible(paper, chunk, include_unverified):
            continue
        record = {k:copy.deepcopy(paper[k]) for k in ('paper_id','title','authors','year','doi','url','synthetic_fixture')}
        record.update(chunk_id=chunk['chunk_id'], evidence_text=chunk['evidence_summary'],
                      # No metadata fallback: paper-level population/sensors need not apply to this finding.
                      population=chunk['population'], sensor_modalities=copy.deepcopy(chunk['sensor_modalities']),
                      measured_variables=copy.deepcopy(chunk['measured_variables']), task_or_state=chunk['task_or_state'],
                      evidence_type=chunk['finding_type'], source_type=paper['publication_type'],
                      notes=None,
                      curation_provenance={k:copy.deepcopy(chunk[k]) for k in
                          ('section','page','figure_or_table','source_location_verified','relationship',
                           'direction','limitations','curation_status')})
        record['curation_provenance']['paper_curation_status'] = paper['curation_status']
        record['curation_provenance']['diagnostic_unverified'] = not eligible(paper, chunk)
        if chunk.get('fall_risk') is not None:
            context = chunk['fall_risk']
            # Project explicit curator-provided metadata; never derive conditions from values.
            for key in ('sensor_modalities', 'measured_variables'):
                if context['measurement'][key] is not None:
                    record[key] = copy.deepcopy(context['measurement'][key])
            if context['population']['description'] is not None:
                record['population'] = context['population']['description']
            record['curation_provenance']['fall_risk'] = copy.deepcopy(chunk['fall_risk'])
        records.append(record)
    return validate_corpus(records)


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)+'\n', encoding='utf-8')


def export_registry(papers, chunks, output, mode='real', include_unverified=False):
    papers, chunks, report = validate_registry(papers, chunks, mode)
    records = convert_registry(papers, chunks, mode, include_unverified)
    coverage = coverage_report(papers, chunks, mode)
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    write_json(output/'validation_report.json', report)
    write_json(output/'coverage_report.json', coverage)
    (output/'coverage_report_ja.md').write_text(render_ja(coverage), encoding='utf-8')
    write_json(output/'literature_manifest.json', manifest(papers, chunks, mode, coverage, len(records), include_unverified))
    name = 'diagnostic_literature_corpus.jsonl' if include_unverified else 'verified_literature_corpus.jsonl'
    write_jsonl(output/name, records)
    return records


def _parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--papers', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--mode', choices=('real','synthetic'), default='real',
                        help='Default real rejects synthetic fixtures; synthetic is test-only')
    return parser


def validate_main():
    parser = _parser('Validate manually curated literature offline')
    parser.add_argument('--output-report', required=True)
    args = parser.parse_args()
    try:
        _, _, report = load_registry(args.papers, args.evidence, args.mode)
    except (ValueError, OSError) as exc:
        write_json(args.output_report, {'valid':False, 'mode':args.mode, 'errors':[str(exc)]})
        parser.error(str(exc))
    write_json(args.output_report, report)
    print(f'Valid: {report["paper_count"]} papers, {report["evidence_chunk_count"]} chunks; warnings={len(report["warnings"])}')


def convert_main():
    parser = _parser('Convert verified manual literature and generate coverage diagnostics offline')
    parser.add_argument('--output', required=True, help='Output directory')
    parser.add_argument('--include-unverified', action='store_true', help='Diagnostic/development only; excluded records stay excluded')
    args = parser.parse_args()
    try:
        papers, chunks, _ = load_registry(args.papers, args.evidence, args.mode)
        records = export_registry(papers, chunks, args.output, args.mode, args.include_unverified)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'{args.mode}: {len(records)} chunks' + (' DIAGNOSTIC / UNVERIFIED' if args.include_unverified else ' verified-only'))


def demo_records():
    papers, chunks = [], []
    for i, status in enumerate(('verified','candidate','excluded'),1):
        papers.append({'paper_id':f'synthetic_paper_{i:04}',
                       'title':'Synthetic Real-Corpus Format Test Paper ' + str(i),
                       'synthetic_fixture':True, 'curation_status':status,
                       'exclusion_reason':'Synthetic exclusion workflow test' if status == 'excluded' else None,
                       'sensor_modalities':['hr','activity'] if i == 1 else ['imu'],
                       'topics':['heart_rate','physical_activity'] if i == 1 else ['balance']})
    for i, pid in enumerate((1,1,2,2,3),1):
        chunks.append({'paper_id':f'synthetic_paper_{pid:04}', 'chunk_id':f'chunk_{i:04}',
                       'evidence_summary':f'Synthetic test evidence {i}: artificial heart rate and physical activity test notation only. No scientific claim.',
                       'section':'Synthetic test section', 'page':str(i),
                       'synthetic_fixture':True, 'source_location_verified':pid == 1,
                       'curation_status':papers[pid-1]['curation_status'],
                       'sensor_modalities':['hr','activity'] if pid == 1 else ['imu'],
                       'measured_variables':['mean_hr_bpm','activity_magnitude_mean'] if pid == 1 else [],
                       'topics':['heart_rate','physical_activity'] if pid == 1 else ['balance'],
                       'finding_type':'method', 'relationship':'none', 'direction':'not_applicable'})
    return papers, chunks


def fall_risk_demo_records():
    papers, chunks = demo_records()
    # Fictional applicability conditions only. Never an actual fall-risk threshold.
    chunks[0]['fall_risk'] = {
        'value_origin':'paper_reported',
        'population':{'description':'Synthetic test population only', 'sample_size':12, 'setting':'Synthetic lab'},
        'measurement':{'sensor_modalities':['hr','activity'], 'devices':['Synthetic device'],
                       'protocol':'Synthetic test protocol; not clinically validated',
                       'measured_variables':['synthetic_indicator']},
        'risk_indicator':{'indicator_name':'synthetic_indicator', 'threshold_value':123,
                          'threshold_operator':'<=', 'unit':'synthetic_unit', 'unit_required':True},
        'fall_outcome':{'fall_definition':'Synthetic outcome for software testing only',
                        'prospective_or_retrospective':'prospective',
                        'ascertainment_method':'Synthetic log', 'followup_duration':'Synthetic test interval'},
        'results':{'result_summary':'Synthetic numerical format example; no scientific claim',
                   'auc':0.75, 'sensitivity':0.6, 'specificity':0.7},
        'limitations':{'paper_reported_limitations':['Synthetic fixture; not empirical evidence']},
        'knowledge_base':{'risk_factor_category':'other', 'evidence_strength_note':'Synthetic format test only'},
        'provenance':{'section':'Synthetic test section','page':'1'}}
    return papers, chunks


def demo_main():
    parser = argparse.ArgumentParser(description='Synthetic curation workflow demonstration; not academic evidence')
    parser.add_argument('--output', default='/tmp/ros4hc_phase5g')
    args = parser.parse_args()
    papers, chunks = fall_risk_demo_records()
    output = Path(args.output)
    write_jsonl(output/'papers.jsonl', papers)
    write_jsonl(output/'evidence_chunks.jsonl', chunks)
    records = export_registry(papers, chunks, output, mode='synthetic')
    bundle = build_bundle({'hr':{'mean_hr_bpm':82},'acc':{'activity_magnitude_mean':1}},
                          LexicalRetrievalBaseline(records))
    write_jsonl(output/'demo_retrieval.jsonl', [bundle])
    print('合成テスト資料・研究上の実証的根拠ではない: 3 papers / 5 chunks -> 2 verified chunks')

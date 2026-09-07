"""Resumable execution-delay comparison using the shared UBS execution pipeline."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from mt5_ea_validator.configuration import WF_PERIODS
from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.report import parse_entry_volume_audit
from mt5_ea_validator.ubs_risk_sensitivity import (
    _archive_case, _limited_annual_executor_factory, _load_json,
    _load_same_risk_quarterly_selection, _run_id, _write_csv, _write_manifest,
)
from mt5_ea_validator.ubs_smoke import (
    JST, UBSStrategy, _scenario, execute_ubs_case, load_ubs_smoke_settings,
)

STRATEGIES = ('xau_h1_c5', 'daily_l')


def case_plan(repeats=3):
    if repeats < 2:
        raise ValueError('Random delay requires at least two repetitions')
    return [dict(case_id=f'{wf}_{sid}_{label}', wf=wf, strategy_id=sid,
                 execution_mode=mode, repetition=rep)
            for wf in WF_PERIODS for sid in STRATEGIES
            for label, mode, rep in [('no_delay', 0, 1), ('fixed_188', 188, 1)]
            + [(f'random_{i}', -1, i) for i in range(1, repeats + 1)]]


def comparison(result, baseline):
    current, original = result['metrics'], baseline['metrics']
    delta = float(current['net_profit']) - float(original['net_profit'])
    dd_delta = float(current['equity_drawdown_percent']) - float(original['equity_drawdown_percent'])
    same = result['deal_audit']['deal_sequence_sha256'] == baseline['deal_audit']['deal_sequence_sha256']
    return dict(net_profit_delta=delta, equity_dd_delta_pp=dd_delta,
                trades_delta=int(current['trades']) - int(original['trades']),
                deal_sequence_same=same,
                reproduction_passed=(same and current['trades'] == original['trades']
                    and abs(delta) <= max(.10, abs(float(original['net_profit'])) * .005) + 1e-9
                    and abs(dd_delta) <= .010000001),
                accounting_note='minor accounting difference' if same and abs(delta) > 1e-9 else '')


def run(settings, baseline_run, *, resume=None, max_cases=None, repeats=3,
        case_runner=execute_ubs_case, volume_parser=parse_entry_volume_audit):
    source = baseline_run.resolve() / 'run_manifest.json'
    old = _load_json(source, label='quarterly baseline')
    if old.get('status') != 'success':
        raise ValueError('Baseline must be successful')
    conditions = dict(symbol=settings.symbol, period=settings.period, model=4,
                      execution_mode=0, deposit=settings.deposit,
                      currency=settings.currency, leverage=settings.leverage)
    if any(old['conditions'].get(k) != v for k, v in conditions.items()):
        raise ValueError('Baseline conditions changed')
    selection_path = Path(old['selection_file'])
    if sha256_file(selection_path) != old['selection_file_sha256']:
        raise ValueError('Selection changed')
    _, selected = _load_same_risk_quarterly_selection(
        settings, selection_path, Path(old['faithful_sets_manifest']),
        Path(old['faithful_validation_run']))
    selected = {s['strategy_id']: s for s in selected}
    baselines = {(r['wf'], r['strategy_id']): r for r in old['results']}
    plan = case_plan(repeats)
    for case in plan:
        base = baselines[(case['wf'], case['strategy_id'])]
        if (base['status'] != 'success'
                or base['selected_set_sha256'] != selected[case['strategy_id']]['selected_set_sha256']
                or (base['from_date'], base['to_date']) != WF_PERIODS[case['wf']]):
            raise ValueError('Invalid baseline case')
    root = settings.output_root.parent / 'execution_delay'
    directory = resume.resolve() if resume else root / _run_id(datetime.now(JST))
    directory.mkdir(parents=True, exist_ok=resume is not None)
    manifest_path = directory / 'run_manifest.json'
    identity = dict(baseline_manifest=str(source), baseline_sha256=sha256_file(source),
                    plan=plan, conditions=conditions, ea_sha256=sha256_file(settings.expert_binary))
    if resume:
        manifest = _load_json(manifest_path, label='delay manifest')
        if any(manifest.get(k) != v for k, v in identity.items()):
            raise ValueError('Resume identity changed')
    else:
        manifest = dict(schema_version=1, **identity, results=[],
                        resource_limits=dict(priority='BelowNormal', logical_cpu_count=4),
                        started_at=datetime.now(JST).isoformat())
    completed = {}
    for record in manifest['results']:
        file = directory / record['case_id'] / 'result.json'
        if sha256_file(file) != record['result_sha256']:
            raise ValueError('Completed result changed')
        completed[record['case_id']] = _load_json(file, label='completed result')
    manifest.update(status='running', error=None)
    _write_manifest(manifest_path, manifest)
    count = 0
    try:
        for case in plan:
            cid = case['case_id']
            if cid in completed:
                continue
            if (directory / 'STOP_AFTER_CASE').exists() or (max_cases is not None and count >= max_cases):
                manifest['status'] = 'paused'
                break
            wf, sid = case['wf'], case['strategy_id']
            if case['execution_mode'] != 0:
                gate = completed.get(f'{wf}_{sid}_no_delay', {})
                if not gate.get('comparison', {}).get('reproduction_passed'):
                    raise ValueError('No Delay reproduction gate has not passed')
            chosen = selected[sid]
            set_file = Path(chosen['selected_set_file'])
            if sha256_file(set_file) != chosen['selected_set_sha256']:
                raise ValueError('Selected set changed')
            strategy = UBSStrategy(strategy_id=sid, file_name=set_file.name)
            current = replace(settings, set_directory=set_file.parent,
                              from_date=WF_PERIODS[wf][0], to_date=WF_PERIODS[wf][1],
                              model=4, execution_mode=case['execution_mode'],
                              timeout_seconds=max(settings.timeout_seconds, 7200), strategies=(strategy,))
            scenario = replace(_scenario(current, strategy), wf=wf,
                               scenario_id=f'ubs_delay_{cid}',
                               staged_set_name=f'UBS_DELAY_{sid}_{chosen["selected_set_sha256"][:8]}.set')
            output = directory / cid
            _archive_case(output, datetime.now(JST))
            print(f'START [{len(completed)+1}/{len(plan)}] {cid}', flush=True)
            result = case_runner(current, strategy, scenario, output,
                                 executor_factory=_limited_annual_executor_factory,
                                 require_report_covered_inputs=True,
                                 max_generated_fallback_ratio=.0003)
            quality = result.get('tick_data_quality') or {}
            if (result.get('status') != 'success' or not quality.get('passed')
                    or 'fallback_minute_count' not in quality
                    or int(quality['fallback_minute_count'] or 0) > 24):
                _write_manifest(output / 'failed_result.json', result)
                raise ValueError(f'Execution or tick-quality audit failed: {cid}')
            volume = volume_parser(Path(result['report_file'])).to_dict()
            for key in ('minimum_entry_volume', 'maximum_entry_volume'):
                if volume[key] is not None and Decimal(str(volume[key])) != Decimal(chosen['selected_lot']):
                    raise ValueError(f'Fixed lot changed: {cid}')
            base = baselines[(wf, sid)] if case['execution_mode'] == 0 else completed[f'{wf}_{sid}_no_delay']
            result.update(**case, selected_lot=chosen['selected_lot'], entry_volume_audit=volume,
                          comparison=comparison(result, base),
                          dd_warning=float(result['metrics']['equity_drawdown_percent']) > 7.5)
            if case['execution_mode'] == 0 and not result['comparison']['reproduction_passed']:
                _write_manifest(output / 'failed_result.json', result)
                raise ValueError(f'No Delay reproduction failed: {cid}')
            _write_manifest(output / 'result.json', result)
            manifest['results'].append(dict(case_id=cid, result_sha256=sha256_file(output / 'result.json')))
            completed[cid] = result
            count += 1
            _write_manifest(manifest_path, manifest)
            rows = [dict(case_id=r['case_id'], wf=r['wf'], strategy_id=r['strategy_id'],
                         execution_mode=r['execution_mode'], repetition=r['repetition'],
                         **r['metrics'], **r['comparison']) for r in completed.values()]
            _write_csv(output / 'cumulative_summary.csv', rows)
            print(f'PASS [{len(completed)}/{len(plan)}] {cid} NP={result["metrics"]["net_profit"]}', flush=True)
        else:
            manifest['status'] = 'success'
    except BaseException as exc:
        manifest.update(status='failed', error=str(exc))
        raise
    finally:
        manifest['updated_at'] = datetime.now(JST).isoformat()
        _write_manifest(manifest_path, manifest)
        print(f'RUN {directory} STATUS {manifest["status"]}', flush=True)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config/ubs_gold_smoke.json'))
    parser.add_argument('--baseline-run', type=Path, required=True)
    parser.add_argument('--resume-run', type=Path)
    parser.add_argument('--max-cases', type=int)
    parser.add_argument('--random-repeats', type=int, default=3)
    args = parser.parse_args()
    run(load_ubs_smoke_settings(args.config), args.baseline_run,
        resume=args.resume_run, max_cases=args.max_cases, repeats=args.random_repeats)


if __name__ == '__main__':
    main()

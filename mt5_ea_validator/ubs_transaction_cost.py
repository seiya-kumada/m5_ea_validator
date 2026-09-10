"""UBS zero-spread-stress pilot using the existing audited execution pipeline."""
from __future__ import annotations

import argparse
import re
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from mt5_ea_validator.configuration import WF_PERIODS
from mt5_ea_validator.mt5 import MT5Executor, run_below_normal_four_cpus, sha256_file
from mt5_ea_validator.report import parse_entry_volume_audit, _TableCellParser, _decode_report
from mt5_ea_validator.transaction_cost import (
    CustomSymbolBuilder, CostStressExecutor, load_build_index,
    _manifest_covers_scenario, parse_symbol_build_manifest, validate_symbol_build_manifest,
)
from mt5_ea_validator.ubs_execution_delay import comparison
from mt5_ea_validator.ubs_risk_sensitivity import (
    _load_json, _load_same_risk_quarterly_selection, _run_id, _write_manifest,
)
from mt5_ea_validator.ubs_smoke import (
    JST, UBSStrategy, _scenario, execute_ubs_case, load_ubs_smoke_settings,
)


def load_baseline(settings, baseline_run):
    source = baseline_run.resolve() / 'run_manifest.json'
    baseline = _load_json(source, label='quarterly baseline')
    expected = dict(symbol=settings.symbol, period=settings.period, model=4,
                    execution_mode=0, deposit=settings.deposit,
                    currency=settings.currency, leverage=settings.leverage)
    if baseline.get('status') != 'success' or any(
            baseline['conditions'].get(k) != v for k, v in expected.items()):
        raise ValueError('Baseline status or conditions differ')
    selection = Path(baseline['selection_file'])
    if sha256_file(selection) != baseline['selection_file_sha256']:
        raise ValueError('Selection changed')
    _, selected = _load_same_risk_quarterly_selection(
        settings, selection, Path(baseline['faithful_sets_manifest']),
        Path(baseline['faithful_validation_run']))
    rows = {(r['wf'], r['strategy_id']): r for r in baseline['results']}
    if len(rows) != 28 or len(baseline['results']) != 28:
        raise ValueError('Expected 28 unique baseline cases')
    for chosen in selected:
        for wf, dates in WF_PERIODS.items():
            row = rows[(wf, chosen['strategy_id'])]
            if (row['status'] != 'success'
                    or row['selected_set_sha256'] != chosen['selected_set_sha256']
                    or (row['from_date'], row['to_date']) != dates):
                raise ValueError('Baseline case identity differs')
    return source, selected, rows


def zero_comparison(result, baseline):
    audit = comparison(result, baseline)
    rounded = {}
    for key in ('profit_factor', 'recovery_factor', 'sharpe_ratio'):
        a, b = result['metrics'].get(key), baseline['metrics'].get(key)
        rounded[key] = (a == b if a is None or b is None
                        else abs(float(a) - float(b)) <= .010000001)
    audit['rounded_metrics_match'] = rounded
    audit['reproduction_passed'] &= all(rounded.values())
    audit['accounting_deltas'] = {
        key: float(result['deal_audit'][key]) - float(baseline['deal_audit'][key])
        for key in ('commission_total', 'swap_total', 'deal_profit_total')}
    return audit


def deal_symbol_audit(path, expected_symbol, expected_count, *, allow_empty=False):
    parser = _TableCellParser()
    parser.feed(_decode_report(path.read_bytes()))
    symbols = [r[2] for r in parser.rows if len(r) >= 13
               and re.fullmatch(r'\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}', r[0])
               and r[3].casefold() in ('buy', 'sell')
               and r[4].casefold() in ('in', 'out', 'in/out')]
    return dict(symbols=sorted(set(symbols)), deal_count=len(symbols),
                passed=len(symbols) == expected_count and (set(symbols) == {expected_symbol}
                       or (allow_empty and not symbols and expected_count == 0)))


def refresh_gate(audit):
    """Permit the approved swap refresh, but no unrelated execution changes."""
    return (audit['deal_sequence_same'] and audit['trades_delta'] == 0
            and all(abs(audit['accounting_deltas'][key]) <= .010000001
                    for key in ('commission_total', 'deal_profit_total')))


def pilot(settings, baseline_run, build_index, *, strategy_id='xau_sr_scalp_h1',
          wf='WF1', source_control=False, source_refresh=False, case_runner=execute_ubs_case,
          volume_parser=parse_entry_volume_audit, stress_points=0, reference_result=None,
          source_zero=None):
    if source_refresh and not source_control:
        raise ValueError('Only the normal source symbol can refresh the baseline')
    source, selected, rows = load_baseline(settings, baseline_run)
    builds = load_build_index(build_index)
    matching = [b for b in builds if b.stress_points == stress_points]
    if len(matching) != 1 or stress_points not in (0, 2, 5, 10):
        raise ValueError('Expected exactly one build for a supported stress level')
    if stress_points and (source_control or reference_result is None or source_zero is None):
        raise ValueError('Positive stress requires the verified S=0 reference')
    build = matching[0]
    tested_symbol = settings.symbol if source_control else build.custom_symbol
    chosen = next(s for s in selected if s['strategy_id'] == strategy_id)
    set_file = Path(chosen['selected_set_file'])
    strategy = UBSStrategy(strategy_id=strategy_id, file_name=set_file.name)
    current = replace(settings, set_directory=set_file.parent,
                      from_date=WF_PERIODS[wf][0], to_date=WF_PERIODS[wf][1],
                      symbol=tested_symbol, model=4, execution_mode=0,
                      timeout_seconds=7200, strategies=(strategy,))
    scenario = replace(_scenario(current, strategy), wf=wf,
                       scenario_id=f'ubs_tcs_pilot_{wf}_{strategy_id}',
                       staged_set_name=f'UBS_TCS_{strategy_id}_{chosen["selected_set_sha256"][:8]}.set')
    expected_source = source_zero.custom_symbol if stress_points else settings.symbol
    if build.source_symbol != expected_source or not _manifest_covers_scenario(build, scenario):
        raise ValueError('Build source or date coverage differs')
    directory = settings.output_root.parent / 'transaction_cost' / _run_id(datetime.now(JST))
    directory.mkdir(parents=True, exist_ok=False)
    manifest = dict(status='running', phase='source control' if source_control else f'S{stress_points} test', strategy_id=strategy_id, wf=wf,
                    baseline_manifest=str(source), baseline_sha256=sha256_file(source),
                    build_index=str(build_index.resolve()), build_sha256=sha256_file(build_index),
                    build=build.to_dict(), selected=chosen,
                    ea_sha256=sha256_file(settings.expert_binary),
                    resource_limits=dict(priority='BelowNormal', logical_cpu_count=4),
                    started_at=datetime.now(JST).isoformat())
    path = directory / 'run_manifest.json'
    _write_manifest(path, manifest)
    print(f'START {manifest["phase"]} {wf}/{strategy_id} {directory}', flush=True)
    try:
        result = case_runner(current, strategy, scenario, directory / 'case',
            executor_factory=lambda s: (MT5Executor(s, process_runner=run_below_normal_four_cpus)
                if source_control else CostStressExecutor(
                    s, stress_points=stress_points, process_runner=run_below_normal_four_cpus)),
            require_report_covered_inputs=True, max_generated_fallback_ratio=.0003)
        _write_manifest(directory / 'result.json', result)
        quality = result.get('tick_data_quality') or {}
        volume = volume_parser(Path(result['report_file'])).to_dict()
        lot_ok = all(volume[k] is not None and Decimal(str(volume[k])) == Decimal(chosen['selected_lot'])
                     for k in ('minimum_entry_volume', 'maximum_entry_volume'))
        if (stress_points and result['metrics']['trades'] == 0
                and volume['entry_deal_count'] == 0 and result['deal_audit']['deal_count'] == 0):
            lot_ok = True
        result.update(comparison=zero_comparison(result, reference_result or rows[(wf, strategy_id)]),
                      entry_volume_audit=volume, selected_lot=chosen['selected_lot'],
                      deal_symbol_audit=deal_symbol_audit(Path(result['report_file']),
                          tested_symbol, result['deal_audit']['deal_count'],
                          allow_empty=bool(stress_points) and result['metrics']['trades'] == 0))
        result.update(source_refresh=source_refresh, refresh_gate_passed=refresh_gate(result['comparison']),
                      wf=wf, from_date=current.from_date, to_date=current.to_date,
                      selected_set_sha256=chosen['selected_set_sha256'], stress_points=stress_points,
                      comparison_basis='S0' if stress_points else 'normal symbol',
                      no_trades=result['metrics']['trades'] == 0)
        _write_manifest(directory / 'result.json', result)
        passed = (result['status'] == 'success' and quality.get('passed')
                  and (quality.get('fallback_minute_count') is not None
                       or quality.get('fallback_warning_count') == 0)
                  and int(quality.get('fallback_minute_count') or 0) <= 24
                  and lot_ok and (True if stress_points else result['refresh_gate_passed'] if source_refresh
                                  else result['comparison']['reproduction_passed'])
                  and result['deal_symbol_audit']['passed'])
        manifest.update(status='success' if passed else 'failed', result=result)
        if not passed:
            raise ValueError('S=0 pilot gate failed; positive stress remains blocked')
    except BaseException as exc:
        manifest.update(status='failed', error=str(exc))
        raise
    finally:
        manifest['finished_at'] = datetime.now(JST).isoformat()
        _write_manifest(path, manifest)
        print(f'FINISH {manifest["status"]}: {directory}', flush=True)
    return directory


def verified_zero(settings, baseline_run, zero_run, zero_index):
    source, selected, _ = load_baseline(settings, baseline_run)
    manifest_path = zero_run / 'run_manifest.json'
    manifest = _load_json(manifest_path, label='S0 gate')
    plan = [dict(wf=wf, strategy_id=s['strategy_id']) for wf in WF_PERIODS for s in selected]
    if (manifest['status'] != 'success' or manifest.get('source_refresh') is not False
            or manifest['plan'] != plan or len(manifest['results']) != 28
            or manifest['baseline_sha256'] != sha256_file(source)
            or manifest['build_sha256'] != sha256_file(zero_index)
            or manifest['ea_sha256'] != sha256_file(settings.expert_binary)):
        raise ValueError('All 28 matching S=0 gates must pass first')
    references = {}
    for case, record in zip(plan, manifest['results'], strict=True):
        path = Path(record['result_file'])
        if record['case'] != case or sha256_file(path) != record['result_sha256']:
            raise ValueError('S0 result identity changed')
        row = _load_json(path, label='S0 result')
        if (not row['comparison']['reproduction_passed'] or row['status'] != 'success'
                or not row['deal_symbol_audit']['passed']):
            raise ValueError('S0 result audit failed')
        references[(case['wf'], case['strategy_id'])] = row
    builds = load_build_index(zero_index)
    if len(builds) != 1 or builds[0].stress_points != 0:
        raise ValueError('Expected single zero build')
    return builds[0], references


def validate_positive_builds(zero, builds):
    if tuple(b.stress_points for b in builds) != (2, 5, 10):
        raise ValueError('Expected S=2,5,10 builds')
    for build in builds:
        if (build.source_symbol != zero.custom_symbol
                or build.source_tick_audit_fnv1a64 != zero.persisted_tick_audit_fnv1a64
                or build.source_m1_audit_fnv1a64 != zero.persisted_m1_audit_fnv1a64
                or build.source_tick_count != zero.persisted_tick_count
                or build.source_m1_bar_count != zero.persisted_m1_bar_count
                or (build.from_msc, build.to_msc_exclusive, build.point, build.tick_size)
                   != (zero.from_msc, zero.to_msc_exclusive, zero.point, zero.tick_size)):
            raise ValueError('Positive build does not derive from the frozen S0 data')


def positive_suite(settings, baseline_run, zero_run, zero_index, positive_index,
                   *, resume=None, max_cases=None):
    zero, references = verified_zero(settings, baseline_run, zero_run, zero_index)
    validate_positive_builds(zero, load_build_index(positive_index))
    plan = [dict(wf=wf, strategy_id=sid, stress_points=stress)
            for stress in (2, 5, 10) for wf, sid in references]
    identity = dict(plan=plan, zero_manifest_sha256=sha256_file(zero_run / 'run_manifest.json'),
                    positive_build_sha256=sha256_file(positive_index),
                    ea_sha256=sha256_file(settings.expert_binary))
    root = settings.output_root.parent / 'transaction_cost'
    directory = resume.resolve() if resume else root / ('positive_suite_' + _run_id(datetime.now(JST)))
    if resume and directory.parent != root.resolve():
        raise ValueError('Invalid resume location')
    directory.mkdir(parents=True, exist_ok=resume is not None)
    path = directory / 'run_manifest.json'
    manifest = _load_json(path, label='positive suite') if resume else dict(**identity, results=[],
        zero_run=str(zero_run.resolve()), positive_index=str(positive_index.resolve()))
    if any(manifest.get(k) != v for k, v in identity.items()):
        raise ValueError('Resume identity changed')
    for i, record in enumerate(manifest['results']):
        if record['case'] != plan[i] or sha256_file(Path(record['result_file'])) != record['result_sha256']:
            raise ValueError('Completed stress result changed')
    manifest.update(status='running', error=None)
    _write_manifest(path, manifest)
    count = 0
    print(f'POSITIVE SUITE {directory}', flush=True)
    try:
        for case in plan[len(manifest['results']):]:
            if (directory / 'STOP_AFTER_CASE').exists() or (max_cases is not None and count >= max_cases):
                manifest['status'] = 'paused'
                break
            manifest.update(active_case=case, updated_at=datetime.now(JST).isoformat())
            _write_manifest(path, manifest)
            output = pilot(settings, baseline_run, positive_index, **case, source_zero=zero,
                           reference_result=references[(case['wf'], case['strategy_id'])])
            result_file = output / 'result.json'
            manifest['results'].append(dict(case=case, result_file=str(result_file),
                                            result_sha256=sha256_file(result_file)))
            manifest.update(active_case=None, updated_at=datetime.now(JST).isoformat())
            _write_manifest(path, manifest)
            count += 1
            print(f'STRESS COMPLETE {len(manifest["results"])}/84', flush=True)
        else:
            manifest['status'] = 'success'
    except BaseException as exc:
        manifest.update(status='failed', error=str(exc))
        raise
    finally:
        manifest['updated_at'] = datetime.now(JST).isoformat()
        _write_manifest(path, manifest)
        print(f'POSITIVE FINISH {manifest["status"]}: {directory}', flush=True)
    return directory


def merge_positive_indexes(settings, zero, indexes):
    """Adopt only individually verified levels; preserve failed parent runs."""
    items, builds, provenance = [], [], []
    for stress in (2, 5, 10):
        source = indexes[stress].resolve()
        raw = _load_json(source, label='source build index')
        matches = [item for item in raw.get('symbols', []) if item['stress_points'] == stress]
        if raw.get('schema_version') != 2 or len(matches) != 1:
            raise ValueError('Expected one recorded build for each selected level')
        item = matches[0]
        artifact = Path(item['artifact_manifest'])
        build = parse_symbol_build_manifest(artifact)
        validate_symbol_build_manifest(build, source_symbol=zero.custom_symbol, stress_points=stress)
        if any(item.get(k) != v for k, v in build.to_dict().items() if k != 'artifact_manifest'):
            raise ValueError('Build index differs from its artifact')
        items.append(item)
        builds.append(build)
        provenance.append(dict(stress_points=stress, index=str(source), index_sha256=sha256_file(source),
                               parent_status=raw['status'], artifact_sha256=sha256_file(artifact)))
    validate_positive_builds(zero, builds)
    directory = settings.project_root / 'data' / 'transaction_cost_stress' / 'symbol_builds' / ('merged_' + _run_id(datetime.now(JST)))
    directory.mkdir(parents=True, exist_ok=False)
    output = directory / 'build_index.json'
    _write_manifest(output, dict(schema_version=2, status='success', symbols=items,
        provenance=provenance, note='Only the listed successful, cross-validated levels are adopted; parent runs remain unchanged'))
    load_build_index(output)
    return output


def zero_suite(settings, baseline_run, build_index, *, resume=None, max_cases=None, source_refresh=False):
    source, selected, _ = load_baseline(settings, baseline_run)
    plan = [dict(wf=wf, strategy_id=s['strategy_id']) for wf in WF_PERIODS for s in selected]
    identity = dict(baseline_sha256=sha256_file(source), build_sha256=sha256_file(build_index),
                    ea_sha256=sha256_file(settings.expert_binary), plan=plan, source_refresh=source_refresh)
    root = settings.output_root.parent / 'transaction_cost'
    prefix = 'source_refresh_' if source_refresh else 'zero_suite_'
    directory = resume.resolve() if resume else root / (prefix + _run_id(datetime.now(JST)))
    if resume and directory.parent != root.resolve():
        raise ValueError('Resume must be a transaction_cost run directory')
    directory.mkdir(parents=True, exist_ok=resume is not None)
    path = directory / 'run_manifest.json'
    manifest = _load_json(path, label='zero suite') if resume else dict(**identity, results=[])
    if any(manifest.get(k) != v for k, v in identity.items()):
        raise ValueError('Resume identity changed')
    for i, record in enumerate(manifest['results']):
        if record['case'] != plan[i] or sha256_file(Path(record['result_file'])) != record['result_sha256']:
            raise ValueError('Completed result changed')
    manifest.update(status='running', error=None)
    _write_manifest(path, manifest)
    print(f'SUITE {directory} completed={len(manifest["results"])}/28', flush=True)
    count = 0
    try:
        for case in plan[len(manifest['results']):]:
            if (directory / 'STOP_AFTER_CASE').exists() or (max_cases is not None and count >= max_cases):
                manifest['status'] = 'paused'
                break
            manifest.update(active_case=case, updated_at=datetime.now(JST).isoformat())
            _write_manifest(path, manifest)
            output = pilot(settings, baseline_run, build_index, **case,
                           source_control=source_refresh, source_refresh=source_refresh)
            result_file = output / 'result.json'
            manifest['results'].append(dict(case=case, result_file=str(result_file),
                                            result_sha256=sha256_file(result_file)))
            manifest.update(active_case=None, updated_at=datetime.now(JST).isoformat())
            _write_manifest(path, manifest)
            count += 1
            print(f'{"SOURCE" if source_refresh else "ZERO"} PASS {len(manifest["results"])}/28', flush=True)
        else:
            manifest['status'] = 'success'
            if source_refresh:
                old = _load_json(source, label='previous baseline')
                refreshed = dict(old, status='success', run_id=directory.name,
                    results=[_load_json(Path(r['result_file']), label='refreshed result')
                             for r in manifest['results']],
                    previous_baseline_manifest=str(source), previous_baseline_sha256=sha256_file(source),
                    purpose='Current baseline for UBS transaction-cost stress; old results preserved',
                    started_at_jst=None, finished_at_jst=datetime.now(JST).isoformat())
                export = directory / 'refreshed_baseline'
                export.mkdir(exist_ok=True)
                _write_manifest(export / 'run_manifest.json', refreshed)
                manifest['refreshed_baseline'] = str(export)
    except BaseException as exc:
        manifest.update(status='failed', error=str(exc))
        raise
    finally:
        manifest['updated_at'] = datetime.now(JST).isoformat()
        _write_manifest(path, manifest)
        print(f'SUITE FINISH {manifest["status"]}: {directory}', flush=True)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build-zero', 'pilot', 'zero-suite', 'source-control', 'source-refresh', 'build-positive', 'positive-suite', 'build-head-check', 'merge-positive'))
    parser.add_argument('--config', type=Path, default=Path('config/ubs_gold_smoke.json'))
    parser.add_argument('--baseline-run', type=Path, required=True)
    parser.add_argument('--build-index', type=Path)
    parser.add_argument('--resume-run', type=Path)
    parser.add_argument('--max-cases', type=int)
    parser.add_argument('--strategy', default='xau_sr_scalp_h1')
    parser.add_argument('--zero-run', type=Path)
    parser.add_argument('--positive-index', type=Path)
    parser.add_argument('--previous-positive-index', type=Path)
    parser.add_argument('--stress-points', nargs='+', type=int, choices=(2, 5, 10), default=(2, 5, 10))
    args = parser.parse_args()
    settings = load_ubs_smoke_settings(args.config)
    if args.action == 'build-zero':
        load_baseline(settings, args.baseline_run)
        builder = CustomSymbolBuilder(_scenario(settings, settings.strategies[0]),
                                      process_runner=run_below_normal_four_cpus)
        print(builder.build(stress_points=(0,)), flush=True)
    else:
        if args.build_index is None:
            parser.error('--build-index is required for pilot')
        if args.action in ('build-positive', 'positive-suite', 'build-head-check', 'merge-positive'):
            if args.zero_run is None:
                parser.error('--zero-run is required')
            zero, _ = verified_zero(settings, args.baseline_run, args.zero_run, args.build_index)
            if args.action in ('build-positive', 'build-head-check'):
                current = replace(settings, symbol=zero.custom_symbol)
                builder = CustomSymbolBuilder(_scenario(current, current.strategies[0]),
                                              process_runner=run_below_normal_four_cpus,
                                              reference_tick_size=zero.tick_size,
                                              reference_first_m1_time=zero.first_m1_time)
                if args.action == 'build-head-check':
                    print(builder.build(stress_points=(5,), from_date='2024.01.01', to_date_exclusive='2024.01.04'), flush=True)
                else:
                    print(builder.build(stress_points=args.stress_points), flush=True)
            elif args.action == 'merge-positive':
                if args.positive_index is None or args.previous_positive_index is None:
                    parser.error('Both positive indexes are required')
                print(merge_positive_indexes(settings, zero,
                    {2: args.previous_positive_index, 5: args.positive_index, 10: args.previous_positive_index}), flush=True)
            else:
                if args.positive_index is None:
                    parser.error('--positive-index is required')
                positive_suite(settings, args.baseline_run, args.zero_run, args.build_index,
                               args.positive_index, resume=args.resume_run, max_cases=args.max_cases)
        elif args.action in ('zero-suite', 'source-refresh'):
            zero_suite(settings, args.baseline_run, args.build_index,
                       resume=args.resume_run, max_cases=args.max_cases,
                       source_refresh=args.action == 'source-refresh')
        else:
            pilot(settings, args.baseline_run, args.build_index,
                  strategy_id=args.strategy, source_control=args.action == 'source-control')


if __name__ == '__main__':
    main()

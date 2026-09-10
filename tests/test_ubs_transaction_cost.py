import unittest
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mt5_ea_validator import ubs_transaction_cost as cost
from mt5_ea_validator.ubs_smoke import load_ubs_smoke_settings
from mt5_ea_validator.transaction_cost import parse_symbol_build_manifest, render_symbol_builder_set, TransactionCostError
from tests.test_transaction_cost import _manifest_text
from tests.test_ubs_risk_sensitivity import _report
from mt5_ea_validator.ubs_transaction_cost import zero_comparison


def result(np=100, pf=1.25, digest='same'):
    return dict(metrics=dict(net_profit=np, trades=10, equity_drawdown_percent=3,
                            profit_factor=pf, recovery_factor=1, sharpe_ratio=2),
                deal_audit=dict(deal_sequence_sha256=digest, commission_total=-2,
                                swap_total=-1, deal_profit_total=np+3))


class CostPilotTests(unittest.TestCase):
    def test_merge_checks_artifact_and_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / 'manifest.txt'
            artifact.write_text(_manifest_text())
            zero = parse_symbol_build_manifest(artifact)
            settings = replace(load_ubs_smoke_settings(Path('config/ubs_gold_smoke.json')), project_root=root)
            indexes, builds = {}, {}
            for stress in (2, 5, 10):
                build = replace(zero, stress_points=stress, source_symbol=zero.custom_symbol)
                builds[stress] = build
                item = dict(build.to_dict(), artifact_manifest=str(artifact))
                index = root / f'{stress}.json'
                cost._write_manifest(index, dict(schema_version=2, status='success', symbols=[item]))
                indexes[stress] = index
            with patch.object(cost, 'parse_symbol_build_manifest', side_effect=list(builds.values())), \
                    patch.object(cost, 'validate_symbol_build_manifest'), patch.object(cost, 'load_build_index'):
                output = cost.merge_positive_indexes(settings, zero, indexes)
            merged = cost._load_json(output, label='test')
            self.assertEqual([p['stress_points'] for p in merged['provenance']], [2, 5, 10])
            self.assertTrue(all(p['artifact_sha256'] for p in merged['provenance']))
            with patch.object(cost, 'parse_symbol_build_manifest', return_value=replace(builds[2], point=.1)), \
                    patch.object(cost, 'validate_symbol_build_manifest'):
                with self.assertRaisesRegex(ValueError, 'differs from its artifact'):
                    cost.merge_positive_indexes(settings, zero, indexes)

    def test_reference_tick_size_is_explicit_and_validated(self):
        kwargs = dict(source_symbol='XAUUSD_TCS0_test', custom_symbol='XAUUSD_TCS2_test',
                      from_date='2024.01.01', to_date_exclusive='2026.07.01',
                      stress_points=2, output_file=Path('manifest.txt'))
        self.assertNotIn('InpReferenceTickSize', render_symbol_builder_set(**kwargs))
        self.assertIn('InpReferenceTickSize=0.01', render_symbol_builder_set(**kwargs, reference_tick_size=.01))
        self.assertIn('InpReferenceFirstM1Time=1704157200', render_symbol_builder_set(**kwargs, reference_first_m1_time=1704157200))
        for value in (0, -1, 1.5, True):
            with self.assertRaises(TransactionCostError):
                render_symbol_builder_set(**kwargs, reference_first_m1_time=value)
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(TransactionCostError):
                render_symbol_builder_set(**kwargs, reference_tick_size=value)

    def test_positive_build_requires_frozen_zero_source(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'manifest.txt'
            file.write_text(_manifest_text())
            zero = parse_symbol_build_manifest(file)
            builds = [replace(zero, stress_points=s, source_symbol=zero.custom_symbol) for s in (2, 5, 10)]
            cost.validate_positive_builds(zero, builds)
            builds[0] = replace(builds[0], source_symbol='XAUUSD')
            with self.assertRaisesRegex(ValueError, 'frozen S0'):
                cost.validate_positive_builds(zero, builds)
            with self.assertRaisesRegex(ValueError, 'S=2,5,10'):
                cost.validate_positive_builds(zero, builds[:1])

    def test_empty_deals_only_allowed_for_positive_stress(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'report.htm'
            file.write_text('<table></table>')
            self.assertFalse(cost.deal_symbol_audit(file, 'CUSTOM', 0)['passed'])
            self.assertTrue(cost.deal_symbol_audit(file, 'CUSTOM', 0, allow_empty=True)['passed'])
            self.assertFalse(cost.deal_symbol_audit(file, 'CUSTOM', 1, allow_empty=True)['passed'])

    def test_positive_suite_waits_for_zero_gate_and_resumes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'run_manifest.json'
            source.write_text('{}')
            build_file = root / 'build.txt'
            build_file.write_text(_manifest_text())
            zero = parse_symbol_build_manifest(build_file)
            settings = replace(load_ubs_smoke_settings(Path('config/ubs_gold_smoke.json')),
                               expert_binary=source, output_root=root / 'smoke')
            references = {(wf, s.strategy_id): result() for wf in cost.WF_PERIODS for s in settings.strategies}
            calls = []
            def fake(settings, baseline, build, **case):
                calls.append(case)
                output = root / ('result' + str(len(calls)))
                output.mkdir()
                (output / 'result.json').write_text('{}')
                return output
            with patch.object(cost, 'verified_zero', side_effect=ValueError('not ready')), \
                    patch.object(cost, 'pilot') as execute:
                with self.assertRaisesRegex(ValueError, 'not ready'):
                    cost.positive_suite(settings, root, root, source, source)
                execute.assert_not_called()
            with patch.object(cost, 'verified_zero', return_value=(zero, references)), \
                    patch.object(cost, 'load_build_index', return_value=()), \
                    patch.object(cost, 'validate_positive_builds'), \
                    patch.object(cost, 'pilot', side_effect=fake), patch('builtins.print'):
                run = cost.positive_suite(settings, root, root, source, source, max_cases=2)
                manifest = cost._load_json(run / 'run_manifest.json', label='test')
                self.assertEqual(len(manifest['plan']), 84)
                self.assertEqual(manifest['status'], 'paused')
                cost.positive_suite(settings, root, root, source, source, resume=run, max_cases=1)
                self.assertEqual(len(calls), 3)
                (root / 'result1' / 'result.json').write_text('tampered')
                with self.assertRaisesRegex(ValueError, 'Completed stress result changed'):
                    cost.positive_suite(settings, root, root, source, source, resume=run)

    def test_refresh_accepts_swap_only_not_execution_changes(self):
        updated = result(np=99)
        updated['deal_audit']['swap_total'] = -2
        updated['deal_audit']['deal_profit_total'] = 103
        audit = cost.zero_comparison(updated, result())
        self.assertFalse(audit['reproduction_passed'])
        self.assertTrue(cost.refresh_gate(audit))
        updated['deal_audit']['commission_total'] = -3
        self.assertFalse(cost.refresh_gate(cost.zero_comparison(updated, result())))
        with self.assertRaisesRegex(ValueError, 'Only the normal'):
            cost.pilot(None, None, None, source_refresh=True)

    def test_zero_suite_resume_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source.json'
            source.write_text('{}')
            settings = replace(load_ubs_smoke_settings(Path('config/ubs_gold_smoke.json')),
                               expert_binary=source, output_root=root / 'smoke')
            selected = [dict(strategy_id=s.strategy_id) for s in settings.strategies]
            calls = []
            def fake(settings, baseline, build, **case):
                calls.append(case)
                output = root / ('case' + str(len(calls)))
                output.mkdir()
                (output / 'result.json').write_text('{}')
                return output
            with patch.object(cost, 'load_baseline', return_value=(source, selected, {})), \
                    patch.object(cost, 'pilot', side_effect=fake):
                run = cost.zero_suite(settings, root, source, max_cases=2)
                self.assertEqual(len(calls), 2)
                cost.zero_suite(settings, root, source, resume=run, max_cases=1)
                self.assertEqual(len(calls), 3)
                (root / 'case1' / 'result.json').write_text('changed')
                with self.assertRaisesRegex(ValueError, 'Completed result changed'):
                    cost.zero_suite(settings, root, source, resume=run)

    def test_refresh_exports_a_separate_complete_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'old.json'
            source.write_text('{"original": true}')
            settings = replace(load_ubs_smoke_settings(Path('config/ubs_gold_smoke.json')),
                               expert_binary=source, output_root=root / 'smoke')
            selected = [dict(strategy_id=s.strategy_id) for s in settings.strategies]
            def fake(settings, baseline, build, **case):
                self.assertTrue(case['source_control'])
                self.assertTrue(case['source_refresh'])
                output = root / (case['wf'] + '_' + case['strategy_id'])
                output.mkdir()
                cost._write_manifest(output / 'result.json', dict(status='success', **case))
                return output
            with patch.object(cost, 'load_baseline', return_value=(source, selected, {})), \
                    patch.object(cost, 'pilot', side_effect=fake), patch('builtins.print'):
                run = cost.zero_suite(settings, root, source, source_refresh=True)
            exported = cost._load_json(run / 'refreshed_baseline' / 'run_manifest.json', label='test')
            self.assertEqual(len(exported['results']), 28)
            self.assertEqual(exported['status'], 'success')
            self.assertEqual(exported['previous_baseline_sha256'], cost.sha256_file(source))
            self.assertEqual(source.read_text(), '{"original": true}')

    def test_pilot_persists_gate_and_keeps_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'baseline.json'
            source.write_text('{}')
            set_file = root / 'selected.set'
            set_file.write_text('StartLots=0.03')
            build_file = root / 'build.txt'
            build_file.write_text(_manifest_text())
            build = parse_symbol_build_manifest(build_file)
            settings = replace(load_ubs_smoke_settings(Path('config/ubs_gold_smoke.json')),
                               expert_binary=source, output_root=root / 'smoke')
            chosen = dict(strategy_id='xau_sr_scalp_h1', selected_lot='0.03',
                          selected_set_file=str(set_file), selected_set_sha256=cost.sha256_file(set_file))
            source_control = False
            def fake(current, strategy, scenario, output, **kwargs):
                self.assertEqual(current.symbol, 'XAUUSD' if source_control else build.custom_symbol)
                self.assertEqual(current.execution_mode, 0)
                self.assertEqual(current.deposit, 3000)
                self.assertEqual(scenario.set_source, set_file)
                executor = kwargs['executor_factory'](scenario)
                if source_control:
                    self.assertIs(type(executor), cost.MT5Executor)
                else:
                    self.assertEqual(executor.stress_points, 0)
                output.mkdir()
                html = output / 'report.htm'
                html.write_text(_report('0.03').replace('XAUUSD', current.symbol), encoding='utf-8')
                sample = result()
                sample['deal_audit']['deal_count'] = 2
                return dict(**sample, status='success', report_file=str(html),
                            tick_data_quality=dict(passed=True, fallback_minute_count=24))
            with patch.object(cost, 'load_baseline', return_value=(source, [chosen],
                    {('WF1', 'xau_sr_scalp_h1'): result()})), \
                    patch.object(cost, 'load_build_index', return_value=(build,)):
                output = cost.pilot(settings, root, build_file, case_runner=fake)
                self.assertEqual(cost._load_json(output / 'run_manifest.json', label='test')['status'], 'success')
                self.assertTrue((output / 'result.json').is_file())
                source_control = True
                control = cost.pilot(settings, root, build_file, source_control=True, case_runner=fake)
                self.assertEqual(cost._load_json(control / 'run_manifest.json', label='test')['phase'], 'source control')

    def test_zero_gate_and_accounting(self):
        audit = zero_comparison(result(100.07), result())
        self.assertTrue(audit['reproduction_passed'])
        self.assertEqual(audit['accounting_note'], 'minor accounting difference')
        self.assertAlmostEqual(audit['accounting_deltas']['deal_profit_total'], .07)

    def test_rounding_and_sequence_gate(self):
        self.assertTrue(zero_comparison(result(pf=1.26), result())['reproduction_passed'])
        for changed in (result(pf=1.28), result(digest='different'), result(np=101)):
            self.assertFalse(zero_comparison(changed, result())['reproduction_passed'])


if __name__ == '__main__':
    unittest.main()

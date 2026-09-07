import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mt5_ea_validator import ubs_execution_delay as delay
from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.ubs_smoke import load_ubs_smoke_settings
from tests.test_ubs_risk_sensitivity import _report


class DelayTests(unittest.TestCase):
    def test_plan_and_gate(self):
        plan = delay.case_plan()
        self.assertEqual(len(plan), 40)
        self.assertEqual([c['execution_mode'] for c in plan[:5]], [0, 188, -1, -1, -1])
        base = dict(metrics=dict(net_profit=10, trades=2, equity_drawdown_percent=5),
                    deal_audit=dict(deal_sequence_sha256='abc'))
        current = copy.deepcopy(base)
        current['metrics']['net_profit'] = 10.07
        self.assertTrue(delay.comparison(current, base)['reproduction_passed'])
        current['deal_audit']['deal_sequence_sha256'] = 'changed'
        self.assertFalse(delay.comparison(current, base)['reproduction_passed'])

    def test_serial_resume_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / 'ea.ex5'
            binary.write_bytes(b'ea')
            settings = replace(load_ubs_smoke_settings(Path('config/ubs_gold_smoke.json')),
                               expert_binary=binary, output_root=root / 'smoke')
            selected = []
            results = []
            for sid in delay.STRATEGIES:
                file = root / f'{sid}.set'
                file.write_text('Risk=0')
                selected.append(dict(strategy_id=sid, selected_lot='0.03',
                                     selected_set_file=str(file), selected_set_sha256=sha256_file(file)))
                for wf, dates in delay.WF_PERIODS.items():
                    results.append(dict(status='success', wf=wf, strategy_id=sid,
                        from_date=dates[0], to_date=dates[1], selected_set_sha256=sha256_file(file),
                        metrics=dict(net_profit=10, trades=1, equity_drawdown_percent=5),
                        deal_audit=dict(deal_sequence_sha256='abc')))
            selection = root / 'selection.json'
            selection.write_text('{}')
            baseline = root / 'baseline'
            baseline.mkdir()
            delay._write_manifest(baseline / 'run_manifest.json', dict(status='success',
                conditions=dict(symbol=settings.symbol, period=settings.period, model=4,
                    execution_mode=0, deposit=3000, currency='USD', leverage=500),
                selection_file=str(selection), selection_file_sha256=sha256_file(selection),
                faithful_sets_manifest='unused', faithful_validation_run='unused', results=results))
            calls = []
            def fake(current, strategy, scenario, output, **kwargs):
                calls.append(current.execution_mode)
                output.mkdir()
                report = output / 'report.htm'
                report.write_text(_report('0.03'), encoding='utf-8')
                result = copy.deepcopy(results[0])
                result.update(report_file=str(report), tick_data_quality=dict(passed=True, fallback_minute_count=None))
                return result
            with patch.object(delay, '_load_same_risk_quarterly_selection', return_value=({}, selected)):
                run = delay.run(settings, baseline, max_cases=2, case_runner=fake)
                self.assertEqual(calls, [0, 188])
                delay.run(settings, baseline, resume=run, max_cases=1, case_runner=fake)
                self.assertEqual(calls, [0, 188, -1])
                saved = run / delay.case_plan()[0]['case_id'] / 'result.json'
                saved.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'Completed result changed'):
                    delay.run(settings, baseline, resume=run, max_cases=1, case_runner=fake)

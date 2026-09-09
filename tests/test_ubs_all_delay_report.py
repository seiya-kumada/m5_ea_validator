import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from mt5_ea_validator import ubs_all_delay_report as report
from mt5_ea_validator.ubs_execution_delay import case_plan


def fixture():
    rows=[]
    for case in case_plan(strategies=report.ALL):
        mode=case['execution_mode']
        value=100 if mode==0 else 90 if mode==188 else 100+case['repetition']
        rows.append(dict(**case, selected_lot='0.01', metrics=dict(net_profit=value,
            equity_drawdown_percent=2, trades=10,profit_factor=2,recovery_factor=3,sharpe_ratio=4),
            delta=dict(net_profit_delta=value-100,deal_sequence_same=mode==0,trades_delta=0)))
    rows.sort(key=lambda r:(report.ALL.index(r['strategy_id']),r['wf'],{0:0,188:1,-1:2}[r['execution_mode']],r['repetition']))
    return rows


class AllDelayReportTests(unittest.TestCase):
    def test_delta_legend_omits_absent_baseline_series(self):
        summary = report.summarize(fixture())
        for kind in ('profit', 'delta', 'dd'):
            svg = report.bars(summary, kind)
            self.assertEqual('青：遅延なし' in svg, kind != 'delta')
            self.assertIn('緑：188ms　灰：ランダム', svg)

    def test_summary_uses_mean_per_wf_not_sum_of_all_random_runs(self):
        s=report.summarize(fixture())[0]
        self.assertEqual(s['baseline'],400)
        self.assertEqual(s['fixed'],360)
        self.assertEqual(s['random'],408)
        self.assertEqual(s['random_delta'],8)
        self.assertEqual(s['dd_random'],2)

    def test_merge_rejects_different_environment(self):
        with patch.object(report,'load_results',side_effect=[({'conditions':{'deposit':3000}},[]),({'conditions':{'deposit':1000}},[])]):
            with self.assertRaisesRegex(ValueError,'conditions differ'):
                report.combine(Path('first'),Path('second'))

    def test_svg_elements_fit_canvas(self):
        rows=fixture(); summary=report.summarize(rows)
        for svg in [report.bars(summary,k) for k in ('profit','delta','dd')]+[report.heatmap(rows)]:
            root=ET.fromstring(svg); height=float(root.attrib['height'])
            for elem in root.iter():
                if 'x' in elem.attrib:
                    self.assertLess(float(elem.attrib['x']),1200)
                if 'y' in elem.attrib:
                    self.assertLess(float(elem.attrib['y']),height)
                if elem.tag.endswith('rect'):
                    self.assertGreaterEqual(float(elem.attrib['width']),0)
        self.assertIn('ランダム3回の平均Net Profit − 遅延なしのNet Profit',report.heatmap(rows))

    def test_report_sections_artifacts_and_overwrite_guard(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(report,'combine',return_value=([],fixture())):
            output=Path(temp)/'report.md'
            report.build(Path('first'),Path('remaining'),output)
            text=output.read_text(encoding='utf-8')
            self.assertLess(text.index('## 1. 目的'),text.index('## 2. 結果'))
            self.assertLess(text.index('## 2. 結果'),text.index('## 3. 詳細'))
            self.assertEqual(len(list((output.parent/'assets'/'report').glob('*.svg'))),4)
            with self.assertRaises(FileExistsError):
                report.build(Path('first'),Path('remaining'),output)

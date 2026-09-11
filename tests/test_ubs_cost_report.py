import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from mt5_ea_validator import ubs_cost_report as report


def fixture():
    rows=[]
    for sid in report.STRATEGIES:
        for wf in report.WF_PERIODS:
            for s in report.LEVELS:
                rows.append(dict(strategy_id=sid,wf=wf,stress_points=s,
                    metrics=dict(net_profit=100-s,trades=10,profit_factor=2,recovery_factor=3,
                                 sharpe_ratio=4,equity_drawdown_percent=2+s/10),
                    delta=dict(deal_sequence_same=s==0,trades_delta=0,net_profit_delta=-s),
                    deal_audit=dict(commission_total=-2,swap_total=-1,deal_profit_total=103-s)))
    return rows


class CostReportTests(unittest.TestCase):
    def test_full_grid_rejects_missing_and_duplicate_cases(self):
        rows=fixture()
        report.validate_grid(rows)
        for bad in (rows[:-1],rows+[rows[0]],rows[:-1]+[rows[0]]):
            with self.assertRaisesRegex(ValueError,'112 unique'):
                report.validate_grid(bad)

    def test_summary_uses_sum_profit_but_max_dd(self):
        rows=fixture()
        rows[0]['metrics']['equity_drawdown_percent']=5
        summary=report.summarize(rows)[report.STRATEGIES[0]]
        self.assertEqual(summary[0]['np'],400)
        self.assertEqual(summary[10]['np'],360)
        self.assertEqual(summary[0]['dd'],5)
        self.assertEqual(summary[10]['dd'],3)
        self.assertEqual(summary[10]['same'],0)
        self.assertEqual(summary[0]['same'],4)
        self.assertEqual(summary[10]['trades'],40)

    def test_svg_bounds_labels_and_delta_legend(self):
        rows=fixture(); summary=report.summarize(rows)
        for kind in ('np','delta','dd'):
            svg=report.bars(summary,kind)
            self.assertEqual('青:S=0' in svg,kind!='delta')
            self.check_svg(svg)
        self.assertIn('加工後のNet Profit − S=0のNet Profit',report.bars(summary,'delta'))
        svg=report.heatmap(rows)
        self.assertIn('S=10のNet Profit − S=0のNet Profit',svg)
        self.check_svg(svg)

    def check_svg(self,svg):
        root=ET.fromstring(svg)
        for element in root.iter():
            for key,limit in (('x',1200),('y',float(root.attrib['height']))):
                if key in element.attrib:
                    self.assertGreaterEqual(float(element.attrib[key]),0)
                    self.assertLess(float(element.attrib[key]),limit)
            if element.tag.endswith('rect'):
                self.assertLessEqual(float(element.attrib['x'])+float(element.attrib['width']),1200)

    def test_report_sections_metrics_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(report,'load_rows',return_value=fixture()):
            out=Path(temp)/'test.md'
            report.build(out)
            text=out.read_text(encoding='utf-8')
            self.assertLess(text.index('## 1. 目的'),text.index('## 2. 結果'))
            self.assertLess(text.index('## 2. 結果'),text.index('## 3. 詳細'))
            self.assertEqual(text.count('#### '),7)
            self.assertIn('2025-07-01～2025-09-30',text)
            self.assertIn('約1年間の連続バックテスト',text)
            self.assertEqual(len(list((out.parent/'assets'/'test').glob('*.svg'))),4)
            before=out.read_bytes()
            with self.assertRaises(FileExistsError):
                report.build(out)
            self.assertEqual(out.read_bytes(),before)

    def test_result_tamper_is_rejected_before_html_read(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); result=root/'result.json'; result.write_text('{}')
            index=root/'index.json'; index.write_text('{}')
            manifest=dict(status='success',positive_index=str(index),zero_manifest_sha256='ok',
                          positive_build_sha256='ok',ea_sha256='ok',
                          results=[dict(result_file=str(result),result_sha256='wrong')]*84)
            def fake_read(path):
                return manifest if Path(path).parent==root else dict(results=[])
            with patch.object(report,'load_ubs_smoke_settings',return_value=SimpleNamespace(expert_binary=index)), \
                    patch.object(report,'verified_zero',return_value=(None,{})), \
                    patch.object(report,'load_build_index'),patch.object(report,'validate_positive_builds'), \
                    patch.object(report,'read',side_effect=fake_read),patch.object(report,'sha256_file',return_value='ok'):
                with self.assertRaisesRegex(ValueError,'Result hash changed'):
                    report.load_rows(root)

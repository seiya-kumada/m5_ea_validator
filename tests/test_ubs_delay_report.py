import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from mt5_ea_validator.ubs_delay_report import load_results, chart, groups
from mt5_ea_validator.ubs_execution_delay import case_plan
from mt5_ea_validator.mt5 import sha256_file


class DelayReportTests(unittest.TestCase):
    def test_hash_and_complete_case_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            records=[]
            for case in case_plan():
                folder=root/case['case_id']; folder.mkdir()
                path=folder/'result.json'
                row=dict(**case,status='success',compatibility={'passed':True},tick_data_quality={'passed':True})
                path.write_text(json.dumps(row),encoding='utf-8')
                records.append(dict(case_id=case['case_id'],result_sha256=sha256_file(path)))
            (root/'run_manifest.json').write_text(json.dumps(dict(status='success',plan=case_plan(),results=records)),encoding='utf-8')
            self.assertEqual(len(load_results(root)[1]),40)
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'hash'):
                load_results(root)

    def test_chart_negative_delta_and_groups(self):
        rows=[dict(**c,metrics={'net_profit':100 if c['execution_mode']==0 else 80}) for c in case_plan()]
        self.assertEqual(len(groups(rows)),8)
        svg=chart(rows,'net_profit','test',delta=True)
        self.assertIn('遅延ありのNet Profit − 遅延なしのNet Profit',svg)
        doc=ET.fromstring(svg)
        for node in doc.findall('{http://www.w3.org/2000/svg}rect'):
            self.assertGreaterEqual(float(node.attrib['width']),0)

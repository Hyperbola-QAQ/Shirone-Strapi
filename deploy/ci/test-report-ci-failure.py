#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('report_ci', Path(__file__).with_name('report-ci-failure.py'))
report_ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_ci)


class ReportTests(unittest.TestCase):
    def test_only_known_stage_and_fixed_categories_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'private.log'
            path.write_text('SHIRONE_STAGE=base_pull\nprivate-token-should-never-be-printed\n'
                            'SHIRONE_STAGE=oci_cms\nconnection reset by peer\n'
                            'SHIRONE_STAGE=private-token-should-never-be-printed\n')
            self.assertEqual(report_ci.report(path), {'stage': 'oci_cms', 'categories': ['network_connection']})

    def test_unknown_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'private.log'
            path.write_text('secret-value\n')
            self.assertEqual(report_ci.report(path), {'stage': 'unknown', 'categories': ['unclassified']})


if __name__ == '__main__':
    unittest.main()

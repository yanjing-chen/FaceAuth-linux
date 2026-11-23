"""Regression tests: TEST verdict remains explicit and never becomes generic OK."""
from pathlib import Path
import importlib.util
import sys
import unittest

SRC = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SRC))
from recognition_result import interpret_test_reply


class RecognitionResultTests(unittest.TestCase):
    def test_success(self):
        info = interpret_test_reply('OK mode=dual sim=0.824 face=我 result=accepted', 0.6)
        self.assertEqual(info.state, 'passed')
        self.assertIn('0.824', info.body)
        self.assertIn('我', info.body)
        self.assertIn('RGB + IR', info.body)

    def test_mismatch(self):
        r=interpret_test_reply('DENIED mode=rgb sim=0.321 face=测试 result=mismatch', 0.6)
        self.assertEqual(r.state,'rejected')
        self.assertIn('相似度未达到匹配阈值',r.body)

    def test_liveness(self):
        r=interpret_test_reply('DENIED mode=dual sim=0.881 face=我 result=liveness')
        self.assertEqual(r.state,'rejected')
        self.assertIn('活体检测',r.body)

    def test_no_template(self):
        r=interpret_test_reply('IGNORE no-template')
        self.assertEqual(r.state,'unavailable')
        self.assertIn('尚未录入',r.body)

    def test_no_config(self):
        self.assertEqual(interpret_test_reply('IGNORE no-config').state,'unavailable')

    def test_corrupt(self):
        self.assertIn('损坏', interpret_test_reply('IGNORE corrupt-template').body)

    def test_camera_error(self):
        self.assertEqual(interpret_test_reply('IGNORE unavailable-or-processing-error').state,'unavailable')

    def test_timeout(self):
        r=interpret_test_reply('ERR TimeoutError: timed out')
        self.assertEqual(r.state,'unavailable')
        self.assertIn('超时',r.body)

    def test_no_false_success(self):
        for raw in ('OK', 'OK enrolled:我', '', 'PONG', 'ERR worker', 'DENIED', 'OK mode=dual sim=0.99 face=我'):
            with self.subTest(raw=raw):
                self.assertNotEqual(interpret_test_reply(raw).state, 'passed')

    def test_malformed_similarity(self):
        r=interpret_test_reply('OK mode=ir sim=nan face=我 result=accepted')
        self.assertEqual(r.state,'unavailable')
        self.assertIn('缺少必要数据',r.body)

    def test_inconsistent_success_is_not_passed(self):
        for raw in ('OK mode=dual sim=0.500 face=我 result=accepted',
                    'OK mode=dual face=我 result=accepted',
                    'OK mode=dual sim=0.900 result=accepted',
                    'OK sim=0.900 face=我 result=accepted'):
            with self.subTest(raw=raw):
                self.assertEqual(interpret_test_reply(raw, 0.6).state, 'unavailable')

    def test_missing_threshold(self):
        r=interpret_test_reply('DENIED mode=ir sim=-0.4 face=one result=mismatch','garbled')
        self.assertIn('未设置',r.body)

    def test_persistent_result_and_dialog_contract(self):
        code=(SRC/'faceauth_gui.py').read_text(encoding='utf-8')
        self.assertIn('self.test_feedback_title.set_label(prefix + result.heading)', code)
        self.assertIn('dialog.present(self.win)', code)
        self.assertIn('self.resume_continuous_preview()', code)
        self.assertIn("if not self.test_feedback.get_visible():", code)
        self.assertIn("timeout=40",code)

if __name__=='__main__': unittest.main()

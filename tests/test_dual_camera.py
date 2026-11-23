"""Two-camera line-protocol tests using fake V4L2 objects (no hardware)."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture_bridge', ROOT / 'scripts/capture_bridge.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

class DummyCap:
    def __init__(self, path, frame):
        self.path = path
        self.frame = frame
        self.closed = False
    def release(self): self.closed = True
    def read(self): return True, self.frame

class DualCameraTests(unittest.TestCase):
    def emulate(self, commands):
        opened = []
        def fake_open(path):
            frame = np.zeros((3, 4, 3), dtype=np.uint8)
            frame[:, :, 0] = 55 if path.endswith('11') else 210
            cap = DummyCap(path, frame)
            opened.append(cap)
            return cap
        sink = io.StringIO()
        with patch.object(capture, 'open_cap', side_effect=fake_open), \
             patch.object(capture.sys, 'stdin', io.StringIO(commands)), \
             contextlib.redirect_stdout(sink):
            capture.main()
        return sink.getvalue().splitlines(), opened

    def test_dual_opens_two_distinct_nodes_and_both_snap(self):
        response, cams = self.emulate('PING\nOPEN dual /dev/video11 /dev/video12\nSNAP\nCLOSE\n')
        self.assertEqual(response[:2], ['PONG', 'OK'])
        self.assertEqual(len(cams), 2)
        self.assertNotEqual(cams[0].path, cams[1].path)
        self.assertTrue(all(c.closed for c in cams))
        parts = response[2].split()
        self.assertEqual(parts[0:3], ['OK', '4', '3'])
        self.assertEqual(parts[4:6], ['4', '3'])
        self.assertEqual(len(bytes.fromhex(parts[3])), 12)
        self.assertEqual(len(bytes.fromhex(parts[6])), 12)
        self.assertNotEqual(parts[3], parts[6])

    def test_dual_refuses_same_video_node(self):
        response, cams = self.emulate('OPEN dual /dev/video11 /dev/video11\n')
        self.assertTrue(response[0].startswith('ERR '), response)
        self.assertEqual(len(cams), 1)  # rejected before opening second node
        self.assertTrue(cams[0].closed) # failure releases already-opened RGB camera

    def test_failed_second_open_closes_first(self):
        first = DummyCap('/dev/video11', np.zeros((3,4,3), dtype=np.uint8))
        def fake_open(path):
            if path == '/dev/video12': raise RuntimeError('IR busy')
            return first
        sink = io.StringIO()
        with patch.object(capture, 'open_cap', side_effect=fake_open), \
             patch.object(capture.sys, 'stdin', io.StringIO('OPEN dual /dev/video11 /dev/video12\n')), \
             contextlib.redirect_stdout(sink):
            capture.main()
        self.assertIn('ERR RuntimeError', sink.getvalue())
        self.assertTrue(first.closed)

if __name__ == '__main__': unittest.main()

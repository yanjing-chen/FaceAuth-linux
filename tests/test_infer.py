import importlib.util
from pathlib import Path
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('faceauth_infer', ROOT/'scripts/infer_engine.py')
infer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(infer)

class InferTests(unittest.TestCase):
    def test_gray_hex(self):
        gray=np.arange(12,dtype=np.uint8).reshape(3,4)
        decoded=infer.decode_hex(gray.tobytes().hex(),4,3)
        np.testing.assert_array_equal(decoded,gray)
    def test_vector_hex_exact_size(self):
        v=np.zeros(128,dtype='<f4');v[0]=1.0
        self.assertEqual(len(v.tobytes().hex()),1024)
        self.assertEqual(np.frombuffer(bytes.fromhex(v.tobytes().hex()),dtype='<f4')[0],1.0)
    def test_ir_color_conversion(self):
        gray=np.zeros((60,80),dtype=np.uint8)
        import cv2
        bgr=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
        self.assertEqual(bgr.shape,(60,80,3))

if __name__ == '__main__':unittest.main()

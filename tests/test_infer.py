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
    def test_ir_enhance_lifts_dark_frame(self):
        # 暗 IR 帧（含微弱人脸对比）经 CLAHE 后均值应提升，且尺寸不变
        import cv2
        dark=np.full((80,80),5,dtype=np.uint8)
        dark[20:60,20:60]=25
        enhanced=infer._enhance_ir(dark)
        self.assertEqual(enhanced.shape,dark.shape)
        self.assertGreater(float(enhanced.mean()),float(dark.mean()))

if __name__ == '__main__':unittest.main()

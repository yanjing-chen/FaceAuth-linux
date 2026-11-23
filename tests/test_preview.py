"""No hardware/model needed: verify frame annotation and selected UI contracts."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import cv2

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('capture_bridge', ROOT/'scripts/capture_bridge.py')
cap=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)

class FakeDetector:
    def __init__(self, faces):self.faces=faces;self.size=None
    def setInputSize(self, size):self.size=size
    def detect(self,img):return (1,self.faces)

class PreviewTests(unittest.TestCase):
    def test_rgb_vs_mono_pixels(self):
        rgb=np.zeros((40,60,3),dtype=np.uint8)
        rgb[:,:,1]=110
        self.assertEqual(cap.visual_color_kind(rgb),'RGB')
        self.assertEqual(cap.visual_color_kind(np.full((40,60),128,dtype=np.uint8)),'MONO')
        self.assertEqual(cap.visual_color_kind(np.full((40,60,3),85,dtype=np.uint8)),'MONO')

    def test_detector_within_source_geometry(self):
        frame=np.zeros((120,180,3),dtype=np.uint8)
        fake=FakeDetector(np.array([[20,15,50,55,0,0,0,0,0,0,0,0,0,0,0]],dtype=np.float32))
        with patch.object(cap,'get_preview_detector',return_value=fake):
            boxes=cap.detect_preview_faces(frame)
        self.assertEqual(boxes,[(20,15,50,55)])
        self.assertEqual(fake.size,(180,120))
        rendered=cap.draw_tracking_corners(frame,boxes)
        self.assertGreater(int(rendered[15,20].sum()),0)
        self.assertEqual(int(rendered[0,0].sum()),0)

    def test_clip_invalid_boxes(self):
        frame=np.zeros((100,150,3),dtype=np.uint8)
        fake=FakeDetector(np.array([[-20,-10,60,40], [170,10,30,30]],dtype=np.float32))
        with patch.object(cap,'get_preview_detector',return_value=fake):
            self.assertEqual(cap.detect_preview_faces(frame),[(0,0,40,30)])

    def test_no_face_is_not_fake_ring(self):
        frame=np.full((150,200,3),80,dtype=np.uint8)
        with patch.object(cap,'get_preview_detector',return_value=FakeDetector(None)):
            coded=bytes.fromhex(cap.preview_jpg(frame))
        im=cv2.imdecode(np.frombuffer(coded,dtype=np.uint8),cv2.IMREAD_COLOR)
        self.assertIsNotNone(im)
        self.assertLess(float(im.std()),1.5)  # no hard-coded guide circle

    def test_statically_enforced_camera_lifecycle(self):
        daemon=(ROOT/'src/daemon/src/main.rs').read_text()
        gui=(ROOT/'scripts/faceauth_gui.py').read_text()
        self.assertIn('if let Some(owner)=existing{x.start_preview(&owner);}', daemon)
        self.assertIn('"PREVIEW_STOP" if p.len()==2', daemon)
        self.assertIn('x.stop_preview();',daemon)
        self.assertIn('ReadableChoiceRow(name, self.device_choice_options(key)',gui)
        self.assertNotIn('Gtk.DropDown.new_from_strings(choices)',gui)
        self.assertIn('self.preview.pause()',gui)
        self.assertIn('self.resume_continuous_preview()',gui)
        self.assertNotIn('toggle_preview(',gui)

if __name__=='__main__':unittest.main()

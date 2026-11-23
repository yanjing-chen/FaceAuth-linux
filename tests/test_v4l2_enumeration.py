"""Video capture capability filtering, without any real V4L2 hardware."""
from pathlib import Path
from unittest.mock import patch
import importlib.util
import struct
import fnmatch
import stat
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('client_v015',ROOT/'scripts/client.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class V4L2QuerycapTests(unittest.TestCase):
    def query(self, capabilities, device_caps):
        def ioctl(fd, code, buf, mutate):
            self.assertEqual(code, 0x80685600)
            struct.pack_into('=II',buf,84,capabilities,device_caps)
            return 0
        with patch.object(mod.os,'open',return_value=99), patch.object(mod.os,'close') as closer, patch.object(mod.fcntl,'ioctl',side_effect=ioctl):
            result=mod.is_video_capture_node('/dev/video0')
        closer.assert_called_once_with(99)
        return result
    def test_real_video_capture(self):
        self.assertTrue(self.query(0x80000000|0x04000000,0x1))
        self.assertTrue(self.query(0x80000000|0x04000000,0x1000))
    def test_metadata_only_is_hidden(self):
        self.assertFalse(self.query(0x80000000|0x04000000,0x00800000))
    def test_device_caps_more_specific_than_capabilities(self):
        self.assertFalse(self.query(0x80000000|0x00000001,0x00800000))
    def test_old_driver_without_device_caps(self):
        self.assertTrue(self.query(0x00000001,0))
    def test_unavailable_is_unknown_not_false_capture_claim(self):
        with patch.object(mod.os,'open',side_effect=PermissionError('busy')):
            self.assertIsNone(mod.is_video_capture_node('/dev/video9'))
    def test_deduplicates_aliases_and_filters_metadata(self):
        aliases={
            '/dev/v4l/by-id/rgb':'/dev/video0',
            '/dev/v4l/by-path/rgb':'/dev/video0',
            '/dev/video0':'/dev/video0',
            '/dev/video1':'/dev/video1',
            '/dev/video2':'/dev/video2',
            '/dev/video3':'/dev/video3',
        }
        def fake_glob(pattern):
            return [p for p in aliases if fnmatch.fnmatch(p, pattern)]
        with patch.object(mod.glob,'glob',side_effect=fake_glob), \
            patch.object(mod.os.path,'realpath',side_effect=lambda p: aliases.get(p,p)), \
            patch.object(mod.os,'stat',return_value=type('Stat',(),{'st_mode': stat.S_IFCHR})()), \
            patch.object(mod,'is_video_capture_node',side_effect=lambda p:p in ('/dev/video0','/dev/video2')) as cap, \
            patch.object(mod,'video_node_name',return_value='Some USB Webcam'):
            results=mod.list_video_devices()
        self.assertEqual(len(results),2)
        # by-path preferred: interface-numbered names stay correct across reboots,
        # while by-id's 'HD_Webcam' label may be attached to the IR node.
        self.assertEqual(results[0][0],'/dev/v4l/by-path/rgb')
        self.assertEqual(results[1][0],'/dev/video2')
        self.assertEqual(cap.call_count,4)

if __name__=='__main__':unittest.main()

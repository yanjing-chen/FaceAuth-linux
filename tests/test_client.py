import importlib.util
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import unittest
import sys

SRC = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('faceauth_client', SRC/'scripts/client.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ClientTests(unittest.TestCase):
    def test_label(self):
        self.assertTrue(module.valid_label('我'))
        self.assertTrue(module.valid_label('测试-1'))
        for txt in ('hello world', 'foo/bar', 'a,b', 'a\nb', ''):
            self.assertFalse(module.valid_label(txt))

    def test_no_hardcoded_video_node(self):
        txt=(SRC/'scripts/faceauth_gui.py').read_text()
        self.assertNotIn('cv2.VideoCapture', txt)
        self.assertNotIn('/dev/video0',txt)
        # pkexec is allowed only via client.admin_request (a short-lived CLI
        # for authorized enroll/delete), never to elevate this GUI itself.
        self.assertNotIn('os.system(',txt)
        # GUI must not launch pkexec directly: privileged writes go through
        # client.admin_request, so the GUI process itself stays unprivileged.
        self.assertNotIn("/usr/bin/pkexec", txt)

    def test_saving_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            original=module.CONFIG
            try:
                module.CONFIG=Path(temp)/'.config'/'faceauth-rebuild'/'config'
                module.save_config({'mode':'rgb','rgb_device':'/dev/v4l/by-id/test'})
                self.assertEqual(stat.S_IMODE(module.CONFIG.stat().st_mode), 0o600)
                self.assertEqual(module.load_config()['mode'],'rgb')
            finally:
                module.CONFIG=original

    def test_rpc_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            sockpath=str(Path(tmp)/'daemon.sock')
            ls=socket.socket(socket.AF_UNIX)
            ls.bind(sockpath)
            ls.listen(1)
            def serve():
                conn,_=ls.accept()
                try:
                    self.assertEqual(conn.recv(100), b'PING\n')
                    conn.sendall(b'PONG\n')
                finally:
                    conn.close()
                    ls.close()
            t=threading.Thread(target=serve);t.start()
            original=os.environ.get('FACEAUTH_SOCKET')
            try:
                os.environ['FACEAUTH_SOCKET']=sockpath
                self.assertEqual(module.request('PING'), 'PONG')
            finally:
                if original is None:os.environ.pop('FACEAUTH_SOCKET',None)
                else:os.environ['FACEAUTH_SOCKET']=original
                t.join(timeout=3)

if __name__ == '__main__':unittest.main()

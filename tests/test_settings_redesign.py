"""v0.1.8 settings layout and security callback regression tests.

These run without GTK (real GNOME visual verification remains required).
"""
from pathlib import Path
import ast
import hashlib
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent / 'FaceAuth-Rebuild-v0.1.7'


class SettingsRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = (ROOT / 'scripts/faceauth_gui.py').read_text()
        cls.tree = ast.parse(cls.gui)

    def test_two_column_dashboard(self):
        gui = self.gui
        self.assertIn("settings_grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL", gui)
        self.assertIn('settings_grid.append(hardware_column)', gui)
        self.assertIn('settings_grid.append(account_column)', gui)
        for name in ('工作模式', '活体检测', 'RGB 彩色', 'IR 红外',
                     '已录入人脸', '系统认证', '刷新状态'):
            self.assertIn(name, gui)
        self.assertIn('settings_clamp.set_maximum_size(1120)', gui)
        self.assertIn("compact=True", gui)

    def test_controls_remain_wired(self):
        gui = self.gui
        for name in ('change_mode', 'change_liveness', 'device_changed',
                     'rescan_devices', 'check_camera_channels', 'on_delete_all',
                     'scene_toggled', 'refresh_lists', 'populate_scenes'):
            self.assertIn('self.' + name, gui)
        self.assertIn('self.settings_feedback.set_label', gui)
        self.assertIn("self._choose.set_label(caption)", gui)
        self.assertIn('self._scroll.set_size_request(570,', gui)

    def test_no_backend_or_pam_change(self):
        """Compare against stable hashes, even in standalone ZIP.
        capture_bridge.py / infer_engine.py baselines were updated on 2026-11-23
        for an authorized recognition-robustness fix (camera warmup + IR CLAHE).
        All other files remain locked to the v0.1.7 stable baseline.
        """
        expected = {
            'scripts/capture_bridge.py': '8bcb75e34cfb4d2ad773ddcc83e11d982acccfbdde500794545d916206b79d32',
            'scripts/client.py': 'cb38d0605fa009cbba5c47c382a2aba74cf1e4fa2230d31169246650d7995b61',
            'scripts/infer_engine.py': 'ca5933dc2717365ff16ed51a543641f10112bc86ad665ab20d38fc49eeda3212',
            'scripts/install-system.sh': '3dd2711f7cffb7998cd3785663be783ef1fcc23d43229eca22b7224ec692e143',
            'scripts/launch-gui.sh': 'cc249d16cb991b24ea3a06f7bc94e6640ea6504f644c303842ab7b4dacab4b66',
            'scripts/recognition_result.py': '7bed65ade3597ab56c1f1781bb49074cde7d3fd98a8ba15171545f431f6b9329',
            'scripts/run-dev.sh': '3b1871506c45edf7f5cfd5a61aa1a4e5c69caa76e03235bc68764a124cce35d3',
            'scripts/uninstall-system.sh': '2a6073f2cadd7560e904b6bd506f4e16d9a7531374f6bd4fa46ebe1d62b14491',
            'src/cli/src/main.rs': 'fe6461df224c42cdab50d7199444b0a6eacc9e61f247d758686d948382452ba8',
            'src/core/src/config.rs': '8fb79af5c0da4b4162b0f9ef109c0ff5f3b74b56ff79af890ea1cad878292a0d',
            'src/core/src/infer.rs': '9120b84f379950de8f4cd3358323db35b6371acc18f2ad853e01218c1801d1d9',
            'src/core/src/lib.rs': '4d56326a12d2eb6190d2f7346482511af3f5c4e0153632c6e2212ce6be05ff62',
            'src/core/src/peer.rs': '4695f4268f995781f9f97dbbaf7770d6d6d4f912833663fd4f4d4e272a4f6609',
            'src/core/src/protocol.rs': '6cee4651880954bee6ae8eb4998f008bc259c4e7f5d865358248643dcbd31aa6',
            'src/core/src/scene.rs': 'c714e06dda49b9bec075d36dc48ddc18226af8c0592e8329582a6964f4ae87da',
            'src/core/src/store.rs': '944d3f15fb06f26fd19fd0f4e0a79c0d63f21375c0929990733a670c4530c02f',
            'src/daemon/src/main.rs': 'd1d004743e0bc8d3be001f4cca848b9d2b6ae68517b5b994c12c4a055152de72',
            'src/pam/src/lib.rs': '38a91653128941f5ca6459f6e773463b19908a97fed68bcc6c0c1e289293c34b',
            'src/scene-admin/src/main.rs': '518de2847a4668d251273713284fb4acf4e0b0f2acc20ab62fd2081b80bf8a64',
        }
        for rel, digest in expected.items():
            self.assertEqual(hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(), digest, rel)

    def test_no_auto_enable_or_depth(self):
        self.assertNotIn('install-system.sh', self.gui)
        self.assertNotIn('Gtk.ComboBox', self.gui)
        self.assertNotIn("('depth3d'", self.gui)


if __name__ == '__main__':
    unittest.main()

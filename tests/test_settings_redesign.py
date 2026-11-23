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
            'scripts/capture_bridge.py': '33d36d1dc4775c05dcb7fab1b76e1c468c01e8fde6a80c3ad30e82f94eb2db3c',
            'scripts/client.py': '6004270100f479739337aac672bf9135cb7a30a2e2006fb76f0f87b286d5b9b8',
            'scripts/infer_engine.py': 'ca5933dc2717365ff16ed51a543641f10112bc86ad665ab20d38fc49eeda3212',
            'scripts/install-system.sh': '6082d55584bcff375b8f93ca102658413b31e33fca6e486c1ed4ca075a9f203f',
            'scripts/launch-gui.sh': 'cc249d16cb991b24ea3a06f7bc94e6640ea6504f644c303842ab7b4dacab4b66',
            'scripts/recognition_result.py': '7bed65ade3597ab56c1f1781bb49074cde7d3fd98a8ba15171545f431f6b9329',
            'scripts/run-dev.sh': '3b1871506c45edf7f5cfd5a61aa1a4e5c69caa76e03235bc68764a124cce35d3',
            'scripts/uninstall-system.sh': '13e2916f0dc2f32dd27ffbb68b7de6ac2c54e7a7680e0c46cb5599a769832b32',
            'src/cli/src/main.rs': 'fe6461df224c42cdab50d7199444b0a6eacc9e61f247d758686d948382452ba8',
            'src/core/src/config.rs': '5673fc685eed0dba11b4bde6e94207c7e12d9fc1a79438ff619e28474141f617',
            'src/core/src/infer.rs': '9120b84f379950de8f4cd3358323db35b6371acc18f2ad853e01218c1801d1d9',
            'src/core/src/lib.rs': '4d56326a12d2eb6190d2f7346482511af3f5c4e0153632c6e2212ce6be05ff62',
            'src/core/src/peer.rs': '4695f4268f995781f9f97dbbaf7770d6d6d4f912833663fd4f4d4e272a4f6609',
            'src/core/src/protocol.rs': '6cee4651880954bee6ae8eb4998f008bc259c4e7f5d865358248643dcbd31aa6',
            'src/core/src/scene.rs': 'c714e06dda49b9bec075d36dc48ddc18226af8c0592e8329582a6964f4ae87da',
            'src/core/src/store.rs': '944d3f15fb06f26fd19fd0f4e0a79c0d63f21375c0929990733a670c4530c02f',
            'src/daemon/src/main.rs': '17b0d094d170e5065a983f543231e2886dd94150557b22fb8041c81808537636',
            'src/pam/src/lib.rs': 'ef128b234435e2a7b1e916bee751ce7b3f6d38e506e87864a98c0ead09c48218',
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

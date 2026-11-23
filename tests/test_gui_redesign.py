"""GUI update must not accidentally change PAM or daemon security contracts."""
from pathlib import Path
import ast, unittest
ROOT = Path(__file__).resolve().parents[1]

class RedesignContracts(unittest.TestCase):
    def test_side_by_side_structure(self):
        gui=(ROOT/'scripts/faceauth_gui.py').read_text()
        ast.parse(gui)
        for token in ('fa-sidebar','fa-camera','fa-live-tag','Adw.Clamp()',"label='录入与测试'",'self.mode_badge'):
            self.assertIn(token,gui)
    def test_keep_callbacks(self):
        gui=(ROOT/'scripts/faceauth_gui.py').read_text()
        for name in ('on_enroll','on_test','enroll_done','test_done','scene_toggled','scene_confirmed','populate_faces'):
            self.assertIn('def '+name+'(',gui)
        self.assertIn("dialog.set_heading('录入成功' if ok else '录入未完成')",gui)
        self.assertIn('dialog.set_heading(result.heading)',gui)
    def test_core_pam_unchanged(self):
        pam=(ROOT/'src/pam/src/lib.rs').read_text()
        self.assertIn('Duration::from_secs(12)',pam)
        self.assertIn('PAM_IGNORE',pam)
        self.assertNotIn('common-auth',pam)
    def test_css_present(self):
        css=(ROOT/'scripts/gui_style.css').read_text()
        self.assertIn('.fa-camera',css)
        self.assertIn('.fa-sidebar',css)

if __name__=='__main__':unittest.main()

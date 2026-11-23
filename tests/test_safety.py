from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
class SafetyContract(unittest.TestCase):
    def test_pam_no_python_or_external_crates(self):
        pam=(ROOT/'src/pam/src/lib.rs').read_text()
        manifest=(ROOT/'src/pam/Cargo.toml').read_text()
        self.assertNotIn('Command::new',pam)
        self.assertNotIn('DISPLAY',pam)
        self.assertIn('PAM_IGNORE',pam)
        self.assertIn('name = "pam_faceauth"',manifest)
        self.assertIn('/run/faceauth-rebuild/daemon.sock',pam)
    def test_no_automatic_live_pam_modifications_in_dev(self):
        shell=(ROOT/'scripts/run-dev.sh').read_text()
        self.assertIn('--dev',shell)
        self.assertNotIn('/etc/pam.d/',shell)
        self.assertIn('initialize_sandbox', (ROOT/'src/daemon/src/main.rs').read_text())
    def test_daemon_credential_proof(self):
        code=(ROOT/'src/daemon/src/main.rs').read_text()
        self.assertIn('peer::credentials(&stream)',code)
        self.assertIn('is_self(uid,user)',code)
        self.assertIn('uid!=0',code)
        self.assertIn('proc_starttime(pid)',code)
    def test_scene_admin_is_whitelisted_and_protected(self):
        scene=(ROOT/'src/core/src/scene.rs').read_text()
        for requirement in ['common-auth','default=ignore','create_new(true)', 'fs::rename', 'vendor-override']:
            self.assertIn(requirement,scene)
        self.assertIn('not an allowed PAM service',scene)
        self.assertIn('validate_managed',scene)
        self.assertIn('pam_faceauth.so',scene)  # prevents duplicate legacy hooking
    def test_polkit_policy_requires_admin(self):
        root=ET.parse(ROOT/'packaging/org.faceauth.rebuild.manage-scenes.policy').getroot()
        self.assertEqual(root.find('action').attrib['id'], 'org.faceauth.rebuild.manage-scenes')
        self.assertEqual(root.find('.//allow_active').text, 'auth_admin')
        self.assertEqual(root.find('.//allow_any').text, 'no')
    def test_script_install_is_explicit(self):
        script=(ROOT/'scripts/install-system.sh').read_text()
        self.assertIn('EUID != 0',script)
        self.assertIn('cargo build --release',script)
        self.assertIn('faceauth-rebuild.service',script)
        self.assertNotIn('SCENE_ON',script)
        self.assertNotIn('sed -i /etc/pam',script)
    def test_root_not_exec_user_program(self):
        config=(ROOT/'src/core/src/config.rs').read_text()
        self.assertIn('NEVER execute it as root',config)
        daemon=(ROOT/'src/daemon/src/main.rs').read_text()
        self.assertIn('/usr/libexec/faceauth-rebuild/scripts',daemon)
    def test_gui_scene_switches_are_real(self):
        gui=(ROOT/'scripts/faceauth_gui.py').read_text()
        self.assertIn('Adw.SwitchRow(',gui)
        self.assertIn('SCENE_ON',gui)
        self.assertIn('SCENE_OFF',gui)
        self.assertIn('choose_finish(res)',gui)
if __name__=='__main__':unittest.main()

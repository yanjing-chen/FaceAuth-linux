"""Static contracts for widget mechanics (actual GTK runtime needs GNOME)."""
from pathlib import Path
import ast
import tempfile
import unittest
from unittest.mock import patch
import importlib.util
ROOT = Path(__file__).resolve().parents[1]

class WidgetContract(unittest.TestCase):
    def test_gui_has_no_3d_choice_or_gtk_comborow(self):
        code=(ROOT/'scripts/faceauth_gui.py').read_text()
        ast.parse(code)
        self.assertIn('class ReadableChoiceRow(Adw.ActionRow):', code)
        self.assertIn('Gtk.Popover()', code)
        self.assertIn('Gtk.MenuButton(', code)
        self.assertIn('Pango.WrapMode.CHAR', code)
        self.assertIn('self._choose.set_label(caption)', code)
        self.assertIn('set_size_request(176 if compact else 264, 42)', code)
        self.assertIn('set_size_request(570,', code)
        self.assertNotIn('Adw.ComboRow(', code)
        self.assertNotIn("('depth3d',", code)
    def test_worker_does_not_accept_depth_command(self):
        code=(ROOT/'scripts/infer_engine.py').read_text()
        self.assertNotIn('do_depth_3d', code)
        self.assertNotIn('decode_depth_hex', code)
        self.assertNotIn('cmd == "DEPTH"', code)
        rust=(ROOT/'src/core/src/lib.rs').read_text()
        self.assertNotIn('mod liveness', rust)
        self.assertFalse((ROOT/'src/core/src/liveness.rs').exists())
    def test_migrates_v013_config_with_private_backup(self):
        spec=importlib.util.spec_from_file_location('faceauth_client_014', ROOT/'scripts/client.py')
        obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config';cfg.write_text('mode=dual\ndepth_device=/dev/video9\nliveness=depth3d\n')
            with patch.object(obj, 'CONFIG', cfg):
                self.assertTrue(obj.migrate_obsolete_settings())
                content=cfg.read_text()
                self.assertNotIn('depth_device=', content)
                self.assertIn('liveness=heuristic', content)
                self.assertTrue((Path(tmp)/'config.before-v0.1.4.bak').exists())
                self.assertFalse(obj.migrate_obsolete_settings())

if __name__=='__main__': unittest.main()

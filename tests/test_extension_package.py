"""Packaging checks: distributables never accidentally bundle ignored artifacts."""
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts/package_extension.py'
spec = importlib.util.spec_from_file_location('extension_packager', MODULE_PATH)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class ExtensionPackageTests(unittest.TestCase):
    def test_default_package_has_only_allowlisted_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'JobPilot-0.8.0'
            packager.package(output, make_zip=True)
            self.assertEqual({p.name for p in output.iterdir()}, set(packager.FILES))
            with zipfile.ZipFile(output.parent / (output.name + '.zip')) as archive:
                self.assertEqual(set(archive.namelist()), set(packager.FILES))
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertNotIn('web_accessible_resources', manifest)
            self.assertNotIn('<all_urls>', manifest['host_permissions'])
            self.assertEqual(manifest['side_panel']['default_path'], 'sidepanel.html')

    def test_private_seed_is_optional_and_outputs_preserve_existing_install(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            profile = temp / 'profile.json'
            profile.write_text(json.dumps({'first_name': 'Example', 'work_history': []}))
            resume = temp / 'Example.pdf'
            resume.write_bytes(b'%PDF-1.4\nExample fixture')
            output = temp / 'JobPilot-private'
            packager.package(output, profile, resume, make_zip=True)
            initial = json.loads((output / 'initial-data.json').read_text())
            self.assertEqual(initial['profile']['first_name'], 'Example')
            self.assertEqual(initial['resumes'][0]['name'], 'Example.pdf')
            self.assertEqual((output / 'initial-resume.pdf').read_bytes(), resume.read_bytes())
            self.assertEqual((output / 'initial-data.json').stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                packager.package(output)

    def test_invalid_private_resume_cannot_leave_a_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            resume = temp / 'Wrong.pdf'
            resume.write_bytes(b'not a pdf')
            output = temp / 'JobPilot'
            with self.assertRaisesRegex(ValueError, 'PDF'):
                packager.package(output, resume=resume)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()

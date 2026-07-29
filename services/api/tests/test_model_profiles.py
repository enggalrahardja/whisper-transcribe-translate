import copy
import tempfile
import unittest
from pathlib import Path

from app.services.model_profiles import load_profile_catalogue, resolve_profile, validate_profile_catalogue


class ModelProfileTests(unittest.TestCase):
    def test_catalogue_and_default_are_local(self):
        catalogue = load_profile_catalogue()
        self.assertEqual(catalogue["defaultProfile"], "Fast")
        self.assertFalse(catalogue["profiles"]["Private"]["networkProvidersAllowed"])
        self.assertNotIn("cloud", str(catalogue).lower())
        for profile in catalogue["profiles"].values():
            self.assertTrue(all(value is False for value in profile["features"].values()))

    def test_profile_to_config_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "base.pt").touch()
            resolved = resolve_profile("Fast", model_dir=Path(temporary), cuda_available=False)
            self.assertEqual(resolved.selected, "Fast")
            self.assertEqual(resolved.configuration["live"]["model"], "base")

    def test_unavailable_model_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "base.pt").touch()
            resolved = resolve_profile("Balanced", model_dir=Path(temporary), cuda_available=False)
            self.assertEqual(resolved.selected, "Fast")
            self.assertIn("small", resolved.warnings[0])

    def test_unsupported_gpu_falls_back(self):
        catalogue = load_profile_catalogue()
        changed = copy.deepcopy(catalogue)
        changed["profiles"]["Balanced"]["live"]["device"] = "cuda"
        changed["profiles"]["Balanced"]["accurateFinal"]["enabled"] = False
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "small.pt").touch()
            (root / "base.pt").touch()
            resolved = resolve_profile("Balanced", model_dir=root, cuda_available=False, catalogue=changed)
            self.assertEqual(resolved.selected, "Fast")

    def test_cloud_provider_is_rejected(self):
        catalogue = load_profile_catalogue()
        changed = copy.deepcopy(catalogue)
        changed["profiles"]["Private"]["translation"]["provider"] = "cloud"
        with self.assertRaisesRegex(ValueError, "local provider"):
            validate_profile_catalogue(changed)


if __name__ == "__main__":
    unittest.main()

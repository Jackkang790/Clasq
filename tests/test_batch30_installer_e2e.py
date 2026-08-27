"""Batch 30 installer contract and current clean-dist acceptance."""
from pathlib import Path
import hashlib
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "installer" / "Clasq.iss"
DIST = ROOT / "dist-batch30" / "Clasq"


class Batch30InstallerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ISS.read_text(encoding="utf-8")

    def test_stable_identity_and_per_user_install_policy(self):
        self.assertIn('#define MyAppId "{{21E38F55-7A79-49A4-84E6-1F6E41F922E2}"', self.source)
        self.assertIn("AppId={#MyAppId}", self.source)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\Clasq", self.source)
        self.assertIn("PrivilegesRequired=lowest", self.source)
        self.assertIn("ArchitecturesAllowed=x64compatible", self.source)

    def test_source_dir_is_overridable_and_whole_onedir_is_installed(self):
        self.assertIn("#ifndef SourceDir", self.source)
        self.assertRegex(
            self.source,
            r'Source:\s*"\{#SourceDir\}\\\*";.*recursesubdirs.*createallsubdirs',
        )
        self.assertNotRegex(self.source, r'Source:.*Clasq\.exe.*DestDir')

    def test_shortcuts_target_installed_executable_not_a_binary_copy(self):
        icons = self.source.split("[Icons]", 1)[1].split("[Tasks]", 1)[0]
        self.assertIn('Filename: "{app}\\{#MyAppExeName}"', icons)
        self.assertIn('Name: "{group}\\Clasq"', icons)
        self.assertIn('Name: "{autodesktop}\\Clasq"', icons)
        self.assertNotIn("Source:", icons)

    def test_uninstall_preserves_localappdata_user_state(self):
        uninstall = self.source.split("[UninstallDelete]", 1)[1]
        self.assertNotRegex(uninstall, r"(?i)(models|file_manager\.db|settings|logs).*Type:")
        self.assertIn("intentionally survive uninstall", uninstall)

    def test_standard_uninstaller_registration_and_start_menu_entry(self):
        self.assertIn("Uninstallable=yes", self.source)
        self.assertIn("CreateUninstallRegKey=yes", self.source)
        self.assertIn("UninstallDisplayName={#MyAppName}", self.source)
        self.assertIn('Name: "{group}\\Clasq 제거"; Filename: "{uninstallexe}"', self.source)

    def test_full_uninstall_is_explicit_and_bounded_to_clasq_data(self):
        self.assertIn("DeleteClasqUserData := HasUninstallParameter('/DELETEUSERDATA')", self.source)
        self.assertIn("MB_YESNOCANCEL or MB_DEFBUTTON2", self.source)
        self.assertIn('#define UserDataDir "{localappdata}\\Clasq"', self.source)
        self.assertIn("DelTree(DataPath, True, True, True)", self.source)
        self.assertIn("Never inspect the database or delete", self.source)
        self.assertNotIn("managed_paths", self.source)

    def test_close_and_signing_policies_remain_explicit(self):
        self.assertIn("CloseApplications=yes", self.source)
        self.assertIn("RestartApplications=no", self.source)
        self.assertIn("AppMutex=Clasq-21E38F55-7A79-49A4-84E6-1F6E41F922E2", self.source)
        self.assertNotIn("SignTool=", self.source)

    def test_application_owns_the_same_lifetime_mutex(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('INSTALLER_APP_MUTEX = "Clasq-21E38F55-7A79-49A4-84E6-1F6E41F922E2"', main)
        self.assertIn("_acquire_installer_app_mutex()", main)
        self.assertIn('ctypes.WinDLL("kernel32", use_last_error=True)', main)


class Batch30CleanDistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DIST.is_dir():
            raise unittest.SkipTest("dist-batch30 clean build is not present")
        cls.files = [path for path in DIST.rglob("*") if path.is_file()]
        cls.names = {path.relative_to(DIST).as_posix().casefold() for path in cls.files}

    def test_onedir_runtime_and_compliance_are_complete(self):
        for relative in (
            "Clasq.exe", "_internal/python313.dll",
            "_internal/runtime/llama-server.exe", "_internal/runtime/ffmpeg.exe",
            "_internal/THIRD_PARTY_NOTICES.md", "_internal/FFMPEG_SOURCE_INFO.txt",
        ):
            self.assertIn(relative.casefold(), self.names)
        self.assertTrue((DIST / "_internal" / "THIRD_PARTY_LICENSES").is_dir())

    def test_models_are_not_bundled(self):
        self.assertFalse(any(name.endswith(".gguf") for name in self.names))

    def test_previous_pruning_remains(self):
        banned = (
            "qt6qml.dll", "qt6quick.dll", "qt6virtualkeyboard.dll",
            "_avif.cp313-win_amd64.pyd", "_imagingtk.cp313-win_amd64.pyd",
            "qt6pdf.dll", "qpdf.dll",
        )
        for filename in banned:
            self.assertFalse(any(name.endswith(filename) for name in self.names), filename)

    def test_ffmpeg_is_pinned(self):
        ffmpeg = DIST / "_internal" / "runtime" / "ffmpeg.exe"
        with ffmpeg.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        self.assertEqual(digest, "ad8f211bc894755e0061c55ab280ae00e8d3d4f15a8cc4372b24cfa247b5942e")


if __name__ == "__main__":
    unittest.main()

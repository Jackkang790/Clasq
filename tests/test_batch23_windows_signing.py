from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.windows_signing import (
    CONFIG_RELATIVE_PATH,
    Credential,
    SigningError,
    build_sign_command,
    build_verify_command,
    development_preflight,
    discover_signtool,
    load_config,
    load_credential,
    preflight_store_certificate,
    resolve_owned_target,
    sign_and_verify,
)


ROOT = Path(__file__).resolve().parents[1]


class Batch23WindowsSigningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(ROOT / CONFIG_RELATIVE_PATH)

    def test_config_parses_sha256_rfc3161_policy(self):
        self.assertEqual(self.config["digest_algorithm"], "SHA256")
        self.assertEqual(self.config["timestamp"]["protocol"], "RFC3161")
        self.assertTrue(self.config["timestamp"]["required_for_release"])

    def test_owned_target_order_is_inner_then_outer(self):
        targets = self.config["owned_targets"]
        self.assertEqual([item["id"] for item in targets], ["application", "installer"])
        self.assertLess(targets[0]["phase"], targets[1]["phase"])

    def test_third_party_targets_are_preserve_only(self):
        preserved = "\n".join(self.config["preserve_unsigned_or_upstream"])
        self.assertIn("ffmpeg.exe", preserved)
        self.assertIn("llama-server.exe", preserved)
        self.assertNotIn("ffmpeg.exe", json.dumps(self.config["owned_targets"]))

    def test_public_config_contains_no_secret_values(self):
        text = (ROOT / CONFIG_RELATIVE_PATH).read_text(encoding="utf-8").lower()
        for forbidden in ('"password"', '"private_key"', '"token_pin"', '"pfx_bytes"'):
            self.assertNotIn(forbidden, text)

    def test_development_mode_allows_unsigned_build(self):
        self.assertEqual(development_preflight(False, {}), "unsigned-development-allowed")

    def test_release_mode_fails_without_signtool(self):
        with self.assertRaisesRegex(SigningError, "SignTool was not found"):
            development_preflight(True, {"ProgramFiles(x86)": "missing"})

    def test_signtool_explicit_override_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "signtool.exe"
            tool.write_bytes(b"fixture")
            self.assertEqual(discover_signtool({"CLASQ_SIGNTOOL_EXE": str(tool)}), tool.resolve())

    def test_signtool_override_rejects_wrong_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "other.exe"
            tool.write_bytes(b"fixture")
            with self.assertRaisesRegex(SigningError, "signtool.exe"):
                discover_signtool({"CLASQ_SIGNTOOL_EXE": str(tool)})

    def test_signtool_selects_newest_versioned_x64_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "Windows Kits/10/bin/10.0.1.0/x64/signtool.exe"
            new = root / "Windows Kits/10/bin/10.0.2.0/x64/signtool.exe"
            for tool in (old, new):
                tool.parent.mkdir(parents=True, exist_ok=True)
                tool.write_bytes(b"fixture")
            self.assertEqual(discover_signtool({}, program_files_x86=root), new.resolve())

    def test_store_credential_requires_exact_thumbprint(self):
        value = "AB" * 20
        credential = load_credential({"CLASQ_SIGN_CERT_THUMBPRINT": value})
        self.assertEqual(credential, Credential("store", value))
        with self.assertRaisesRegex(SigningError, "exact hexadecimal"):
            load_credential({"CLASQ_SIGN_CERT_THUMBPRINT": "subject-name"})

    def test_credential_sources_are_mutually_exclusive(self):
        with self.assertRaisesRegex(SigningError, "exactly one"):
            load_credential({"CLASQ_SIGN_CERT_THUMBPRINT": "AB" * 20, "CLASQ_SIGN_PFX": "x.pfx"})

    def test_pfx_credential_is_external_and_password_is_not_config(self):
        with tempfile.TemporaryDirectory() as directory:
            pfx = Path(directory) / "release.pfx"
            pfx.write_bytes(b"fixture")
            credential = load_credential({"CLASQ_SIGN_PFX": str(pfx), "CLASQ_SIGN_PFX_PASSWORD": "secret"})
            self.assertEqual(credential.kind, "pfx")
            self.assertEqual(credential.password, "secret")

    def test_sign_command_uses_sha256_and_rfc3161(self):
        command, _ = build_sign_command(
            Path("signtool.exe"), Path("Clasq.exe"), Credential("store", "AB" * 20),
            "https://timestamp.example.invalid",
        )
        self.assertEqual(command[1], "sign")
        self.assertIn("/fd", command)
        self.assertIn("/tr", command)
        self.assertIn("/td", command)
        self.assertEqual(command.count("SHA256"), 2)

    def test_timestamp_must_be_https(self):
        with self.assertRaisesRegex(SigningError, "HTTPS"):
            build_sign_command(Path("signtool.exe"), Path("Clasq.exe"), Credential("store", "AB" * 20), "http://timestamp.invalid")

    def test_pfx_password_is_redacted_from_reportable_command(self):
        command, redacted = build_sign_command(
            Path("signtool.exe"), Path("Clasq.exe"), Credential("pfx", "release.pfx", "top-secret"),
            "https://timestamp.example.invalid",
        )
        self.assertIn("top-secret", command)
        self.assertNotIn("top-secret", redacted)
        self.assertIn("<redacted>", redacted)

    def test_verify_command_requires_timestamp_warning_as_failure(self):
        command = build_verify_command(Path("signtool.exe"), Path("Clasq.exe"))
        self.assertEqual(command[1:4], ["verify", "/pa", "/all"])
        self.assertIn("/tw", command)

    def test_only_allowlisted_owned_target_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "Clasq.exe"
            target.write_bytes(b"fixture")
            self.assertEqual(resolve_owned_target("application", root, self.config), target.resolve())
            with self.assertRaisesRegex(SigningError, "not Clasq-owned"):
                resolve_owned_target("ffmpeg", root, self.config)

    def test_missing_or_ambiguous_target_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(SigningError, "exactly one"):
                resolve_owned_target("installer", root, self.config)
            (root / "Clasq_Setup_a.exe").write_bytes(b"a")
            (root / "Clasq_Setup_b.exe").write_bytes(b"b")
            with self.assertRaisesRegex(SigningError, "found 2"):
                resolve_owned_target("installer", root, self.config)

    def test_store_preflight_rejects_ambiguous_selector(self):
        result = subprocess.CompletedProcess([], 0, '[{"Thumbprint":"A"},{"Thumbprint":"A"}]', "")
        with self.assertRaisesRegex(SigningError, "matched 2"):
            preflight_store_certificate("AB" * 20, runner=lambda *a, **k: result)

    def test_store_preflight_requires_private_key_and_eku(self):
        no_key = {"Thumbprint": "AB" * 20, "NotAfter": "2099-01-01T00:00:00+00:00", "HasPrivateKey": False, "CodeSigningEku": True}
        result = subprocess.CompletedProcess([], 0, json.dumps(no_key), "")
        with self.assertRaisesRegex(SigningError, "private key"):
            preflight_store_certificate("AB" * 20, runner=lambda *a, **k: result)

    def test_sign_then_verify_uses_argument_lists_and_shell_false(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Clasq.exe"
            target.write_bytes(b"unsigned")
            calls = []
            def runner(command, **kwargs):
                calls.append((command, kwargs))
                if command[1] == "sign":
                    target.write_bytes(target.read_bytes() + b"-signed")
                return subprocess.CompletedProcess(command, 0, "ok", "")
            report = sign_and_verify(
                target, Path("signtool.exe"), Credential("store", "AB" * 20),
                "https://timestamp.example.invalid", runner=runner,
                signature_reader=lambda _: {"Status": "Valid", "Timestamped": True, "Thumbprint": "AB" * 20, "Subject": "CN=Fixture"},
            )
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(call[1]["shell"] is False for call in calls))
            self.assertNotEqual(report["pre_sign_sha256"], report["post_sign_sha256"])
            self.assertEqual(report["target"], "Clasq.exe")

    def test_post_sign_verification_failure_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Clasq.exe"
            target.write_bytes(b"fixture")
            results = iter([
                subprocess.CompletedProcess([], 0, "signed", ""),
                subprocess.CompletedProcess([], 1, "", "tampered"),
            ])
            with self.assertRaisesRegex(SigningError, "post-sign verification failed"):
                sign_and_verify(
                    target, Path("signtool.exe"), Credential("store", "AB" * 20),
                    "https://timestamp.example.invalid", runner=lambda *a, **k: next(results),
                )

    def test_wrong_signer_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Clasq.exe"
            target.write_bytes(b"fixture")
            ok = subprocess.CompletedProcess([], 0, "ok", "")
            with self.assertRaisesRegex(SigningError, "expected certificate"):
                sign_and_verify(
                    target, Path("signtool.exe"), Credential("store", "AB" * 20),
                    "https://timestamp.example.invalid", runner=lambda *a, **k: ok,
                    signature_reader=lambda _: {"Status": "Valid", "Timestamped": True, "Thumbprint": "CD" * 20},
                )

    def test_gitignore_blocks_private_key_artifacts(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("*.pfx", "*.p12", "*.pvk", "*.key"):
            self.assertIn(pattern, text)

    def test_prior_packaging_guards_are_unchanged(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        self.assertIn("exclude_verified_root_duplicates(a.binaries)", spec)
        self.assertIn("resolve_build_ffmpeg(project)", spec)
        self.assertIn("THIRD_PARTY_LICENSES", spec)


if __name__ == "__main__":
    unittest.main()

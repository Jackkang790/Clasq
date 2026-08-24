"""Signing-ready Windows release support using Microsoft SignTool.

This is a build/release tool. It is not imported by the production application.
Development builds remain unsigned; release mode is explicitly fail-closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


CONFIG_RELATIVE_PATH = Path("packaging/windows-signing.json")
SECRET_KEYS = {"password", "private_key", "pfx_bytes", "token_pin"}
THUMBPRINT_RE = re.compile(r"^[0-9A-F]{40}(?:[0-9A-F]{24})?$")


class SigningError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credential:
    kind: str
    selector: str
    password: str | None = None


def preflight_store_certificate(
    thumbprint: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """Require one valid code-signing certificate with an accessible key."""
    script = r"""
$items = @(
  Get-ChildItem Cert:\CurrentUser\My,Cert:\LocalMachine\My -CodeSigningCert |
    Where-Object { $_.Thumbprint -eq $env:CLASQ_PREFLIGHT_THUMBPRINT } |
    ForEach-Object {
      [pscustomobject]@{
        Thumbprint = $_.Thumbprint
        Subject = $_.Subject
        NotAfter = $_.NotAfter.ToUniversalTime().ToString('o')
        HasPrivateKey = $_.HasPrivateKey
        CodeSigningEku = [bool]($_.EnhancedKeyUsageList | Where-Object {$_.ObjectId.Value -eq '1.3.6.1.5.5.7.3.3'})
      }
    }
)
ConvertTo-Json -Compress -InputObject $items
"""
    env = dict(os.environ)
    env["CLASQ_PREFLIGHT_THUMBPRINT"] = thumbprint
    result = runner(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=False, shell=False, env=env,
    )
    if result.returncode != 0:
        raise SigningError("certificate-store preflight failed")
    records = json.loads(result.stdout or "[]")
    if isinstance(records, dict):
        records = [records]
    if len(records) != 1:
        raise SigningError(f"certificate selector matched {len(records)} certificates")
    record = records[0]
    if not record.get("HasPrivateKey"):
        raise SigningError("code-signing certificate private key is unavailable")
    if not record.get("CodeSigningEku"):
        raise SigningError("certificate does not contain the code-signing EKU")
    from datetime import datetime, timezone
    expires = datetime.fromisoformat(record["NotAfter"].replace("Z", "+00:00"))
    if expires <= datetime.now(timezone.utc):
        raise SigningError("code-signing certificate is expired")
    return record


def read_authenticode_signature(
    target: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    script = r"""
$s = Get-AuthenticodeSignature -LiteralPath $env:CLASQ_SIGNATURE_TARGET
[pscustomobject]@{
  Status = [string]$s.Status
  Thumbprint = if ($s.SignerCertificate) {$s.SignerCertificate.Thumbprint} else {''}
  Subject = if ($s.SignerCertificate) {$s.SignerCertificate.Subject} else {''}
  Timestamped = [bool]$s.TimeStamperCertificate
} | ConvertTo-Json -Compress
"""
    env = dict(os.environ)
    env["CLASQ_SIGNATURE_TARGET"] = str(target)
    result = runner(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=False, shell=False, env=env,
    )
    if result.returncode != 0:
        raise SigningError("Authenticode status inspection failed")
    return json.loads(result.stdout)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise SigningError("unsupported signing configuration schema")
    serialized = json.dumps(data).lower()
    if any(f'"{key}"' in serialized for key in SECRET_KEYS):
        raise SigningError("signing configuration must not contain secrets")
    if data.get("digest_algorithm") != "SHA256":
        raise SigningError("release signing digest must be SHA256")
    timestamp = data.get("timestamp", {})
    if timestamp.get("protocol") != "RFC3161" or timestamp.get("digest_algorithm") != "SHA256":
        raise SigningError("RFC3161/SHA256 timestamp policy is required")
    targets = data.get("owned_targets", [])
    ids = [item.get("id") for item in targets]
    if ids != ["application", "installer"] or len(set(ids)) != len(ids):
        raise SigningError("owned signing targets must be application then installer")
    return data


def _version_tuple(path: Path) -> tuple[int, ...]:
    for part in reversed(path.parts):
        if re.fullmatch(r"\d+(?:\.\d+)+", part):
            return tuple(int(value) for value in part.split("."))
    return ()


def discover_signtool(
    environ: Mapping[str, str] | None = None,
    *,
    program_files_x86: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("CLASQ_SIGNTOOL_EXE", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.name.lower() != "signtool.exe" or not candidate.is_file():
            raise SigningError("CLASQ_SIGNTOOL_EXE must name an existing signtool.exe")
        return candidate.resolve()

    root = program_files_x86 or Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    sdk_bin = root / "Windows Kits" / "10" / "bin"
    candidates = list(sdk_bin.glob("*/x64/signtool.exe")) if sdk_bin.is_dir() else []
    if not candidates:
        raise SigningError(
            "Microsoft SignTool was not found. Install the Windows SDK or set "
            "CLASQ_SIGNTOOL_EXE explicitly. PATH is intentionally not searched."
        )
    return max(candidates, key=lambda path: (_version_tuple(path), str(path))).resolve()


def load_credential(environ: Mapping[str, str] | None = None) -> Credential:
    env = os.environ if environ is None else environ
    thumbprint = re.sub(r"\s+", "", env.get("CLASQ_SIGN_CERT_THUMBPRINT", "")).upper()
    pfx = env.get("CLASQ_SIGN_PFX", "").strip()
    if bool(thumbprint) == bool(pfx):
        raise SigningError("configure exactly one certificate source: thumbprint or PFX")
    if thumbprint:
        if not THUMBPRINT_RE.fullmatch(thumbprint):
            raise SigningError("certificate thumbprint must be exact hexadecimal SHA-1 or SHA-256")
        return Credential("store", thumbprint)
    path = Path(pfx).expanduser()
    if path.suffix.lower() not in {".pfx", ".p12"} or not path.is_file():
        raise SigningError("CLASQ_SIGN_PFX must name an existing .pfx or .p12 file")
    return Credential("pfx", str(path.resolve()), env.get("CLASQ_SIGN_PFX_PASSWORD"))


def resolve_owned_target(target_id: str, root: Path, config: dict) -> Path:
    item = next((entry for entry in config["owned_targets"] if entry["id"] == target_id), None)
    if item is None:
        raise SigningError(f"target is not Clasq-owned: {target_id}")
    matches = sorted(root.glob(item["relative_path"]))
    if len(matches) != 1 or not matches[0].is_file():
        raise SigningError(f"expected exactly one {target_id} target, found {len(matches)}")
    return matches[0].resolve()


def build_sign_command(
    signtool: Path,
    target: Path,
    credential: Credential,
    timestamp_url: str,
) -> tuple[list[str], list[str]]:
    if not timestamp_url.lower().startswith("https://"):
        raise SigningError("RFC3161 timestamp URL must use HTTPS")
    args = [str(signtool), "sign", "/fd", "SHA256", "/tr", timestamp_url, "/td", "SHA256"]
    redacted = list(args)
    if credential.kind == "store":
        args += ["/sha1", credential.selector]
        redacted += ["/sha1", credential.selector]
    elif credential.kind == "pfx":
        args += ["/f", credential.selector]
        redacted += ["/f", credential.selector]
        if credential.password is not None:
            args += ["/p", credential.password]
            redacted += ["/p", "<redacted>"]
    else:
        raise SigningError(f"unsupported credential kind: {credential.kind}")
    args.append(str(target))
    redacted.append(str(target))
    return args, redacted


def build_verify_command(signtool: Path, target: Path, *, require_timestamp: bool = True) -> list[str]:
    args = [str(signtool), "verify", "/pa", "/all"]
    if require_timestamp:
        args.append("/tw")
    args.append(str(target))
    return args


def sign_and_verify(
    target: Path,
    signtool: Path,
    credential: Credential,
    timestamp_url: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    signature_reader: Callable[[Path], dict] | None = None,
) -> dict:
    if not target.is_file():
        raise SigningError(f"signing target not found: {target}")
    before = sha256_file(target)
    command, redacted = build_sign_command(signtool, target, credential, timestamp_url)
    signed = runner(command, capture_output=True, text=True, check=False, shell=False)
    if signed.returncode != 0:
        raise SigningError(f"SignTool sign failed ({signed.returncode}): {signed.stderr.strip()}")
    verified = runner(
        build_verify_command(signtool, target),
        capture_output=True, text=True, check=False, shell=False,
    )
    if verified.returncode != 0:
        raise SigningError(f"post-sign verification failed ({verified.returncode})")
    signature = signature_reader(target) if signature_reader else None
    if signature is not None:
        if signature.get("Status") != "Valid" or not signature.get("Timestamped"):
            raise SigningError("Authenticode status or timestamp verification failed")
        if credential.kind == "store" and signature.get("Thumbprint", "").upper() != credential.selector:
            raise SigningError("signed artifact does not match the expected certificate thumbprint")
    return {
        "target": target.name,
        "pre_sign_sha256": before,
        "post_sign_sha256": sha256_file(target),
        "signature_state": "verified",
        "timestamp_required": True,
        "command": redacted,
        "signer": signature.get("Subject", "") if signature else "verified-by-signtool",
    }


def development_preflight(require_signing: bool, environ: Mapping[str, str] | None = None) -> str:
    if not require_signing:
        return "unsigned-development-allowed"
    discover_signtool(environ)
    load_credential(environ)
    env = os.environ if environ is None else environ
    if not env.get("CLASQ_SIGN_TIMESTAMP_URL", "").strip():
        raise SigningError("release signing requires CLASQ_SIGN_TIMESTAMP_URL")
    return "release-signing-ready"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target", choices=("application", "installer"))
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--require-signing", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.project / CONFIG_RELATIVE_PATH)
    state = development_preflight(args.require_signing)
    if not args.require_signing:
        print(state)
        return 0
    if args.target is None or args.artifact_root is None:
        raise SigningError("release signing requires --target and --artifact-root")
    tool = discover_signtool()
    credential = load_credential()
    if credential.kind == "store":
        preflight_store_certificate(credential.selector)
    target = resolve_owned_target(args.target, args.artifact_root, config)
    report = sign_and_verify(
        target, tool, credential, os.environ["CLASQ_SIGN_TIMESTAMP_URL"],
        signature_reader=read_authenticode_signature,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SigningError as exc:
        raise SystemExit(f"signing prerequisite failed: {exc}")

# Windows one-dir build prerequisites

Clasq pins its FFmpeg build input in `packaging/ffmpeg-manifest.json`. The
binary is not stored in Git and is staged separately from PyInstaller output.

Obtain the exact FFmpeg artifact named in the manifest, then validate and stage
it:

```powershell
py -3.13 scripts/prepare_ffmpeg.py --source C:\path\to\ffmpeg.exe
```

Alternatively, set `CLASQ_FFMPEG_EXE` to that artifact. The spec validates and
atomically stages the override before packaging. Overrides do not bypass size,
SHA-256, version, or Windows x86-64 PE validation.

The normal staged input is `.build/runtime/ffmpeg/ffmpeg.exe`. The resolver
does not search `PATH`, `C:\ffmpeg`, or previous `dist-*` directories. Missing
or mismatched input stops the build with a preparation command.

```powershell
$env:CLASQ_LLAMA_RUNTIME = 'C:\path\to\llama-runtime'
py -3.13 -m PyInstaller --noconfirm --clean --workpath build-batch20 --distpath dist-batch20 clasq.spec
```

## Windows release signing

Development builds remain unsigned and require no certificate:

```powershell
py -3.13 scripts/windows_signing.py
```

Release signing is an explicit, fail-closed step. Build the one-dir application,
sign and verify the application-owned executable, build the installer from that
directory, then sign and verify the installer:

```text
PyInstaller clean build
-> sign Clasq.exe
-> verify Clasq.exe
-> Inno Setup build
-> sign Clasq_Setup_<version>.exe
-> verify installer
```

Use an exact certificate-store thumbprint when possible:

```powershell
$env:CLASQ_SIGN_CERT_THUMBPRINT = '<exact certificate thumbprint>'
$env:CLASQ_SIGN_TIMESTAMP_URL = '<certificate-provider RFC3161 HTTPS URL>'
py -3.13 scripts/windows_signing.py --require-signing --target application --artifact-root dist-release/Clasq
```

After the application signature verifies, build the installer with the existing
Inno Setup command and sign its output directory as the outer artifact:

```powershell
& $IsccExe '/DSourceDir=..\dist-release\Clasq' '/DInstallerOutputDir=..\dist-release\installer' 'installer\Clasq.iss'
py -3.13 scripts/windows_signing.py --require-signing --target installer --artifact-root dist-release/installer
```

Do not rebuild or modify the one-dir tree between application signing and the
installer build, and do not modify the installer after its signature verifies.

For an externally supplied PFX, set `CLASQ_SIGN_PFX` and optionally
`CLASQ_SIGN_PFX_PASSWORD` through a secure release/CI secret. The password is
redacted from reports, but SignTool's PFX interface places it in the child
process argument list; importing the certificate securely and selecting its
exact store thumbprint is preferred. Never commit PFX/P12 files or passwords.

`CLASQ_SIGNTOOL_EXE` may explicitly select an installed Windows SDK SignTool.
Without it, the tool selects the newest versioned x64 SDK copy. It deliberately
does not search `PATH`. The timestamp service is not hardcoded because it must
match the selected certificate/provider policy.

The signing target policy is in `packaging/windows-signing.json`. It permits
only `Clasq.exe` and the final Inno Setup installer. Bundled FFmpeg,
llama-server, CUDA, Qt, DLL and PYD artifacts are preserved and are not signed
with the Clasq certificate.
# Runtime logs

Clasq writes local application diagnostics to `%LOCALAPPDATA%\Clasq\logs\clasq.log`.
The UTF-8 log rotates at 4 MiB and retains four backups. It contains operational
metadata, not document bodies, prompts, or full AI responses. Set
`CLASQ_LOG_LEVEL=DEBUG` explicitly for development diagnostics; the default is
`INFO`.

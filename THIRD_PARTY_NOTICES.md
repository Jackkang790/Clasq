# Third-Party Notices

Clasq's Windows distribution contains third-party software. This file records
artifact provenance and points to the unmodified upstream license texts shipped
in `THIRD_PARTY_LICENSES`. It is an inventory, not legal advice and not a new
license grant. Clasq's own license remains in `LICENSE`.

## Runtime executables and libraries

| Component | Version / artifact | Distribution form | License evidence | Review status |
| --- | --- | --- | --- | --- |
| FFmpeg | 8.1.2 full build from gyan.dev | Unmodified standalone static executable, invoked by subprocess | Provider identifies the build as GPLv3; upstream FFmpeg license files are included | **LEGAL REVIEW REQUIRED** for distribution/source and Clasq compatibility obligations |
| llama.cpp / llama-server | build 10549, commit `b2e5e9b28` | Executable and DLLs | MIT license at the exact upstream commit | Verified |
| PySide6 / shiboken6 / Qt | 6.11.1 | Python bindings, dynamically loaded Qt DLLs and plugins | Wheel metadata and official Qt/PySide licensing documentation | **LEGAL REVIEW REQUIRED** |
| Qt Virtual Keyboard | 6.11.1 | Not bundled in the Batch 26 Windows one-dir | Excluded with its unused QML/Quick dependency chain; Qt license texts remain for other bundled Qt components | Technically verified as not bundled; other Qt review remains |
| Qt PDF | 6.11.1 | Not bundled in the Batch 28 Windows one-dir | Clasq PDF recognition, extraction and indexing use pypdf; Qt license texts remain for other bundled Qt components | Technically verified as not bundled; other Qt review remains |
| Python | 3.13.15 | Embedded interpreter | PSF License Version 2 and incorporated licenses | Verified |
| OpenSSL | 3.0.21 | DLLs used by TLS support | Apache License 2.0 | Verified |
| NVIDIA CUDA Runtime / cuBLAS | CUDA 12 family | DLLs used by the llama.cpp CUDA backend | NVIDIA CUDA Toolkit EULA reference | **LEGAL REVIEW REQUIRED** for redistribution terms |

The FFmpeg executable is not linked into the Clasq Python executable. Clasq
launches it as a separate process with an argument list to extract video frames.
The provider's "static" designation describes how the standalone FFmpeg
executable was built; it does not describe Clasq linking to FFmpeg. No conclusion
about license compatibility follows from this technical boundary.

## Runtime Python packages

The distribution includes Pillow 12.3.0 (MIT-CMU), lxml 6.1.1 (BSD-3-Clause),
Requests 2.34.2 (Apache-2.0), certifi 2026.7.22 (MPL-2.0), charset-normalizer
3.5.0 (MIT), urllib3 2.7.0 (MIT), python-docx 1.2.0 (MIT), python-pptx 1.0.2
(MIT), openpyxl 3.1.5 (MIT), pypdf 6.15.0 (BSD-3-Clause), python-bidi
0.6.11 (LGPL-3.0-or-later), olefile 0.47 (BSD), and setuptools 84.0.0
(MIT plus separately licensed vendored components). Their upstream license and
notice files are provided under `THIRD_PARTY_LICENSES/Python-Packages`.

The machine-readable, evidence-linked inventory is maintained in
`packaging/third-party-components.json`. Build-only components are recorded
there but are not presented as bundled runtime packages.

## Downloaded model

The GGUF model is not contained in the Windows one-dir distribution. Clasq may
download `unsloth/Qwen3-VL-8B-Instruct-GGUF` (Q4_K_M and mmproj) into the user's
cache. The model repository declares Apache-2.0. Model redistribution and product
usage obligations remain **LEGAL REVIEW REQUIRED**.

## Source and license references

FFmpeg version, hash, configuration, and source references are in
`FFMPEG_SOURCE_INFO.txt`. Official license texts are in
`THIRD_PARTY_LICENSES`; upstream/source URLs and evidence are in the component
inventory. Patent or royalty clearance for codecs is outside this technical
inventory and requires separate legal review.

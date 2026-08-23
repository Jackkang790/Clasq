# Clasq Windows 설치 프로그램 빌드 스크립트
# 실행: cd Z:\sjb\Clasq && .\scripts\build_installer.ps1
# 사전 조건: PyInstaller 빌드 완료 (dist\Clasq 존재)

param(
    [string]$ISCC = "C:\Users\USER1\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
    [string]$IssFile = "installer\Clasq.iss"
)

Set-Location (Split-Path $PSScriptRoot -Parent)
$ErrorActionPreference = "Stop"

Write-Host "=== Clasq Installer Build ===" -ForegroundColor Cyan

# 사전 검사
if (-not (Test-Path $ISCC)) { Write-Error "ISCC.exe not found: $ISCC"; exit 1 }
if (-not (Test-Path $IssFile)) { Write-Error ".iss not found: $IssFile"; exit 1 }
if (-not (Test-Path "dist\Clasq\Clasq.exe")) {
    Write-Error "dist\Clasq\Clasq.exe not found. Run build_windows.ps1 first."
    exit 1
}

$distMB = [math]::Round((Get-ChildItem "dist\Clasq" -Recurse | Measure-Object Length -Sum).Sum / 1MB, 0)
Write-Host "dist/Clasq 크기: ${distMB}MB"

# 출력 디렉터리
New-Item -ItemType Directory -Force "dist\installer" | Out-Null

# 빌드 실행
Write-Host ""
Write-Host "Inno Setup 빌드 시작..." -ForegroundColor Yellow
$t0 = [DateTime]::Now
& $ISCC $IssFile
$elapsed = ([DateTime]::Now - $t0).TotalSeconds

if ($LASTEXITCODE -ne 0) { Write-Error "빌드 실패 (exit $LASTEXITCODE)"; exit 1 }

# 결과
$out = Get-ChildItem "dist\installer\*.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
if ($out) {
    $sizeMB = [math]::Round($out.Length / 1MB, 0)
    Write-Host ""
    Write-Host "=== 완료 ($([math]::Round($elapsed,0))s) ===" -ForegroundColor Green
    Write-Host "설치 파일: $($out.FullName)"
    Write-Host "크기: ${sizeMB}MB"
} else {
    Write-Error "installer 파일을 찾을 수 없습니다"
}

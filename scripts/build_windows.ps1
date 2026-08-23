# Clasq Windows 배포 빌드 스크립트
# 실행: cd Z:\sjb\Clasq && .\scripts\build_windows.ps1

param(
    [string]$Python = "C:\Users\USER1\AppData\Local\Programs\Python\Python313\python.exe",
    [switch]$Clean
)

Set-Location (Split-Path $PSScriptRoot -Parent)
$ErrorActionPreference = "Stop"

Write-Host "=== Clasq Windows Build ===" -ForegroundColor Cyan
Write-Host "Python: $Python"
Write-Host "Project: $(Get-Location)"

# 사전 검사
if (-not (Test-Path $Python)) { Write-Error "Python not found: $Python"; exit 1 }
if (-not (Test-Path "clasq.spec")) { Write-Error "clasq.spec not found"; exit 1 }
if (-not (Test-Path "C:\llama-cpp\bin\llama-server.exe")) {
    Write-Error "llama-server.exe not found at C:\llama-cpp\bin"
    exit 1
}

# 기존 빌드 정리
if ($Clean -and (Test-Path "dist\Clasq")) {
    Write-Host "기존 dist\Clasq 삭제..."
    Remove-Item "dist\Clasq" -Recurse -Force
}
if (Test-Path "build") { Remove-Item "build" -Recurse -Force -ErrorAction SilentlyContinue }

# PyInstaller 설치 확인
$pyiCheck = & $Python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
if (-not $pyiCheck) {
    Write-Host "PyInstaller 설치 중..."
    & $Python -m pip install pyinstaller --quiet
}
Write-Host "PyInstaller: $pyiCheck"

# 빌드 실행
Write-Host ""
Write-Host "빌드 시작..." -ForegroundColor Yellow
$t0 = [DateTime]::Now
& $Python -m PyInstaller clasq.spec --noconfirm --clean
$elapsed = ([DateTime]::Now - $t0).TotalSeconds

if ($LASTEXITCODE -ne 0) { Write-Error "빌드 실패 (exit $LASTEXITCODE)"; exit 1 }

# 결과 확인
Write-Host ""
Write-Host "=== 빌드 완료 ($([math]::Round($elapsed,1))s) ===" -ForegroundColor Green

$distDir = "dist\Clasq"
if (Test-Path $distDir) {
    $exe = Get-Item "$distDir\Clasq.exe" -ErrorAction SilentlyContinue
    $runtimeFiles = Get-ChildItem "$distDir\runtime" -ErrorAction SilentlyContinue
    $totalMB = [math]::Round((Get-ChildItem $distDir -Recurse | Measure-Object Length -Sum).Sum / 1MB, 0)

    Write-Host "Clasq.exe: $($exe.Length) bytes"
    Write-Host "runtime/ 파일: $($runtimeFiles.Count)개"
    Write-Host "전체 크기: ${totalMB}MB"
    Write-Host "경로: $(Resolve-Path $distDir)"
} else {
    Write-Error "dist\Clasq 디렉터리가 없습니다"
}

; Clasq Windows 설치 프로그램
; Inno Setup 6.7+
; 빌드: iscc installer\Clasq.iss

#define AppName "Clasq"
#define AppVersion "1.0.0"
#define AppPublisher "Lobo2u"
#define AppURL "https://github.com/Jackkang790/Clasq"
#define AppExeName "Clasq.exe"
#define DistDir "..\dist\Clasq"

[Setup]
; 앱 고유 ID — 변경하지 마라 (업데이트/제거 추적에 사용됨)
AppId={{4F05ECAB-8304-4CE5-9A65-C3DDD44E5AFA}

AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; 64bit Windows 전용 설치 (Program Files\Clasq)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}

; 바탕화면 바로가기 기본값: 미선택
AllowNoIcons=yes

; 출력 설정
OutputDir=..\dist\installer
OutputBaseFilename=Clasq_Setup_{#AppVersion}

; 압축: lzma2 + solid — 2GB 바이너리에 적절한 균형
; ultra64는 시간이 너무 걸리고 바이너리 DLL은 압축률 차이 미미
Compression=lzma2
SolidCompression=yes
CompressionThreads=4

; 설치에 관리자 권한 필요 (Program Files 쓰기용)
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; 실행 중인 Clasq.exe는 설치 전 종료 안내
; llama-server는 이름으로 강제 종료하지 않음 (사용자 독립 프로세스 보호)
CloseApplications=yes
CloseApplicationsFilter=Clasq.exe
RestartIfNeededByRun=no

; 설치 UI
WizardStyle=modern
SetupLogging=yes

; 아이콘 없음 (추가 시: SetupIconFile=..\assets\clasq.ico)

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
    Description: "바탕 화면에 바로 가기 만들기"; \
    GroupDescription: "추가 작업:"; \
    Flags: unchecked

[Files]
; ── PyInstaller 산출물 ─────────────────────────────────────────────────
; dist/Clasq/ 구조를 {app}/ 에 그대로 복사
; _internal/ 하위 구조를 유지해야 PyInstaller 상대경로가 깨지지 않음

; 메인 실행 파일
Source: "{#DistDir}\Clasq.exe"; \
    DestDir: "{app}"; \
    Flags: ignoreversion

; _internal/ 전체 (Python, 패키지, runtime, assets)
Source: "{#DistDir}\_internal\*"; \
    DestDir: "{app}\_internal"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ── 모델 파일은 포함하지 않음 ────────────────────────────────────────
; qwen3vl-8b-q4_k_m.gguf, mmproj-bf16.gguf
; → 최초 실행 시 %LOCALAPPDATA%\Clasq\models\ 에 자동 다운로드

[Icons]
; 시작 메뉴
Name: "{group}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    Comment: "AI 파일 관리 시스템"

Name: "{group}\{#AppName} 제거"; \
    Filename: "{uninstallexe}"

; 바탕화면 (선택 작업)
Name: "{commondesktop}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    Comment: "AI 파일 관리 시스템"; \
    Tasks: desktopicon

[Run]
; 설치 완료 후 Clasq 실행 (선택)
Filename: "{app}\{#AppExeName}"; \
    Description: "{#AppName} 실행"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; 제거 전 Clasq.exe 종료 (실행 중인 경우)
Filename: "taskkill.exe"; \
    Parameters: "/f /im Clasq.exe"; \
    Flags: runhidden waituntilterminated; \
    RunOnceId: "StopClasq"
; 주의: llama-server는 PID를 모르므로 이름으로 종료하지 않음
;       앱이 자동 종료하지 못한 경우 사용자가 직접 종료해야 함

[UninstallDelete]
; Program Files\Clasq 내 앱 파일 제거
; %LOCALAPPDATA%\Clasq\models (AI 모델)는 자동 삭제하지 않음
; → 재설치 후에도 기존 모델 재사용 가능
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeSetup: Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // Clasq.exe 실행 중이면 종료 요청
  if FindWindowByClassName('Qt651QWindowIcon') > 0 then begin
    if MsgBox('Clasq가 현재 실행 중입니다.' + #13#10 +
              '설치를 계속하려면 Clasq를 닫아주세요.',
              mbInformation, MB_OKCANCEL) = IDCANCEL then begin
      Result := False;
      Exit;
    end;
  end;
end;

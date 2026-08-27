#ifndef MyAppName
  #define MyAppName "Clasq"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "1.2.1"
#endif
#define MyAppPublisher "Clasq"
#define MyAppExeName "Clasq.exe"
#ifndef MyAppId
  #define MyAppId "{{21E38F55-7A79-49A4-84E6-1F6E41F922E2}"
#endif
#ifndef UserDataDir
  #define UserDataDir "{localappdata}\Clasq"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Clasq"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\dist\installer"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName}
DefaultDirName={localappdata}\Programs\Clasq
DefaultGroupName=Clasq
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#InstallerOutputDir}
OutputBaseFilename=Clasq_Setup_{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
Uninstallable=yes
CreateUninstallRegKey=yes
CloseApplications=yes
RestartApplications=no
AppMutex=Clasq-21E38F55-7A79-49A4-84E6-1F6E41F922E2

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Clasq"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Clasq 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Clasq"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Clasq"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; User models, database, settings and logs intentionally survive uninstall.
; Only files within the application installation directory are removed.

[Code]
var
  DeleteClasqUserData: Boolean;

function HasUninstallParameter(const Name: String): Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
  begin
    if CompareText(ParamStr(Index), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
var
  Choice: Integer;
begin
  Result := True;
  DeleteClasqUserData := HasUninstallParameter('/DELETEUSERDATA');

  { Silent automation defaults to the safe, data-preserving uninstall. }
  if DeleteClasqUserData or HasUninstallParameter('/SILENT') or
     HasUninstallParameter('/VERYSILENT') then
    Exit;

  Choice := MsgBox(
    'Clasq 프로그램을 제거합니다.' + #13#10 + #13#10 +
    '아니요: 프로그램만 제거하고 다운로드한 AI 모델 및 사용자 데이터를 유지합니다.' + #13#10 +
    '예: Clasq 설정, 분석 데이터, 캐시 및 다운로드한 AI 모델도 함께 삭제합니다.' + #13#10 + #13#10 +
    '어느 경우에도 Clasq에 추가한 원본 사진, 영상, 문서 및 작업 폴더는 삭제되지 않습니다.' + #13#10 + #13#10 +
    'Clasq 사용자 데이터까지 완전히 삭제하시겠습니까?',
    mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON2);

  if Choice = IDYES then
    DeleteClasqUserData := True
  else if Choice = IDCANCEL then
    Result := False;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  if (CurUninstallStep <> usPostUninstall) or not DeleteClasqUserData then
    Exit;

  { This fixed root is owned by Clasq. Never inspect the database or delete
    registered source/work folders. An external CLASQ_MODEL_CACHE_DIR is also
    deliberately outside this boundary. }
  DataPath := ExpandConstant('{#UserDataDir}');
  Log('Full uninstall requested; deleting Clasq-owned user data: ' + DataPath);
  if DirExists(DataPath) and not DelTree(DataPath, True, True, True) then
  begin
    Log('WARNING: unable to remove all Clasq-owned user data: ' + DataPath);
    if not HasUninstallParameter('/SILENT') and
       not HasUninstallParameter('/VERYSILENT') then
      MsgBox(
        '일부 Clasq 사용자 데이터를 삭제하지 못했습니다.' + #13#10 +
        '다른 프로세스가 파일을 사용 중인지 확인한 뒤 남은 폴더를 직접 삭제해 주세요:' + #13#10 + DataPath,
        mbError, MB_OK);
  end;
end;

// Release signing is deliberately orchestrated outside this file so certificate
// selectors and secrets never enter the .iss source. The required order is:
// signed+verified Clasq.exe -> ISCC -> signed+verified installer. See BUILD.md.

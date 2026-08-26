#define MyAppName "Clasq"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Clasq"
#define MyAppExeName "Clasq.exe"
#ifndef SourceDir
  #define SourceDir "..\dist\Clasq"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\dist\installer"
#endif

[Setup]
AppId={{21E38F55-7A79-49A4-84E6-1F6E41F922E2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
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
Name: "{autodesktop}\Clasq"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Clasq"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; User models, database, settings and logs intentionally survive uninstall.
; Only files within the application installation directory are removed.

; Release signing is deliberately orchestrated outside this file so certificate
; selectors and secrets never enter the .iss source. The required order is:
; signed+verified Clasq.exe -> ISCC -> signed+verified installer. See BUILD.md.

; DataAgent Setup — bundles the app + opencode CLI (+ opt-in Cloudflare WARP MSI).
;
; Installs the app, and — only when built with /DIncludeWarp (see
; scripts/build_windows.ps1 -IncludeWarp) — silently installs Cloudflare WARP
; (single admin elevation — this installer already runs elevated), registers
; the device and connects the tunnel so the Data Agent AI's egress-IP
; rotation works out of the box. The opencode CLI is placed next to the app
; exe so the AI server resolves it immediately
; (agent.opencode_bootstrap checks the exe's own dir).
;
; Build with: scripts/build_windows.ps1 [-IncludeWarp]
;   (or: iscc installers\DataAgent-Setup.iss
;    or: iscc /DIncludeWarp installers\DataAgent-Setup.iss  — with WARP)

#define MyAppName "DataAgent"
#define MyAppVersion "1.0.0"
; 4-part form for the exe version resources (Inno convention).
#define MyAppVersionInfo "1.0.0.0"
#define MyAppExeName "DataAgentShell.exe"
#define MyAppWebExeName "DataAgent.exe"
#define MyAppPublisher "DataAgent"

[Setup]
AppId={{7C2B4E1A-9F3D-4A6B-8E5C-2D1F0A9B6C4E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=DataAgent-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes
; Exe properties + installer branding (see packaging\windows\).
SetupIconFile=..\packaging\windows\app.ico
VersionInfoVersion={#MyAppVersionInfo}
VersionInfoProductVersion={#MyAppVersionInfo}
; Close a running DataAgent on upgrade so in-use files can be replaced.
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The desktop shell (PyInstaller onedir build of run.py -> DataAgentShell.exe).
; DataAgentShell.exe launches the desktop window by default (no args).
Source: "..\dist\DataAgentShell\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The headless web server (PyInstaller onefile build of run.py). Used by the
; "DataAgent (web)" shortcut and by advanced users who prefer the browser.
Source: "..\dist\DataAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
; Bundled opencode CLI (downloaded by build_windows.ps1). Placed next to the
; app exe so agent.opencode_bootstrap resolves it via the exe's own directory.
Source: "opencode\opencode.exe"; DestDir: "{app}"; Flags: ignoreversion
#ifdef IncludeWarp
; Bundled WARP MSI (universal x64/arm64, downloaded by build_windows.ps1 -IncludeWarp).
; Deleted after install.
Source: "warp\Cloudflare_WARP_Release.msi"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName} (web)"; Filename: "{app}\{#MyAppWebExeName}"; Parameters: "web"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
#ifdef IncludeWarp
const
  WARP_CLI = '{autopf}\Cloudflare\Cloudflare WARP\warp-cli.exe';
#endif

function ExecHidden(const Filename, Params: string; const Wait: Boolean): Integer;
var
  ResultCode: Integer;
begin
  if Exec(Filename, Params, '', SW_HIDE, Wait, ResultCode) then
    Result := ResultCode
  else
    Result := -1;
end;

#ifdef IncludeWarp
procedure InstallWarp;
var
  MsiPath: string;
  CliPath: string;
  I: Integer;
begin
  MsiPath := ExpandConstant('{tmp}\Cloudflare_WARP_Release.msi');
  Log('Installing Cloudflare WARP: ' + MsiPath);
  // Silent MSI. We are already elevated (PrivilegesRequired=admin), so no
  // extra UAC prompt. 3010 (reboot required) is still a success.
  ExecHidden(ExpandConstant('{sys}\msiexec.exe'), '/i "' + MsiPath + '" /qn /norestart', True);

  // Wait up to ~5 min for warp-cli.exe to appear.
  CliPath := ExpandConstant(WARP_CLI);
  for I := 0 to 150 do
  begin
    if FileExists(CliPath) then Break;
    Sleep(2000);
  end;

  if FileExists(CliPath) then
  begin
    Log('Registering WARP device…');
    ExecHidden(CliPath, 'registration new', True);
    Log('Connecting WARP tunnel…');
    ExecHidden(CliPath, 'connect', True);
  end
  else
    Log('warp-cli.exe not found after MSI install — skipping register/connect.');
end;
#endif

procedure CurStepChanged(CurStep: TSetupStep);
begin
#ifdef IncludeWarp
  if CurStep = ssPostInstall then
    InstallWarp;
#endif
end;
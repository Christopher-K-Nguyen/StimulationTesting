; StimulationTesting -- Inno Setup script
;
; Builds a single-file .exe installer that drops the PyInstaller-frozen
; application under Program Files, adds Start Menu / Desktop shortcuts,
; and walks through three prerequisite installs after the file copy:
;
;   1. Microsoft Visual C++ 2015-2022 Redistributable (x64)  -- REQUIRED
;        Silent download + install (/quiet /norestart). Bundled PyQt6,
;        scipy, numpy, matplotlib all ship MSVC-built C extensions; on a
;        clean Windows machine without VS or any other Python install,
;        the frozen .exe will fail with "api-ms-win-crt-runtime-l1-1-0.dll
;        is missing" without this.
;
;   2. Plexon PlexStim 2.0 SDK                              -- RECOMMENDED
;        Required to drive a real PlexStim. Without it the GUI still
;        launches in simulator mode. Plexon's setup is interactive; we
;        download StimulatorV2Setup.exe and exec it for the user.
;
;   3. NI-VISA (or any IVI VISA implementation, e.g. TekVISA)  -- OPTIONAL
;        Required to drive a real Tektronix scope over USB-TMC at full
;        performance. The app falls back to the bundled pyvisa-py
;        backend without it (works for most USB-TMC scopes, slightly
;        slower). The download URL is opened in the user's browser
;        because NI rotates direct URLs and gates them behind login.
;
; To build this file:
;     python installer/build.py            (runs both PyInstaller + ISCC)
;     ISCC installer/StimulationTesting.iss   (just Inno Setup, after the
;                                              PyInstaller build)

#define AppName "StimulationTesting"
#define AppVersion "0.2.0"
#define AppPublisher "Neural Interfaces Lab"
#define AppExeName "StimulationTesting.exe"
#define ViewerExeName "StimulationTestingViewer.exe"

#define PlexStimUrl "https://plexon.com/wp-content/uploads/2017/06/StimulatorV2Setup.exe"
#define VCRedistUrl "https://aka.ms/vs/17/release/vc_redist.x64.exe"
#define NiVisaPageUrl "https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html"

[Setup]
AppId={{6E5CDD7E-9D2E-4D38-8B2E-1E2A0BD8C9F1}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/
AppSupportURL=https://github.com/
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir={#SourcePath}\Output
OutputBaseFilename=StimulationTesting-Setup-{#AppVersion}
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"
Name: "viewericon"; Description: "Add a desktop shortcut for the &Viewer too"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Components]
Name: "app"; Description: "{#AppName} (required)"; Types: full compact custom; Flags: fixed
Name: "prereq"; Description: "Install missing prerequisites"; Types: full
Name: "prereq\vcredist"; Description: "Microsoft Visual C++ 2015-2022 Redistributable (x64) -- required"; Types: full compact
Name: "prereq\plexstim"; Description: "Plexon PlexStim 2.0 SDK (real stimulator)"; Types: full
Name: "prereq\nivisa"; Description: "NI-VISA runtime (real Tektronix scope)"; Types: full

[Files]
; Drop the entire PyInstaller dist tree (StimulationTesting + its DLLs +
; the vendored PlexStim64.dll) under {app}.
Source: "dist\StimulationTesting\*"; DestDir: "{app}"; Components: app; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{#AppName} Viewer"; Filename: "{app}\{#ViewerExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon
Name: "{autodesktop}\{#AppName} Viewer"; Filename: "{app}\{#ViewerExeName}"; \
    Tasks: viewericon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
    Flags: postinstall skipifsilent nowait

; ---------------------------------------------------------------------------
; Prerequisite checks -- run near the end of the install, after our app
; is on disk but before the wizard's final page. Each one detects whether
; the dependency is already present, and only prompts the user if it
; isn't. Order matters: VC++ first (silent, ~24 MB) so the rest of the
; deps work; then PlexStim and NI-VISA (interactive, vendor UIs).
; ---------------------------------------------------------------------------
[Code]
const
  UninstallPath  = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall';
  VCRuntimesPath = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';
  NiVisaPath     = 'SOFTWARE\National Instruments\NI-VISA';
  IviVisaPath    = 'SOFTWARE\IVI Foundation\VISA\Win64\CurrentVersion';

{ ----- Generic helpers ----------------------------------------------------- }

function DownloadFile(const Url, Dest: String): Boolean;
{ Shell out to PowerShell's Invoke-WebRequest. Inno Setup 6 has
  DownloadTemporaryFile via [Setup]'s DownloadURL machinery, but that
  needs a custom wizard page. PowerShell is on every supported Windows
  and works with no extra setup. Returns True on a successful 0-exit
  with the file actually present. }
var
  ResultCode: Integer;
begin
  Result := False;
  if Exec('powershell.exe',
          '-NoProfile -ExecutionPolicy Bypass -Command ' +
          '"$ProgressPreference=''SilentlyContinue''; ' +
          'Invoke-WebRequest -UseBasicParsing -Uri ''' + Url + ''' ' +
          '-OutFile ''' + Dest + '''"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := (ResultCode = 0) and FileExists(Dest);
  end;
end;

function UninstallEntryMatches(const Hive: Integer; const Needles: array of String): Boolean;
{ Walk one Uninstall hive and look for an entry whose DisplayName
  contains any of the supplied substrings (case-insensitive). Used for
  apps where the installer GUID has changed across versions. }
var
  Names: TArrayOfString;
  i, j: Integer;
  Display, Lower: String;
begin
  Result := False;
  if not RegGetSubkeyNames(Hive, UninstallPath, Names) then
    Exit;
  for i := 0 to GetArrayLength(Names) - 1 do
  begin
    if RegQueryStringValue(Hive, UninstallPath + '\' + Names[i],
                           'DisplayName', Display) then
    begin
      Lower := LowerCase(Display);
      for j := 0 to GetArrayLength(Needles) - 1 do
      begin
        if Pos(LowerCase(Needles[j]), Lower) > 0 then
        begin
          Result := True;
          Exit;
        end;
      end;
    end;
  end;
end;

{ ----- Prereq 1: Visual C++ 2015-2022 Redistributable (x64) ---------------- }

function IsVCRedistInstalled(): Boolean;
{ Microsoft documents the canonical detection method as reading the
  Installed DWORD under VC\Runtimes\x64. The same key is updated for
  VS 2015, 2017, 2019, and 2022 (they share a redistributable line). }
var
  Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM, VCRuntimesPath, 'Installed', Installed)
            and (Installed = 1);
end;

procedure InstallVCRedistIfMissing();
var
  Tmp: String;
  ResultCode: Integer;
begin
  if IsVCRedistInstalled() then
  begin
    Log('VC++ Redist already present, skipping.');
    Exit;
  end;
  Log('VC++ Redist not detected; downloading from ' + '{#VCRedistUrl}');
  Tmp := ExpandConstant('{tmp}\vc_redist.x64.exe');
  if not DownloadFile('{#VCRedistUrl}', Tmp) then
  begin
    MsgBox('Could not download the Microsoft Visual C++ Redistributable.' #13#10 +
           'The application will not launch without it.' #13#10 #13#10 +
           'Download manually from:' #13#10 +
           '{#VCRedistUrl}',
           mbCriticalError, MB_OK);
    Exit;
  end;
  if not Exec(Tmp, '/quiet /norestart', '', SW_SHOW,
              ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('Failed to launch the Visual C++ Redistributable installer.' #13#10 +
           'You can run it manually from:' #13#10 + Tmp,
           mbError, MB_OK);
    Exit;
  end;
  { vc_redist exits 0 = success, 1638 = newer already installed, 3010 =
    success but reboot needed. Anything else is a failure to surface. }
  if (ResultCode <> 0) and (ResultCode <> 1638) and (ResultCode <> 3010) then
    MsgBox('Visual C++ Redistributable installer returned exit code ' +
           IntToStr(ResultCode) + '.' #13#10 +
           'The application may not launch correctly.',
           mbInformation, MB_OK);
end;

{ ----- Prereq 2: Plexon PlexStim 2.0 SDK ----------------------------------- }

function IsPlexStimInstalled(): Boolean;
var
  Needles: array of String;
begin
  SetArrayLength(Needles, 3);
  Needles[0] := 'plexstim';
  Needles[1] := 'stimulator v2';
  Needles[2] := 'plexon inc';
  { PlexStim's installer is 32-bit, so its uninstall entry historically
    lives under WOW6432Node on 64-bit Windows. We check both hives so the
    detection works whether Plexon ships a 32- or 64-bit installer. }
  Result := UninstallEntryMatches(HKLM, Needles) or
            UninstallEntryMatches(HKLM32, Needles);
end;

procedure InstallPlexStimIfMissing();
var
  Answer, ResultCode: Integer;
  Downloaded: String;
begin
  if IsPlexStimInstalled() then
  begin
    Log('PlexStim SDK already present, skipping.');
    Exit;
  end;

  Answer := MsgBox(
    'The Plexon PlexStim 2.0 SDK was not detected on this machine.' #13#10 #13#10 +
    'Without it, the application cannot connect to a real PlexStim ' +
    'stimulator (it will only run in simulator mode).' #13#10 #13#10 +
    'Download and install the SDK now? (~5 MB)',
    mbConfirmation, MB_YESNO);
  if Answer <> IDYES then Exit;

  Downloaded := ExpandConstant('{tmp}\StimulatorV2Setup.exe');
  if DownloadFile('{#PlexStimUrl}', Downloaded) then
  begin
    if not Exec(Downloaded, '', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      MsgBox('Could not launch the PlexStim installer. You can run it ' +
             'later from:' #13#10 + Downloaded,
             mbInformation, MB_OK);
  end
  else
  begin
    MsgBox('Failed to download the PlexStim installer.' #13#10 +
           'You can download it manually from:' #13#10 +
           '{#PlexStimUrl}',
           mbInformation, MB_OK);
  end;
end;

{ ----- Prereq 3: NI-VISA (any IVI VISA runtime) ---------------------------- }

function IsVisaInstalled(): Boolean;
{ Any IVI-compliant VISA runtime drops a 64-bit visa64.dll into
  System32 and writes HKLM\SOFTWARE\IVI Foundation\VISA\Win64. NI-VISA
  also writes the NI-specific key. We accept any of the three signals
  because TekVISA, R&S, Keysight IO Libraries, and NI-VISA all satisfy
  pyvisa equally well. }
var
  Dummy: String;
  System32: String;
begin
  System32 := ExpandConstant('{sys}');
  if FileExists(System32 + '\visa64.dll') then
  begin
    Result := True;
    Exit;
  end;
  if RegQueryStringValue(HKLM, IviVisaPath, 'CurrentVersion', Dummy) then
  begin
    Result := True;
    Exit;
  end;
  Result := RegQueryStringValue(HKLM, NiVisaPath, 'CurrentVersion', Dummy);
end;

procedure InstallNiVisaIfMissing();
var
  Answer, ResultCode: Integer;
begin
  if IsVisaInstalled() then
  begin
    Log('VISA runtime already present, skipping.');
    Exit;
  end;

  Answer := MsgBox(
    'No VISA runtime (NI-VISA, TekVISA, etc.) was detected on this machine.' #13#10 #13#10 +
    'The application can still talk to most USB-TMC scopes via the ' +
    'bundled pyvisa-py backend, but a real VISA runtime is recommended ' +
    'for best performance and broader instrument support.' #13#10 #13#10 +
    'Open NI''s download page in your browser? (free, ~700 MB; the ' +
    '"Runtime" component alone is enough)',
    mbConfirmation, MB_YESNO);
  if Answer <> IDYES then Exit;

  { NI rotates direct download URLs and gates them behind a free login,
    so we open the page in the user's default browser rather than try
    to fetch a specific .exe. ShellExec on a URL launches the default
    handler. }
  if not ShellExec('open', '{#NiVisaPageUrl}', '', '', SW_SHOW, ewNoWait, ResultCode) then
    MsgBox('Could not open browser. Please visit:' #13#10 +
           '{#NiVisaPageUrl}',
           mbInformation, MB_OK);
end;

{ ----- Wizard hooks -------------------------------------------------------- }

procedure CurStepChanged(CurStep: TSetupStep);
{ Run all three prereq checks immediately after our files are on disk
  but before the final wizard page so the user sees the result inside
  the install flow rather than after the wizard has dismissed. Each
  check is gated by the corresponding component selection so a user
  who unticks "Install missing prerequisites" gets a clean app-only
  install. }
begin
  if CurStep <> ssPostInstall then Exit;

  if IsComponentSelected('prereq\vcredist') then InstallVCRedistIfMissing();
  if IsComponentSelected('prereq\plexstim') then InstallPlexStimIfMissing();
  if IsComponentSelected('prereq\nivisa')   then InstallNiVisaIfMissing();
end;

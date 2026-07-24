; PULSAR — Inno Setup script
;
; Produces the **PULSAR** GUI and **POLARIS** viewer
; installer. The codebase, repo URL, on-disk install directory
; (``%ProgramFiles%\StimulationTesting``), and launcher .exe filenames
; (``StimulationTesting.exe`` / ``StimulationTestingViewer.exe``) all
; intentionally retain the legacy ``StimulationTesting`` name for
; backward compatibility with installed-base prefs / shortcuts /
; AppId-driven upgrades. The user-facing AppName + Start-Menu group +
; Add/Remove-Programs entry + setup-EXE filename all show the new
; "PULSAR" branding. See ``installer/README.md`` for the full
; rename ledger.
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
;   2. Plexon PlexStim 2.0 SDK ("Sim-2")                    -- RECOMMENDED
;        Required to drive a real PlexStim. Without it the GUI still
;        launches in simulator mode. Plexon's setup is interactive; we
;        download StimulatorV2Setup.exe and exec it for the user.
;        Detection probes the registry uninstall hive first, then falls
;        back to a filesystem check of:
;          - C:\PlexonSDKs\<sdk-name>     (modern installer default)
;          - C:\Program Files\Plexon Inc\PlexStim 2.0 (older default)
;        PyPlexStim is NOT shipped by the SDK installer; this app
;        vendors its own Python wrapper, so end-users only need the
;        SDK installer (for the kernel-mode USB driver).
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

; The user-facing product is "PULSAR" (GUI) / "POLARIS"
; (viewer). The codebase / repo / on-disk dir / launcher .exe filenames
; intentionally stay as "StimulationTesting" for back-compat with
; installed-base prefs, shortcuts, and the AppId-driven upgrade path.
; See installer/README.md for the full rename ledger.
#define AppName "PULSAR"
#define ViewerDisplayName "POLARIS"
; AppVersion is normally injected by ``installer/build.py`` via
; ``ISCC /DAppVersion=…`` so it always tracks ``stimtest.__version__``.
; The #ifndef fallback below lets a hand-run ``ISCC StimulationTesting.iss``
; still build (e.g. when re-packaging an existing PyInstaller dist
; tree without re-running build.py) using whatever version the
; codebase had at the time this file was last edited.
#ifndef AppVersion
    #define AppVersion "0.2.223"
#endif
#define AppPublisher "Solzbacher Lab, University of Utah"
; Launcher .exe filenames — intentionally NOT renamed so existing
; users' Start-Menu / desktop shortcuts (and the file-system layout
; under {app}) continue to work across the rename. To repath these,
; update both the [Icons] block below AND the PyInstaller .spec's
; ``APP_NAME`` constant in lockstep, and ship an installer upgrade
; that ``InstallDelete``s the old names.
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
; Audit finding #30 — these used to point at the github.com landing
; page (no repo path). The Add/Remove Programs "support" link now
; resolves to the actual project. The codebase intentionally retains
; the legacy "StimulationTesting" repo name (renaming the GitHub
; repo is a separate migration with its own redirect concerns).
AppPublisherURL=https://github.com/Bortz1234/StimulationTesting
AppSupportURL=https://github.com/Bortz1234/StimulationTesting
; ``DefaultDirName`` intentionally NOT bound to ``{#AppName}``. We want
; existing installations to upgrade in place at
; ``%ProgramFiles%\StimulationTesting`` rather than forking the
; install path across the rename (the AppId GUID drives the upgrade
; detection regardless of folder name). The user-visible AppName +
; Start-Menu group + Add/Remove-Programs entry reflect the new
; "PULSAR" branding.
DefaultDirName={autopf}\StimulationTesting
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir={#SourcePath}\Output
; Audit finding #29 — the installer .exe filename is the first thing
; users see at download time. Show the new branding.
;
; The DEFAULT build is the REGULAR / public installer — share it with
; anyone.  Two INDEPENDENT opt-in flags each append a suffix so every
; artifact has a distinct filename:
;   /DWITH_CWRU_PROFILE=1  → bundles the CWRU login profile  (-CWRU)
;   /DWITH_INTERSTELLAR=1  → experimental interpulse-bias build
;                            (-INTERSTELLAR; build.py --with-interstellar)
; Both together → -CWRU-INTERSTELLAR.
#ifdef WITH_INTERSTELLAR
  #ifdef WITH_CWRU_PROFILE
OutputBaseFilename=PULSAR-Setup-{#AppVersion}-CWRU-INTERSTELLAR
  #else
OutputBaseFilename=PULSAR-Setup-{#AppVersion}-INTERSTELLAR
  #endif
#else
  #ifdef WITH_CWRU_PROFILE
OutputBaseFilename=PULSAR-Setup-{#AppVersion}-CWRU
  #else
OutputBaseFilename=PULSAR-Setup-{#AppVersion}
  #endif
#endif
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExeName}
; Required installer-window icon. Drop ``installer/app.ico`` next to
; this script and the wizard picks it up automatically.  Missing
; icon now FAILS the compile rather than emitting a warning that
; could be missed in CI logs — preventing a release from accidentally
; shipping with the default Inno icon.  Set the BUILD_ALLOW_NO_ICON
; preprocessor flag (``ISCC /DBUILD_ALLOW_NO_ICON=1``) to fall back
; to the default icon on purpose, e.g. for a smoke-test build before
; the brand asset has been committed.
#if FileExists(SourcePath + "\app.ico")
  SetupIconFile={#SourcePath}\app.ico
#else
  #ifdef BUILD_ALLOW_NO_ICON
    #pragma message "MISSING_APP_ICON — installer/app.ico not found; " + \
                    "BUILD_ALLOW_NO_ICON is set so falling back to " + \
                    "Inno Setup's default icon."
  #else
    #error MISSING_APP_ICON: installer/app.ico not found. \
        Commit a real app.ico before building the release installer, \
        or pass /DBUILD_ALLOW_NO_ICON=1 to ISCC to allow a fallback build.
  #endif
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"
Name: "viewericon"; Description: "Add a desktop shortcut for {#ViewerDisplayName} too"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Components]
Name: "app"; Description: "{#AppName} (required)"; Types: full compact custom; Flags: fixed
Name: "prereq"; Description: "Install missing prerequisites"; Types: full
Name: "prereq\vcredist"; Description: "Microsoft Visual C++ 2015-2022 Redistributable (x64) -- required"; Types: full compact
; Stim-2 (PlexStim) and NI-VISA are no longer component checkboxes here —
; they're handled by the "Required hardware drivers" wizard page (which
; auto-runs their installers on click) so they can be installed + the PC
; rebooted BEFORE PULSAR, per the operator's requested flow.

[Files]
; Drop the entire PyInstaller dist tree (StimulationTesting + its DLLs +
; the vendored PlexStim64.dll) under {app}.
Source: "dist\StimulationTesting\*"; DestDir: "{app}"; Components: app; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; OPTIONAL bundled prerequisite installers — so the wizard page can
; AUTO-RUN them instead of opening a website.  ``dontcopy`` keeps them
; out of {app} (extracted to {tmp} on demand via ExtractTemporaryFile);
; ``skipifsourcedoesntexist`` means the build still succeeds when they're
; absent (the wizard then downloads Stim-2 / opens NI's page instead).
; To enable full offline auto-run, drop the vendor installers here:
;   installer/prereqs/stim2-setup.exe   (Plexon Stimulator V2 setup)
;   installer/prereqs/nivisa-setup.exe  (NI-VISA, e.g. the online installer)
Source: "prereqs\stim2-setup.exe";  Flags: dontcopy skipifsourcedoesntexist
Source: "prereqs\nivisa-setup.exe"; Flags: dontcopy skipifsourcedoesntexist
; CWRU collaborator login profile.  Pure DATA (login name + SHA-256
; password hash + the shape IDs it unlocks) — NO code, so distributing
; it is safe and importing it never executes anything.  Lands in
; {app}\profiles\ so a collaborator can Admin -> Import Profile... and
; point at it right from the install directory; logging in as ``cwru``
; then unlocks the halfpipe / bowtie / speedbumps shapes.  The shape
; GEOMETRY already ships in PULSAR (stimtest.waveforms.SHAPE_*); the
; profile only flips the visibility gate.
; ``skipifsourcedoesntexist`` — the profile is gitignored (CWRU-private,
; see .gitignore), so it's present in the operator's local build tree but
; absent on a public-repo checkout; the build still succeeds either way.
;
; OPT-IN: only bundled when ``/DWITH_CWRU_PROFILE=1`` is passed to ISCC, so
; the DEFAULT (public) installer never ships the CWRU profile.  A public
; install therefore has no "cwru" login mystery (the login dialog mentions
; only Admin).
#ifdef WITH_CWRU_PROFILE
Source: "profiles\cwru.pulsarprofile.json"; DestDir: "{app}\profiles"; \
    Components: app; Flags: ignoreversion skipifsourcedoesntexist
#endif

[Icons]
; Audit finding #28 — shortcut labels use {#AppName} ("PULSAR") for
; the main GUI and {#ViewerDisplayName} ("POLARIS") for the
; viewer, matching what the running windows display via setWindowTitle.
; The .exe filenames they point at remain "StimulationTesting*.exe"
; for installed-base compatibility.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{#ViewerDisplayName}"; Filename: "{app}\{#ViewerExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon
Name: "{autodesktop}\{#ViewerDisplayName}"; Filename: "{app}\{#ViewerExeName}"; \
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
  { We only need the DIRECTORY attribute for the FindFirst walk in
    PlexonSdkFolderExists.  Use a PRIVATE name — NOT the Win32
    ``FILE_ATTRIBUTE_DIRECTORY`` — because newer Inno Setup (6.3+, seen
    on 6.7.3) PREDECLARES the Win32 file-attribute constants as built-ins,
    so re-declaring one aborts the compile with "Duplicate identifier".
    A private name compiles on every Inno version.  Value matches the
    Win32 SDK header (0x10). }
  FILE_ATTR_DIRECTORY = $00000010;

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

function PlexStimDllInDir(const Dir: String): Boolean;
{ True if either PlexStim driver DLL (64-bit PlexStim64.dll or 32-bit
  PlexStim.dll) is present directly in Dir or in Dir\bin. }
begin
  Result :=
    FileExists(Dir + '\PlexStim64.dll') or
    FileExists(Dir + '\PlexStim.dll')   or
    FileExists(Dir + '\bin\PlexStim64.dll') or
    FileExists(Dir + '\bin\PlexStim.dll');
end;

function IsPlexStimInstalled(): Boolean;
{ Operator's criterion: "the Stim-2 installation check is to see if the
  DLL has been installed."  The Stim-2 (PlexStim 2.0) installer drops
  the PlexStim driver DLL — PlexStim64.dll (64-bit) / PlexStim.dll
  (32-bit) — on the system.  We probe, in order:
    1. the Windows system dirs (System32 / SysWOW64),
    2. the fixed Plexon install folders (older installers),
    3. a walk of C:\PlexonSDKs subfolders (modern installer; the exact
       SDK subfolder name varies across releases).
  Any hit ⇒ installed.  (Note: this is the OS-installed driver DLL, NOT
  the copy PULSAR vendors in stimtest/hardware/pyplexstim/bin.) }
var
  FindRec: TFindRec;
  Sys32, SysWow, Root: String;
begin
  Result := False;

  { 1. Windows system directories. }
  Sys32  := ExpandConstant('{sys}');         { System32 (native bitness) }
  SysWow := ExpandConstant('{syswow64}');     { 32-bit DLLs on 64-bit Win }
  if FileExists(Sys32 + '\PlexStim64.dll') or
     FileExists(Sys32 + '\PlexStim.dll') or
     FileExists(SysWow + '\PlexStim.dll') or
     FileExists(SysWow + '\PlexStim64.dll') then
  begin
    Result := True;
    Exit;
  end;

  { 2. Fixed Plexon install folders. }
  if PlexStimDllInDir(ExpandConstant('{commonpf64}') + '\Plexon Inc\PlexStim 2.0') or
     PlexStimDllInDir(ExpandConstant('{commonpf32}') + '\Plexon Inc\PlexStim 2.0') or
     PlexStimDllInDir(ExpandConstant('{commonpf64}') + '\Plexon Inc\PlexStim') or
     PlexStimDllInDir(ExpandConstant('{commonpf32}') + '\Plexon Inc\PlexStim') then
  begin
    Result := True;
    Exit;
  end;

  { 3. Modern installer: C:\PlexonSDKs\<varying-name>\... — walk subdirs. }
  Root := 'C:\PlexonSDKs';
  if DirExists(Root) and FindFirst(Root + '\*', FindRec) then
  begin
    try
      repeat
        if ((FindRec.Attributes and FILE_ATTR_DIRECTORY) <> 0) and
           (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          if PlexStimDllInDir(Root + '\' + FindRec.Name) then
          begin
            Result := True;
            Exit;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
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

{ ----- Prerequisite wizard page (Stim-2 + NI-VISA) ------------------------- }
{ Shown right after the Welcome page, BEFORE anything is installed, so the
  operator can install the hardware drivers and reboot FIRST (the order the
  user asked for): "After rebooting the computer, the user may try installing
  PULSAR and POLARIS."  Each missing driver gets a clickable blue hyperlink
  that opens its download page in the default browser; both drivers need a
  restart, which the page spells out. }

function RunBundledInstaller(const TmpName: String): Boolean;
(* Run a prerequisite installer BUNDLED into this setup via a [Files]
  ``dontcopy`` entry (extracted on demand to the temp folder).  NOTE: do
  NOT write the literal tmp constant in this brace comment — an Inno
  ``{ }`` comment ends at the FIRST close brace, so a brace-constant here
  would terminate the comment early and break the compile.  Returns False
  when
  it wasn't bundled (the file was absent at build time and skipped via
  ``skipifsourcedoesntexist``) so the caller can fall back to a download
  / web page.  ``ewNoWait`` so the PULSAR wizard stays responsive while
  the driver installer's own UI runs. *)
var
  ExtractedPath: String;
  ResultCode: Integer;
begin
  Result := False;
  try
    ExtractTemporaryFile(TmpName);   { raises if not compiled into setup }
  except
    Exit;                            { not bundled — caller falls back }
  end;
  ExtractedPath := ExpandConstant('{tmp}\') + TmpName;
  if FileExists(ExtractedPath) then
    Result := Exec(ExtractedPath, '', '', SW_SHOW, ewNoWait, ResultCode);
end;

procedure RunStimInstaller(Sender: TObject);
{ Automatically install Stim-2: run a bundled installer if the build
  included one (installer/prereqs/stim2-setup.exe), else download the
  ~5 MB Plexon Stimulator V2 setup and run it, else open the page. }
var
  Tmp: String;
  ResultCode: Integer;
begin
  if RunBundledInstaller('stim2-setup.exe') then Exit;
  Tmp := ExpandConstant('{tmp}\StimulatorV2Setup.exe');
  if DownloadFile('{#PlexStimUrl}', Tmp) and
     Exec(Tmp, '', '', SW_SHOW, ewNoWait, ResultCode) then Exit;
  ShellExec('open', '{#PlexStimUrl}', '', '', SW_SHOW, ewNoWait, ResultCode);
end;

procedure RunVisaInstaller(Sender: TObject);
{ Automatically install NI-VISA: run a bundled installer if the build
  included one (installer/prereqs/nivisa-setup.exe).  NI gates its
  downloads behind a free login + rotating URLs and the runtime is
  ~700 MB, so we can NOT reliably fetch it automatically — fall back to
  opening NI's download page.  Bundle installer/prereqs/nivisa-setup.exe
  (e.g. the NI-VISA *online* installer) to make this auto-run too. }
var
  ResultCode: Integer;
begin
  if RunBundledInstaller('nivisa-setup.exe') then Exit;
  if not ShellExec('open', '{#NiVisaPageUrl}', '', '', SW_SHOW, ewNoWait, ResultCode) then
    MsgBox('Could not open the browser. Please visit:' #13#10 +
           '{#NiVisaPageUrl}', mbInformation, MB_OK);
end;

procedure AddPageLabel(APage: TWizardPage; const ACaption: String;
                       ATop, AHeight: Integer; AWrap: Boolean);
{ A plain (black) text label spanning the page width. }
var
  L: TNewStaticText;
begin
  L := TNewStaticText.Create(APage);
  L.Parent := APage.Surface;
  L.Left := 0;
  L.Top := ATop;
  L.Width := APage.SurfaceWidth;
  L.AutoSize := False;
  L.WordWrap := AWrap;
  L.Height := AHeight;
  L.Caption := ACaption;
end;

procedure AddPageLink(APage: TWizardPage; const ACaption: String;
                      ATop: Integer; AOnClick: TNotifyEvent);
{ A blue, underlined, hand-cursor label that acts as a clickable hyperlink. }
var
  L: TNewStaticText;
begin
  L := TNewStaticText.Create(APage);
  L.Parent := APage.Surface;
  L.Left := ScaleX(12);
  L.Top := ATop;
  L.AutoSize := True;
  L.Caption := ACaption;
  L.Cursor := crHand;
  L.Font.Color := clBlue;
  L.Font.Style := [fsUnderline];
  L.OnClick := AOnClick;
end;

procedure InitializeWizard();
var
  PrereqPage: TWizardPage;
  StimOk, VisaOk: Boolean;
  y: Integer;
begin
  PrereqPage := CreateCustomPage(wpWelcome,
    'Required hardware drivers',
    'These must be installed (and the PC restarted) before PULSAR can control the stimulator and oscilloscope.');

  StimOk := IsPlexStimInstalled();
  VisaOk := IsVisaInstalled();
  y := ScaleY(4);

  { --- Stim-2 (Plexon PlexStim 2.0) --- }
  if StimOk then
    AddPageLabel(PrereqPage,
      'Plexon Stim-2 (PlexStim 2.0):   INSTALLED', y, ScaleY(15), False)
  else
  begin
    AddPageLabel(PrereqPage,
      'Plexon Stim-2 (PlexStim 2.0):   NOT FOUND', y, ScaleY(15), False);
    AddPageLink(PrereqPage,
      'Click here to download and run the Stim-2 installer',
      y + ScaleY(17), @RunStimInstaller);
    y := y + ScaleY(17);
  end;
  y := y + ScaleY(30);

  { --- NI-VISA --- }
  if VisaOk then
    AddPageLabel(PrereqPage,
      'NI-VISA runtime:   INSTALLED', y, ScaleY(15), False)
  else
  begin
    AddPageLabel(PrereqPage,
      'NI-VISA runtime:   NOT FOUND', y, ScaleY(15), False);
    AddPageLink(PrereqPage,
      'Click here to download and run NI-VISA (opens NI''s page if not bundled)',
      y + ScaleY(17), @RunVisaInstaller);
    y := y + ScaleY(17);
  end;
  y := y + ScaleY(36);

  { --- Restart notice + recommended order --- }
  if StimOk and VisaOk then
    AddPageLabel(PrereqPage,
      'Both drivers are installed. Click Next to continue installing PULSAR and POLARIS.',
      y, ScaleY(40), True)
  else
    AddPageLabel(PrereqPage,
      'Click a link above to download and run that driver''s installer now.' + #13#10 +
      'IMPORTANT: installing Stim-2 and/or NI-VISA REQUIRES restarting your computer.' + #13#10 +
      'Recommended order: install the missing driver(s), RESTART Windows, then run ' +
      'this installer again to install PULSAR and POLARIS.' + #13#10 + #13#10 +
      'You can still click Next to continue now, but PULSAR will run in simulator mode ' +
      'until the drivers are installed.',
      y, ScaleY(112), True);
end;

{ ----- Wizard hooks -------------------------------------------------------- }

procedure CurStepChanged(CurStep: TSetupStep);
{ Stim-2 and NI-VISA are now handled by the prerequisite wizard page
  (the "Required hardware drivers" page) BEFORE install, per the
  operator's "install drivers + reboot first, then PULSAR" flow — the
  page auto-runs their installers on click.  Only the quiet, required
  VC++ runtime is still auto-installed here at post-install. }
begin
  if CurStep <> ssPostInstall then Exit;

  { ``WizardIsComponentSelected`` — the modern name; ``IsComponentSelected``
    still works on Inno 6.7.3 but emits a deprecation hint. }
  if WizardIsComponentSelected('prereq\vcredist') then InstallVCRedistIfMissing();
end;

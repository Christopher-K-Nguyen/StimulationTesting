' ===================================================================
' PULSAR — silent launcher (no cmd.exe window).
'
' Double-click this file to launch the GUI without a console window
' appearing.  Errors during startup still reach the user via:
'
'   * <repo>\test\pulsar_launch_error.log (if test\ exists)
'     OR <repo>\pulsar_launch_error.log — full traceback written
'     by run_gui.py's crash handler.  Logs live in the project tree,
'     NOT in %APPDATA% or %TEMP%, so the operator finds them next to
'     the bench-test session files.
'   * A tkinter message box   — pops up automatically if the import
'     fails or main() raises, so a launch failure doesn't appear to
'     "do nothing" (the silent-launch trap).
'   * <repo>\test\pulsar_launcher.log (or <repo>\pulsar_launcher.log)
'     — one line per launch attempt recording which Python was probed,
'     which one was selected, and the timestamp.  Lets the operator
'     diagnose "GUI did nothing" failures without rerunning under the
'     visible BAT.
'
' For day-to-day use, prefer this file.  Use PULSAR.bat instead when
' you want a visible console — e.g. when developing, when the GUI is
' failing in a way the crash handler can't catch (segfault during a
' C extension's import, exit -1073740940 / STATUS_HEAP_CORRUPTION
' from a PyQt6 / numpy / Plexon DLL heap corruption), or when you
' want to see Qt's stderr warnings live.
'
' Interpreter selection:
'   Bundled runtime per pyproject.toml is Python 3.13.  PyQt6 wheels
'   for Python 3.14 are very new and on some installs corrupt the
'   heap at startup — we EXPLICITLY prefer 3.13 first via the Windows
'   "py" launcher and fall back through 3.12 / 3.11 / 3.10 before
'   trying the system's default 3.x or plain pythonw.exe on PATH.
'
'   The probe uses ``py.exe`` (CONSOLE variant), not ``pyw.exe``
'   (windowed), because WshShell.Run can suppress console flicker via
'   windowStyle=0 while still capturing the child's exit code.  Using
'   ``pyw.exe`` for the probe is flaky — the windowless child detaches
'   from the shell in a way that defeats reliable exit-code reads.
'   The GUI itself launches under ``pyw.exe -<version>`` (windowless)
'   AFTER the probe selects the right version.
'
' Author: Christopher K. Nguyen
' ===================================================================

Option Explicit

Dim fso, shell, scriptDir, runGuiPath, launcherLogPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
runGuiPath = scriptDir & "\run_gui.py"

' Working directory must be the project root so ``from stimtest.gui...``
' resolves the same way it does for ``PULSAR.bat``.
shell.CurrentDirectory = scriptDir

' Launcher diagnostic log — same project-tree convention as the
' Python crash log: prefer <repo>\test\pulsar_launcher.log when
' test\ exists, otherwise <repo>\pulsar_launcher.log.  Operators
' can read this to see why a silent VBS launch did nothing.
If fso.FolderExists(scriptDir & "\test") Then
    launcherLogPath = scriptDir & "\test\pulsar_launcher.log"
Else
    launcherLogPath = scriptDir & "\pulsar_launcher.log"
End If

LauncherLog "---- " & Now() & " ----"
LauncherLog "Launcher: PULSAR.vbs"
LauncherLog "Script dir: " & scriptDir

' --- Probe each candidate Python version for PyQt6 -----------------
'
' WshShell.Run with windowStyle=0 (hidden) and waitOnReturn=True is
' synchronous AND silent AND returns the child's exit code.  This is
' the right primitive for the probe — shell.Exec is also synchronous
' but always flashes a console window briefly, and shell.Exec on
' pyw.exe (windowless) returns unreliable exit codes because the
' child detaches.

Dim selectedGuiCmd : selectedGuiCmd = ""
Dim selectedDescription : selectedDescription = ""
Dim versions : versions = Array("3.13", "3.12", "3.11", "3.10")
Dim i, probeRc
For i = 0 To UBound(versions)
    If selectedGuiCmd = "" Then
        probeRc = ProbePyQt6("py.exe -" & versions(i))
        LauncherLog "Probe py -" & versions(i) & ": exitCode=" & probeRc
        If probeRc = 0 Then
            ' Probe OK with console py.exe — use windowless pyw.exe
            ' for the actual GUI launch so no console window appears.
            selectedGuiCmd = "pyw.exe -" & versions(i)
            selectedDescription = "py -" & versions(i) & " (PyQt6 import OK)"
        End If
    End If
Next

' Default 3.x via the "py" launcher.
If selectedGuiCmd = "" Then
    probeRc = ProbePyQt6("py.exe -3")
    LauncherLog "Probe py -3 (default 3.x): exitCode=" & probeRc
    If probeRc = 0 Then
        selectedGuiCmd = "pyw.exe -3"
        selectedDescription = "py -3 default (PyQt6 import OK)"
    End If
End If

' Plain "python" on PATH — last resort.  Often the same as "py -3"
' but can differ if the user has a venv active or a Microsoft Store
' Python install hijacked the PATH order.
If selectedGuiCmd = "" Then
    probeRc = ProbePyQt6("python.exe")
    LauncherLog "Probe python (on PATH): exitCode=" & probeRc
    If probeRc = 0 Then
        ' Use pythonw.exe for the windowless GUI launch.
        selectedGuiCmd = "pythonw.exe"
        selectedDescription = "python on PATH (PyQt6 import OK)"
    End If
End If

' If NOTHING has PyQt6, fall back to the visible PULSAR.bat — its
' diagnostic block prints `where python`, `where py`, `py -0p` and
' tells the user how to install PyQt6.  Better than silently doing
' nothing.
If selectedGuiCmd = "" Then
    LauncherLog "FATAL: no Python with PyQt6 found — falling back to visible PULSAR.bat"
    Dim cmdFallback
    cmdFallback = "cmd.exe /c """ & scriptDir & "\PULSAR.bat"""
    shell.Run cmdFallback, 1, False
    WScript.Quit 0
End If

LauncherLog "Selected: " & selectedDescription
LauncherLog "GUI launch cmd: " & selectedGuiCmd & " """ & runGuiPath & """"

' --- Launch the GUI with the selected interpreter ------------------
' Quote the script path in case the install lives under a path with
' spaces (e.g. "C:\Program Files\...").  Shell.Run's first argument
' is the command line; everything after the first token is passed to
' the child as-is, so we wrap ``run_gui.py`` in double-quotes.
'
' Same 4-quotes-each-side idiom (`"prefix """ & path & """"`
' = 4 + path + 4 = 8 quotes total).
Dim cmdGui
cmdGui = selectedGuiCmd & " """ & runGuiPath & """"

' WshShell.Run arguments:
'   command   - the command line to run
'   style     - 0 = hidden, 1 = normal, 7 = minimised  (see docs)
'   waitFlag  - False = return immediately, True = wait for exit
'
' We use waitFlag=False (detached) so this VBS exits immediately and
' the user sees no extra process in the taskbar.
shell.Run cmdGui, 0, False
WScript.Quit 0


' ===================================================================
' Helper functions
' ===================================================================

' ProbePyQt6 — run "<pythonCmd> -c 'import PyQt6'" synchronously,
' return the child's exit code (0 = PyQt6 imported cleanly,
' non-zero = failed or process couldn't even spawn).
'
' Uses WshShell.Run with windowStyle=0 (hidden) and waitOnReturn=True
' so the probe is BOTH silent AND synchronous AND exit-code-reliable.
' shell.Exec is also synchronous but always shows a brief console
' flicker; shell.Exec on a windowless launcher (pyw.exe) returns
' unreliable exit codes because the child detaches.  shell.Run with
' the right flags gives us all three properties.
Function ProbePyQt6(pythonCmd)
    On Error Resume Next
    Dim rc
    rc = shell.Run(pythonCmd & " -c ""import PyQt6""", 0, True)
    If Err.Number <> 0 Then
        ' Couldn't even spawn the process (interpreter not found,
        ' PATH miss, etc.).  Return a sentinel non-zero value.
        Err.Clear
        ProbePyQt6 = 9999
        Exit Function
    End If
    ProbePyQt6 = rc
    On Error Goto 0
End Function

' LauncherLog — append one timestamped line to the launcher diagnostic
' log.  Best-effort: silently swallow any failure (no log dir, no
' disk space, file locked).  The log gives the operator a record of
' what the silent VBS launcher did across multiple invocations
' without having to switch to the visible PULSAR.bat to see output.
Sub LauncherLog(msg)
    On Error Resume Next
    Dim f
    Set f = fso.OpenTextFile(launcherLogPath, 8, True)  ' 8 = ForAppending
    If Err.Number = 0 Then
        f.WriteLine msg
        f.Close
    End If
    Err.Clear
    On Error Goto 0
End Sub

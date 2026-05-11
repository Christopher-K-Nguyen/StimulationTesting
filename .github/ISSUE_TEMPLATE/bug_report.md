---
name: Bug report
about: Report a defect in PULSAR or POLARIS
title: "[BUG] "
labels: bug
assignees: ''
---

## Summary

<!-- One or two sentences describing what went wrong. -->

## Environment

- **PULSAR version**: <!-- Help → About, or `python -c "import stimtest; print(stimtest.__version__)"` -->
- **OS**: <!-- e.g. Windows 11 23H2, Ubuntu 22.04, macOS 14 -->
- **Python**: <!-- `python --version` -->
- **Install type**: <!-- editable (`pip install -e`) / Windows installer / source checkout -->
- **Hardware**: <!-- simulator / real PlexStim + Tektronix; include scope model -->

## Steps to reproduce

1.
2.
3.

## Expected behaviour

<!-- What you thought would happen. -->

## Actual behaviour

<!-- What actually happened. Include exact error messages and tracebacks
in a fenced code block. -->

```
<paste traceback here>
```

## Screenshots / session files

<!-- Attach .npz files, GUI screenshots, or plots if relevant.
Drag-and-drop into the issue body works. -->

## Hardware probe (real-hardware bugs only)

<!-- Run `python scripts/hardware_probe.py` and paste the full output
below. It captures PlexStim SDK + VISA + scope-dialect state. -->

```
<paste hardware_probe output here>
```

## Additional context

<!-- Anything else: recent changes, related issues, suspected modules. -->

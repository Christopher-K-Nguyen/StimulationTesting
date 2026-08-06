"""No undefined names on rarely-executed paths.

A dormant ``NameError`` is invisible to the test suite whenever it lives on a
path the tests don't reach, and it can sit there for months:

* ``calibration.py`` referenced ``accept`` in the verification SUMMARY POPUP,
  left behind when the ACCEPTANCE_PCT gain gate was deleted.  The popup is
  only reached by a sweep that RUNS TO COMPLETION -- while the R band was
  +/-10 % every channel exhausted its retries and the operator aborted, so it
  stayed hidden until the -20 %/+10 % band let a sweep finish, and then it
  crashed a real bench run.
* ``tektronix.py`` referenced ``src`` in the ``[scope-time]`` diagnostic.  That
  line sits inside a ``try:``, so the NameError was SWALLOWED and the whole
  diagnostic -- XZEro / PT_Off / XINcr / npts / axis-method, the exact fields
  needed to debug t=0 -- had never once been emitted.

Both were found by pyflakes in seconds.  This test keeps them out.

Annotation-only hits are expected and allowlisted: the modules use
``from __future__ import annotations``, so a name used solely in an annotation
is never evaluated at runtime.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

pytest.importorskip("pyflakes")

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (file, name) pairs that appear ONLY inside annotations, which are strings
# under `from __future__ import annotations` and therefore never evaluated.
ANNOTATION_ONLY = {
    ("stimtest/gui/calibration.py", "np"),
    ("stimtest/gui/rich.py", "QtWidgets"),
    ("stimtest/gui/setup_tab.py", "List"),
}


def _undefined_names():
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", "stimtest", "run_gui.py",
         "run_viewer.py"],
        cwd=ROOT, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    hits = []
    for line in out.splitlines():
        m = re.match(r"^(.*?):(\d+):\d+: undefined name '([^']+)'", line.strip())
        if not m:
            continue
        path = m.group(1).replace("\\", "/")
        if path.startswith(str(ROOT).replace("\\", "/")):
            path = path[len(str(ROOT)) + 1:].replace("\\", "/")
        hits.append((path, int(m.group(2)), m.group(3)))
    return hits


def test_no_runtime_undefined_names():
    unexpected = [(p, ln, n) for (p, ln, n) in _undefined_names()
                  if (p, n) not in ANNOTATION_ONLY]
    assert not unexpected, (
        "undefined name(s) that will raise NameError when the line runs:\n"
        + "\n".join(f"  {p}:{ln}  {n!r}" for p, ln, n in unexpected))


def test_the_two_known_offenders_stay_fixed():
    cal = (ROOT / "stimtest/gui/calibration.py").read_text(encoding="utf-8")
    assert "{accept:" not in cal, "the ACCEPTANCE_PCT leftover is back"
    tek = (ROOT / "stimtest/hardware/tektronix.py").read_text(encoding="utf-8")
    assert "{src or" not in tek, "the unbound trig_src reference is back"
    # ...and the diagnostic still reports the trigger source some way.
    assert "trig_src=" in tek


def test_allowlisted_files_use_future_annotations():
    """The allowlist is only safe while those modules defer annotations."""
    for rel, _name in ANNOTATION_ONLY:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "from __future__ import annotations" in src, rel

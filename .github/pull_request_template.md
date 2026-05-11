<!--
Thanks for contributing to PULSAR / POLARIS. Please fill in the sections
below — they map 1:1 to the PR checklist in CONTRIBUTING.md.
-->

## Summary

<!-- One paragraph: what does this PR do, and why? -->

## Type of change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would change existing behaviour)
- [ ] Documentation / packaging / CI only
- [ ] Refactor / cleanup (no behaviour change)

## Scope

- [ ] GUI (PULSAR)
- [ ] Viewer (POLARIS)
- [ ] Experiment runners / hardware
- [ ] Analysis / metrics
- [ ] Persistence (.npz schema)
- [ ] Installer / packaging
- [ ] Tests / CI

## How was this tested?

<!-- e.g. "ran `pytest tests/ -q` (391 passing), launched the GUI in
simulator mode and verified the new dialog opens, smoke-tested on a
real TBS2204B + PlexStim 2.0 rig." -->

## Checklist

- [ ] `python -m pytest tests/ -q` passes locally.
- [ ] Added at least one test for new features.
- [ ] Added a regression test for bug fixes (failing before, passing after).
- [ ] Public functions / classes have docstrings.
- [ ] Touched the installer? The rename ledger in
      `installer/README.md` still reflects reality.
- [ ] Bumped `__version__` in `stimtest/__init__.py`? Also bumped
      `version =` in `pyproject.toml` (the version-drift guard in
      `installer/build.py` aborts the build otherwise).
- [ ] Added a `CHANGELOG.md` entry under `## [Unreleased]`.

## Related issues

<!-- "Closes #123", "Refs #456", etc. -->

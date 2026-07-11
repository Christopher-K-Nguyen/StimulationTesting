"""Regression tests for ExperimentRunner.commit_loaded_channels — the
config-dependent PS_LoadAllChannels commit.

MATLAB ``loadPattern.m`` committed staged channel parameters differently
by configuration:

  * **monopolar** (no return channels) → ONE ``PS_LoadAllChannels``
  * **multipolar** (returns present)   → per-channel ``PS_LoadChannel``
    (return channels stay UNLOADED as passive sinks)

PULSAR historically committed per-channel for BOTH cases, so a monopolar
run armed nothing for ``PS_StartStimAllChannels`` → the stim "started"
(err 0) but delivered no current and emitted no digital sync, so the
scope never triggered (``NUMACq = 0``).  ``commit_loaded_channels``
restores the config-dependent commit; these tests pin it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from stimtest.experiments.base import ExperimentRunner


class _DummyRunner(ExperimentRunner):
    """Concrete subclass so we can construct via ``__new__`` (bypassing
    the real ``__init__`` which needs a Session + Stimulator)."""

    def run(self):  # satisfy the lone @abstractmethod
        raise NotImplementedError


def _runner():
    r = _DummyRunner.__new__(_DummyRunner)
    r.stim = MagicMock()
    r.session = MagicMock()
    r._emit = MagicMock()
    return r


def test_monopolar_commits_via_load_all_channels():
    """No return channels → exactly one PS_LoadAllChannels."""
    r = _runner()
    r.commit_loaded_channels(SimpleNamespace(active=1, returns=()))
    r.stim.load_all_channels.assert_called_once_with()


def test_monopolar_with_none_returns_also_commits():
    """``returns=None`` is also 'no returns' → still LoadAllChannels."""
    r = _runner()
    r.commit_loaded_channels(SimpleNamespace(active=1, returns=None))
    r.stim.load_all_channels.assert_called_once_with()


def test_multipolar_does_not_load_all_channels():
    """Return channels present → must NOT PS_LoadAllChannels (that would
    commit the return channels, which must stay unloaded); the
    per-channel PS_LoadChannel loads stand."""
    r = _runner()
    r.commit_loaded_channels(SimpleNamespace(active=1, returns=(3, 5)))
    r.stim.load_all_channels.assert_not_called()


def test_commit_swallows_driver_error():
    """A LoadAllChannels failure is logged, not raised — the run
    continues and ``start_all`` surfaces any real arming problem."""
    r = _runner()
    r.stim.load_all_channels.side_effect = RuntimeError("boom")
    r.commit_loaded_channels(SimpleNamespace(active=1, returns=()))  # no raise
    r._emit.assert_called_once()

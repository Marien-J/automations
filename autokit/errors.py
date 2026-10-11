"""Exception hierarchy shared by the framework and automations.

The distinction matters for unattended operation:

* :class:`RecoverableError` – an interaction did not produce the expected
  screen transition (menu did not open, dialogue did not advance, ...). The
  automation may re-observe the screen and retry within a bounded budget.
* :class:`UnsafeState` – the screen is not in a state the automation can act on
  safely (unknown dialogue, client disconnected, target missing, ...). The run
  stops issuing input and records the reason.
* :class:`EmergencyStopped` – the user asked the automation to stop.
"""

from __future__ import annotations


class AutomationError(Exception):
    """Base class for all automation errors."""


class RecoverableError(AutomationError):
    """An interaction failed in a way that may succeed on a re-checked retry."""


class WaitTimeout(RecoverableError):
    """An expected screen transition did not happen within its time limit."""


class UnsafeState(AutomationError):
    """The screen cannot be interpreted safely; stop sending game input."""


class EmergencyStopped(AutomationError):
    """The emergency stop was triggered (hotkey, stop file, manual mouse move)."""


class ConfigError(AutomationError):
    """Settings or calibration are invalid or incomplete."""

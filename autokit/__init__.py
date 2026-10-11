"""autokit — a small framework for screen-based (visual) automations.

The framework is game-agnostic. It provides:

* ``autokit.vision``   – screen capture, colour masks/blobs, template matching
* ``autokit.input``    – guarded, human-paced mouse/keyboard control
* ``autokit.safety``   – emergency stop (hotkey, stop file, manual mouse override)
* ``autokit.runtime``  – automation base class, profiles, waits, rule-driven
                         dialogue handling, UI step sequences and the runner
* ``autokit.calibration`` – capture reference templates/colours/regions
* ``autokit.testing``  – deterministic simulated clock/desktop for tests

Concrete automations live in the top-level ``automations`` package, one folder
per automation. Game-specific widget helpers (e.g. RuneLite/OSRS menus) live in
their own packages such as ``osrskit``.
"""

__version__ = "0.1.0"

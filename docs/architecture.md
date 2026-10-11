# Architecture

```
automations/                     repository
├── autokit/                     framework, game-agnostic
│   ├── geometry.py, clock.py, errors.py, events.py, safety.py
│   ├── vision/                  capture, Frame, colour blobs, templates, anchor
│   ├── input/                   OS backend, smooth motion, guarded controller
│   ├── runtime/                 Automation base, Context, profiles, settings,
│   │                            dialogue rule engine, verified clicks, registry, runner
│   ├── calibration/             interactive capture of templates, colours and regions
│   ├── testing/                 virtual clock + simulated desktop
│   └── cli.py                   python -m autokit …
├── osrskit/                     RuneLite / OSRS widgets built on autokit
│   ├── client.py                anchor tracking, disconnect/logout detection, Layout
│   ├── context_menu.py          find the right-click menu, choose an option by label
│   ├── inventory.py             28-slot scan: item counts, free slots
│   ├── highlights.py            Object Markers / NPC Indicators outlines
│   ├── steps.py                 small data-driven click sequences (e.g. Call Butler)
│   └── testing/widgets.py       drawing helpers for simulated OSRS screens
├── automations/                 one package per automation
│   └── osrs_construction_mahogany_tables/
│       ├── automation.py        state machine (observe → decide → verified procedure)
│       ├── perception.py        screenshot → Snapshot
│       ├── refill.py            plank ledger + butler trip tracking (pure logic)
│       ├── settings.py          typed settings; defaults.yaml is the only source of defaults
│       ├── assets.py            what the user calibrates
│       ├── sim.py               simulated POH for dry runs and tests
│       └── README.md            setup, calibration, verification
├── profiles/                    per-user calibration and run logs (git-ignored)
├── tests/                       unit tests + end-to-end simulator scenarios
└── docs/
```

Dependencies only point downwards. `automations` uses `osrskit` and `autokit`,
`osrskit` uses `autokit`, and `autokit` knows no game. An automation for
another game or app would add a sibling of `osrskit`, or use `autokit` directly.

## Runtime model

```
          ┌──────────── loop until finished() or a stop ───────────┐
screen ─▶ │ observe: one screenshot → Snapshot (no input)          │
          │ decide:  pick the single most important handler        │
          │ act:     verified procedure: input → wait for the      │
          │          expected visual state (timeout = safeguard)   │
          └────────────────────────────────────────────────────────┘
```

* `RecoverableError`: an action did not produce its expected state. The
  base `Automation.run` loop records it and re-observes before trying again.
  `max_consecutive_failures` failures in a row become an `UnsafeState`.
  Verified progress (`mark_progress()`) resets the count.
* `UnsafeState`: the screen cannot be interpreted safely, for example an
  unknown dialogue, a missing target or a disconnect. The runner latches the
  emergency stop, saves a screenshot and records the reason.
* `EmergencyStopped`: from the hotkey, the stop file, the user moving the
  mouse, or Ctrl+C.

## Safety layers

1. **One latch for all input.** `InputController` checks the `EmergencyStop`
   before every mouse step, button and key. Once it is triggered, nothing more
   is sent. The runner always triggers it when a run ends, for any reason.
2. **Manual override.** If the cursor is not where the controller left it,
   the user has taken the mouse, and the run stops.
3. **Labels, not positions.** Menu options and dialogue choices are selected
   only when their calibrated label is visible exactly once. The label is
   re-checked under the cursor just before the click. Matching is strict:
   - grayscale correlation finds candidates;
   - colour is compared on the label's own pixels, so a recoloured
     ("unavailable") entry fails;
   - a per-pixel mismatch limit rejects labels that differ by one word;
   - `isolated` labels must not be part of a longer line.
4. **Unknown means stop.** An open dialogue is detected by the *absence* of
   the idle chat line. That catches dialogues nobody has seen before. If no
   rule recognises one, the run stops instead of guessing.
5. **Bounded retries** everywhere: per conversation rule (`max_uses`), per
   order (`max_order_attempts`), per trip (`max_summons_per_trip`), and per
   interaction (`max_consecutive_failures`).

## Perception primitives

* **Colour blobs** (`vision.color`) turn a RuneLite outline into a filled shape.
  Click points are chosen deep inside it, never on the edge.
* **Templates** (`vision.templates`) are reference images captured from the
  user's own client. The calibration wizard also records where each one was
  captured, so fixed UI elements are searched only near that spot. This is
  faster and avoids look-alikes elsewhere on screen.
* **Anchor** (`vision.anchor`): all calibrated coordinates are relative to one
  fixed UI element. Moving the window needs no recalibration.

## Dialogue rule engine (`runtime/rules.py`)

A conversation is a list of rules of the form *when labels X are visible
(and Y are not) → click / key / type*, applied first-match. After each action
the engine waits for the screen to change. A `done` callback ends the
conversation, and a `stalled` callback decides what an unrecognised state
means. Rules are YAML data, so they can be adapted to a server's wording
without changing code. A rule can carry a `signal` (e.g. `order_submitted`)
that the automation uses for bookkeeping.

## Profiles

A profile is one user's setup for one automation:

* `settings.yaml`: overrides merged onto the automation's `defaults.yaml`
  and parsed strictly into dataclasses, so unknown keys are errors;
* `calibration.json`: regions, colours, the anchor position, and where each
  template was captured;
* `templates/*.png`;
* `runs/`.

## Testing

`autokit.testing` provides `SimClock`, a virtual clock whose `sleep`
advances a simulated world, and `SimDesktop`, a screen source and input
backend that records every input event. An automation's simulator renders its
screens and implements the game rules. Tests then run the **unchanged**
automation and compare its report with the simulator's ground truth: what was
really built, ordered, delivered, clicked and typed.

# Writing a new automation

Use `automations/osrs_construction_mahogany_tables/` as the worked example.

## 1. Create the package

```
automations/my_new_automation/
├── __init__.py        AUTOMATION = MyAutomation
├── automation.py      the Automation subclass
├── assets.py          AssetSpec list: what the user calibrates
├── defaults.yaml      every setting, with comments (the single source of defaults)
├── settings.py        frozen dataclasses mirroring defaults.yaml
├── perception.py      screenshot → Snapshot (optional, but keeps automation.py readable)
├── sim.py             optional simulator for dry runs and tests
└── README.md          setup, calibration and verification steps for users
```

The registry finds any sub-package of `automations` that exports `AUTOMATION`.
`python -m autokit list` should then show it.

## 2. Skeleton

```python
from pathlib import Path

from autokit.errors import RecoverableError, UnsafeState
from autokit.runtime.automation import AssetSpec, Automation, Context
from autokit.runtime.settings import parse_dataclass

from .settings import Settings  # your frozen dataclasses mirroring defaults.yaml

ASSETS = (
    AssetSpec("client_anchor", "template", "A fixed piece of the UI."),
    AssetSpec("viewport", "region", "The game view."),
    AssetSpec("target", "color", "Highlight colour of the thing to click."),
    AssetSpec("menu_do_it", "template", "Menu text 'Do-it Thing'.", isolated=True),
)


class MyAutomation(Automation):
    id = "my_new_automation"
    title = "Do the thing"
    description = "One line shown by `autokit list`."
    assets = ASSETS
    anchor_template = "client_anchor"
    package_dir = Path(__file__).resolve().parent

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.settings = parse_dataclass(Settings, ctx.profile.settings)
        self.max_consecutive_failures = self.settings.safety.max_consecutive_failures

    def finished(self) -> str | None:
        return None  # or a reason string once the goal is reached

    def step(self) -> None:
        snap = self.observe()  # your perception: one screenshot -> recognised state, no input
        self.decide(snap)(snap)  # your decision: one verified procedure
```

## 3. Rules of thumb

* **Recognise before acting.** Every input must follow a positive
  recognition: a label, a highlight, a known dialogue. Use
  `osrskit.context_menu.choose_option` for menus,
  `autokit.runtime.actions.click_template` for buttons, and
  `RuleEngine` for conversations.
* **Verify after acting.** Wait with `ctx.wait_until(predicate, timeout,
  "what you expect")` for the state the action should cause. Never treat
  elapsed time alone as success.
* **Bounded retries.** Raise `RecoverableError` when an expected transition
  does not happen. The base loop re-observes and gives up after
  `max_consecutive_failures`. Call `mark_progress()` after verified success.
* **Unknown means stop.** Raise `UnsafeState` (and `ctx.log.save_frame`)
  when the screen does not match anything you can handle safely.
* **Keep logic pure where possible.** Bookkeeping such as `refill.py` should
  not touch the screen, so it can be unit-tested directly.
* **Data over code** for anything server-specific: wording, dialogue order,
  click sequences. Put it in `defaults.yaml` as rules or steps.
* Use `ctx.sleep`, `ctx.pause` and `ctx.input`, never `time.sleep` or a raw
  backend, so the emergency stop and the simulator keep working.

## 4. Test it

Write a simulator. It is a small `SimWorld` that renders frames and reacts to
input; see `sim.py`, and reuse `osrskit.testing.widgets` for OSRS-style
screens. Then run the real automation against it with `SimClock` and
`SimDesktop`, as in `tests/conftest.py`. Assert against the simulator's ground
truth, for example "nothing wrong was clicked" or "no input after the stop",
not only against the automation's own report.

Then verify on the real client with `python -m autokit check <id>` and
`check --hover` before the first supervised `run`.

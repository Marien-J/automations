# Construction: Mahogany tables + Demon Butler

Builds a Mahogany table on one Table space in your POH dining room, removes
it, and repeats. Mahogany planks are kept in stock by sending the Demon Butler
to the bank, and the cycle continues while he is away. It works only from screen
pixels and normal mouse and keyboard input. It is meant for **your own private
server**.

## How it works

Each loop iteration takes one screenshot, recognises the state of everything
it cares about, and does the single most important thing:

| Recognised state | Action |
| --- | --- |
| Client not found, login screen, "Connection lost" | Send nothing; wait `disconnect_grace_s`, then stop |
| Right-click menu or Furniture Creation Menu left open | Close it without selecting anything (counts as a failure) |
| Removal confirmation, and this run just chose Remove | Press `1`, then verify the table is gone |
| Known butler or level-up dialogue | Apply the matching dialogue rule |
| Dialogue that nothing recognises | Wait `unrecognised_grace_s`, then **stop** and save a screenshot |
| Inventory tab not open | Click the inventory tab (if calibrated), otherwise stop |
| Butler trip overdue | Call him (at most `max_summons_per_trip`), otherwise stop ordering |
| Butler back and still holding planks, with free slots | Talk to him to collect them |
| Planks ≤ `refill_threshold` and no trip pending | Order `request_quantity` planks |
| Mahogany table visible | Right-click → **Remove Mahogany table** → `1` → verify the Table space is back |
| Table space visible, ≥ 6 planks | Right-click → **Build Table space** → **Mahogany table** → verify it was built |
| Table space visible, < 6 planks | Wait for the pending delivery (or stop if refills are off) |
| Neither visible, or several candidates | Wait `unrecognised_grace_s`, then **stop** |

Every action is verified. Each input is followed by a wait for the expected
visual change, and the timeout is only a safeguard. A build counts only when
these all agree:

- the furniture menu has closed,
- the Table space outline is gone,
- the Mahogany table outline is visible (if calibrated),
- the inventory plank count changed,
- the +840 XP drop appeared (only if `require_xp_drop` is on).

Menu options and furniture entries are only ever selected after their exact
label is recognised. A label that differs by one word, such as "Build Chair
space", does not match. If the expected option is missing, the menu is closed
without clicking anything.

**Planks and trips.** Every change in the plank count is explained: builds
take 6, and anything else is a delivery. Each order becomes a pending trip with
its requested and received amounts. While a trip is pending no new order is
placed, and the butler is not called. When he returns with more planks than fit
(for example 19 of 24), the rest stays "held by the butler". The automation
collects them after the next build frees space. If he hands over nothing, it
concludes he holds nothing. A plank count that drops for no reason, for example
because the inventory is covered, stops the run.

## 1. Prepare RuneLite

1. **Object Markers.** Shift + right-click the empty **Table space** → *Mark
   object*, in a distinctive colour (cyan in your screenshot). Build a Mahogany
   table and mark *that* object too, in a **different** colour such as magenta.
   They are different objects, so each keeps its own marker. The second marker
   is optional but strongly recommended: it is what proves the table exists.
2. **NPC Indicators.** Highlight `Demon butler` in a third colour that nothing
   else on screen uses. Hull or outline style both work.
3. Optional: mark the dining-room **bell-pull** if you want to summon with it.
4. Building mode on, inventory tab open, camera facing the table. Keep the
   window size fixed after calibrating; moving the window is fine.
5. Inventory: hammer, saw, coins for wages (or a servant's money bag), mahogany
   planks. The rest should be empty.

## 2. Create and calibrate a profile

```bash
pip install -e .            # from the repository root
python -m autokit init osrs_construction_mahogany_tables
python -m autokit calibrate osrs_construction_mahogany_tables
```

The wizard goes through each item below. For each one it takes a screenshot
after a countdown, so you can put the game in the right state first. Then you
drag a box around the element: roughly first, then precisely on a zoomed view.
To redo a single item, use `--only name1,name2`. If you know a marker's exact
colour, set it directly:

```bash
python -m autokit set-color osrs_construction_mahogany_tables hotspot_empty 0,255,255
```

| Name | What to capture | Needed |
| --- | --- | --- |
| `client_anchor` | Compass + top of minimap frame (~40×40), something that never changes | always |
| `viewport`, `chatbox`, `inventory_grid` | Regions: game view; chatbox; slot 1 top-left → slot 28 bottom-right | always |
| `chat_idle` | Your name + `*` at the bottom of the chatbox with **no** dialogue open | always |
| `hotspot_empty` | Colour: the Table space marker | always |
| `table_built` | Colour: the Mahogany table marker | recommended |
| `menu_build_table_space` | Menu text **Build Table space** | always |
| `furniture_menu_title`, `furniture_mahogany_table` | Menu title; the **Mahogany table** name while buildable | always |
| `furniture_close` | The menu's close button | recommended |
| `menu_remove_mahogany_table` | Menu text **Remove Mahogany table** | always |
| `remove_confirm` | The removal question (e.g. *Really remove it?*) | always |
| `item_mahogany_plank` | One plank icon, tightly | always |
| `inventory_empty_slot`, `inventory_open` | An empty slot; the selected inventory tab | recommended |
| `butler` | Colour: the butler's NPC indicator | butler |
| `butler_opt_fetch`, `butler_opt_mahogany_planks`, `prompt_enter_amount` | The fetch option line, the mahogany planks line, the amount prompt | butler |
| `butler_opt_repeat_order` | The repeat option, only while it says mahogany planks × `repeat_option_quantity` | optional |
| `butler_main_options`, `butler_returned_msg`, `butler_holding_msg` | His menu title, return message, "keeping items" message | recommended |
| `butler_wage_request`, `butler_opt_pay_wage`, `butler_wage_insufficient` | Wage question, pay option, "not enough coins" message | recommended |
| `tab_settings`, `settings_house_options`, `house_options_call_butler`, `tab_inventory` | House Options summon path | summon = house_options |
| `bell_pull`, `menu_ring_bell_pull` | Bell-pull colour; its *Ring* menu entry | summon = bell_pull |
| `connection_lost`, `login_screen`, `level_up_msg`, `xp_drop_construction` | Extra recognisers | optional |
| `uptext`, `uptext_talk_to_butler` | Mouse-over text area; *Talk-to Demon butler* | optional |

Capture each **option label as the whole line**. These labels are matched as
complete lines, so "Mahogany planks" never matches inside "Fetch 24 x Mahogany
planks".

## 3. Adapt the dialogue rules to your server

The butler's exact wording on your server is unknown, and public transcripts
do not fully document it. His conversation is therefore defined as **rules**
in `settings.yaml` (`butler.rules`, `butler.order_rules`). Each rule says: when
these labels are visible, do exactly this one thing.

```yaml
- {name: fetch from bank, when: [butler_opt_fetch], click: butler_opt_fetch}
- {name: enter quantity, when: [prompt_enter_amount], type: "{quantity}", enter: true, signal: order_submitted}
```

If your server shows an extra message, for example *"Very well, master"* after
an order, the run stops with **unrecognised dialogue** and saves a screenshot
in `runs/`. Capture that message and add a rule, then run again:

```bash
python -m autokit calibrate osrs_construction_mahogany_tables --only butler_ack_msg
```
```yaml
- {name: acknowledge order, when: [butler_ack_msg], key: space}
```

Rules whose labels you have not captured are skipped and listed at start-up.
If your server opens the butler's dialogue as soon as he is called, that is
handled: the automation joins the open conversation instead of clicking him.

## 4. Verify on your server: check, then supervised runs

1. `python -m autokit check osrs_construction_mahogany_tables`. Sends no
   input. It prints what it recognises (table state, dialogue, butler, plank
   count, free slots) and saves an annotated screenshot. Try it in each state:
   table empty, table built, menu open, butler dialogue open.
2. `... check --hover` moves the mouse over the detected table and butler, and
   never clicks. Use it to confirm that clicks would land correctly, which
   catches display-scaling problems.
3. One cycle: set `run.max_tables: 1`, then `python -m autokit run
   osrs_construction_mahogany_tables`.
4. One refill: start with 12 planks, `max_tables: 4`.
5. Long run: `max_tables: 0` (no limit), with `max_minutes` as you like.

**Stopping:** press **F12** (`safety.emergency_hotkey`), move the mouse
yourself, press Ctrl+C in the terminal, or create the file `STOP` in the
profile folder. After a stop, nothing is sent. Every run writes
`runs/<timestamp>/events.jsonl`, plus screenshots when it stopped because of
the screen.

## 5. Compare with standard OSRS mechanics

The run summary includes what was actually observed on your server, to
compare with the assumptions:

| Summary field | Standard OSRS |
| --- | --- |
| `observed_planks_per_build` | `{6: n}`: 6 planks per Mahogany table |
| `xp_estimated` | 840 XP per table; check against your XP tracker |
| `observed_trip_s_avg` | ~7.2 s trip, plus dialogue and travel overhead |
| `observed_max_delivery_per_trip` | ≤ 26 (butler capacity) |
| `observed_orders_between_wages` | 8 services per wage (10,000 coins, or 7,500 after Love Story) |

If any of these differ, adjust `construction.planks_per_table`,
`butler.expected_trip_s`, `butler.trip_timeout_s` or `butler.refill_threshold`.
The threshold of 12 assumes the butler returns within about two build/remove
cycles. Raise it if you often find yourself waiting for planks.

## Simulator

`python -m autokit simulate osrs_construction_mahogany_tables --scenario
default` runs the real automation against a simulated house. The scenarios are
`default`, `butler-absent`, `bell-pull`, `unpaid-wages`, `camera`,
`reconnect`, `disconnect`, `unknown-dialogue`, `no-build-mode` and
`low-level`. The tests in `tests/osrs_construction_mahogany_tables/` use the
same simulator. It approximates the real client and is not a substitute for
the checks in step 4.

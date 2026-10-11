"""Everything the user calibrates for this automation (see README.md).

Groups decide when an asset is required: ``core`` always; ``butler`` when
``butler.enabled``; ``summon_<method>`` for the configured summon method.
"""

from __future__ import annotations

from autokit.runtime.automation import AssetSpec
from osrskit.context_menu import CLASSIC_MENU_BACKGROUND

ANCHOR = "client_anchor"


def _t(name: str, text: str, *, required: bool = True, group: str = "core", isolated: bool = False) -> AssetSpec:
    return AssetSpec(name, "template", text, required=required, group=group, isolated=isolated)


def _c(name: str, text: str, *, required: bool = True, group: str = "core", default=None) -> AssetSpec:
    return AssetSpec(name, "color", text, required=required, group=group, default=default)


def _r(name: str, text: str, *, required: bool = True, group: str = "core") -> AssetSpec:
    return AssetSpec(name, "region", text, required=required, group=group)


ASSETS: tuple[AssetSpec, ...] = (
    # -- client, layout ---------------------------------------------------------
    _t(
        ANCHOR,
        "A part of the game UI that never changes while you play, e.g. the compass and the top of "
        "the minimap frame (about 40x40 px). Used to find the client window and to notice disconnects.",
    ),
    _r("viewport", "The 3D game view only: exclude the chatbox, minimap and side panel."),
    _r("chatbox", "The whole chatbox area where NPC dialogues and options appear."),
    _r(
        "inventory_grid",
        "Open the inventory tab. Drag from the top-left corner of slot 1 to the "
        "bottom-right corner of slot 28 (all 4x7 slots, nothing more).",
    ),
    _t(
        "chat_idle",
        "With NO dialogue open: your name and the '*' input marker at the bottom of the chatbox. "
        "Its absence is how any open dialogue (known or not) is noticed.",
    ),
    # -- the table --------------------------------------------------------------
    _c(
        "hotspot_empty",
        "Building mode on, table removed: draw a small box on the Object Marker outline of "
        "the empty Table space (the cyan outline in your screenshot).",
    ),
    _c(
        "table_built",
        "Recommended. Build a Mahogany table, mark it with Object Markers in a DIFFERENT colour "
        "(e.g. magenta), then box a piece of that outline. Confirms that the table really exists.",
        required=False,
    ),
    _t(
        "menu_build_table_space",
        "Right-click the empty Table space. Select exactly the text 'Build Table space'.",
        isolated=True,
    ),
    _t("furniture_menu_title", "Open the Furniture Creation Menu. Select its title text."),
    _t(
        "furniture_mahogany_table",
        "In the Furniture Creation Menu, select the name 'Mahogany table' while it is "
        "buildable (you have >= 6 planks), so an unavailable entry never matches.",
        isolated=True,
    ),
    _t("furniture_close", "Recommended: the close (X) button of the Furniture Creation Menu.", required=False),
    _t(
        "menu_remove_mahogany_table",
        "Right-click the built table. Select exactly 'Remove Mahogany table'.",
        isolated=True,
    ),
    _t("remove_confirm", "Choose Remove: select the confirmation question text (e.g. 'Really remove it?')."),
    _t("xp_drop_construction", "Optional: the Construction XP drop for one table (e.g. '+840').", required=False),
    # -- inventory --------------------------------------------------------------
    _t("item_mahogany_plank", "One mahogany plank in the inventory: box the icon tightly, inside its slot."),
    _t(
        "inventory_empty_slot",
        "Recommended: one EMPTY inventory slot (box inside the slot). Enables counting "
        "free space, needed to collect partially delivered planks.",
        required=False,
    ),
    _t(
        "inventory_open",
        "Recommended: the inventory tab icon while it is the selected (highlighted) tab.",
        required=False,
    ),
    _t(
        "tab_inventory",
        "The inventory tab icon (any state); clicked to reopen the inventory.",
        group="summon_house_options",
    ),
    # -- client health ----------------------------------------------------------
    _t("connection_lost", "Optional: the 'Connection lost' message box text.", required=False),
    _t("login_screen", "Optional: a distinctive part of the login screen.", required=False),
    _t(
        "level_up_msg",
        "Optional: the start of the level-up message in the chatbox ('Congratulations, ...').",
        required=False,
    ),
    _c(
        "menu_background",
        "Optional: the right-click menu background (default: classic OSRS brown).",
        required=False,
        default=CLASSIC_MENU_BACKGROUND,
    ),
    # -- butler -----------------------------------------------------------------
    _c(
        "butler",
        "The NPC Indicators highlight colour of the Demon Butler (use a colour no other marker uses).",
        group="butler",
    ),
    _t(
        "butler_main_options",
        "Optional: a label shown only in the butler's main options dialogue; that dialogue "
        "is then known to be safe to leave open.",
        required=False,
        group="butler",
    ),
    _t(
        "butler_opt_fetch",
        "The option that asks the butler to fetch from the bank (exact text of that line).",
        group="butler",
        isolated=True,
    ),
    _t(
        "butler_opt_planks",
        "Optional: an intermediate 'Planks' category option, if your server shows one.",
        required=False,
        group="butler",
        isolated=True,
    ),
    _t(
        "butler_opt_more",
        "Optional: a 'More...' option to page through items, if needed to reach mahogany planks.",
        required=False,
        group="butler",
        isolated=True,
    ),
    _t(
        "butler_opt_mahogany_planks",
        "The 'Mahogany planks' option in the butler's item list (whole line).",
        group="butler",
        isolated=True,
    ),
    _t("prompt_enter_amount", "The quantity prompt text (e.g. 'Enter amount:').", group="butler"),
    _t(
        "butler_opt_repeat_order",
        "Optional: the repeat-order option, captured ONLY while it names mahogany "
        "planks and the quantity in butler.repeat_option_quantity (e.g. 24).",
        required=False,
        group="butler",
        isolated=True,
    ),
    _t(
        "butler_returned_msg",
        "Optional: the butler's 'I have returned...' message text.",
        required=False,
        group="butler",
    ),
    _t(
        "butler_holding_msg",
        "Optional: the message when he keeps items because your inventory is full.",
        required=False,
        group="butler",
    ),
    _t("butler_wage_request", "Optional: the wage request text.", required=False, group="butler"),
    _t(
        "butler_opt_pay_wage", "Optional: the option that pays the wage.", required=False, group="butler", isolated=True
    ),
    _t("butler_wage_insufficient", "Optional: the message shown when you cannot pay.", required=False, group="butler"),
    _r("uptext", "Optional: the top-left mouse-over text area of the game view.", required=False, group="butler"),
    _t(
        "uptext_talk_to_butler",
        "Optional: mouse-over text 'Talk-to Demon butler' (verified before left-clicking).",
        required=False,
        group="butler",
    ),
    _t(
        "menu_talk_to_butler",
        "Only for talk_mode: menu — the 'Talk-to Demon butler' right-click entry.",
        required=False,
        group="butler",
        isolated=True,
    ),
    # -- summoning --------------------------------------------------------------
    _t("tab_settings", "The settings tab icon.", group="summon_house_options"),
    _t("settings_house_options", "The 'House Options' button in the settings tab.", group="summon_house_options"),
    _t("house_options_call_butler", "The 'Call Butler'/'Call Servant' button.", group="summon_house_options"),
    _c("bell_pull", "Object Marker colour of the dining-room bell-pull.", group="summon_bell_pull"),
    _t("menu_ring_bell_pull", "Right-click the bell-pull: its 'Ring' entry.", group="summon_bell_pull", isolated=True),
)

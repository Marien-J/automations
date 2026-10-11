"""Client presence, position and connection state."""

from __future__ import annotations

from dataclasses import dataclass

from autokit.errors import ConfigError
from autokit.geometry import Rect
from autokit.runtime.profile import Profile
from autokit.vision.anchor import AnchorTracker
from autokit.vision.frame import Frame
from autokit.vision.templates import TemplateLibrary, find_best

# How far a fixed UI element may be from where it was calibrated.
HOME_MARGIN_PX = 30


@dataclass(frozen=True)
class ClientStatus:
    ok: bool
    dx: int = 0
    dy: int = 0
    problem: str | None = None


class ClientWatch:
    """Finds the client through its anchor and looks for disconnect screens.

    ``connection_lost`` and ``login_screen`` are optional templates; without
    them a disconnect is still caught when the anchor disappears or when the
    game stops reacting (every action verifies its result).
    """

    def __init__(
        self,
        library: TemplateLibrary,
        anchor: str,
        anchor_rect: Rect,
        *,
        connection_lost: str = "connection_lost",
        login_screen: str = "login_screen",
        homes: dict[str, Rect] | None = None,
    ) -> None:
        self.tracker = AnchorTracker(library.get(anchor), anchor_rect)
        self.connection_lost = library.get(connection_lost) if library.has(connection_lost) else None
        self.login_screen = library.get(login_screen) if library.has(login_screen) else None
        self.homes = homes or {}

    @classmethod
    def from_profile(cls, profile: Profile, anchor: str) -> ClientWatch:
        if profile.anchor_rect is None:
            raise ConfigError(f"anchor '{anchor}' has no calibrated position; calibrate it first")
        homes = {}
        for name in ("connection_lost", "login_screen"):
            home = profile.template_home(name)
            if home is not None:
                homes[name] = home
        return cls(profile.templates, anchor, profile.anchor_rect, homes=homes)

    def _area(self, name: str, dx: int = 0, dy: int = 0) -> Rect | None:
        home = self.homes.get(name)
        return None if home is None else home.translate(dx, dy).expand(HOME_MARGIN_PX)

    def status(self, frame: Frame) -> ClientStatus:
        if self.login_screen is not None and find_best(frame, self.login_screen) is not None:
            return ClientStatus(False, problem="logged out: the login screen is visible")
        fix = self.tracker.locate(frame)
        if fix is None:
            problem = "game client not visible (anchor not found): window covered, moved off-screen or logged out"
            return ClientStatus(False, problem=problem)
        if self.connection_lost is not None and find_best(
            frame, self.connection_lost, self._area("connection_lost", fix.dx, fix.dy)
        ):
            return ClientStatus(False, fix.dx, fix.dy, problem="connection lost message is visible")
        return ClientStatus(True, fix.dx, fix.dy)


class Layout:
    """Calibrated regions shifted to where the client currently is."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.dx = 0
        self.dy = 0

    def update(self, status: ClientStatus) -> None:
        if status.ok:
            self.dx, self.dy = status.dx, status.dy

    def region(self, name: str) -> Rect | None:
        rect = self.profile.region(name)
        return None if rect is None else rect.translate(self.dx, self.dy)

    def near(self, template: str, margin: int = HOME_MARGIN_PX) -> Rect | None:
        """Search area around where ``template`` was captured, if that is known."""
        home = self.profile.template_home(template)
        return None if home is None else home.translate(self.dx, self.dy).expand(margin)

    def require(self, name: str) -> Rect:
        rect = self.region(name)
        if rect is None:
            raise ConfigError(f"region '{name}' is not calibrated")
        return rect

"""Plank accounting and Demon Butler trip tracking.

Nothing here touches the screen. The automation feeds in observations (plank
counts, whether the butler is visible) and actions it performed (a table was
built, an order was submitted); these classes decide what is due and make sure
an order is never duplicated while a trip is outstanding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .settings import ButlerSettings


class PlankLedger:
    """Explains every change of the inventory plank count.

    Consumption by builds is announced with :meth:`consume`; whatever the next
    observed count adds on top of the expected value is a delivery.
    """

    def __init__(self) -> None:
        self.count: int | None = None
        self._consumed = 0

    def consume(self, planks: int) -> None:
        self._consumed += planks

    def observe(self, count: int) -> int:
        """Record a count; returns the unexplained change (+delivery / -missing)."""
        if self.count is None:
            self.count = count
            self._consumed = 0
            return 0
        expected = self.count - self._consumed
        self.count = count
        self._consumed = 0
        return count - expected


@dataclass
class Trip:
    requested: int
    submitted_at: float
    departed_at: float | None = None
    returned_at: float | None = None
    received: int = 0
    summons: int = 0

    @property
    def outstanding(self) -> int:
        return max(0, self.requested - self.received)


@dataclass
class TripRecord:
    requested: int
    received: int
    trip_s: float | None  # submission -> first sign of return


@dataclass
class Refill:
    settings: ButlerSettings
    trip: Trip | None = None
    disabled_reason: str | None = None
    failed_orders: int = 0
    summoned_at: float | None = None
    history: list[TripRecord] = field(default_factory=list)
    unexpected_planks: int = 0

    # -- what is due -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.settings.enabled and self.disabled_reason is None

    def can_order(self) -> bool:
        return self.enabled and self.trip is None

    def wants_order(self, planks: int) -> bool:
        return self.can_order() and planks <= self.settings.refill_threshold

    def wants_collect(self, free_slots: int | None) -> bool:
        trip = self.trip
        if trip is None or trip.returned_at is None or trip.outstanding == 0:
            return False
        return free_slots is None or free_slots > 0

    def overdue(self, now: float) -> bool:
        trip = self.trip
        return (
            trip is not None
            and trip.departed_at is not None
            and trip.returned_at is None
            and now - trip.submitted_at > self.settings.trip_timeout_s
        )

    def summon_in_progress(self, now: float) -> bool:
        return self.summoned_at is not None and now - self.summoned_at < self.settings.arrival_timeout_s

    # -- events --------------------------------------------------------------------
    def order_submitted(self, quantity: int, now: float) -> None:
        if self.trip is not None:
            raise RuntimeError("order submitted while a trip is pending")  # guarded by can_order()
        self.trip = Trip(quantity, now)

    def update(self, now: float, *, butler_visible: bool, chat_idle: bool, departure_timeout: float) -> str | None:
        """Advance the trip from what is on screen; returns an event name or ``None``."""
        trip = self.trip
        if trip is None:
            return None
        if trip.departed_at is None:
            if not butler_visible and chat_idle:
                trip.departed_at = now
                self.failed_orders = 0
                return "departed"
            if butler_visible and chat_idle and now - trip.submitted_at > departure_timeout:
                self.trip = None
                return "order_not_taken"
            return None
        if trip.returned_at is None and butler_visible and now - trip.departed_at >= 1.0:
            trip.returned_at = now
            return "returned"
        return None

    def mark_returned(self, now: float) -> None:
        if self.trip is not None and self.trip.returned_at is None:
            self.trip.returned_at = now
            if self.trip.departed_at is None:
                self.trip.departed_at = self.trip.submitted_at

    def delivered(self, planks: int, now: float) -> bool:
        """Account ``planks`` received; returns True when the trip is complete."""
        trip = self.trip
        if trip is None:
            self.unexpected_planks += planks
            return False
        self.mark_returned(now)
        trip.received += planks
        if trip.outstanding == 0:
            self._finish(now)
            return True
        return False

    def nothing_more_held(self, now: float) -> None:
        """Talking to the butler produced no planks: he holds nothing for us."""
        if self.trip is not None:
            self._finish(now)

    def _finish(self, now: float) -> None:
        trip = self.trip
        assert trip is not None
        took = None if trip.returned_at is None else trip.returned_at - trip.submitted_at
        self.history.append(TripRecord(trip.requested, trip.received, took))
        self.trip = None
        self.summoned_at = None

    def order_failed(self) -> None:
        self.failed_orders += 1
        if self.failed_orders >= self.settings.max_order_attempts:
            self.disable(f"{self.failed_orders} butler orders in a row failed")

    def disable(self, reason: str) -> None:
        if self.disabled_reason is None:
            self.disabled_reason = reason

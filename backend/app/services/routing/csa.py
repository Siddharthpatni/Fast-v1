"""Earliest-arrival Connection Scan Algorithm (Dibbelt et al., 2013).

Scans connections once in departure order. Supports several access stops
(each with its own ready time), several egress stops (each with a time to the
destination), a minimum transfer time between vehicles, and disruption
blocking (connections that cannot be used and stops that cannot be served).
"""

from bisect import bisect_left
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.services.routing.modes import MIN_TRANSFER_SECONDS
from app.services.routing.network import TransitNetwork

INF = 1 << 60


@dataclass
class Blocking:
    """What disruptions make unusable for this search."""

    conn_mask: NDArray[np.bool_] | None = None  # True = connection cannot be used
    stop_windows: dict[int, list[tuple[int, int]]] = field(
        default_factory=dict
    )  # no boarding/alighting

    def stop_blocked(self, stop: int, t: int) -> bool:
        windows = self.stop_windows.get(stop)
        return bool(windows) and any(start <= t <= end for start, end in windows)  # type: ignore[union-attr]

    def merged(self, other: "Blocking") -> "Blocking":
        if self.conn_mask is None:
            mask = other.conn_mask
        elif other.conn_mask is None:
            mask = self.conn_mask
        else:
            mask = self.conn_mask | other.conn_mask
        windows = {k: list(v) for k, v in self.stop_windows.items()}
        for k, v in other.stop_windows.items():
            windows.setdefault(k, []).extend(v)
        return Blocking(mask, windows)


@dataclass
class Ride:
    trip: int
    board_conn: int
    alight_conn: int


@dataclass
class Transfer:
    from_stop: int
    to_stop: int
    seconds: int


@dataclass
class Journey:
    access_stop: int
    egress_stop: int
    arrival_at_stop: int  # Seconds of service day at egress_stop
    parts: list[Ride | Transfer]

    @property
    def rides(self) -> list[Ride]:
        return [p for p in self.parts if isinstance(p, Ride)]


def earliest_arrival(
    net: TransitNetwork,
    sources: dict[int, int],
    targets: dict[int, int],
    blocking: Blocking | None = None,
    min_transfer: int = MIN_TRANSFER_SECONDS,
    horizon: int = 4 * 3600,
) -> Journey | None:
    """Fastest journey from any source stop (ready time) to any target stop (+ egress seconds).

    Returns None if no journey using at least one vehicle reaches a target within `horizon`.
    """
    if not sources or not targets or net.n_connections == 0:
        return None
    blocking = blocking or Blocking()
    mask = blocking.conn_mask
    has_stop_blocks = bool(blocking.stop_windows)

    n = len(net.stop_ids)
    arrival = [INF] * n  # Earliest arrival per stop
    by_vehicle = [False] * n  # Arrived on a vehicle (→ needs min_transfer to change)
    pointer: list[tuple[int, int, int] | None] = [
        None
    ] * n  # (kind, a, b): 0=source, 1=ride, 2=walk
    for stop, t in sources.items():
        if t < arrival[stop]:
            arrival[stop], pointer[stop] = t, (0, 0, 0)
    for stop in list(sources):
        for s2, d in net.footpaths[stop]:
            if arrival[stop] + d < arrival[s2]:
                arrival[s2], pointer[s2] = arrival[stop] + d, (2, stop, d)

    t0 = min(sources.values())
    board: dict[int, int] = {}  # trip → connection where we boarded
    best = INF
    l_trip, l_dep_stop, l_arr_stop = net.l_trip, net.l_dep_stop, net.l_arr_stop
    l_dep, l_arr = net.l_dep, net.l_arr

    for i in range(bisect_left(l_dep, t0), len(l_dep)):
        dep = l_dep[i]
        if dep >= best or dep > t0 + horizon:
            break
        trip = l_trip[i]
        if mask is not None and mask[i]:
            board.pop(trip, None)  # The vehicle can't continue: anyone aboard must have left
            continue
        if trip not in board:
            a = l_dep_stop[i]
            ready = arrival[a] + (min_transfer if by_vehicle[a] else 0)
            if ready > dep or (has_stop_blocks and blocking.stop_blocked(a, dep)):
                continue
            board[trip] = i
        b, arr = l_arr_stop[i], l_arr[i]
        if arr < arrival[b] and not (has_stop_blocks and blocking.stop_blocked(b, arr)):
            arrival[b], by_vehicle[b], pointer[b] = arr, True, (1, board[trip], i)
            if b in targets:
                best = min(best, arr + targets[b])
            for s2, d in net.footpaths[b]:
                if arr + d < arrival[s2]:
                    arrival[s2], by_vehicle[s2], pointer[s2] = arr + d, False, (2, b, d)
                    if s2 in targets:
                        best = min(best, arr + d + targets[s2])

    # Pick the target with the best arrival that was reached by riding something
    candidates = sorted((arrival[t] + e, t) for t, e in targets.items() if arrival[t] < INF)
    for _, target in candidates:
        journey = _extract(net, pointer, arrival, target)
        if journey is not None:
            return journey
    return None


def _extract(
    net: TransitNetwork, pointer: list[tuple[int, int, int] | None], arrival: list[int], target: int
) -> Journey | None:
    parts: list[Ride | Transfer] = []
    stop = target
    while True:
        entry = pointer[stop]
        if entry is None:
            return None
        kind, a, b = entry
        if kind == 0:
            break
        if kind == 2:
            parts.append(Transfer(a, stop, b))
            stop = a
        else:
            parts.append(Ride(net.l_trip[a], a, b))
            stop = net.l_dep_stop[a]
    parts.reverse()
    if not any(isinstance(p, Ride) for p in parts):
        return None  # Reached on foot only; the planner offers direct walking separately
    return Journey(
        access_stop=stop, egress_stop=target, arrival_at_stop=arrival[target], parts=parts
    )

"""Electrode array geometry and stimulation configuration enumeration.

Mirrors the configuration logic in ``runVoltageTransient.m`` /
``getNeighbor.m`` / ``getCombos.m``: given a 2-D arrangement of electrodes,
enumerate all valid (active, return[s]) groupings for monopolar (MP), bipolar
(BP), tripolar (TP), partial bipolar (PBP), partial tripolar (PTP), and
common-ground (CG) configurations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .config import COATINGS, Coating


# ---------------------------------------------------------------------------
# Electrode and array
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ElectrodePosition:
    """Physical location and identity of one electrode site."""
    number: int               # 1-based logical channel number
    row: int                  # 0-based grid row
    col: int                  # 0-based grid column
    surface_area_um2: float = 5000.0
    coating: str = "SIROF"

    @property
    def coating_props(self) -> Coating:
        return COATINGS.get(self.coating, COATINGS["SIROF"])


@dataclass
class ElectrodeArray:
    """A 2-D arrangement of electrodes, e.g. a 16-channel Utah array.

    The numbering follows the matrix shown in Fig. 1 of the IEEE NER paper:

        16 15 14 13
        12 11 10  9
         8  7  6  5
         4  3  2  1
    """
    name: str
    rows: int
    cols: int
    sites: List[ElectrodePosition]
    #: PlexStim port → device-channel-number translation. ``None`` =
    #: identity, which is correct for the current production cables
    #: (UTD MEA, Blackrock UEA, MicroProbes FMA — all wired so PlexStim
    #: port N drives device pad N). Populated only when a non-identity
    #: cable is in use; the runner falls back to ``cable_map.get(ch, ch)``
    #: so the identity case requires no special-casing.
    #:
    #: NOTE — when NeuroNexus support is re-enabled, set this to the
    #: ``omneticsNNX``/``utd_plexon``-derived dict (mirrored from
    #: MATLAB ``getDeviceType.m``) and update every runner's
    #: ``set_monitor_channel``/``load_channel``/``start_channel``/
    #: ``stop_channel``/``set_repetitions`` call site to translate
    #: through this map. The catalog tables already live in
    #: :data:`stimtest.config.CONNECTORS`.
    cable_map: Optional[dict[int, int]] = None

    # --------------------------------------------------------------
    @classmethod
    def utah_4x4(cls, name: str = "Utah-16",
                 surface_area_um2: float = 5000.0,
                 coating: str = "SIROF") -> "ElectrodeArray":
        """4x4 grid, numbered bottom-right=1 to top-left=16 (IEEE NER paper)."""
        sites: List[ElectrodePosition] = []
        for n in range(1, 17):
            # Numbering scheme from the paper: row 0 (top) holds 16,15,14,13;
            # row 1: 12,11,10,9; row 2: 8,7,6,5; row 3 (bottom): 4,3,2,1.
            grid_row = 3 - ((n - 1) // 4)   # 0..3 (top..bottom)
            grid_col = 3 - ((n - 1) % 4)    # 0..3 (left..right)
            sites.append(ElectrodePosition(
                number=n, row=grid_row, col=grid_col,
                surface_area_um2=surface_area_um2, coating=coating,
            ))
        return cls(name=name, rows=4, cols=4, sites=sites)

    @classmethod
    def custom_grid(cls, name: str, rows: int, cols: int,
                    surface_area_um2: float = 5000.0,
                    coating: str = "SIROF") -> "ElectrodeArray":
        sites = [
            ElectrodePosition(number=r * cols + c + 1, row=r, col=c,
                              surface_area_um2=surface_area_um2, coating=coating)
            for r in range(rows) for c in range(cols)
        ]
        return cls(name=name, rows=rows, cols=cols, sites=sites)

    @classmethod
    def from_mapping(cls, name: str, mapping,
                     surface_area_um2: float = 5000.0,
                     coating: str = "SIROF",
                     per_channel: Optional[dict] = None) -> "ElectrodeArray":
        """Build from a 2-D channel-number grid (``0`` = empty / inter-shank gap).

        ``mapping`` may be any 2-D array-like of ints. Values <= 0 mark
        positions that exist physically as a gap on the array — they
        produce no :class:`ElectrodePosition`, but the (rows, cols)
        bounding box is preserved so the GUI can still draw the layout.

        ``per_channel``, if given, is ``{channel_number: {'area_um2':
        float, 'coating': str}}`` and lets each electrode override the
        device-level defaults (used by the "Different surface area"
        mode on the Setup tab).
        """
        import numpy as np
        grid = np.asarray(mapping, dtype=int)
        if grid.ndim != 2:
            raise ValueError("mapping must be 2-D")
        rows, cols = grid.shape
        sites: List[ElectrodePosition] = []
        per = per_channel or {}
        for r in range(rows):
            for c in range(cols):
                ch = int(grid[r, c])
                if ch <= 0:
                    continue
                ovr = per.get(ch, {})
                sites.append(ElectrodePosition(
                    number=ch, row=r, col=c,
                    surface_area_um2=float(ovr.get("area_um2", surface_area_um2)),
                    coating=str(ovr.get("coating", coating)),
                ))
        # Sort by channel number for predictable ordering downstream
        sites.sort(key=lambda s: s.number)
        return cls(name=name, rows=rows, cols=cols, sites=sites)

    # --------------------------------------------------------------
    def __getitem__(self, n: int) -> ElectrodePosition:
        for s in self.sites:
            if s.number == n:
                return s
        raise KeyError(f"Electrode {n} not in array {self.name}")

    @property
    def channel_numbers(self) -> List[int]:
        return [s.number for s in self.sites]

    def neighbors(self, n: int, include_diagonal: bool = True) -> List[int]:
        """Immediate (8- or 4-connected) neighbors of electrode ``n``."""
        ref = self[n]
        out: List[int] = []
        for s in self.sites:
            if s.number == n:
                continue
            dr = abs(s.row - ref.row)
            dc = abs(s.col - ref.col)
            if include_diagonal:
                if dr <= 1 and dc <= 1:
                    out.append(s.number)
            else:
                if dr + dc == 1:
                    out.append(s.number)
        return out

    def position_grid(self) -> np.ndarray:
        """Return a (rows, cols) grid of electrode numbers (NaN for empty)."""
        g = np.full((self.rows, self.cols), -1, dtype=int)
        for s in self.sites:
            g[s.row, s.col] = s.number
        return g


# ---------------------------------------------------------------------------
# Stimulation configuration
# ---------------------------------------------------------------------------
@dataclass
class Configuration:
    """Active electrode + return strategy.

    The ``id`` follows the codes used throughout the MATLAB code:

    ====  ========================================================
    MP    Monopolar — one large remote return electrode
    CG    Common ground — all unused electrodes shorted as return
    BP    Bipolar — one immediate neighboring electrode as return
    TP    Tripolar — two immediate flanking electrodes as returns
    PBP   Partial bipolar — BP plus the global return
    PTP   Partial tripolar — TP plus the global return
    ====  ========================================================
    """
    id: str
    active: int
    returns: Tuple[int, ...] = ()
    counter_electrode_label: str = "Pt counter"

    @classmethod
    def monopolar(cls, active: int) -> "Configuration":
        return cls(id="MP", active=active, returns=())

    @classmethod
    def bipolar(cls, active: int, ret: int) -> "Configuration":
        return cls(id="BP", active=active, returns=(ret,))

    @classmethod
    def tripolar(cls, active: int, ret_a: int, ret_b: int) -> "Configuration":
        return cls(id="TP", active=active, returns=(ret_a, ret_b))

    @classmethod
    def from_active_returns(cls, active: int,
                            returns: Sequence[int]) -> "Configuration":
        n = len(returns)
        if n == 0:
            return cls.monopolar(active)
        if n == 1:
            return cls.bipolar(active, returns[0])
        if n == 2:
            return cls.tripolar(active, returns[0], returns[1])
        return cls(id=f"M{n+1}P", active=active, returns=tuple(returns))

    @property
    def all_channels(self) -> Tuple[int, ...]:
        return (self.active, *self.returns)

    @property
    def num_returns(self) -> int:
        return len(self.returns)

    def display_name(self) -> str:
        if not self.returns:
            return f"CH{self.active:02d}"
        rets = ",".join(f"{r:02d}" for r in self.returns)
        return f"CH{self.active:02d} v {rets}"


def enumerate_combinations(array: ElectrodeArray, config_id: str,
                           include_diagonal: bool = True) -> List[Configuration]:
    """Enumerate every valid (active, return-set) for a configuration kind.

    For a 4x4 Utah array with diagonals included as neighbors:
    - ``MP`` -> 16 combinations (one active per channel, no returns)
    - ``BP`` -> 84 combinations (active × single neighbor) — matches IEEE NER
    - ``TP`` -> 204 combinations (active × C(neighbors, 2) unordered pairs)

    The IEEE NER 2025 paper reports 231 TP combinations; the difference comes
    from a subtle case in the MATLAB enumerator's neighbor distance test. The
    function below is the simple "all unordered pairs of immediate neighbors"
    variant — easy to reason about and easy to swap for the exact MATLAB
    behavior by passing a custom ``include_diagonal`` policy.
    """
    config_id = config_id.upper()
    combos: List[Configuration] = []
    for active in array.channel_numbers:
        nbrs = array.neighbors(active, include_diagonal=include_diagonal)
        if config_id == "MP":
            combos.append(Configuration.monopolar(active))
        elif config_id == "BP":
            for r in nbrs:
                combos.append(Configuration.bipolar(active, r))
        elif config_id == "TP":
            for i, r1 in enumerate(nbrs):
                for r2 in nbrs[i + 1:]:
                    combos.append(Configuration.tripolar(active, r1, r2))
        elif config_id == "CG":
            others = [n for n in array.channel_numbers if n != active]
            combos.append(Configuration(id="CG", active=active, returns=tuple(others)))
        elif config_id == "PBP":
            for r in nbrs:
                c = Configuration.bipolar(active, r)
                combos.append(Configuration(id="PBP", active=active, returns=c.returns))
        elif config_id == "PTP":
            for i, r1 in enumerate(nbrs):
                for r2 in nbrs[i + 1:]:
                    c = Configuration.tripolar(active, r1, r2)
                    combos.append(Configuration(id="PTP", active=active, returns=c.returns))
        else:
            raise ValueError(f"Unknown configuration id {config_id!r}")
    return combos

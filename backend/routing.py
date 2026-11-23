"""Route planning from MTA static GTFS data (standard library only).

We build a map of which subway lines stop at which stations, then find which
lines connect two stations, with up to a few transfers. Stations that are
linked by walking transfers (listed in transfers.txt) count as one complex,
so Times Sq-42 St on the 1/2/3 connects to the N/Q/R/W there.

It does NOT know travel times or stop order yet; it answers "which lines get
me from A to B, and where do I switch?"

Needed files from the MTA static GTFS zip (unzipped into one folder):
    stops.txt, trips.txt, stop_times.txt   (transfers.txt is used if present)
"""

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

INDEX_VERSION = 2


def _read_csv(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        yield from csv.DictReader(f)


def _normalize_line(route_id: str, known: Set[str]) -> str:
    """Express variants (6X, 7X) count as their base line when it exists."""
    if route_id.endswith("X") and route_id[:-1] in known:
        return route_id[:-1]
    return route_id


class Index:
    def __init__(self, stations: Dict[str, str], line_stations: Dict[str, Set[str]],
                 transfers: Iterable[Tuple[str, str]] = ()):
        self.stations = stations                  # station id -> name
        self.line_stations = line_stations        # line -> set of station ids
        self.transfers = [tuple(t) for t in transfers]

        self.station_lines: Dict[str, Set[str]] = defaultdict(set)
        for line, sts in line_stations.items():
            for st in sts:
                self.station_lines[st].add(line)

        # Group stations joined by walking transfers into complexes.
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in self.transfers:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        self.complex_of: Dict[str, str] = {s: find(s) for s in stations}
        self.complex_members: Dict[str, Set[str]] = defaultdict(set)
        for s, c in self.complex_of.items():
            self.complex_members[c].add(s)
        self.complex_lines: Dict[str, Set[str]] = {
            c: set().union(*(self.station_lines.get(m, set()) for m in members))
            for c, members in self.complex_members.items()
        }
        self._line_complexes: Dict[str, Set[str]] = {}
        self._neighbors: Dict[str, Set[str]] = {}

    # ------------------------------------------------------------ building
    @classmethod
    def from_gtfs(cls, gtfs_dir: Path) -> "Index":
        gtfs_dir = Path(gtfs_dir)
        for name in ("stops.txt", "trips.txt", "stop_times.txt"):
            if not (gtfs_dir / name).exists():
                raise FileNotFoundError(f"{name} not found in {gtfs_dir}")

        station_of: Dict[str, str] = {}
        names: Dict[str, str] = {}
        for row in _read_csv(gtfs_dir / "stops.txt"):
            parent = row.get("parent_station") or ""
            station = parent or row["stop_id"]
            station_of[row["stop_id"]] = station
            names.setdefault(station, row["stop_name"])
            if not parent:
                names[station] = row["stop_name"]

        route_by_trip = {
            row["trip_id"]: row["route_id"]
            for row in _read_csv(gtfs_dir / "trips.txt")
        }

        raw: Dict[str, Set[str]] = defaultdict(set)
        for row in _read_csv(gtfs_dir / "stop_times.txt"):
            route = route_by_trip.get(row["trip_id"])
            station = station_of.get(row["stop_id"])
            if route and station:
                raw[route].add(station)

        known = set(raw)
        line_stations: Dict[str, Set[str]] = defaultdict(set)
        for route, sts in raw.items():
            line_stations[_normalize_line(route, known)] |= sts

        used = set().union(*line_stations.values()) if line_stations else set()
        stations = {sid: n for sid, n in names.items() if sid in used}

        transfers: List[Tuple[str, str]] = []
        transfers_path = gtfs_dir / "transfers.txt"
        if transfers_path.exists():
            for row in _read_csv(transfers_path):
                a = station_of.get(row["from_stop_id"], row["from_stop_id"])
                b = station_of.get(row["to_stop_id"], row["to_stop_id"])
                if a != b:
                    transfers.append((a, b))
        return cls(stations, dict(line_stations), transfers)

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps({
            "version": INDEX_VERSION,
            "stations": self.stations,
            "line_stations": {l: sorted(s) for l, s in self.line_stations.items()},
            "transfers": self.transfers,
        }))

    @classmethod
    def load(cls, path: Path) -> "Index":
        data = json.loads(Path(path).read_text())
        if data.get("version") != INDEX_VERSION:
            raise ValueError("saved index is from an older version")
        return cls(data["stations"],
                   {l: set(s) for l, s in data["line_stations"].items()},
                   data.get("transfers", []))

    # ------------------------------------------------------------- lookups
    def _describe(self, station_id: str) -> dict:
        return {
            "id": station_id,
            "name": self.stations.get(station_id, station_id),
            "lines": sorted(self.station_lines.get(station_id, [])),
        }

    def search(self, query: str, limit: int = 10) -> List[dict]:
        q = query.strip().lower()
        if not q:
            return []
        starts, contains = [], []
        for sid, name in self.stations.items():
            n = name.lower()
            if n.startswith(q):
                starts.append(sid)
            elif q in n:
                contains.append(sid)
        key = lambda sid: (self.stations[sid], sid)
        return [self._describe(s) for s in (sorted(starts, key=key) + sorted(contains, key=key))[:limit]]

    def resolve(self, text: str) -> str:
        """Accept a station id or (part of) a station name."""
        if text in self.stations:
            return text
        matches = self.search(text, limit=1)
        if not matches:
            raise KeyError(text)
        return matches[0]["id"]

    # ------------------------------------------------------------- routing
    def _complexes_of_line(self, line: str) -> Set[str]:
        if line not in self._line_complexes:
            self._line_complexes[line] = {
                self.complex_of[s] for s in self.line_stations.get(line, ())
                if s in self.complex_of
            }
        return self._line_complexes[line]

    def _line_neighbors(self, line: str) -> Set[str]:
        if line not in self._neighbors:
            out: Set[str] = set()
            for c in self._complexes_of_line(line):
                out |= self.complex_lines[c]
            out.discard(line)
            self._neighbors[line] = out
        return self._neighbors[line]

    def _transfer_complex(self, a: str, b: str) -> str:
        """Best place to switch between two lines: the biggest hub."""
        common = self._complexes_of_line(a) & self._complexes_of_line(b)
        return max(sorted(common), key=lambda c: len(self.complex_lines[c]))

    def _station_on(self, line: str, complex_id: str) -> str:
        """A station inside this complex that the given line stops at."""
        members = sorted(
            m for m in self.complex_members[complex_id]
            if line in self.station_lines.get(m, ())
        )
        return members[0]

    def _option(self, lines: List[str], origin: str, dest: str) -> dict:
        oc, dc = self.complex_of[origin], self.complex_of[dest]
        points = [oc] + [self._transfer_complex(a, b) for a, b in zip(lines, lines[1:])] + [dc]
        legs = []
        for i, line in enumerate(lines):
            if i == 0 and line in self.station_lines.get(origin, ()):
                frm = origin
            else:
                frm = self._station_on(line, points[i])
            if i == len(lines) - 1 and line in self.station_lines.get(dest, ()):
                to = dest
            else:
                to = self._station_on(line, points[i + 1])
            legs.append({"line": line, "from": self._describe(frm), "to": self._describe(to)})
        return {"transfers": len(lines) - 1, "legs": legs}

    def plan(self, origin: str, dest: str, max_transfers: int = 2, limit: int = 5) -> List[dict]:
        if origin not in self.complex_of or dest not in self.complex_of:
            return []
        oc, dc = self.complex_of[origin], self.complex_of[dest]
        if oc == dc:
            return []
        starts = self.complex_lines[oc]
        goals = self.complex_lines[dc]

        direct = sorted(starts & goals)
        if direct:
            return [self._option([l], origin, dest) for l in direct[:limit]]

        frontier = [[l] for l in sorted(starts)]
        for _ in range(max_transfers):
            found, nxt = [], []
            for path in frontier:
                for other in sorted(self._line_neighbors(path[-1])):
                    if other in path:
                        continue
                    new = path + [other]
                    (found if other in goals else nxt).append(new)
            if found:
                return [self._option(p, origin, dest) for p in found[:limit]]
            frontier = nxt[:2000]
        return []


def load_or_build_index(gtfs_dir: Path, cache_path: Path) -> Index:
    """Use the saved index if it is current, otherwise rebuild from GTFS."""
    cache_path = Path(cache_path)
    if cache_path.exists():
        try:
            return Index.load(cache_path)
        except (ValueError, KeyError):
            pass  # old or damaged cache: rebuild below
    index = Index.from_gtfs(gtfs_dir)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    index.save(cache_path)
    return index

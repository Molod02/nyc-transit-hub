"""Offline tests for route planning using a tiny made-up GTFS feed.

Run with:  python -m unittest test_routing   (no extra packages needed)

Made-up network:
    Line X: Alpha - Bravo - Charlie
    Line Y: Charlie - Delta - Echo
    Line Z: Echo - Foxtrot - Golf
So Alpha -> Echo needs X then Y (transfer at Charlie);
Alpha -> Golf needs X, Y, Z (two transfers).
"""

import csv
import tempfile
import unittest
from pathlib import Path

from routing import Index, load_or_build_index

STATIONS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf"]
LINES = {
    "X": ["Alpha", "Bravo", "Charlie"],
    "Y": ["Charlie", "Delta", "Echo"],
    "Z": ["Echo", "Foxtrot", "Golf"],
}


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_gtfs(folder: Path):
    # Each station has a parent row and a north platform with a parent.
    stops = []
    for s in STATIONS:
        stops.append([s, s, 1, ""])
        stops.append([s + "N", s, 0, s])
    write_csv(folder / "stops.txt", ["stop_id", "stop_name", "location_type", "parent_station"], stops)
    trips, times = [], []
    for line, sts in LINES.items():
        trip = f"{line}-trip"
        trips.append([line, trip])
        for i, s in enumerate(sts):
            times.append([trip, s + "N", i])
    trips.append(["Y", "YX-trip"])  # an express-ish trip with the same stops
    for i, s in enumerate(LINES["Y"]):
        times.append(["YX-trip", s + "N", i])
    write_csv(folder / "trips.txt", ["route_id", "trip_id"], trips)
    write_csv(folder / "stop_times.txt", ["trip_id", "stop_id", "stop_sequence"], times)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        make_gtfs(self.dir)
        self.index = Index.from_gtfs(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_maps_platforms_to_stations(self):
        self.assertEqual(set(self.index.stations), set(STATIONS))
        self.assertEqual(self.index.station_lines["Charlie"], {"X", "Y"})

    def test_direct_route(self):
        opts = self.index.plan("Alpha", "Charlie")
        self.assertEqual(len(opts), 1)
        self.assertEqual(opts[0]["transfers"], 0)
        self.assertEqual(opts[0]["legs"][0]["line"], "X")

    def test_one_transfer(self):
        opts = self.index.plan("Alpha", "Echo")
        self.assertEqual(opts[0]["transfers"], 1)
        legs = opts[0]["legs"]
        self.assertEqual([l["line"] for l in legs], ["X", "Y"])
        self.assertEqual(legs[0]["to"]["name"], "Charlie")
        self.assertEqual(legs[1]["from"]["name"], "Charlie")

    def test_two_transfers(self):
        opts = self.index.plan("Alpha", "Golf")
        self.assertEqual(opts[0]["transfers"], 2)
        self.assertEqual([l["line"] for l in opts[0]["legs"]], ["X", "Y", "Z"])

    def test_too_many_transfers_returns_nothing(self):
        self.assertEqual(self.index.plan("Alpha", "Golf", max_transfers=1), [])

    def test_search_and_resolve(self):
        self.assertEqual(self.index.search("char")[0]["id"], "Charlie")
        self.assertEqual(self.index.resolve("echo"), "Echo")
        with self.assertRaises(KeyError):
            self.index.resolve("nowhere")

    def test_save_load_and_cache(self):
        cache = self.dir / "index.json"
        first = load_or_build_index(self.dir, cache)
        self.assertTrue(cache.exists())
        second = load_or_build_index(self.dir / "missing", cache)  # uses cache
        self.assertEqual(first.stations, second.stations)
        self.assertEqual(first.line_stations, second.line_stations)

    def test_missing_files_raise(self):
        with self.assertRaises(FileNotFoundError):
            Index.from_gtfs(self.dir / "nope")


if __name__ == "__main__":
    unittest.main()

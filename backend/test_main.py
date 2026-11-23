"""Offline tests: build fake GTFS-realtime alert feeds, no network needed."""

import time

from fastapi.testclient import TestClient
from google.transit import gtfs_realtime_pb2

import main


def make_feed(alerts):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())
    for i, (routes, header, active) in enumerate(alerts):
        e = feed.entity.add()
        e.id = f"alert-{i}"
        tr = e.alert.header_text.translation.add()
        tr.text = header
        tr.language = "en"
        for r in routes:
            e.alert.informed_entity.add().route_id = r
        if not active:
            p = e.alert.active_period.add()
            p.start = 1
            p.end = 2  # long expired
    return feed


def test_parse_alerts_skips_inactive_and_maps_express():
    feed = make_feed([
        (["L"], "L trains delayed", True),
        (["6X"], "6 express rerouted", True),
        (["A"], "old alert", False),
    ])
    alerts = main.parse_alerts(feed)
    assert [a["routes"] for a in alerts] == [["L"], ["6"]]


def test_build_status_marks_affected_lines():
    feed = make_feed([(["L"], "L trains delayed", True)])
    statuses = {s["line"]: s for s in main.build_status(main.parse_alerts(feed))}
    assert statuses["L"]["status"] == "Service Alert"
    assert statuses["L"]["message"] == "L trains delayed"
    assert statuses["A"]["status"] == "Good Service"
    assert len(statuses) == len(main.LINES)


def test_status_endpoint_uses_feed(monkeypatch):
    feed = make_feed([(["G"], "G service suspended", True)])
    monkeypatch.setattr(main, "fetch_feed", lambda path: feed)
    main._cache.clear()
    client = TestClient(main.app)
    data = client.get("/status").json()
    assert {"line": "G", "status": "Service Alert", "message": "G service suspended"} in data
    assert client.get("/health").json() == {"ok": True}


def make_trip_feed(now):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for i, (stop, minutes) in enumerate([("A27N", 5), ("A27S", 2), ("A28N", 1), ("A27N", -10)]):
        e = feed.entity.add()
        e.id = f"trip-{i}"
        e.trip_update.trip.route_id = "A"
        stu = e.trip_update.stop_time_update.add()
        stu.stop_id = stop
        stu.arrival.time = int(now + minutes * 60)
    return feed


def test_parse_arrivals_filters_station_and_sorts():
    now = time.time()
    result = main.parse_arrivals([make_trip_feed(now)], "A27", now=now)
    assert [a["minutes"] for a in result] == [2, 5]  # A28 and past train excluded
    assert result[0]["direction"].startswith("Downtown")
    assert result[1]["direction"].startswith("Uptown")


def test_route_endpoint_503_without_gtfs(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "GTFS_DIR", tmp_path / "missing")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "_index", None)
    r = TestClient(main.app).get("/route", params={"origin": "a", "destination": "b"})
    assert r.status_code == 503


def test_status_endpoint_502_when_mta_down(monkeypatch):
    def boom(path):
        raise RuntimeError("down")
    monkeypatch.setattr(main, "fetch_feed", boom)
    main._cache.clear()
    assert TestClient(main.app).get("/status").status_code == 502

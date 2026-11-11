"""End-to-end scan with a fake yad2 (no network)."""
import copy

from hunter import scan as scan_mod
from hunter.config import load_config
from hunter.db import DB
from tests.test_hunter import ITEM, MARKER


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
        self.headers = {"Content-Type": "application/json" if payload is not None else "text/html"}
        self.url = "fake"

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, markers, blocked_items=()):
        self.markers = markers
        self.blocked_items = set(blocked_items)
        self.headers = {}
        self.calls = 0

    def get(self, url, params=None, **kw):
        self.calls += 1
        if "realestate-feed" in url:
            city = params["city"]
            return FakeResp(200, {"data": {"markers": [m for m in self.markers
                                                       if m["address"]["city"]["id"] == city]}})
        token = url.rsplit("/", 1)[-1]
        if token in self.blocked_items:
            return FakeResp(302)
        item = copy.deepcopy(ITEM)
        item["token"] = token
        m = next(m for m in self.markers if m["token"] == token)
        item["price"] = m["price"]
        return FakeResp(200, {"data": item})


def make_marker(token, price=6000, city=8600, rooms=3):
    m = copy.deepcopy(MARKER)
    m.update(token=token, price=price)
    m["address"]["city"]["id"] = city
    m["additionalDetails"]["roomsCount"] = rooms
    return m


def run(monkeypatch, tmp_path, session, sent):
    cfg = load_config()
    cfg["db_path"] = str(tmp_path / "t.db")
    cfg["llm"]["enabled"] = False
    cfg["run"]["delay_seconds"] = [0, 0]
    real_client = scan_mod.Yad2Client

    def client_factory(**kw):
        c = real_client(delay_seconds=(0, 0), backoff=(), session=session)
        return c

    monkeypatch.setattr(scan_mod, "Yad2Client", client_factory)
    monkeypatch.setattr(scan_mod.Notifier, "send", lambda self, text, photo=None: sent.append(text) or True)
    monkeypatch.setattr(scan_mod.time, "sleep", lambda s: None)
    return cfg, scan_mod.run_scan(cfg)


def test_bootstrap_then_only_new(monkeypatch, tmp_path):
    markers = [make_marker("a1"), make_marker("a2", price=7400), make_marker("a3", rooms=2),
               make_marker("g1", city=6300, price=5000)]
    sent = []
    cfg, stats = run(monkeypatch, tmp_path, FakeSession(markers), sent)
    assert stats["seen"] == 4 and stats["error"] is None
    assert len(sent) == 1 and "הסריקה הראשונית" in sent[0]  # summary only, no flood
    db = DB(cfg["db_path"])
    assert db.get("yad2:a3")["verdict"] == "reject"   # 2 rooms, rejected from feed data
    assert db.get("yad2:a1")["verdict"] == "match"

    # second run: one new listing appears -> exactly one notification
    markers.append(make_marker("n1", price=6200))
    markers[0]["price"] = 5800  # price drop on a1
    sent.clear()
    cfg, stats = run(monkeypatch, tmp_path, FakeSession(markers), sent)
    assert stats["new"] == 1 and stats["notified"] == 1
    assert any("ירידת מחיר" in s for s in sent)


def test_block_keeps_progress(monkeypatch, tmp_path):
    markers = [make_marker("b1"), make_marker("b2")]
    sent = []
    cfg, stats = run(monkeypatch, tmp_path, FakeSession(markers, blocked_items={"b1"}), sent)
    assert stats["error"] and stats["seen"] == 2
    db = DB(cfg["db_path"])
    assert db.get_meta("bootstrapped") is None  # bootstrap not finished
    assert len(db.pending_details(10)) >= 1

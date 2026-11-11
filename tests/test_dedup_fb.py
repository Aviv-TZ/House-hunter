import copy

import pytest

import hunter.facebook as fbmod
from hunter import scan as scan_mod
from hunter.config import load_config
from hunter.db import DB
from hunter.dedup import is_duplicate, normalize_phone, text_hash
from hunter.facebook import clean_post_text
from hunter.models import Listing
from hunter.rules import analyze, keyword_signals
from hunter.scan import pick_groups
from tests.test_hunter import full_listing

POST = ("להשכרה בנגבה רמת גן! דירת 3.5 חדרים ברחוב ארלוזורוב 12, קומה 3 עם מעלית, מטבח גדול ומשופץ, "
        "חיות מחמד בברכה, חניה בשפע ברחוב. 6,900 ש\"ח. כניסה 1.12. ללא תיווך. 050-1234567")


def fb(source_id, text=POST, source="fb_group", **kw):
    return Listing(source=source, source_id=source_id, url=f"https://fb/{source_id}", description=text, **kw)


# ---------- dedup ----------
def test_same_post_text_hash_ignores_ui_noise():
    a = clean_post_text(POST + "\nלייק\nתגובה\n12\nשיתוף")
    b = clean_post_text(POST + "\n3 תגובות\nLike\nComment")
    assert text_hash(a) == text_hash(b)


def test_phone_normalization():
    assert normalize_phone("+972-50-123-4567") == normalize_phone("050 1234567") == "0501234567"


def test_dup_by_phone():
    a = fb("a", phone="0501234567", price=6900, rooms=3.5)
    b = fb("b", text="דירה שונה לגמרי בניסוח כדי לבדוק טלפון בלבד בלי שום קשר לטקסט", phone="+972501234567",
           price=7000, rooms=3.5)
    assert is_duplicate(a, b) == "אותו טלפון"


def test_dup_by_address_cross_source():
    y = full_listing()  # yad2: קריניצי 100, floor 3, 3 rooms, 6000
    f = fb("f", text="פוסט קצר", street="רחוב קריניצי", house_number="100", city_id=8600, rooms=3, price=6100)
    assert is_duplicate(f, y) == "אותה כתובת, חדרים ומחיר"


def test_not_dup_different_floor_or_price():
    y = full_listing()
    f = fb("f", text="פוסט קצר", street="קריניצי", floor=1, city_id=8600, rooms=3, price=6000)
    assert is_duplicate(f, y) is None
    f2 = fb("f2", text="פוסט קצר", street="קריניצי", house_number="100", city_id=8600, rooms=3, price=7400)
    assert is_duplicate(f2, y) is None


def test_similar_text_but_different_apartment_not_dup():
    template = "משרד תיווך פלוני מציג: דירה מהממת ומוארת, משופצת מהיסוד, קרובה לכל, צרו קשר עוד היום לפרטים נוספים"
    a = fb("a", text=template + " 3 חדרים", rooms=3, price=5000)
    b = fb("b", text=template + " 5 חדרים", rooms=5, price=7400)
    assert is_duplicate(a, b) is None


# ---------- parking ----------
def test_parking_keywords():
    assert keyword_signals("יש חנייה בשפע ברחוב")["parking"] == "street_easy"
    assert keyword_signals("חניה פרטית בטאבו")["parking"] == "private"
    assert keyword_signals("קשה למצוא חניה")["parking"] == "street_hard"


def test_parking_bonus_order():
    cfg = load_config()
    base = full_listing()
    base.parking, base.parking_type = None, None
    none = analyze(base, cfg).score
    base.description += " חניה בשפע ברחוב"
    street = analyze(base, cfg).score
    base.parking_type = "private"
    private = analyze(base, cfg).score
    assert private - none >= 12 and none < street < private


def test_priority_by_name_for_facebook():
    cfg = load_config()
    l = fb("p", city_id=8600, neighborhood="שיכון הותיקים")
    assert analyze(l, cfg).priority


def test_group_rotation():
    groups = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    visits = {"a": "2026-11-11T10:00", "b": "2026-11-11T09:00"}
    assert [g["id"] for g in pick_groups(groups, visits, 2)] == ["c", "b"]


# ---------- full facebook flow with fake browser + fake Claude ----------
EXTRACT = {"is_rental_offer": True, "roommates_ad": False, "price": 6900, "rooms": 3.5, "sqm": None,
           "city": "רמת גן", "neighborhood": "נגבה", "street": "ארלוזורוב", "house_number": "12", "floor": 3,
           "elevator": "yes", "parking": "street_easy", "pets": "allowed", "kitchen": "large", "broker_fee": "no",
           "entrance_date": "1.12", "phone": "0501234567", "red_flags": [], "summary": "3.5 חדרים בנגבה"}
LOOKING = dict(EXTRACT, is_rental_offer=False, price=None, phone=None, street=None, house_number=None,
               summary="מחפשים דירה")


class FakeLLM:
    calls = 0

    def analyze(self, listing):
        FakeLLM.calls += 1
        return copy.deepcopy(LOOKING if "מחפשים" in listing.description else EXTRACT)


class FakeFB:
    posts = {}

    def __init__(self, cfg, debug=False):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def scan_group(self, g):
        return [fb(f"{g['id']}_{i}", text=clean_post_text(t), source_label=g["name"]) for i, t in enumerate(self.posts[g["id"]])]

    def scan_marketplace(self, url):
        return [fb("m1", text="דירה 3.5 חדרים 6,900", source="fb_market")]

    def enrich_marketplace(self, listing):
        listing.description = POST.replace("050-1234567", "0501234567") + " (Marketplace)"
        return listing


@pytest.fixture
def fb_env(monkeypatch, tmp_path):
    cfg = load_config()
    cfg["db_path"] = str(tmp_path / "t.db")
    cfg["yad2"] = {"enabled": False}
    cfg["facebook"] = dict(cfg["facebook"], enabled=True, groups=[{"id": "g1", "name": "דירות ברמת גן"},
                                                                  {"id": "g2", "name": "דירות גבעתיים"}])
    cfg["facebook"]["groups_file"] = "nonexistent.yaml"
    FakeLLM.calls = 0
    monkeypatch.setattr(fbmod, "FacebookSource", FakeFB)
    monkeypatch.setattr(scan_mod.LLMAnalyzer, "from_config", classmethod(lambda cls, c: FakeLLM()))
    sent = []
    monkeypatch.setattr(scan_mod.Notifier, "send", lambda self, text, photo=None: sent.append(text) or True)
    monkeypatch.setattr(scan_mod.time, "sleep", lambda s: None)
    return cfg, sent


def test_facebook_flow_dedups_across_groups_and_marketplace(fb_env):
    cfg, sent = fb_env
    FakeFB.posts = {"g1": [POST + "\nלייק\n4"], "g2": [POST + "\nשיתוף", "מחפשים דירת 3 חדרים ברמת גן עד 7000"]}
    stats = scan_mod.run_scan(cfg, only="facebook")
    assert stats["error"] is None
    db = DB(cfg["db_path"])
    canon = [r for r in db.all() if r["dup_of"] is None and r["verdict"] in ("match", "check")]
    assert len(canon) == 1, [dict(r) for r in db.all()]
    assert len(db.duplicates_of(canon[0]["key"])) == 2      # other group (same text) + marketplace (phone)
    assert FakeLLM.calls == 3                               # identical repost skipped Claude
    looking = [r for r in db.all() if "מחפשים" in r["data"]]
    assert looking[0]["verdict"] == "reject"
    assert len(sent) == 1 and "הסריקה הראשונית של פייסבוק" in sent[0]

    # next run: a new post in g1 -> one notification; old posts are not re-notified
    sent.clear()
    db.set_meta("fb_last_run", "2000-01-01T00:00:00")
    FakeFB.posts["g1"].append(POST.replace("ארלוזורוב 12", "ביאליק 40").replace("050-1234567", "052-7654321")
                              .replace("6,900", "6,500") + " דירה חדשה בשוק")
    new_extract = dict(EXTRACT, street="ביאליק", house_number="40", phone="0527654321", price=6500)
    FakeLLM.analyze = lambda self, l: copy.deepcopy(new_extract if "ביאליק" in l.description else EXTRACT)
    stats = scan_mod.run_scan(cfg, only="facebook")
    assert stats["notified"] == 1 and "ביאליק" in sent[0] and "חניה בשפע" in sent[0]

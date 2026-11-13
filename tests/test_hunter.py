import copy

import pytest

from hunter.config import load_config
from hunter.db import DB
from hunter.rules import analyze, keyword_signals
from hunter.yad2 import listing_from_marker, merge_item_details

MARKER = {
    "address": {
        "region": {"text": "תל אביב והסביבה", "id": 3},
        "city": {"id": 8600, "text": "רמת גן"},
        "neighborhood": {"id": 237, "text": "נגבה"},
        "street": {"text": "קריניצי"},
        "house": {"number": 100, "floor": 3},
    },
    "adType": "private",
    "price": 6000,
    "token": "abc123",
    "additionalDetails": {"property": {"text": "דירה"}, "roomsCount": 3, "squareMeter": 80},
    "metaData": {"images": ["https://img.yad2.co.il/x.jpeg"]},
    "tags": [{"name": "חניה", "id": 1003}],
}

ITEM = {
    "token": "abc123", "adType": "private", "price": 6000,
    "furnitureInfo": "מזגנים בכל חדר, מטבח מאובזר",
    "additionalDetails": {"entranceDate": "2026-11-01T00:00:00", "squareMeter": 80, "roomsCount": 3,
                          "parkingSpacesCount": 1, "buildingTopFloor": 4},
    "inProperty": {"includeElevator": True, "includeParking": True, "includeBalcony": True,
                   "includeSecurityRoom": False, "includeFurniture": True},
    "address": {"house": {"number": 100, "floor": 3}},
    "metaData": {"description": "דירה עורפית שקטה, מטבח גדול ומרווח, חיות מחמד בברכה",
                 "images": ["https://img.yad2.co.il/x.jpeg"]},
    "dates": {"createdAt": "2026-10-12T11:22:52"},
}


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def full_listing(**item_overrides):
    item = copy.deepcopy(ITEM)
    for k, v in item_overrides.items():
        item[k] = v
    return merge_item_details(listing_from_marker(copy.deepcopy(MARKER)), item)


def test_parse_marker():
    l = listing_from_marker(MARKER)
    assert l.key == "yad2:abc123"
    assert l.city_id == 8600 and l.neighborhood_id == 237
    assert l.floor == 3 and l.rooms == 3 and l.price == 6000
    assert l.is_broker is False and l.parking is True
    assert l.url.endswith("/realestate/item/abc123")


def test_parse_details():
    l = full_listing()
    assert l.details_fetched and l.elevator is True and l.top_floor == 4
    assert l.entrance_date == "2026-11-01"
    assert "מטבח גדול" in l.description


def test_full_match_priority(cfg):
    a = analyze(full_listing(), cfg)
    assert a.verdict == "match", a
    assert a.priority and a.pets is True and a.kitchen == "large"


def test_no_elevator_high_floor_rejected(cfg):
    item_inprop = dict(ITEM["inProperty"], includeElevator=False)
    a = analyze(full_listing(inProperty=item_inprop), cfg)
    assert a.verdict == "reject"
    assert any("מעלית" in r for r in a.reasons)


def test_no_elevator_first_floor_ok(cfg):
    l = full_listing(inProperty=dict(ITEM["inProperty"], includeElevator=False))
    l.floor = 1
    assert analyze(l, cfg).verdict == "match"


def test_pets_unknown_needs_check(cfg):
    meta = dict(ITEM["metaData"], description="דירה מקסימה עם מטבח מרווח")
    a = analyze(full_listing(metaData=meta), cfg)
    assert a.verdict == "check"
    assert any("חיות" in f for f in a.flags)


def test_pets_forbidden_rejected(cfg):
    meta = dict(ITEM["metaData"], description="מטבח גדול. ללא בעלי חיים")
    assert analyze(full_listing(metaData=meta), cfg).verdict == "reject"


def test_small_kitchen_rejected(cfg):
    meta = dict(ITEM["metaData"], description="מטבחון, חיות מחמד בברכה")
    assert analyze(full_listing(metaData=meta), cfg).verdict == "reject"


def test_broker_price_rule(cfg):
    cheap = full_listing(adType="commercial", price=6500)
    assert analyze(cheap, cfg).verdict == "check"  # broker allowed, flagged
    pricey = full_listing(adType="commercial", price=7300)
    assert analyze(pricey, cfg).verdict == "reject"


def test_broker_mentioned_in_text(cfg):
    meta = dict(ITEM["metaData"], description="מטבח גדול, חיות בברכה. דמי תיווך חודש")
    l = full_listing(metaData=meta, price=7400)
    assert analyze(l, cfg).verdict == "reject"
    meta = dict(ITEM["metaData"], description="מטבח גדול, חיות בברכה. ללא תיווך!")
    assert analyze(full_listing(metaData=meta, price=7400), cfg).verdict == "match"


def test_llm_fills_unknowns(cfg):
    meta = dict(ITEM["metaData"], description="דירה יפה ושקטה")
    l = full_listing(metaData=meta)
    a = analyze(l, cfg, {"pets": "allowed", "kitchen": "normal", "broker_fee": "no",
                         "roommates_ad": False, "red_flags": [], "summary": "דירה יפה"})
    assert a.verdict == "match" and a.summary == "דירה יפה"


def test_roommates_and_sublet(cfg):
    meta = dict(ITEM["metaData"], description="מחפשים שותף/ה לדירה, מטבח גדול")
    assert analyze(full_listing(metaData=meta), cfg).verdict == "reject"
    l = full_listing()
    l.property_type = "סאבלט"
    assert analyze(l, cfg).verdict == "reject"


def test_keyword_signals():
    assert keyword_signals("אין חיות")["pets"] is False
    assert keyword_signals("מתאים לבעלי חיים")["pets"] is True
    assert keyword_signals("דירה")["pets"] is None


def test_db_new_and_price_drop(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    l = listing_from_marker(MARKER)
    assert db.upsert_seen(l) == (True, None)
    l.price = 5500
    is_new, old = db.upsert_seen(l)
    assert not is_new and old == 6000
    db.set_status(l.key, "contacted", "דיברתי עם בעל הדירה")
    row = db.get(l.key)
    assert row["status"] == "contacted" and row["price"] == 5500


def test_suspicious_low_price(cfg):
    a = analyze(full_listing(price=2300), cfg)
    assert a.verdict == "reject" and any("לא סביר" in r for r in a.reasons)
    a = analyze(full_listing(price=3800), cfg)
    assert a.verdict == "check" and any("חשוד" in f for f in a.flags)


def test_unopened_listing_gets_preliminary_verdict(cfg):
    from hunter.yad2 import listing_from_marker
    cheap = listing_from_marker(dict(MARKER, price=1999))
    assert analyze(cheap, cfg).verdict == "reject"


ROOM_AD = ("מתפנה חדר גדול ומרווח בדירת 3 חדרי שינה וסלון ברמת גן *חדר גדול עם ארון קיר ענק, החדר מגיע ללא ריהוט "
           "נוסף. * דירה מאובזרת לחלוטין. * בדירה נשארים שותפה ושותף. כניסה: תחילת נובמבר. שכר דירה: 2200 לחודש.")


@pytest.mark.parametrize("text", [
    ROOM_AD,
    "בדירה נשארים שותפה ושותף",
    "מחליפים שותפה בדירה מקסימה בגבעתיים",
    "חדר להשכרה בבורוכוב",
    "חדר מרוהט בדירת 4 חדרים, בדירה גרים עוד 2",
    "דרושה שותפה לדירה ברמת גן",
])
def test_roommate_ads_detected(text):
    assert keyword_signals(text)["roommate_ad"] is True


@pytest.mark.parametrize("text", [
    "דירת 4 חדרים מתאימה לשותפים, מטבח גדול",
    "מתאים לדירת שותפים או למשפחה",
    "3 חדרי שינה וסלון, חדר רחצה משופץ",
    "דירה מקסימה, חדר שינה גדול עם ארון קיר",
])
def test_whole_apartment_not_roommate(text):
    assert keyword_signals(text)["roommate_ad"] is False


def test_room_ad_rejected(cfg):
    meta = dict(ITEM["metaData"], description=ROOM_AD)
    a = analyze(full_listing(metaData=meta, price=4200), cfg)
    assert a.verdict == "reject" and any("שותפים" in r for r in a.reasons)

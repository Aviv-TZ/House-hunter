"""Run the in-page extraction scripts on a synthetic page shaped like Facebook's markup."""
import os

import pytest

from hunter.facebook import GROUP_POSTS_JS, JOINED_GROUPS_JS, MARKET_CARDS_JS, clean_post_text, post_id

PAGE = """
<html><body><div role="feed">
  <div role="article">
    <a href="https://www.facebook.com/groups/123456/posts/987654321/?__cft__=x">2 שע'</a>
    <div>להשכרה דירת 3 חדרים בנגבה, 6800 ש"ח, קומה 2 עם מעלית</div>
    <img src="https://scontent.xx.fbcdn.net/v/abc.jpg">
    <div role="article"><div>תגובה: מעוניין! זה עדיין רלוונטי?</div></div>
  </div>
  <div role="article">
    <a href="#">אתמול</a><div>פוסט בלי קישור קבוע אבל עם מספיק טקסט כדי להיחשב פוסט אמיתי</div>
  </div>
</div>
<a href="https://www.facebook.com/marketplace/item/111222333/?ref=x"><img src="https://scontent/m.jpg">
  <span>‏6,900 ‏₪</span><span>דירה 3 חדרים</span><span>רמת גן</span></a>
<a href="https://www.facebook.com/marketplace/item/111222333/">dup card</a>
<a href="https://www.facebook.com/groups/ramatganrent/"><div>דירות להשכרה ברמת גן</div><div>פעילות אחרונה</div></a>
<a href="https://www.facebook.com/groups/feed/">feed</a>
<a href="https://www.facebook.com/groups/555/?ref=bookmarks">חתולים מאמצים</a>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        pw = sync_api.sync_playwright().start()
        exe = os.environ.get("PW_CHROMIUM_PATH")  # optional: use a pre-installed chromium
        browser = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
    except Exception as e:  # no browser installed
        pytest.skip(f"no browser: {e}")
    p = browser.new_page()
    p.set_content(PAGE)
    yield p
    browser.close()
    pw.stop()


def test_group_posts(page):
    posts = page.evaluate(GROUP_POSTS_JS)
    assert len(posts) == 2  # nested comment article skipped
    first = posts[0]
    assert "/groups/123456/posts/987654321" in first["link"]
    assert first["img"].startswith("https://scontent")
    assert post_id(first["link"], "123456", first["text"]) == "987654321"
    assert posts[1]["link"] is None
    assert post_id(None, "123456", posts[1]["text"]).startswith("h")


def test_marketplace_cards(page):
    cards = page.evaluate(MARKET_CARDS_JS)
    assert [c["id"] for c in cards] == ["111222333"]
    assert "6,900" in cards[0]["text"]


def test_joined_groups(page):
    groups = {g["id"]: g["name"] for g in page.evaluate(JOINED_GROUPS_JS)}
    assert groups == {"ramatganrent": "דירות להשכרה ברמת גן", "555": "חתולים מאמצים"}


def test_clean_post_text():
    assert clean_post_text("טקסט חשוב מאוד\nלייק\n12\nתגובה\n3 תגובות\nשיתוף") == "טקסט חשוב מאוד"

"""Helpers for first-time setup (used by the Windows .bat files)."""
from __future__ import annotations

import os
import re

import requests

from .config import ROOT

ENV_PATH = ROOT / ".env"


def set_env_value(key: str, value: str) -> None:
    """Write KEY=value into .env (replace the line if it exists)."""
    text = ENV_PATH.read_text(encoding="utf-8-sig") if ENV_PATH.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{re.escape(key)}=.*$", text, flags=re.M):
        text = re.sub(rf"^{re.escape(key)}=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    os.environ[key] = value


def setup_telegram() -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("\n[!] TELEGRAM_BOT_TOKEN is empty. Open 2-settings.bat, paste the token from BotFather, save, "
              "and run this again.")
        return False
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
    except requests.RequestException as e:
        print(f"\n[!] Could not reach Telegram: {e}")
        return False
    if not r.get("ok"):
        print("\n[!] Telegram says the token is wrong. Copy it again from BotFather (the whole line).")
        return False
    chats = [u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u]
    if not chats:
        print("\n[!] No message found. Open your new bot in Telegram, press START / send it any message "
              "(for example: hi), then run this again.")
        return False
    set_env_value("TELEGRAM_CHAT_ID", str(chats[-1]))
    from .notify import Notifier

    ok = Notifier().send("✅ House Hunter מחובר! מכאן יגיעו התראות על דירות חדשות.")
    print("\n[OK] Telegram connected - check your phone for a test message." if ok else
          "\n[!] Saved the chat id, but the test message failed.")
    return ok


def check_anthropic() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("[i] No Claude key - the agent will use simple keyword rules (Facebook posts need Claude).")
        return
    try:
        import anthropic

        anthropic.Anthropic().models.list(limit=1)
        print("[OK] Claude key works.")
    except Exception as e:  # noqa: BLE001 - this is a friendly setup check
        print(f"[!] Claude key problem: {type(e).__name__}. Check the key and that you added credit "
              "in console.anthropic.com > Billing.")

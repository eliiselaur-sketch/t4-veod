"""
Varuvariant: salvesta sisselogitud sessioon faili auth.json.

Kasuta seda AINULT siis, kui automaatne parooliga sisselogimine ei tööta
(nt Waybiller küsib SMS-koodi või sessioon aegub). Käivita oma arvutis:

    pip install playwright
    python -m playwright install chromium
    python scraper/save_session.py

Avaneb brauser kõigi nelja lehega. Logi igale poole käsitsi sisse,
tule tagasi siia aknasse ja vajuta Enter. Skript salvestab auth.json-i
ja prindib teksti, mille kopeerid GitHubi saladusse AUTH_STATE_B64.

NB! auth.json annab ligipääsu sinu kontodele – ära lisa seda repositooriumisse
ega saada kellelegi. .gitignore hoiab selle Gitist eemal.
"""

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

URLS = [
    "https://maa.verston.ee/gross/",
    "https://marina.almic.ee/gross/",
    "https://kiiusoon.devplesk.almic.ee/gross/",
    "https://app.waybiller.com/waybills/active",
]
OUT = Path(__file__).resolve().parent.parent / "auth.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(locale="et-EE", timezone_id="Europe/Tallinn")
    for url in URLS:
        ctx.new_page().goto(url)
    input("\nLogi kõigil neljal sakil sisse ja vajuta siis siin Enter... ")
    ctx.storage_state(path=str(OUT))
    browser.close()

b64 = base64.b64encode(OUT.read_bytes()).decode()
print(f"\nSalvestatud: {OUT}")
print("\nKopeeri kogu allolev rida GitHubi saladusse AUTH_STATE_B64:\n")
print(b64)

"""
T4 karjääride veod – andmete toomine.

Logib sisse QuarryMoni (Almic) lehtedele ja Waybillerisse, loeb tänase päeva
kaalumised / veolehed ning lisab uue seisu faili docs/data.json.

Paroolid ja sessioon tulevad AINULT keskkonnamuutujatest (GitHub Secrets):
  Iga QuarryMoni lehe kohta kas PIN-kood VÕI e-post + parool:
  TIKA_PIN      või TIKA_EMAIL + TIKA_PASSWORD          (maa.verston.ee)
  VIRUKULA_PIN  või VIRUKULA_EMAIL + VIRUKULA_PASSWORD  (marina.almic.ee)
  JAAKNA_PIN    või JAAKNA_EMAIL + JAAKNA_PASSWORD      (kiiusoon.devplesk.almic.ee)
  QM_EMAIL, QM_PASSWORD                – valikuline ühine konto, kui lehel oma saladust pole
  WAYBILLER_EMAIL, WAYBILLER_PASSWORD  – Waybilleri konto
  AUTH_STATE_FILE                      – valikuline: salvestatud sessioonifail (auth.json)

Käivitus:  python scraper/fetch.py            (töötab ainult tööajal, vt ACTIVE_*)
           python scraper/fetch.py --force    (töötab alati)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

TZ = ZoneInfo("Europe/Tallinn")
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "docs" / "data.json"

# Tööaeg (Tallinna aeg). Väljaspool seda skript ei tee midagi, et ei tekiks tühje seise.
ACTIVE_DAYS = {0, 1, 2, 3, 4}          # E–R
ACTIVE_FROM_HOUR = int(os.environ.get("ACTIVE_FROM_HOUR", "8"))
ACTIVE_TO_HOUR = int(os.environ.get("ACTIVE_TO_HOUR", "19"))   # viimane käivitus kell 19:00
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "120"))             # kui kaua ajalugu hoida

# --- Allikad -----------------------------------------------------------------

QUARRYMON = [
    # karjäär, aadress, env-eesliide, clientId, contractId
    {"karjaar": "Tika 2", "url": "https://maa.verston.ee", "env": "TIKA", "clientId": 421, "contractId": 257},
    {"karjaar": "Viruküla", "url": "https://marina.almic.ee", "env": "VIRUKULA", "clientId": None, "contractId": 19},
    {"karjaar": "Jaakna II", "url": "https://kiiusoon.devplesk.almic.ee", "env": "JAAKNA", "clientId": None, "contractId": 517},
]

WAYBILLER_URL = "https://app.waybiller.com"
WAYBILLER_DESTINATIONS = [
    "T4 Haimre-Konuvere",
    "Verston Eesti OÜ T4_Haimre-Konuvere",
    "Konuvere 327625ET",
]
# Waybilleri lähtekoha nimi -> karjäär (esimene sobiv reegel võidab)
ORIGIN_RULES = [
    (re.compile(r"asfalt", re.I), None),                  # asfaldiveod välja
    (re.compile(r"tika", re.I), "Tika 2"),
    (re.compile(r"jaakna", re.I), "Jaakna II"),
    (re.compile(r"viruk", re.I), "Viruküla"),
    (re.compile(r"orava", re.I), "Orava"),
    (re.compile(r"harku", re.I), "Harku"),
    (re.compile(r"ruukivi|hoidla tee 15", re.I), "Ruukivi"),
]
ALL_QUARRIES = ["Tika 2", "Viruküla", "Jaakna II", "Orava", "Harku", "Ruukivi"]
WAYBILLER_ONLY = {"Orava", "Harku", "Ruukivi"}


def log(msg: str) -> None:
    print(f"[{datetime.now(TZ):%H:%M:%S}] {msg}", flush=True)


def env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


# --- Sisselogimine -----------------------------------------------------------

def fill_login(page: Page, email: str, password: str) -> None:
    """Täidab tavalise e-posti + parooli vormi ja saadab selle ära."""
    user = page.locator(
        "input[type=email], input[name=email], input[name=username], input[name=login], "
        "input[autocomplete=username], input[type=text]"
    ).first
    pwd = page.locator("input[type=password]").first
    user.wait_for(state="visible", timeout=20_000)
    user.fill(email)
    pwd.fill(password)
    submit = page.locator("button[type=submit], input[type=submit], button:has-text('Logi'), button:has-text('Log in'), button:has-text('Sign in')").first
    if submit.count() and submit.is_visible():
        submit.click()
    else:
        pwd.press("Enter")
    page.wait_for_load_state("networkidle", timeout=45_000)


def is_login_page(page: Page) -> bool:
    return "login" in page.url.lower() or page.locator("input[type=password]").count() > 0


# --- QuarryMon ---------------------------------------------------------------

QM_JS = """
async ({date, clientId, contractId}) => {
  if (typeof imperial === 'undefined') return {error: 'imperial puudub (pole sisse logitud?)'};
  return await new Promise((res) => {
    const t = setTimeout(() => res({error: 'aegus'}), 40000);
    imperial.app.doRequest('/WeightController/doLoadGrossWeightsAsync/', {
      data: {tableFilterType: null, filterDirection: 'DESC', tableDate: date, clientId, transportId: null,
             quarryId: null, fractionId: null, contractId, faulty: false, archived: false, normal: true,
             buildingObject: false, page: 1, rowAmount: 1000, search: '', extraOption: null},
      onSuccess: (r) => {
        clearTimeout(t);
        const d = document.createElement('div');
        d.innerHTML = r.data.content;
        const rows = [...d.querySelectorAll('.Rtable-row')].map((x) => x.innerText.replace(/\\s+/g, ' '));
        const plates = new Set();
        let net = 0;
        rows.forEach((x) => {
          const p = x.match(/Sõiduk (\\S+)/); if (p) plates.add(p[1]);
          const n = x.match(/Neto ([\\d.]+)/); if (n) net += +n[1];
        });
        res({autosid: plates.size, koormaid: rows.length, tonnid: Math.round(net * 10) / 10});
      },
    });
  });
}
"""


def qm_login(page: Page, pin: str | None, email: str | None, password: str | None) -> None:
    """QuarryMoni sisselogimine: PIN-koodiga (numbriklahvid) või e-posti ja parooliga."""
    toggle = page.locator(".tabs-nav.login-pin label").first
    if pin:
        pad = page.locator(".login-by-pin .pad").first
        if not pad.is_visible() and toggle.count():
            toggle.click()
        pad.wait_for(state="visible", timeout=20_000)
        for digit in pin:
            page.locator(f'.login-by-pin .pad[data-num="{digit}"]').first.click(timeout=10_000)
            page.wait_for_timeout(200)
        # Mõnel lehel logib täis PIN ise sisse ja leht vahetub – siis nuppu enam pole.
        page.wait_for_timeout(2_000)
        btn = page.locator('.login-by-pin [data-action="pinLogin"]').first
        try:
            if "login" in page.url.lower() and btn.is_visible():
                btn.click(timeout=5_000)
        except Exception:  # noqa: BLE001
            pass
    else:
        user = page.locator("input[name=email]").first
        if not user.is_visible() and toggle.count():
            toggle.click()
        user.wait_for(state="visible", timeout=20_000)
        user.fill(email)
        page.locator("input[name=password]").first.fill(password)
        btn = page.locator('[data-action="logIn"]').first
        if btn.count() and btn.is_visible():
            btn.click()
        else:
            page.locator("input[name=password]").first.press("Enter")
    # oota, kuni sisselogimise järel leht vahetub
    try:
        page.wait_for_url(lambda u: "login" not in u.lower(), timeout=20_000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_load_state("networkidle", timeout=45_000)


def goto_retry(page: Page, url: str) -> None:
    for attempt in range(3):
        try:
            page.goto(url, wait_until="networkidle", timeout=60_000)
            return
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            log(f"  uus katse ({str(e).splitlines()[0][:80]})")
            page.wait_for_timeout(3_000)


def fetch_quarrymon(page: Page, site: dict, today: datetime) -> dict | None:
    pin = env(f"{site['env']}_PIN")
    email = env(f"{site['env']}_EMAIL") or env("QM_EMAIL")
    password = env(f"{site['env']}_PASSWORD") or env("QM_PASSWORD")
    try:
        goto_retry(page, site["url"] + "/gross/")
        if is_login_page(page):
            if not (pin or (email and password)):
                log(f"{site['karjaar']}: sessioon aegunud ja PIN/parool puudub")
                return None
            log(f"{site['karjaar']}: login sisse ({'PIN' if pin else 'e-post + parool'})")
            qm_login(page, pin, email, password)
            goto_retry(page, site["url"] + "/gross/")
            if is_login_page(page):
                log(f"{site['karjaar']}: sisselogimine ebaõnnestus")
                return None
        res = page.evaluate(QM_JS, {"date": today.strftime("%d.%m.%Y"),
                                    "clientId": site["clientId"], "contractId": site["contractId"]})
        if not res or "error" in res:
            log(f"{site['karjaar']}: viga {res}")
            return None
        log(f"{site['karjaar']}: {res}")
        return res
    except PWTimeout as e:
        log(f"{site['karjaar']}: aegus ({e})")
    except Exception as e:  # noqa: BLE001
        log(f"{site['karjaar']}: viga ({e})")
    return None


# --- Waybiller ---------------------------------------------------------------

WB_JS = """
async ({start, end, dests}) => {
  let all = [], off = 0;
  for (let i = 0; i < 50; i++) {
    const r = await fetch(`/api/v1/waybills/?limit=200&offset=${off}&start_date=${start}&end_date=${end}&status=0,1,2,3`,
                          {credentials: 'include', headers: {'Accept': 'application/json'}});
    if (!r.ok) return {error: 'HTTP ' + r.status};
    const j = await r.json();
    const res = j.results || j;
    all = all.concat(res);
    if (!j.next || res.length < 200) break;
    off += 200;
  }
  return all
    .filter((w) => (w.all_waybill_destinations || []).some((d) => dests.includes(d.destination_name)))
    .map((w) => ({
      origin: w.origin_name || '',
      plate: w.truck_reg_number || '',
      company: w.driver_company_name || '?',
      amount: (w.rows || []).reduce((s, r) => s + (+(r.accepted_amount ?? r.dispatched_amount) || 0), 0),
    }));
}
"""


def map_origin(name: str) -> str | None:
    for rx, karjaar in ORIGIN_RULES:
        if rx.search(name):
            return karjaar
    return None


def fetch_waybiller(page: Page, today: datetime) -> list[dict] | None:
    email, password = env("WAYBILLER_EMAIL"), env("WAYBILLER_PASSWORD")
    try:
        page.goto(WAYBILLER_URL + "/waybills/active", wait_until="networkidle", timeout=60_000)
        if is_login_page(page):
            if not (email and password):
                log("Waybiller: sessioon aegunud ja parool puudub")
                return None
            log("Waybiller: login sisse")
            fill_login(page, email, password)
            page.goto(WAYBILLER_URL + "/waybills/active", wait_until="networkidle", timeout=60_000)
            if is_login_page(page):
                log("Waybiller: sisselogimine ebaõnnestus (kas küsib lisakinnitust? kasuta auth.json-i)")
                return None
        day_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (day_start + timedelta(days=1) - timedelta(seconds=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = page.evaluate(WB_JS, {"start": start, "end": end, "dests": WAYBILLER_DESTINATIONS})
        if isinstance(res, dict) and "error" in res:
            log(f"Waybiller: viga {res}")
            return None
        log(f"Waybiller: {len(res)} T4 veolehte")
        return res
    except PWTimeout as e:
        log(f"Waybiller: aegus ({e})")
    except Exception as e:  # noqa: BLE001
        log(f"Waybiller: viga ({e})")
    return None


def summarize_waybills(waybills: list[dict]):
    per_q: dict[str, dict] = {}
    per_f: dict[tuple, dict] = {}
    for w in waybills:
        q = map_origin(w["origin"])
        if not q:
            continue
        a = per_q.setdefault(q, {"plates": set(), "n": 0, "t": 0.0})
        a["plates"].add(w["plate"]); a["n"] += 1; a["t"] += w["amount"]
        f = per_f.setdefault((q, w["company"]), {"plates": set(), "n": 0})
        f["plates"].add(w["plate"]); f["n"] += 1
    q_out = {q: {"autosid": len(v["plates"]), "koormaid": v["n"], "tonnid": round(v["t"], 1)} for q, v in per_q.items()}
    f_out = [{"karjaar": q, "vedaja": c, "autosid": len(v["plates"]), "koormaid": v["n"]} for (q, c), v in per_f.items()]
    return q_out, f_out


# --- Põhiloogika -------------------------------------------------------------

def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"rows": [], "firmad": []}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="käivita ka väljaspool tööaega")
    args = ap.parse_args()

    now = datetime.now(TZ)
    if not args.force and not (now.weekday() in ACTIVE_DAYS and ACTIVE_FROM_HOUR <= now.hour <= ACTIVE_TO_HOUR):
        log(f"Väljaspool tööaega ({now:%a %H:%M}), ei tee midagi.")
        return 0

    auth_file = env("AUTH_STATE_FILE")
    use_auth = auth_file and Path(auth_file).exists() and Path(auth_file).stat().st_size > 10

    qm: dict[str, dict | None] = {}
    waybills = None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            storage_state=auth_file if use_auth else None,
            locale="et-EE", timezone_id="Europe/Tallinn",
        )
        page = ctx.new_page()
        for site in QUARRYMON:
            qm[site["karjaar"]] = fetch_quarrymon(page, site, now)
        waybills = fetch_waybiller(page, now)
        browser.close()

    wb_q, wb_f = summarize_waybills(waybills) if waybills is not None else ({}, [])

    kell = now.strftime("%H:%M")
    paev = now.strftime("%Y-%m-%d")
    aeg = f"{paev} {kell}"
    rows, allikad = [], {}
    for q in ALL_QUARRIES:
        if q in WAYBILLER_ONLY:
            if waybills is None:
                continue
            v, src = wb_q.get(q, {"autosid": 0, "koormaid": 0, "tonnid": 0}), "Waybiller"
        else:
            v, src = qm.get(q), "QuarryMon"
            if v is None and waybills is not None:      # QuarryMon ei vastanud -> Waybilleri varuandmed
                v, src = wb_q.get(q, {"autosid": 0, "koormaid": 0, "tonnid": 0}), "Waybiller (varu)"
            if v is None:
                continue
        rows.append({"kuupaev": paev, "kell": kell, "aeg": aeg, "karjaar": q, "allikas": src,
                     "autosid": v["autosid"], "koormaid": v["koormaid"], "tonnid": v["tonnid"]})
        allikad[q] = src

    if not rows:
        log("Ühestki allikast andmeid ei tulnud – data.json jääb muutmata.")
        return 1

    firmad = [{"kuupaev": paev, "kell": kell, "aeg": aeg, **f} for f in wb_f]

    data = load_data()
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    data["rows"] = [r for r in data.get("rows", []) if r["aeg"] != aeg and r["kuupaev"] >= cutoff] + rows
    data["firmad"] = [r for r in data.get("firmad", []) if r["aeg"] != aeg and r["kuupaev"] >= cutoff] + firmad
    data["uuendatud"] = now.isoformat(timespec="seconds")
    data["viimane_kontroll"] = {
        "aeg": aeg,
        "allikad": allikad,
        "puudu": [q for q in ALL_QUARRIES if q not in allikad],
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Salvestatud {len(rows)} karjääri seis ({aeg}). Puudu: {data['viimane_kontroll']['puudu'] or 'ei midagi'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

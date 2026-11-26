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
  DATA_PIN                             – lehe PIN/parool; andmed salvestatakse krüpteeritult
                                         faili docs/data.enc.json (AES-256-GCM)

Käivitus:  python scraper/fetch.py                       (töötab ainult tööajal, vt ACTIVE_*)
           python scraper/fetch.py --force               (töötab alati)
           python scraper/fetch.py --backfill 2026-10-01 (materjalide ajalugu sellest päevast eilseni)
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
DATA_FILE = ROOT / "docs" / "data.json"          # vana, krüpteerimata (ainult üleminekuks)
ENC_FILE = ROOT / "docs" / "data.enc.json"       # krüpteeritud andmed (PIN-iga)
PBKDF2_ITER = 600_000

# Tööaeg (Tallinna aeg). Väljaspool seda skript ei tee midagi, et ei tekiks tühje seise.
ACTIVE_DAYS = {0, 1, 2, 3, 4}          # E–R
ACTIVE_FROM = os.environ.get("ACTIVE_FROM", "06:30")                  # esimene käivitus (HH:MM)
ACTIVE_TO = os.environ.get("ACTIVE_TO", "19:00")                      # viimane käivitus (HH:MM)
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
        const mat = {};
        const events = [];
        let net = 0;
        rows.forEach((x) => {
          const p = x.match(/Sõiduk (\\S+)/); if (p) plates.add(p[1]);
          const n = x.match(/Neto ([\\d.]+)/); const v = n ? +n[1] : 0; net += v;
          const f = x.match(/Fraktsioon (.*?) (?:Transport|Bruto|Klient|Karjäär) /);
          const name = f ? f[1].trim() : 'Määramata';
          const m = mat[name] || (mat[name] = {koormaid: 0, tonnid: 0});
          m.koormaid += 1; m.tonnid += v;
          const tm = x.match(/Date \\d\\d\\.\\d\\d\\.\\d\\d (\\d\\d:\\d\\d)/);
          if (p) events.push({nr: p[1], kell: tm ? tm[1] : null, materjal: name, t: v});
        });
        res({autosid: plates.size, koormaid: rows.length, tonnid: Math.round(net * 10) / 10, materjalid: mat, events});
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


def fetch_quarrymon(page: Page, site: dict, days: list[datetime]) -> dict:
    """Logib lehele sisse ja tagastab {kuupäev: tulemus} iga küsitud päeva kohta (puuduv päev = viga)."""
    pin = env(f"{site['env']}_PIN")
    email = env(f"{site['env']}_EMAIL") or env("QM_EMAIL")
    password = env(f"{site['env']}_PASSWORD") or env("QM_PASSWORD")
    try:
        goto_retry(page, site["url"] + "/gross/")
        if is_login_page(page):
            if not (pin or (email and password)):
                log(f"{site['karjaar']}: sessioon aegunud ja PIN/parool puudub")
                return {}
            log(f"{site['karjaar']}: login sisse ({'PIN' if pin else 'e-post + parool'})")
            qm_login(page, pin, email, password)
            goto_retry(page, site["url"] + "/gross/")
            if is_login_page(page):
                log(f"{site['karjaar']}: sisselogimine ebaõnnestus")
                return {}
        out = {}
        for day in days:
            res = page.evaluate(QM_JS, {"date": day.strftime("%d.%m.%Y"),
                                        "clientId": site["clientId"], "contractId": site["contractId"]})
            if not res or "error" in res:
                log(f"{site['karjaar']} {day:%d.%m}: viga {res}")
                continue
            out[day.strftime("%Y-%m-%d")] = res
            log(f"{site['karjaar']} {day:%d.%m}: {res['autosid']} autot, {res['koormaid']} koormat, {res['tonnid']} t")
        return out
    except PWTimeout as e:
        log(f"{site['karjaar']}: aegus ({e})")
    except Exception as e:  # noqa: BLE001
        log(f"{site['karjaar']}: viga ({e})")
    return {}


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
      ts: w.dispatcher_timestamp || null,
      company: w.driver_company_name || '?',
      amount: (w.rows || []).reduce((s, r) => s + (+(r.accepted_amount ?? r.dispatched_amount) || 0), 0),
      items: (w.rows || []).map((r) => ({
        m: r.accepted_assortment_displayed_name || r.assortment_displayed_name || 'Määramata',
        a: +(r.accepted_amount ?? r.dispatched_amount) || 0,
      })),
    }));
}
"""


ASPHALT_RX = re.compile(r"asfalt|^\s*(AC|SMA|AB|PA)\s*\d", re.I)


def is_asphalt(material: str) -> bool:
    """Asfaltsegud (nt „AC 16 surf", „SMA 11") jäetakse kõikjalt välja."""
    return bool(ASPHALT_RX.search(material or ""))


def drop_asphalt(waybills: list[dict]) -> list[dict]:
    out = []
    for w in waybills:
        items = [it for it in (w.get("items") or []) if not is_asphalt(it.get("m", ""))]
        if w.get("items") and not items:
            continue                      # terve veoleht oli asfalt
        if w.get("items"):
            w = dict(w, items=items, amount=sum(it["a"] for it in items))
        out.append(w)
    return out


def map_origin(name: str) -> str | None:
    for rx, karjaar in ORIGIN_RULES:
        if rx.search(name):
            return karjaar
    return None


def fetch_waybiller(page: Page, days: list[datetime]) -> dict:
    """Tagastab {kuupäev: [veoleht, ...]} iga küsitud päeva kohta."""
    email, password = env("WAYBILLER_EMAIL"), env("WAYBILLER_PASSWORD")
    try:
        page.goto(WAYBILLER_URL + "/waybills/active", wait_until="networkidle", timeout=60_000)
        if is_login_page(page):
            if not (email and password):
                log("Waybiller: sessioon aegunud ja parool puudub")
                return {}
            log("Waybiller: login sisse")
            fill_login(page, email, password)
            page.goto(WAYBILLER_URL + "/waybills/active", wait_until="networkidle", timeout=60_000)
            if is_login_page(page):
                log("Waybiller: sisselogimine ebaõnnestus (kas küsib lisakinnitust? kasuta auth.json-i)")
                return {}
        out = {}
        for day in days:
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            start = day_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            end = (day_start + timedelta(days=1) - timedelta(seconds=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            res = page.evaluate(WB_JS, {"start": start, "end": end, "dests": WAYBILLER_DESTINATIONS})
            if isinstance(res, dict) and "error" in res:
                log(f"Waybiller {day:%d.%m}: viga {res}")
                continue
            res = drop_asphalt(res)
            out[day.strftime("%Y-%m-%d")] = res
            log(f"Waybiller {day:%d.%m}: {len(res)} T4 veolehte (asfaldita)")
        return out
    except PWTimeout as e:
        log(f"Waybiller: aegus ({e})")
    except Exception as e:  # noqa: BLE001
        log(f"Waybiller: viga ({e})")
    return {}


KAART_JS = r"""
async ({start, end, dests}) => {
  const H = {credentials: 'include', headers: {'Accept': 'application/json'}};
  let all = [], off = 0;
  for (let i = 0; i < 50; i++) {
    const r = await fetch(`/api/v1/waybills/?limit=200&offset=${off}&start_date=${start}&end_date=${end}&status=0,1,2`, H);
    if (!r.ok) return {error: 'HTTP ' + r.status};
    const j = await r.json(); const res = j.results || j;
    all = all.concat(res);
    if (!j.next || res.length < 200) break;
    off += 200;
  }
  const t4 = all.filter((w) => (w.all_waybill_destinations || []).some((d) => dests.includes(d.destination_name)));
  const latest = {}, count = {}, byPlate = {};
  for (const w of t4) {
    const p = w.truck_reg_number || '?';
    count[p] = (count[p] || 0) + 1;
    (byPlate[p] = byPlate[p] || []).push(w);
    if (!latest[p] || w.dispatcher_timestamp > latest[p].dispatcher_timestamp) latest[p] = w;
  }
  Object.values(byPlate).forEach((l) => l.sort((a, b) => String(b.dispatcher_timestamp).localeCompare(String(a.dispatcher_timestamp))));
  const coords = (s) => { if (!s) return null; const m = String(s).split(/[ ,]+/).map(Number); return m.length >= 2 && !isNaN(m[0]) ? m.slice(0, 2) : null; };
  const one = async (w) => {
    const o = {id: w.id, nr: w.truck_reg_number || '?', status: w.status, gps: !!w.truck_gps_service_integration,
               vedaja: w.driver_company_name || '', origin: w.origin_name || '', laaditud: w.dispatcher_timestamp,
               koormaid: count[w.truck_reg_number || '?'],
               materjalid: (w.rows || []).map((r) => r.accepted_assortment_displayed_name || r.assortment_displayed_name || '')};
    try {
      const d = await (await fetch(`/api/v1/waybills/${w.id}/`, H)).json();
      o.juht = d.driver_name || ''; o.telefon = d.driver_phone || '';
      o.kohal = (d.final_waybill_destination || {}).destination_timestamp || d.accepted_timestamp || null;
      o.alustas = d.driver_timestamp || null;
      o.origin_xy = coords(d.origin_coordinates);
      o.dest_xy = coords((d.final_waybill_destination || {}).destination_coords);
    } catch (e) { o.viga = String(e); }
    // GPS-i lipp Waybilleri nimekirjas pole usaldusväärne: küsime marsruuti alati.
    // Kui viimasel lehel punkte pole, proovime sama auto eelmist tänast lehte.
    for (const ww of (byPlate[o.nr] || [w]).slice(0, 2)) {
      try {
        const r = await (await fetch(`/api/v1/waybills/${ww.id}/route/?since=1`, H)).json();
        const R = r.results || [];
        if (R.length) { const q = R[R.length - 1]; o.pos = {x: q.x, y: q.y, t: q.t, sama: ww.id === w.id}; break; }
      } catch (e) { /* asukohta pole */ }
    }
    return o;
  };
  const list = Object.values(latest), out = [];
  for (let i = 0; i < list.length; i += 6) out.push(...await Promise.all(list.slice(i, i + 6).map(one)));
  return out;
}
"""


def _latlon(a, b):
    """Waybilleri koordinaatide järjekord pole alati sama; Eestis on laius ~57–60, pikkus ~21–29."""
    if a is None or b is None:
        return None
    a, b = float(a), float(b)
    return [a, b] if a > b else [b, a]


def _km(p, q):
    import math
    if not p or not q:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(h)), 1)


def fetch_kaart(page: Page, now: datetime) -> dict | None:
    """Tänaste T4 autode viimane veoleht: juht, telefon, olek ja (GPS-iga autodel) asukoht."""
    try:
        if not page.url.startswith(WAYBILLER_URL) or is_login_page(page):
            return None
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (day_start + timedelta(days=1) - timedelta(seconds=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = page.evaluate(KAART_JS, {"start": start, "end": end, "dests": WAYBILLER_DESTINATIONS})
        if not isinstance(res, list):
            log(f"Kaart: viga {res}")
            return None
    except Exception as e:  # noqa: BLE001
        log(f"Kaart: viga ({e})")
        return None
    dest = None
    autod = []
    for o in res:
        if any(is_asphalt(m) for m in o.get("materjalid") or []):
            continue
        d = o.get("dest_xy")
        if d and not dest:
            dest = _latlon(*d)
        pos = None
        if o.get("pos"):
            pos = _latlon(o["pos"]["x"], o["pos"]["y"]) + [o["pos"]["t"]]
        orig = _latlon(*o["origin_xy"]) if o.get("origin_xy") else None
        autod.append({
            "id": o["id"], "nr": o["nr"], "status": o["status"], "gps": bool(o.get("pos")),
            "vedaja": o.get("vedaja", ""), "juht": o.get("juht", ""), "telefon": o.get("telefon", ""),
            "karjaar": map_origin(o.get("origin", "")) or o.get("origin", ""),
            "karjaar_xy": orig, "laaditud": o.get("laaditud"), "alustas": o.get("alustas"), "kohal": o.get("kohal"),
            "koormaid": o.get("koormaid", 0), "pos": pos,
        })
    for a in autod:
        a["kaugus_km"] = _km(a["pos"], dest) if a["pos"] else None
    log(f"Kaart: {len(autod)} autot, GPS-asukoht {sum(1 for a in autod if a['pos'])} autol.")
    return {"aeg": now.isoformat(timespec="seconds"), "sihtkoht": dest, "autod": autod}


def summarize_waybills(waybills: list[dict]):
    per_q: dict[str, dict] = {}
    per_f: dict[tuple, dict] = {}
    per_m: dict[str, dict] = {}
    for w in waybills:
        q = map_origin(w["origin"])
        if not q:
            continue
        a = per_q.setdefault(q, {"plates": set(), "n": 0, "t": 0.0})
        a["plates"].add(w["plate"]); a["n"] += 1; a["t"] += w["amount"]
        f = per_f.setdefault((q, w["company"]), {"plates": set(), "n": 0})
        f["plates"].add(w["plate"]); f["n"] += 1
        for it in w.get("items") or []:
            m = per_m.setdefault(q, {}).setdefault(it["m"], {"koormaid": 0, "tonnid": 0.0})
            m["koormaid"] += 1; m["tonnid"] += it["a"]
    q_out = {q: {"autosid": len(v["plates"]), "koormaid": v["n"], "tonnid": round(v["t"], 1)} for q, v in per_q.items()}
    f_out = [{"karjaar": q, "vedaja": c, "autosid": len(v["plates"]), "koormaid": v["n"]} for (q, c), v in per_f.items()]
    return q_out, f_out, per_m


def clean_material(name: str) -> str:
    """Ühtlustab materjali nime: alakriipsud tühikuteks, topelttühikud välja."""
    return re.sub(r"\s+", " ", (name or "Määramata").replace("_", " ")).strip()


def day_materials(day: str, qm_day: dict, wb_list: list | None) -> list[dict]:
    """Ühe päeva materjalid karjääride kaupa. QuarryMoni karjäärid QuarryMonist, muidu Waybillerist."""
    wb_m = summarize_waybills(wb_list)[2] if wb_list is not None else {}
    out = []
    for q in ALL_QUARRIES:
        if q not in WAYBILLER_ONLY and q in qm_day:
            mats, src = qm_day[q].get("materjalid") or {}, "QuarryMon"
        elif wb_list is not None:
            mats, src = wb_m.get(q, {}), ("Waybiller" if q in WAYBILLER_ONLY else "Waybiller (varu)")
        else:
            continue
        merged: dict[str, dict] = {}
        for name, v in mats.items():
            if is_asphalt(name):
                continue
            m = merged.setdefault(clean_material(name), {"koormaid": 0, "tonnid": 0.0})
            m["koormaid"] += v["koormaid"]; m["tonnid"] += v["tonnid"]
        for name, v in merged.items():
            if v["koormaid"]:
                out.append({"karjaar": q, "materjal": name, "koormaid": v["koormaid"],
                            "tonnid": round(v["tonnid"], 1), "allikas": src})
    return out


# --- Põhiloogika -------------------------------------------------------------

def _derive_key(pin: str, salt: bytes, iterations: int) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(pin.encode("utf-8"))


def _pin() -> str:
    return os.environ.get("DATA_PIN", "").strip().upper()  # suur- ja väiketähed pole olulised


def load_data() -> dict:
    import base64
    pin = _pin()
    if ENC_FILE.exists():
        if not pin:
            raise SystemExit("docs/data.enc.json on olemas, aga DATA_PIN saladus puudub.")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        env = json.loads(ENC_FILE.read_text(encoding="utf-8"))
        key = _derive_key(pin, base64.b64decode(env["salt"]), int(env["iter"]))
        try:
            raw = AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)
        except Exception:
            raise SystemExit("data.enc.json ei avanenud – DATA_PIN on muutunud? Kustuta docs/data.enc.json, "
                             "siis alustatakse uue PIN-iga tühjalt (või taasta vana PIN).")
        return json.loads(raw.decode("utf-8"))
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"rows": [], "firmad": []}


def save_data(data: dict) -> None:
    """Salvestab andmed. DATA_PIN olemasolul krüpteeritult ja eemaldab avatud data.json-i."""
    import base64
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    pin = _pin()
    if not pin:
        log("HOIATUS: DATA_PIN puudub – andmed salvestatakse krüpteerimata (leht on avalik).")
        DATA_FILE.write_bytes(raw)
        return
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = None
    if ENC_FILE.exists():  # sama sool -> brauser saab salvestatud võtit edasi kasutada
        try:
            env = json.loads(ENC_FILE.read_text(encoding="utf-8"))
            if int(env.get("iter", 0)) == PBKDF2_ITER:
                salt = base64.b64decode(env["salt"])
        except Exception:
            salt = None
    salt = salt or os.urandom(16)
    iv = os.urandom(12)
    key = _derive_key(pin, salt, PBKDF2_ITER)
    ct = AESGCM(key).encrypt(iv, raw, None)
    env = {"v": 1, "alg": "AES-256-GCM/PBKDF2-SHA256", "iter": PBKDF2_ITER,
           "salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
           "ct": base64.b64encode(ct).decode()}
    ENC_FILE.write_text(json.dumps(env, separators=(",", ":")), encoding="utf-8")
    if DATA_FILE.exists():
        DATA_FILE.unlink()
        log("Avatud data.json eemaldatud, edaspidi ainult data.enc.json.")


def wb_events(waybills: list[dict]) -> dict[str, list]:
    """Waybilleri veolehed -> {karjäär: [{nr, kell, vedaja, materjal, t}]}"""
    out: dict[str, list] = {}
    for w in waybills:
        q = map_origin(w["origin"])
        if not q or not w.get("plate"):
            continue
        kell = None
        if w.get("ts"):
            try:
                kell = datetime.fromisoformat(w["ts"].replace("Z", "+00:00")).astimezone(TZ).strftime("%H:%M")
            except ValueError:
                pass
        items = w.get("items") or [{"m": "Määramata", "a": w.get("amount", 0)}]
        out.setdefault(q, []).append({"nr": w["plate"], "kell": kell, "vedaja": w.get("company") or "?",
                                      "materjal": clean_material(items[0]["m"]), "t": w.get("amount", 0)})
    return out


def norm_plate(nr: str) -> str:
    return re.sub(r"[^0-9A-ZÕÄÖÜŠŽ]", "", (nr or "").upper())


def analyse_trucks(now: datetime, qm_today: dict, wb_list: list | None) -> dict:
    """Iga auto tänane käik: millises karjääris, kas veab veel, kas tõsteti ümber."""
    wb_ev = wb_events(wb_list) if wb_list is not None else {}
    carrier = {}
    for evs in wb_ev.values():
        for e in evs:
            carrier[norm_plate(e["nr"])] = e["vedaja"]
    events = []
    for q in ALL_QUARRIES:
        if q not in WAYBILLER_ONLY and q in qm_today:
            src = [dict(e, karjaar=q) for e in (qm_today[q].get("events") or [])]
        else:
            src = [dict(e, karjaar=q) for e in wb_ev.get(q, [])]
        events += [e for e in src if e.get("kell")]
    now_min = now.hour * 60 + now.minute
    to_min = lambda k: int(k[:2]) * 60 + int(k[3:5])  # noqa: E731
    by_plate: dict[str, list] = {}
    for e in events:
        by_plate.setdefault(norm_plate(e["nr"]), []).append(e)
    autod, liikumised = [], []
    for key, evs in by_plate.items():
        evs.sort(key=lambda e: e["kell"])
        mins = [to_min(e["kell"]) for e in evs]
        gaps = sorted(b - a for a, b in zip(mins, mins[1:]) if b > a)
        tsykkel = gaps[len(gaps) // 2] if gaps else None
        piir = min(max(int((tsykkel or 60) * 1.5), 45), 120)   # kaua võib koormate vahe olla, et auto loetakse vedavaks
        viimane = mins[-1]
        staatus = "veab" if now_min - viimane <= piir else "lõpetanud"
        etapid = []
        for e in evs:
            if etapid and etapid[-1]["karjaar"] == e["karjaar"]:
                etapid[-1]["kuni"] = e["kell"]; etapid[-1]["koormaid"] += 1
            else:
                if etapid:
                    liikumised.append({"nr": evs[0]["nr"], "kell": e["kell"], "kust": etapid[-1]["karjaar"],
                                       "kuhu": e["karjaar"], "vedaja": carrier.get(key, "")})
                etapid.append({"karjaar": e["karjaar"], "alates": e["kell"], "kuni": e["kell"], "koormaid": 1})
        autod.append({
            "nr": evs[0]["nr"], "vedaja": carrier.get(key, ""), "karjaar": evs[-1]["karjaar"],
            "staatus": staatus, "koormaid": len(evs), "tonnid": round(sum(e.get("t") or 0 for e in evs), 1),
            "esimene": evs[0]["kell"], "viimane": evs[-1]["kell"], "tsykkel_min": tsykkel, "piir_min": piir,
            "materjal": evs[-1].get("materjal", ""), "etapid": etapid,
        })
    autod.sort(key=lambda a: (a["staatus"] != "veab", a["karjaar"], a["nr"]))
    liikumised.sort(key=lambda m: m["kell"])
    karjaarid = {}
    for q in ALL_QUARRIES:
        kaisid = {norm_plate(a["nr"]) for a in autod if any(s["karjaar"] == q for s in a["etapid"])}
        karjaarid[q] = {"veab": sum(1 for a in autod if a["karjaar"] == q and a["staatus"] == "veab"),
                        "kaisid": len(kaisid)}
    return {"kuupaev": now.strftime("%Y-%m-%d"), "aeg": now.strftime("%Y-%m-%d %H:%M"),
            "unikaalseid": len(autod), "veab": sum(1 for a in autod if a["staatus"] == "veab"),
            "karjaarid": karjaarid, "autod": autod, "liikumised": liikumised}


def thin_rows(rows: list[dict], today: str, key) -> list[dict]:
    """Tänased seisud jäävad kõik alles, varasematest päevadest ainult iga tunni viimane seis."""
    last_in_hour: dict[tuple, str] = {}
    for r in rows:
        if r["kuupaev"] != today:
            h = (r["kuupaev"], r["kell"][:2])
            if r["kell"] > last_in_hour.get(h, ""):
                last_in_hour[h] = r["kell"]
    return [r for r in rows if r["kuupaev"] == today or last_in_hour.get((r["kuupaev"], r["kell"][:2])) == r["kell"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="käivita ka väljaspool tööaega")
    ap.add_argument("--backfill", metavar="YYYY-MM-DD",
                    help="täida materjalide ajalugu sellest päevast kuni eilseni (tunniseise ei lisa)")
    args = ap.parse_args()

    now = datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not args.backfill and not args.force and not (now.weekday() in ACTIVE_DAYS and ACTIVE_FROM <= now.strftime("%H:%M") <= ACTIVE_TO):
        log(f"Väljaspool tööaega ({now:%a %H:%M}), ei tee midagi.")
        return 0

    data = load_data()
    materjalid: dict = data.setdefault("materjalid", {})

    if args.backfill:
        first = datetime.strptime(args.backfill, "%Y-%m-%d").replace(tzinfo=TZ)
        days = []
        d = first
        while d < today:
            days.append(d)
            d += timedelta(days=1)
    else:
        days = [today]
        # päeva esimesel käivitusel loe üle ka eilne lõplik seis
        yesterday = today - timedelta(days=1)
        if now.strftime("%Y-%m-%d") not in materjalid:
            days.insert(0, yesterday)
    log(f"Päevad: {', '.join(x.strftime('%d.%m') for x in days)}")

    auth_file = env("AUTH_STATE_FILE")
    use_auth = auth_file and Path(auth_file).exists() and Path(auth_file).stat().st_size > 10

    qm: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            storage_state=auth_file if use_auth else None,
            locale="et-EE", timezone_id="Europe/Tallinn",
        )
        page = ctx.new_page()
        for site in QUARRYMON:
            qm[site["karjaar"]] = fetch_quarrymon(page, site, days)
        wb = fetch_waybiller(page, days)
        kaart = None if args.backfill else fetch_kaart(page, now)
        browser.close()

    # materjalid iga päeva kohta
    for d in days:
        key = d.strftime("%Y-%m-%d")
        qm_day = {q: res[key] for q, res in qm.items() if key in res}
        if not qm_day and key not in wb:
            continue
        materjalid[key] = day_materials(key, qm_day, wb.get(key))
    data["materjalid"] = dict(sorted(materjalid.items()))

    if args.backfill:
        save_data(data)
        log(f"Materjalide ajalugu täidetud: {len(days)} päeva.")
        return 0

    key = today.strftime("%Y-%m-%d")
    waybills = wb.get(key)
    wb_q, wb_f, _ = summarize_waybills(waybills) if waybills is not None else ({}, [], {})

    kell = now.strftime("%H:%M")
    paev = key
    aeg = f"{paev} {kell}"
    rows, allikad = [], {}
    for q in ALL_QUARRIES:
        if q in WAYBILLER_ONLY:
            if waybills is None:
                continue
            v, src = wb_q.get(q, {"autosid": 0, "koormaid": 0, "tonnid": 0}), "Waybiller"
        else:
            v, src = qm.get(q, {}).get(key), "QuarryMon"
            if v is None and waybills is not None:      # QuarryMon ei vastanud -> Waybilleri varuandmed
                v, src = wb_q.get(q, {"autosid": 0, "koormaid": 0, "tonnid": 0}), "Waybiller (varu)"
            if v is None:
                continue
        rows.append({"kuupaev": paev, "kell": kell, "aeg": aeg, "karjaar": q, "allikas": src,
                     "autosid": v["autosid"], "koormaid": v["koormaid"], "tonnid": v["tonnid"]})
        allikad[q] = src

    qm_today = {q: res[key] for q, res in qm.items() if key in res}
    autod = analyse_trucks(now, qm_today, waybills)
    for r in rows:
        r["veab"] = autod["karjaarid"].get(r["karjaar"], {}).get("veab", 0)

    if not rows:
        log("Ühestki allikast andmeid ei tulnud – data.json jääb muutmata.")
        return 1

    firmad = [{"kuupaev": paev, "kell": kell, "aeg": aeg, **f} for f in wb_f]

    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    data["rows"] = thin_rows([r for r in data.get("rows", []) if r["aeg"] != aeg and r["kuupaev"] >= cutoff] + rows, paev, None)
    data["firmad"] = thin_rows([r for r in data.get("firmad", []) if r["aeg"] != aeg and r["kuupaev"] >= cutoff] + firmad, paev, None)
    data["autod"] = autod
    if kaart is not None:
        data["kaart"] = kaart          # ainult tänane seis, kirjutatakse iga kord üle (ajalugu ei hoita)
    paevad = data.setdefault("paevad_autod", {})
    paevad[paev] = {"unikaalseid": autod["unikaalseid"], "liikumisi": len(autod["liikumised"]),
                    "karjaarid": {q: v["kaisid"] for q, v in autod["karjaarid"].items() if v["kaisid"]},
                    "aeg": kell}
    data["uuendatud"] = now.isoformat(timespec="seconds")
    data["viimane_kontroll"] = {
        "aeg": aeg,
        "allikad": allikad,
        "puudu": [q for q in ALL_QUARRIES if q not in allikad],
    }
    save_data(data)
    log(f"Salvestatud {len(rows)} karjääri seis ({aeg}). Puudu: {data['viimane_kontroll']['puudu'] or 'ei midagi'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

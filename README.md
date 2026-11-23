# T4 karjääride veod – avalik leht

See projekt teeb iga tunni tagant (E–R kell 8–19 Tallinna aja järgi) järgmist:

1. **GitHub Actions** käivitab skripti `scraper/fetch.py`.
2. Skript logib sisse QuarryMoni lehtedele ja Waybillerisse:
   - QuarryMon: Tika 2, Viruküla, Jaakna II
   - Waybiller: Orava, Harku, Ruukivi ja veofirmad
3. Uus seis lisatakse faili `docs/data.json`.
4. Leht `docs/index.html` avaldatakse **GitHub Pagesis** aadressil `https://<kasutajanimi>.github.io/<repo>/`.

Lehe avamiseks ei pea keegi sisse logima. Leht laeb uued andmed ise iga 5 minuti tagant ja näitab ülaosas, mis kell andmeid viimati uuendati. Kui andmed on tööajal üle 2 tunni vanad, muutub see riba oranžiks.

```
.github/workflows/update.yml   automaatika (iga täistund)
scraper/fetch.py               andmete toomine
scraper/save_session.py        varuvariant: sessiooni salvestamine (auth.json)
docs/index.html                avalik leht
docs/data.json                 andmed (algseis kuni 23.11.2026)
requirements.txt               Pythoni sõltuvus (Playwright)
```

---

## Seadistamine samm-sammult (u 20 minutit)

### 1. Loo GitHubi konto
1. Mine aadressile https://github.com/signup ja loo tasuta konto.
   - Tööasjade jaoks võid kasutada tööaadressi.
2. Kinnita e-post.

### 2. Loo repositoorium ja laadi failid üles
1. Vajuta paremal üleval **+** → **New repository**.
2. Täida väljad:
   - **Repository name:** näiteks `t4-veod`.
   - **Public:** vali see. Tasuta GitHub Pages töötab ainult avaliku repositooriumiga, vt allpool „Mida peab teadma".
   - Ära lisa README-d ega .gitignore'i, need on projektis olemas.
3. Vajuta **Create repository**.
4. Avanenud lehel vajuta **uploading an existing file**.
5. Paki `t4-veod.zip` lahti ja lohista **kogu sisu** (kaustad `.github`, `docs`, `scraper` ja failid) üleslaadimise aknasse.
   - Kaust `.github` on peidetud. Macis näed seda Finderis klahvidega **Cmd+Shift+.**, Windowsis Exploreris valikuga **View → Hidden items**.
6. Vajuta **Commit changes**.
7. Kontrolli, et repositooriumis on nüüd näha `.github/workflows/update.yml`.

### 3. Salvesta paroolid (GitHub Secrets)
Paroolid ei ole koodis. Need on GitHubi krüpteeritud saladustes, mida keegi ei näe, ka sina ise pärast salvestamist mitte.

1. Ava repositooriumis **Settings** → **Secrets and variables** → **Actions**.
2. Vajuta **New repository secret**.
3. Lisa iga saladus eraldi: nimi täpselt nagu tabelis, väärtus sinu andmed.

Iga QuarryMoni lehe jaoks lisa **kas PIN-kood või e-post ja parool**, olenevalt sellest, kuidas sa sellele lehele sisse logid:

| Leht | PIN-koodiga | E-posti ja parooliga |
|---|---|---|
| Tika (maa.verston.ee) | `TIKA_PIN` | `TIKA_EMAIL` + `TIKA_PASSWORD` |
| Viruküla (marina.almic.ee) | `VIRUKULA_PIN` | `VIRUKULA_EMAIL` + `VIRUKULA_PASSWORD` |
| Jaakna (kiiusoon.devplesk.almic.ee) | `JAAKNA_PIN` | `JAAKNA_EMAIL` + `JAAKNA_PASSWORD` |

Waybilleri jaoks lisa alati need kaks:

| Nimi | Väärtus |
|---|---|
| `WAYBILLER_EMAIL` | Waybilleri e-post |
| `WAYBILLER_PASSWORD` | Waybilleri parool |

### 4. Lülita avalik veebilink sisse (GitHub Pages)
1. Ava **Settings** → **Pages**.
2. Vali **Build and deployment → Source** alt **GitHub Actions**.
   - **Ära** vali „Deploy from a branch".

### 5. Esimene käivitus
1. Ava **Actions**.
   - Kui GitHub küsib, kas lubada töövood, vajuta **I understand… enable**.
2. Vali vasakult **Uuenda andmed** → **Run workflow** → **Run workflow**.
3. Oota 2–3 minutit, kuni kõrvale tuleb roheline linnuke.
4. Ava töövoo kokkuvõte. Lingi leiad kaardilt **avalda**, see on kujul `https://<kasutajanimi>.github.io/t4-veod/`.
5. Saada see link kolleegidele.

Edaspidi käivitub töövoog ise iga täistund. Sul ei ole vaja midagi vajutada ega kinnitada.

### 6. Materjalide ajalugu (ühekordne)
Lehe tabel **„Kokku toodud"** liidab kokku iga päeva materjalid. Vanemate päevade täitmiseks tee nii:

1. Ava **Actions** → **Uuenda andmed** → **Run workflow**.
2. Kirjuta lahtrisse „Materjalide ajaloo täitmine alates" kuupäev, näiteks `2026-04-01`.
3. Vajuta **Run workflow**. Käivitus võib kesta 15–30 minutit.

Edaspidi lisandub iga päev automaatselt.

### 7. Kui midagi ei tööta
Ava **Actions** → viimane käivitus → **uuenda** → samm **Too andmed**. Logis on iga allika kohta rida.

| Logis on | Mida teha |
|---|---|
| `sessioon aegunud ja parool puudub` | Saladus on puudu või nimi on valesti kirjutatud. |
| `sisselogimine ebaõnnestus` | Vale parool või leht küsib lisakinnitust (SMS, e-posti kood). Kasuta allolevat varuvarianti. |
| `Väljaspool tööaega` | Kõik on korras. Väljaspool E–R 8–19 skript ei tee midagi. |

Kui mõni allikas ei vasta, jääb leht alles. Viimase uuenduse riba näitab, mis puudu jäi. QuarryMoni asemel võetakse siis Waybilleri numbrid ja tabelis on märge „Waybilleri järgi".

---

## Varuvariant: salvestatud sessioon (auth.json)

Seda on vaja ainult siis, kui parooliga sisselogimine ei õnnestu, näiteks kui Waybiller küsib koodi. Sessioon kehtib tavaliselt kuni nädal. Siis tuleb seda korrata.

1. Paigalda oma arvutisse Python: https://www.python.org/downloads/
2. Ava terminal projekti kaustas ja käivita:
   ```
   pip install playwright
   python -m playwright install chromium
   python scraper/save_session.py
   ```
3. Avaneb brauser nelja sakiga. Logi igale poole käsitsi sisse, kaasa arvatud koodid.
4. Mine tagasi terminali ja vajuta **Enter**.
5. Terminal prindib pika tekstirea. Kopeeri see GitHubi saladuseks nimega **`AUTH_STATE_B64`**.
   - Esimesel korral loo see **New repository secret** kaudu.
   - Hiljem ava saladus ja vajuta **Update**.

Kui `AUTH_STATE_B64` on olemas, kasutab skript kõigepealt seda. Parooli proovib ta ainult siis, kui sessioon on aegunud.

**Ära lisa faili `auth.json` repositooriumisse ega saada seda kellelegi.** See annab ligipääsu sinu kontodele. `.gitignore` hoiab selle Gitist eemal.

---

## Mida peab teadma

- **Avalik repositoorium tähendab avalikke andmeid.** Kood ja `docs/data.json` on kõigile nähtavad, nagu ka leht ise. Neis on ainult karjääride kogused, autode arv ja veofirmade nimed. Registrinumbreid, paroole ega sessiooni neis pole. Kontrolli, et see sobib Verstoni andmepoliitikaga.
- **Kasutustingimused.** Automaatne sisselogimine ja andmete lugemine käib sinu kontodega. Veendu, et Almic ja Waybiller seda lubavad.
  - Waybilleril on ka ametlik API. Kui saad neilt API võtme, on see kindlam kui sisselogimine.
- **Kellaajad.**
  - GitHubi tunnine käivitus võib hilineda 5–20 minutit, tipptundidel isegi rohkem.
  - Tööaega saad muuta failis `scraper/fetch.py`: `ACTIVE_FROM_HOUR` ja `ACTIVE_TO_HOUR`.
- **GitHubi serverid asuvad välismaal.** Kui mõni leht lubab sisse logida ainult Eesti IP-aadressilt, ei pääse skript sinna. Logis on siis `sisselogimine ebaõnnestus` või `aegus`.
- **Kujunduse muutmine.** Muuda `docs/index.html`-i otse GitHubis (pliiatsi ikoon) ja salvesta. Leht avaldatakse automaatselt uuesti.
- **Kulu.** Avaliku repositooriumi jaoks on GitHub Actions ja Pages tasuta.

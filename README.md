# Agent Learning — mini-Jožko

Light verzia firemného AI agenta. Cieľ: pochopiť, ako agent funguje pod kapotou.
Agent má vlastné vedomosti, beží cez API a píšeš s ním cez WhatsApp.

## Architektúra

```
WhatsApp → Meta Cloud API → POST /webhook (FastAPI) → agent.reply() → Claude API
                                                              ↑
                                          system prompt (osobnosť) + knowledge.md
```

| Súbor            | Čo robí                                                        |
|------------------|---------------------------------------------------------------|
| `agent.py`       | Srdce — skladá volanie Claude (osobnosť + knowledge + pamäť). |
| `main.py`        | FastAPI server + WhatsApp webhook.                            |
| `knowledge.md`   | Vedomosti agenta (u firemného Jožka = SKILLS.md).            |
| `.env`           | Tajné kľúče (necommituje sa).                                |

## 1. Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # potom vyplň .env reálnymi hodnotami
```

Minimum na lokálny test agenta: stačí `ANTHROPIC_API_KEY` v `.env`.

## 2. Lokálny test agenta (bez WhatsApp)

Najprv over, že samotný agent funguje — píšeš mu rovno do terminálu:

```bash
python agent.py
```

Keď toto funguje, máš hotové srdce. WhatsApp je už len "obal" okolo.

## 3. WhatsApp cez Meta Cloud API

1. Choď na <https://developers.facebook.com> → vytvor App (typ *Business*).
2. Pridaj produkt **WhatsApp** → *API Setup*.
3. Skopíruj do `.env`:
   - **Temporary access token** → `WHATSAPP_TOKEN`
   - **Phone number ID** (testovacie číslo dostaneš zadarmo) → `WHATSAPP_PHONE_NUMBER_ID`
4. Vymysli si ľubovoľný `WHATSAPP_VERIFY_TOKEN` a daj ho do `.env`.
5. Pridaj svoje súkromné číslo do zoznamu *To* (recipientov), aby ti testovacie
   číslo mohlo písať.

## 4. Spustenie + napojenie webhooku

Webhook musí byť verejne dostupný cez HTTPS. Lokálne na to použiješ tunel (ngrok):

```bash
# terminál 1 — server
uvicorn main:app --reload --port 8000

# terminál 2 — verejný HTTPS tunel na localhost:8000
ngrok http 8000
```

ngrok ti dá URL typu `https://abcd1234.ngrok-free.app`. V Mete (*WhatsApp → Configuration → Webhook*):
- **Callback URL**: `https://abcd1234.ngrok-free.app/webhook`
- **Verify token**: rovnaký ako `WHATSAPP_VERIFY_TOKEN` v `.env`
- Klikni *Verify and save* → potom *Subscribe* na pole **messages**.

Teraz napíš na testovacie WhatsApp číslo — agent odpovie. 🎉

## Čo ďalej (keď základ funguje)

- **Tools** — nechať agenta volať funkcie (počasie, DB, GA4 API…). To je tá
  ďalšia veľká vrstva smerom k plnému Jožkovi.
- **Databáza** — uložiť históriu konverzácie (teraz je len v pamäti).
- **RAG** — keď knowledge prerastie ~50 strán, vektorová DB namiesto súboru.

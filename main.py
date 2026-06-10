"""
API + WhatsApp most.

Tu žije webhook, ktorý volá Meta. Dve cesty:
  GET  /webhook  → jednorazové overenie webhooku pri nastavovaní v Mete.
  POST /webhook  → sem Meta posiela každú prichádzajúcu WhatsApp správu.

Schéma:
  WhatsApp → Meta → POST /webhook → agent.reply() → späť cez Meta Graph API → WhatsApp
"""

import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response

load_dotenv()
import agent  # importujeme až po load_dotenv, aby mal agent k dispozícii env premenné

app = FastAPI()

# Konfigurácia z .env
VERIFY_TOKEN = os.environ["WHATSAPP_VERIFY_TOKEN"]
WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_NUMBER_ID = os.environ["WHATSAPP_PHONE_NUMBER_ID"]

GRAPH_API_VERSION = "v21.0"
SEND_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"


@app.get("/")
def health():
    """Rýchla kontrola, že server beží."""
    return {"status": "ok", "model": agent.MODEL}


# --- 1. OVERENIE WEBHOOKU (GET) -----------------------------------------------
# Keď v Mete zadáš URL webhooku, Meta naň pošle GET s tvojím verify tokenom
# a náhodnou "challenge" hodnotou. My overíme token a vrátime challenge späť —
# tým Mete dokážeme, že webhook je náš.
@app.get("/webhook")
def verify(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        # Vrátime challenge ako čistý text (to Meta očakáva).
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)


# --- 2. PRÍJEM SPRÁV (POST) ---------------------------------------------------
@app.post("/webhook")
async def receive(request: Request):
    body = await request.json()

    # Meta zabaľuje správy hlboko do štruktúry. Bezpečne sa cez ňu prehrabeme.
    try:
        change = body["entry"][0]["changes"][0]["value"]
        messages = change.get("messages")
        if not messages:
            # Môže to byť status update (delivered/read), nie nová správa — ignoruj.
            return {"status": "ignored"}

        message = messages[0]
        from_number = message["from"]          # číslo odosielateľa
        user_text = message["text"]["body"]    # samotný text správy
    except (KeyError, IndexError):
        # Neznámy formát (napr. obrázok/audio) — pre v1 ignorujeme.
        return {"status": "ignored"}

    # Zavoláme agenta. (Blokujúce volanie Claude beží v FastAPI threadpoole,
    # takže event loop neblokuje — pre učebnú verziu úplne v poriadku.)
    answer = agent.reply(from_number, user_text)

    # Pošleme odpoveď späť cez Meta Graph API.
    _send_whatsapp_message(from_number, answer)

    return {"status": "ok"}


def _send_whatsapp_message(to: str, text: str):
    """Odošle textovú správu cez Meta Graph API."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    resp = httpx.post(SEND_URL, headers=headers, json=payload, timeout=30)
    if resp.status_code >= 400:
        # Vypíšeme chybu do konzoly — pomôže pri ladení (zlý token, expirovaný atď.)
        print(f"[WhatsApp send error] {resp.status_code}: {resp.text}")

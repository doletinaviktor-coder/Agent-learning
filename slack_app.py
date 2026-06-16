"""
Slack vstup pre agenta (Socket Mode).

ALTERNATÍVA k main.py (WhatsApp). Kľúčové: agent.py sa NEMENÍ — len sme vymenili
"obal" okolo neho. To je odmena za čistý návrh: jadro agenta (persona + knowledge
+ pamäť + nástroje) je nezávislé od toho, odkiaľ správa príde.

Socket Mode = Slack sa pripája cez WebSocket smerom von, takže NEpotrebuješ
verejnú URL ani tunel (žiadne cloudflared, žiadne prepisovanie webhooku).
Beží to ako jeden Python proces: `python slack_app.py`.

Potrebné tokeny v .env:
  SLACK_BOT_TOKEN   (xoxb-...) — bot na čítanie/písanie správ
  SLACK_APP_TOKEN   (xapp-...) — app-level token pre Socket Mode spojenie
"""

import os
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()
import agent  # ROVNAKÉ jadro ako pri WhatsApp — žiadna zmena

app = App(token=os.environ["SLACK_BOT_TOKEN"])


def _respond(event, say, client):
    """Spoločná logika: vezmi text, zavolaj agenta, odpovedz."""
    # Ignoruj správy od botov (vrátane seba) a systémové (edity, joiny…),
    # inak by si odpovedal sám sebe → nekonečná slučka.
    if event.get("bot_id") or event.get("subtype"):
        return

    user = event["user"]
    text = event.get("text", "")
    print(f"[in]  {user}: {text}")

    # Okamžitá spätná väzba: pošleme placeholder, nech user vidí, že bot pracuje.
    # Zapamätáme si jeho channel + ts, aby sme ho potom prepísali odpoveďou.
    placeholder = say("💭 Moment, pozerám sa na to…")

    try:
        # user_id pre DB históriu prefixujeme 'slack:' — oddelený menný priestor
        # od WhatsApp čísel, takže pamäť sa nemieša.
        answer = agent.reply(f"slack:{user}", text)
    except Exception as e:
        answer = f"Ups, niečo sa pokazilo: {e}"
        print(f"[error] {e}")

    print(f"[out] {user}: {answer}")

    # Prepíšeme placeholder finálnou odpoveďou (vyzerá, akoby sa "premyslel").
    client.chat_update(channel=placeholder["channel"], ts=placeholder["ts"], text=answer)


@app.event("message")
def handle_dm(event, say, client):
    """Priame správy botovi (DM)."""
    _respond(event, say, client)


@app.event("app_mention")
def handle_mention(event, say, client):
    """Zmienky @bot v kanáli."""
    _respond(event, say, client)


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("Slack agent beží (Socket Mode). Ctrl+C na koniec.")
    handler.start()

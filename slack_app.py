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
import re
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()
import agent  # ROVNAKÉ jadro ako pri WhatsApp — žiadna zmena

app = App(token=os.environ["SLACK_BOT_TOKEN"])


def _to_slack_mrkdwn(text: str) -> str:
    """Poistka: skonvertuje GitHub markdown na Slack mrkdwn, keby model skĺzol.
    Persóna mu káže písať rovno správne, toto je len záchranná sieť."""
    # **tučné** -> *tučné*  (Slack používa jednu hviezdičku)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # ### Nadpis -> *Nadpis*
    text = re.sub(r"^\s*#{1,6}\s*(.+?)\s*$", r"*\1*", text, flags=re.M)
    # zahoď oddeľovacie riadky markdown tabuľky (|---|---|)
    text = re.sub(r"^\s*\|?[\s:|-]*-{2,}[\s:|-]*\|?\s*$\n?", "", text, flags=re.M)
    # z dátového riadku tabuľky "| a | b |" sprav "a | b" (čitateľnejšie ako bullet)
    text = re.sub(r"^\s*\|\s*(.+?)\s*\|\s*$",
                  lambda m: "• " + " | ".join(c.strip() for c in m.group(1).split("|")),
                  text, flags=re.M)
    return text


def _respond(event, say, client):
    """Spoločná logika: vezmi text, zavolaj agenta, odpovedz."""
    # Ignoruj správy od botov (vrátane seba) a systémové (edity, joiny…),
    # inak by si odpovedal sám sebe → nekonečná slučka.
    if event.get("bot_id") or event.get("subtype"):
        return

    user = event["user"]
    text = event.get("text", "")
    channel = event["channel"]

    # VLÁKNO = jedna konverzácia. Koreň vlákna je thread_ts (ak sme vo vlákne),
    # inak ts samotnej správy (= nová samostatná správa zakladá nové vlákno).
    # Históriu kľúčujeme podľa vlákna, takže:
    #   • nová správa = nové vlákno = ČISTÁ história (modelu pošleme len toto vlákno),
    #   • odpoveď ide DO vlákna, takže konverzácia drží pokope.
    # Tým držíme prompt malý a náklady pod kontrolou.
    thread_ts = event.get("thread_ts") or event["ts"]
    conv_id = f"slack:{channel}:{thread_ts}"
    print(f"[in]  {conv_id} {user}: {text}")

    # Placeholder posielame priamo do vlákna (thread_ts), nech vidno, že bot pracuje.
    placeholder = say(text="💭 Moment, pozerám sa na to…", thread_ts=thread_ts)

    try:
        answer = agent.reply(conv_id, text)
    except Exception as e:
        answer = f"Ups, niečo sa pokazilo: {e}"
        print(f"[error] {e}")

    print(f"[out] {conv_id}: {answer}")

    # Prepíšeme placeholder finálnou odpoveďou (vyzerá, akoby sa "premyslel").
    client.chat_update(channel=placeholder["channel"], ts=placeholder["ts"],
                       text=_to_slack_mrkdwn(answer))


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

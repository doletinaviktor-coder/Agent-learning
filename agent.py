"""
Agent core — srdce celého projektu.

Toto je jediné miesto, kde voláme Claude. Zámerne je oddelené od WhatsApp/API
vrstvy (main.py), aby si videl, že "agent" je len: system prompt + knowledge +
história správ → volanie modelu → text. Presne ako firemný Jožko, len menšie.
"""

import os
from dotenv import load_dotenv
from anthropic import Anthropic

import db  # perzistentná pamäť konverzácie (SQLite)

load_dotenv()  # načíta premenné z .env súboru
db.init_db()   # pripraví databázu (vytvorí tabuľku, ak treba)

# Klient sa autentifikuje cez ANTHROPIC_API_KEY z prostredia (z .env).
client = Anthropic()

# Model sa dá prepnúť v .env bez zásahu do kódu.
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")

# --- 1. OSOBNOSŤ AGENTA -------------------------------------------------------
# System prompt definuje, KTO agent je a AKO sa správa. Je to to isté ako
# "Kto je Jožko" — len kratšie. Knowledge (vedomosti) pridávame zvlášť nižšie.
PERSONA = """Si mini-asistent pre digitálnu analytiku, bežíš cez WhatsApp.

Pravidlá:
- Odpovedaj po slovensky (prepni do EN, ak ti user píše po anglicky).
- Buď stručný — toto je chat, nie report. Max pár viet.
- Nikdy si nevymýšľaj čísla ani fakty. Ak nevieš, povedz že nevieš.
- Žiadne corporate fluff, žiadne "rád ti pomôžem". Priamo k veci.

Tvoje vedomosti sú v sekcii nižšie. Odpovedaj na ich základe."""


def _load_knowledge() -> str:
    """Načíta knowledge.md zo súboru. Pri každom volaní = môžeš editovať
    knowledge za behu bez reštartu servera."""
    with open("knowledge.md", "r", encoding="utf-8") as f:
        return f.read()


def _build_system():
    """Poskladá system prompt z osobnosti + knowledge.

    Knowledge dávame do samostatného bloku s `cache_control` — Claude si ho
    nacacheuje, takže pri ďalších správach neplatíš zaň plnú cenu (~90% lacnejšie).
    Knowledge je stabilné, takže je to ideálny kandidát na cache.
    """
    return [
        {"type": "text", "text": PERSONA},
        {
            "type": "text",
            "text": "# KNOWLEDGE BASE\n\n" + _load_knowledge(),
            "cache_control": {"type": "ephemeral"},
        },
    ]


# --- 2. PAMÄŤ KONVERZÁCIE ------------------------------------------------------
# Claude API je bezstavové — pri každom volaní mu musíš poslať CELÚ históriu.
# História je teraz v SQLite (modul db.py), takže prežije reštart servera.
MAX_HISTORY = 20  # koľko posledných správ posielame modelu (aby prompt nerástol)


def reply(user_id: str, user_text: str) -> str:
    """Vezme správu od usera a vráti odpoveď agenta.

    user_id = telefónne číslo (oddelená pamäť pre každého usera v DB).
    """
    # 1. Uložíme prichádzajúcu správu do DB.
    db.add_message(user_id, "user", user_text)

    # 2. Načítame posledných MAX_HISTORY správ (vrátane tej, čo sme práve uložili).
    history = db.get_history(user_id, MAX_HISTORY)

    # 3. Zavoláme Claude s celou históriou.
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=_build_system(),
        messages=history,
    )

    # response.content je zoznam blokov — vyberieme z neho text.
    answer = "".join(block.text for block in response.content if block.type == "text")

    # 4. Uložíme odpoveď agenta do DB (aby si ju pamätal pri ďalšej správe).
    db.add_message(user_id, "assistant", answer)

    return answer


# --- Lokálny test bez WhatsApp ------------------------------------------------
# Spusti `python agent.py` a píš agentovi rovno do terminálu.
if __name__ == "__main__":
    print(f"Agent beží na modeli {MODEL}. Píš (Ctrl+C na koniec):\n")
    while True:
        try:
            text = input("Ty: ")
        except (KeyboardInterrupt, EOFError):
            print("\nČau!")
            break
        print("Agent:", reply("local-test", text), "\n")

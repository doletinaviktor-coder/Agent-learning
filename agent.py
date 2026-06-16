"""
Agent core — srdce celého projektu.

Toto je jediné miesto, kde voláme Claude. Zámerne je oddelené od WhatsApp/API
vrstvy (main.py), aby si videl, že "agent" je len: system prompt + knowledge +
história správ → volanie modelu → text. Presne ako firemný Jožko, len menšie.
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic

import db   # perzistentná pamäť konverzácie (SQLite)
import ga4  # Google Analytics 4 Data API

load_dotenv()  # načíta premenné z .env súboru
db.init_db()   # pripraví databázu (vytvorí tabuľku, ak treba)

# Klient sa autentifikuje cez ANTHROPIC_API_KEY z prostredia (z .env).
client = Anthropic()

# Model sa dá prepnúť v .env bez zásahu do kódu.
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")

# --- 1. OSOBNOSŤ AGENTA -------------------------------------------------------
# System prompt definuje, KTO agent je a AKO sa správa. Je to to isté ako
# "Kto je Jožko" — len kratšie. Knowledge (vedomosti) pridávame zvlášť nižšie.
PERSONA = """Si osobný asistent Viktora. Bežíš cez WhatsApp.

Ako sa správaš:
- Hovoríš po slovensky (prepni do EN, ak ti Viktor píše po anglicky).
- Priamo a vecne. Žiadny corporate fluff, žiadne "rád ti pomôžem".
- Stručne — WhatsApp správa, nie esej. Ak treba viac, najprv ponúkni rozvedenie.
- Úprimne: ak niečo nevieš alebo si nie si istý, povedz to. Nikdy si nevymýšľaj fakty.
- Proaktívne: ak vidíš užitočný ďalší krok, navrhni ho v jednej vete.

Info o Viktorovi a jeho kontexte máš v sekcii nižšie — čerpaj z neho, keď sa pýta
na seba alebo svoje projekty."""


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


# --- 2. NÁSTROJE (TOOLS) ------------------------------------------------------
# Nástroj = funkcia, ktorú agent SÁM zavolá, keď ju potrebuje. Má dve časti:
#   (a) SCHÉMA — popis pre Claude (meno, čo robí, aké parametre). Podľa toho sa
#       Claude rozhodne, či a kedy nástroj zavolať.
#   (b) IMPLEMENTÁCIA — skutočná Python funkcia, ktorú vykonáme MY.
#
# Claude nástroj nikdy nespustí sám — len povie "chcem zavolať get_current_time".
# Spustenie je na nás (v _run_tool nižšie) a výsledok mu pošleme späť.

TOOLS = [
    {
        "name": "get_current_time",
        "description": (
            "Vráti aktuálny dátum a čas. Použi vždy, keď sa user pýta na dnešný "
            "dátum, deň v týždni, čas alebo 'koľko je hodín'. Claude sám o sebe "
            "nevie, aký je teraz reálny čas — preto na to potrebuje tento nástroj."
        ),
        # Tento nástroj nepotrebuje žiadne vstupy → prázdne properties.
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ga4_list_properties",
        "description": (
            "Vylistuje Google Analytics 4 property, ku ktorým má agent prístup "
            "(názov + ID). Použi, keď user nešpecifikoval konkrétny web/property, "
            "alebo keď potrebuješ zistiť property ID predtým, než zavoláš get_ga4_metrics."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ga4_run_report",
        "description": (
            "Flexibilný Google Analytics 4 report — vytiahne ľubovoľné GA4 metriky "
            "(voliteľne rozpadnuté podľa dimenzií) za zadané obdobie, voliteľne s "
            "medziročným (YoY) porovnaním. Ak nepoznáš property_id, najprv zavolaj "
            "ga4_list_properties.\n\n"
            "Časté METRIKY: sessions, totalUsers, activeUsers, newUsers, "
            "screenPageViews, eventCount, keyEvents, conversions, purchaseRevenue, "
            "totalRevenue, transactions, ecommercePurchases, averagePurchaseRevenue, "
            "bounceRate, engagementRate, averageSessionDuration.\n"
            "Časté DIMENZIE: date, country, deviceCategory, "
            "sessionDefaultChannelGroup, sessionSource, sessionMedium, "
            "sessionSourceMedium, pagePath, pageTitle, landingPage, newVsReturning, "
            "itemName.\n"
            "Obrat/tržby = purchaseRevenue (alebo totalRevenue). Pre YoY daj "
            "compare_yoy=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "Číselné ID GA4 property (z ga4_list_properties).",
                },
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GA4 metriky, napr. ['sessions','purchaseRevenue'].",
                },
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Voliteľný rozpad, napr. ['country'] alebo ['sessionDefaultChannelGroup'].",
                },
                "start_date": {
                    "type": "string",
                    "description": "Začiatok: 'YYYY-MM-DD' alebo 'NdaysAgo'/'today'/'yesterday'. Default '30daysAgo'.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Koniec obdobia. Default 'today'.",
                },
                "compare_yoy": {
                    "type": "boolean",
                    "description": "Ak true, porovná s rovnakým obdobím pred rokom (YoY).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max počet riadkov pri dimenziách. Default 25.",
                },
            },
            "required": ["metrics"],
        },
    },
]


def _run_tool(name: str, tool_input: dict) -> str:
    """Vykoná nástroj podľa mena a vráti výsledok ako text.

    Celé je v try/except — keď nástroj zlyhá (napr. GA4 API vráti chybu),
    vrátime chybový text ako výsledok, nech sa agent nezosype. Claude potom
    userovi povie, že sa niečo nepodarilo.
    """
    try:
        if name == "get_current_time":
            return datetime.now().strftime("Dnes je %d.%m.%Y, %H:%M:%S")
        if name == "ga4_list_properties":
            return ga4.list_properties()
        if name == "ga4_run_report":
            return ga4.run_report(
                metrics=tool_input.get("metrics", []),
                dimensions=tool_input.get("dimensions"),
                start_date=tool_input.get("start_date", "30daysAgo"),
                end_date=tool_input.get("end_date", "today"),
                property_id=tool_input.get("property_id"),
                limit=tool_input.get("limit", 25),
                compare_yoy=tool_input.get("compare_yoy", False),
            )
        return f"Neznámy nástroj: {name}"
    except Exception as e:
        return f"Nástroj {name} zlyhal: {e}"


# --- 3. PAMÄŤ KONVERZÁCIE ------------------------------------------------------
# Claude API je bezstavové — pri každom volaní mu musíš poslať CELÚ históriu.
# História je teraz v SQLite (modul db.py), takže prežije reštart servera.
MAX_HISTORY = 20  # koľko posledných správ posielame modelu (aby prompt nerástol)


def reply(user_id: str, user_text: str) -> str:
    """Vezme správu od usera a vráti odpoveď agenta.

    user_id = telefónne číslo (oddelená pamäť pre každého usera v DB).
    """
    # 1. Uložíme prichádzajúcu správu do DB.
    db.add_message(user_id, "user", user_text)

    # 2. Načítame históriu (plain-text správy). `messages` je pracovná kópia pre
    #    túto požiadavku — počas tool slučky do nej dočasne pribudnú tool bloky,
    #    ale do DB ukladáme len finálnu text odpoveď (tool round-trip je efemérny).
    messages = db.get_history(user_id, MAX_HISTORY)

    # 3. AGENTICKÁ SLUČKA. Voláme Claude dovtedy, kým nás žiada o nástroj.
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_build_system(),
            tools=TOOLS,              # <-- sprístupníme agentovi nástroje
            messages=messages,
        )

        # Ak Claude NEchce nástroj (bežná odpoveď), vyskočíme zo slučky.
        if response.stop_reason != "tool_use":
            break

        # Claude chce nástroj. Jeho odpoveď (obsahuje 'tool_use' bloky) musíme
        # pridať do histórie ako asistentskú správu — inak by nevedel, čo žiadal.
        messages.append({"role": "assistant", "content": response.content})

        # Vykonáme každý požadovaný nástroj a pripravíme výsledky.
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _run_tool(block.name, block.input)
                print(f"[tool] {block.name}({block.input}) -> {result}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,   # musí sedieť s id z tool_use bloku
                    "content": result,
                })

        # Výsledky pošleme späť ako user správu a slučka sa zopakuje —
        # Claude teraz z výsledku poskladá finálnu odpoveď.
        messages.append({"role": "user", "content": tool_results})

    # 4. Vytiahneme finálny text a uložíme ho do DB.
    answer = "".join(block.text for block in response.content if block.type == "text")
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

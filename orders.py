"""
Objednávky z CMS (BUXUS) — import surového CSV exportu do SQLite + flexibilný report.

Filozofia (rovnaká ako pri GA4): flexibilné nástroje, kde si agent sám zvolí
metriky, zoskupenie a obdobie. Tým z objednávok vytiahne prakticky čokoľvek
(tržby, počty, AOV, rozpad podľa platby/dopravy/statusu, YoY, opakujúci zákazníci).

OSOBNÉ ÚDAJE: importujeme analytické stĺpce + EMAIL (a z neho odvodenú identitu
zákazníka), pretože email je jediný spôsob ako spoznať vracajúcich sa zákazníkov
(40 % objednávok je hosťovských, bez customer_id). Mená, adresy a telefóny
NEimportujeme — na analytiku ich netreba. orders.db je gitignored (*.db).
Pravidlo: do CHATU agent nikdy nesype konkrétne emaily, len agregáty.

BUXUS export nemá hlavičky, stĺpce poznáme len podľa poradia (zmapované nižšie
po profilovaní dát). Suma je v zmiešanej mene (staré objednávky v Sk, novšie
v EUR) — normalizujeme na EUR podľa stĺpca meny (oficiálny kurz 30,126 Sk/EUR).
"""

import csv
import os
import sqlite3

csv.field_size_limit(10_000_000)

DB_PATH = "orders.db"
ORDERS_CSV = "data/tblShopOrders.csv"
ITEMS_CSV = "data/tblShopOrderItems.csv"
OPTIONS_CSV = "data/tblShopOrderItemOptions.csv"

# Stĺpce tblShopOrderItems (položky objednávky).
IT_ID = 0          # item_id
IT_ORDER = 2       # order_id (FK na orders.id)
IT_PRODUCT = 3     # product_id
IT_QTY = 4         # množstvo
IT_PRICE = 5       # jednotková cena (v mene objednávky)

# Stĺpce tblShopOrderItemOptions (key-value k položkám).
OPT_ITEM = 0       # item_id (FK na items.id)
OPT_KEY = 1        # kľúč (názov produktu je pod 'slovak_name')
OPT_VAL = 2        # hodnota
NAME_KEY = "slovak_name"

# Prepočet mien na EUR (jednotiek za 1 EUR). SKK je oficiálny fixný kurz,
# ostatné sú približné dlhodobé priemery — pre trendy stačia; na presné tržby
# by sme potrebovali historické denné kurzy. Eshop predáva do SK/CZ/HU/RO.
FX_PER_EUR = {
    "eur": 1.0, "€": 1.0,
    "sk": 30.126, "skk": 30.126,   # oficiálny konverzný kurz SR (1.1.2009)
    "czk": 25.0,                    # česká koruna
    "huf": 390.0,                   # maďarský forint
    "ron": 4.95,                    # rumunský lei
}

# Mapovanie stĺpcov tblShopOrders (index v CSV -> význam).
COL_ID = 0           # id objednávky
COL_ORDER_NUM = 1    # číslo objednávky (rok-prefixované)
COL_DATE = 3         # dátum vytvorenia (YYYY-MM-DD HH:MM:SS)
COL_CUSTOMER = 4     # customer_id (často prázdne pri hosťoch)
COL_PAYMENT = 5      # spôsob platby
COL_DELIVERY = 8     # spôsob dopravy
COL_TOTAL = 9        # suma objednávky (v mene podľa COL_CURRENCY)
COL_EMAIL = 22       # email zákazníka (identita pre opakované nákupy)
COL_CURRENCY = 29    # mena (EUR / Sk / € / sk)
COL_FLAG = 32        # T/F flag
COL_STATUS = 33      # status kód (0–9)


def _to_eur(total_raw, currency) -> float:
    """Normalizuje sumu na EUR podľa meny riadku."""
    try:
        val = float(total_raw)
    except (ValueError, TypeError):
        return 0.0
    cur = (currency or "").strip().lower()
    rate = FX_PER_EUR.get(cur, 1.0)  # neznámu menu berieme 1:1 (= EUR)
    return val / rate


def _cell(row, idx):
    """Bezpečne vytiahne bunku (prázdne, ak stĺpec chýba)."""
    return row[idx].strip() if idx < len(row) else ""


def _customer_key(email, customer_id, order_id) -> str:
    """Stabilná identita zákazníka pre dedup: email > customer_id > id objednávky."""
    e = (email or "").strip().lower()
    if e and "@" in e:
        return e
    if customer_id:
        return f"cid:{customer_id}"
    return f"anon:{order_id}"


def import_csv(csv_path=ORDERS_CSV, db_path=DB_PATH, rebuild=True) -> str:
    """Naimportuje objednávky z CSV do SQLite. rebuild=True tabuľku najprv zmaže."""
    if not os.path.exists(csv_path):
        return f"CSV neexistuje: {csv_path}"

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    if rebuild:
        c.execute("DROP TABLE IF EXISTS orders")
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY,
            order_number  TEXT,
            created_at    TEXT,      -- 'YYYY-MM-DD HH:MM:SS'
            order_date    TEXT,      -- 'YYYY-MM-DD'
            year          INTEGER,
            month         TEXT,      -- 'YYYY-MM'
            customer_id   INTEGER,
            email         TEXT,      -- normalizovaný (lowercase); osobný údaj
            customer_key  TEXT,      -- identita na dedup (email | cid:.. | anon:..)
            payment       TEXT,
            delivery      TEXT,
            currency      TEXT,
            total_raw     REAL,
            total_eur     REAL,
            status        INTEGER,
            flag          TEXT
        )
    """)

    n = 0
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        batch = []
        for row in reader:
            if not row:
                continue
            id_raw = _cell(row, COL_ID)
            if not id_raw.isdigit():
                continue

            date_full = _cell(row, COL_DATE)
            order_date = date_full[:10] if len(date_full) >= 10 else ""
            year = int(order_date[:4]) if order_date[:4].isdigit() else None
            month = order_date[:7] if len(order_date) >= 7 else ""

            cust = _cell(row, COL_CUSTOMER)
            cust_id = int(cust) if cust.isdigit() else None
            email = _cell(row, COL_EMAIL).lower()
            currency = _cell(row, COL_CURRENCY)
            total_raw = _cell(row, COL_TOTAL)
            status = _cell(row, COL_STATUS)

            batch.append((
                int(id_raw),
                _cell(row, COL_ORDER_NUM),
                date_full,
                order_date,
                year,
                month,
                cust_id,
                email,
                _customer_key(email, cust_id, id_raw),
                _cell(row, COL_PAYMENT),
                _cell(row, COL_DELIVERY),
                currency,
                float(total_raw) if _is_num(total_raw) else 0.0,
                _to_eur(total_raw, currency),
                int(status) if status.lstrip("-").isdigit() else None,
                _cell(row, COL_FLAG),
            ))
            n += 1
            if len(batch) >= 5000:
                c.executemany("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                batch = []
        if batch:
            c.executemany("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)

    c.execute("CREATE INDEX IF NOT EXISTS idx_order_date ON orders(order_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_year ON orders(year)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_custkey ON orders(customer_key)")
    # Bezpečný VIEW 'o' pre voľný SQL (orders_query) — BEZ osobných údajov
    # (vynecháva email a customer_key, ktorý obsahuje email).
    c.execute("DROP VIEW IF EXISTS o")
    c.execute("""
        CREATE VIEW o AS SELECT
            id, order_number, created_at, order_date, year, month,
            customer_id, payment, delivery, currency, total_raw, total_eur,
            status, flag
        FROM orders
    """)
    conn.commit()
    conn.close()
    return f"Naimportovaných {n} objednávok do {db_path}."


def _is_num(s):
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def import_items(items_csv=ITEMS_CSV, options_csv=OPTIONS_CSV, db_path=DB_PATH,
                 rebuild=True) -> str:
    """Naimportuje položky objednávok (+ názvy produktov z options) do `order_items`.

    Predpokladá, že `orders` tabuľka už existuje (kvôli mene/dátumu objednávky).
    Cena položky je v mene objednávky → prepočítame na EUR rovnakým kurzom.
    """
    if not os.path.exists(items_csv):
        return f"CSV neexistuje: {items_csv}"

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 1) Mapy z objednávok: mena (na prepočet) — dátum doťahujeme JOINom pri reporte.
    order_currency = {}
    for oid, cur in c.execute("SELECT id, currency FROM orders"):
        order_currency[oid] = cur

    # 2) Názvy produktov z options (kľúč 'slovak_name'): {item_id: názov}.
    names = {}
    if os.path.exists(options_csv):
        with open(options_csv, newline="", encoding="utf-8", errors="replace") as f:
            for r in csv.reader(f):
                if len(r) <= OPT_VAL:
                    continue
                if r[OPT_KEY].strip() == NAME_KEY:
                    iid = r[OPT_ITEM].strip()
                    if iid.isdigit():
                        names[int(iid)] = r[OPT_VAL].strip()

    # 3) Tabuľka položiek.
    if rebuild:
        c.execute("DROP TABLE IF EXISTS order_items")
    c.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            item_id      INTEGER PRIMARY KEY,
            order_id     INTEGER,
            product_id   INTEGER,
            product_name TEXT,
            qty          REAL,
            unit_price   REAL,      -- v mene objednávky
            line_eur     REAL       -- qty * unit_price prepočítané na EUR
        )
    """)

    n = 0
    with open(items_csv, newline="", encoding="utf-8", errors="replace") as f:
        batch = []
        for r in csv.reader(f):
            if len(r) <= IT_PRICE:
                continue
            iid = _cell(r, IT_ID)
            if not iid.isdigit():
                continue
            iid = int(iid)
            oid = _cell(r, IT_ORDER)
            oid = int(oid) if oid.isdigit() else None
            pid = _cell(r, IT_PRODUCT)
            pid = int(pid) if pid.isdigit() else None
            qty = float(_cell(r, IT_QTY)) if _is_num(_cell(r, IT_QTY)) else 0.0
            price = float(_cell(r, IT_PRICE)) if _is_num(_cell(r, IT_PRICE)) else 0.0
            cur = order_currency.get(oid, "EUR")
            rate = FX_PER_EUR.get((cur or "").strip().lower(), 1.0)
            line_eur = qty * price / rate
            batch.append((iid, oid, pid, names.get(iid, ""), qty, price, line_eur))
            n += 1
            if len(batch) >= 5000:
                c.executemany("INSERT OR REPLACE INTO order_items VALUES (?,?,?,?,?,?,?)", batch)
                batch = []
        if batch:
            c.executemany("INSERT OR REPLACE INTO order_items VALUES (?,?,?,?,?,?,?)", batch)

    c.execute("CREATE INDEX IF NOT EXISTS idx_oi_order ON order_items(order_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_oi_product ON order_items(product_id)")
    # VIEW 'oi' pre voľný SQL (položky nemajú osobné údaje, len konzistentný názov).
    c.execute("DROP VIEW IF EXISTS oi")
    c.execute("""
        CREATE VIEW oi AS SELECT
            item_id, order_id, product_id, product_name, qty, unit_price, line_eur
        FROM order_items
    """)
    conn.commit()
    conn.close()
    return f"Naimportovaných {n} položiek (s {len(names)} názvami produktov) do {db_path}."


# --- flexibilný report ----------------------------------------------------------

# Číselník status kódov objednávok — z BUXUS dropdownu (0-indexovaný; oddeľovač
# "------" zaberá pozíciu 11, preto chýbajú kódy 11/13/14/17). Potvrdené dátami:
# kód 4 = "zaplatená" tvorí 79,7 % objednávok = hlavný úspešný stav.
STATUS_LABELS = {
    0: "NOVÁ",
    1: "akcept. (predfaktúra)",
    2: "akcept. (dobierka)",
    3: "zrušená",
    4: "zaplatená",
    5: "čaká na faktúru",
    6: "čaká na expedíciu",
    7: "expedovaná",
    8: "dokončená",
    9: "rozpracovaná",
    10: "zaplatená (TatraPay)",
    12: "nedokončená",       # 12/15/16 — okrajové, na finálne potvrdenie číselníkom
    15: "nulové položky",
    16: "chyby v objednávke",
}
# Kódy, ktoré sa pri only_valid=True NErátajú do reálnych tržieb: zrušené,
# nedokončené, nulové, chybné a ešte nepotvrdené NOVÉ objednávky.
CANCELLED_STATUSES = {0, 3, 12, 15, 16}


def _status_label(st):
    """Vráti 'kód (názov)' ak názov poznáme, inak len kód."""
    name = STATUS_LABELS.get(st)
    return f"{st} ({name})" if name else str(st)


# Trh -> meny objednávok (Prodoshop predáva do SK/CZ/HU/RO). Slúži na izoláciu
# trhu pri prepojení s GA4 (každý trh má vlastnú GA4 property). SK = EUR (+ staré
# Sk/€), lebo z týchto trhov len SK platí eurom.
MARKET_CURRENCIES = {
    "SK": {"eur", "€", "sk", "skk"},
    "CZ": {"czk"},
    "HU": {"huf"},
    "RO": {"ron"},
}
# GA4 property ID jednotlivých trhov Prodoshopu (na cross-source porovnanie).
MARKET_GA4 = {"SK": "273129016", "HU": "333370282", "CZ": "333388671", "RO": "338899233"}


# Povolené metriky -> SQL agregát (bezpečný whitelist, žiadne user SQL).
_METRICS = {
    "orders": "COUNT(*)",
    "revenue": "SUM(total_eur)",
    "aov": "AVG(total_eur)",
    "customers": "COUNT(DISTINCT customer_key)",
}
# Povolené zoskupenia -> SQL stĺpec.
_GROUPS = {
    "year": "year",
    "month": "month",
    "payment": "payment",
    "delivery": "delivery",
    "status": "status",
    "currency": "currency",
}


def _where(start_date, end_date, status, only_valid=False, market=None, prefix=""):
    """Poskladá WHERE klauzulu. prefix napr. 'o.' pri JOINe. only_valid=True
    vynechá stornované statusy. market (SK/CZ/HU/RO) obmedzí na meny daného trhu."""
    where, params = [], []
    if start_date:
        where.append(f"{prefix}order_date >= ?"); params.append(start_date)
    if end_date:
        where.append(f"{prefix}order_date <= ?"); params.append(end_date)
    if status is not None:
        where.append(f"{prefix}status = ?"); params.append(int(status))
    if only_valid and CANCELLED_STATUSES:
        placeholders = ",".join("?" * len(CANCELLED_STATUSES))
        where.append(f"({prefix}status IS NULL OR {prefix}status NOT IN ({placeholders}))")
        params.extend(sorted(CANCELLED_STATUSES))
    if market:
        curs = MARKET_CURRENCIES.get(market.upper())
        if curs:
            ph = ",".join("?" * len(curs))
            where.append(f"LOWER({prefix}currency) IN ({ph})")
            params.extend(sorted(curs))
    return (" WHERE " + " AND ".join(where)) if where else "", params


def run_report(metrics=None, group_by=None, start_date=None, end_date=None,
               status=None, only_valid=False, market=None, limit=50, db_path=DB_PATH) -> str:
    """Flexibilný report nad objednávkami.

    metrics    = zoznam z {orders, revenue, aov, customers}. Default ['orders','revenue'].
    group_by   = voliteľné z {year, month, payment, delivery, status, currency}.
    start/end  = 'YYYY-MM-DD' filter na order_date.
    status     = filter na konkrétny status kód.
    only_valid = True vynechá stornované/nedokončené/chybné objednávky (reálne tržby).
    market     = SK/CZ/HU/RO — obmedzí na trh (podľa meny). SK = EUR.
    """
    if not os.path.exists(db_path):
        return "Databáza objednávok ešte neexistuje (spusti import z CSV)."

    metrics = [m for m in (metrics or ["orders", "revenue"]) if m in _METRICS]
    if not metrics:
        return f"Neznáme metriky. Dostupné: {', '.join(_METRICS)}."

    select_aggs = [f"{_METRICS[m]} AS {m}" for m in metrics]
    where_sql, params = _where(start_date, end_date, status, only_valid, market)
    grp_col = _GROUPS.get(group_by) if group_by else None

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    if grp_col:
        sql = (f"SELECT {grp_col}, " + ", ".join(select_aggs) +
               f" FROM orders{where_sql} GROUP BY {grp_col} ORDER BY {grp_col} LIMIT ?")
        c.execute(sql, params + [limit])
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "Žiadne dáta pre zadané parametre."
        head = f"Objednávky podľa {group_by}"
        if start_date or end_date:
            head += f" ({start_date or '...'} – {end_date or '...'})"
        out = [head + ":"]
        for r in rows:
            label = _status_label(r[0]) if group_by == "status" else r[0]
            parts = [_fmt(metrics[i], r[i + 1]) for i in range(len(metrics))]
            out.append(f"- {label}: " + ", ".join(parts))
        return "\n".join(out)

    sql = "SELECT " + ", ".join(select_aggs) + f" FROM orders{where_sql}"
    c.execute(sql, params)
    r = c.fetchone()
    conn.close()
    head = "Objednávky súhrn"
    if start_date or end_date:
        head += f" ({start_date or '...'} – {end_date or '...'})"
    parts = [_fmt(metrics[i], r[i]) for i in range(len(metrics))]
    return head + ": " + ", ".join(parts)


def customer_report(start_date=None, end_date=None, status=None, only_valid=False,
                    market=None, db_path=DB_PATH) -> str:
    """Analýza zákazníkov: noví vs vracajúci sa, miera návratnosti, tržby a CLV.

    Identita = email (fallback customer_id). Vracia LEN agregáty — žiadne emaily.
    only_valid = True ráta len reálne (nestornované) objednávky.
    market     = SK/CZ/HU/RO — obmedzí na trh.
    """
    if not os.path.exists(db_path):
        return "Databáza objednávok ešte neexistuje (spusti import z CSV)."

    where_sql, params = _where(start_date, end_date, status, only_valid, market)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # Na zákazníka: počet objednávok + suma.
    c.execute(
        "SELECT customer_key, COUNT(*) AS cnt, SUM(total_eur) AS rev "
        f"FROM orders{where_sql} GROUP BY customer_key", params)
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "Žiadne dáta pre zadané parametre."

    total_customers = len(rows)
    repeat = [r for r in rows if r[1] > 1]
    repeat_n = len(repeat)
    onetime_n = total_customers - repeat_n
    total_orders = sum(r[1] for r in rows)
    total_rev = sum(r[2] or 0 for r in rows)
    repeat_rev = sum(r[2] or 0 for r in repeat)
    repeat_rate = repeat_n / total_customers * 100 if total_customers else 0
    rev_per_cust = total_rev / total_customers if total_customers else 0
    orders_per_cust = total_orders / total_customers if total_customers else 0

    head = "Zákazníci"
    if start_date or end_date:
        head += f" ({start_date or '...'} – {end_date or '...'})"
    return "\n".join([
        head + ":",
        f"- unikátnych zákazníkov: {total_customers:,}",
        f"- z toho vracajúci sa (>1 obj.): {repeat_n:,} ({repeat_rate:.1f}%)",
        f"- jednorazoví: {onetime_n:,}",
        f"- priem. objednávok na zákazníka: {orders_per_cust:.2f}",
        f"- priem. tržba na zákazníka (CLV): {rev_per_cust:,.2f} EUR",
        f"- podiel tržieb od vracajúcich sa: "
        f"{(repeat_rev/total_rev*100 if total_rev else 0):.1f}% "
        f"({repeat_rev:,.0f} z {total_rev:,.0f} EUR)",
    ])


def products_report(metrics=None, start_date=None, end_date=None, status=None,
                    only_valid=False, market=None, sort_by="revenue", limit=20,
                    db_path=DB_PATH) -> str:
    """Top produkty podľa tržieb / predaného množstva / počtu objednávok.

    Spája order_items s orders (kvôli dátumu a statusu objednávky). Zoskupuje
    podľa product_id, názov berie z product_name. Default: top 20 podľa tržieb.

    metrics  = z {revenue (EUR), quantity (ks), orders (počet objednávok)}.
    sort_by  = podľa čoho zoradiť (jedna z metrík). Default 'revenue'.
    """
    if not os.path.exists(db_path):
        return "Databáza objednávok ešte neexistuje (spusti import z CSV)."
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # over, či tabuľka položiek existuje
    if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='order_items'").fetchone():
        conn.close()
        return "Položky objednávok ešte nie sú naimportované (spusti import položiek)."

    metric_sql = {
        "revenue": "SUM(oi.line_eur)",
        "quantity": "SUM(oi.qty)",
        "orders": "COUNT(DISTINCT oi.order_id)",
    }
    metrics = [m for m in (metrics or ["revenue", "quantity"]) if m in metric_sql]
    if not metrics:
        return f"Neznáme metriky. Dostupné: {', '.join(metric_sql)}."
    if sort_by not in metric_sql:
        sort_by = metrics[0]
    if sort_by not in metrics:
        metrics.append(sort_by)

    where, params = ["oi.product_id IS NOT NULL"], []
    if start_date:
        where.append("o.order_date >= ?"); params.append(start_date)
    if end_date:
        where.append("o.order_date <= ?"); params.append(end_date)
    if status is not None:
        where.append("o.status = ?"); params.append(int(status))
    if only_valid and CANCELLED_STATUSES:
        ph = ",".join("?" * len(CANCELLED_STATUSES))
        where.append(f"(o.status IS NULL OR o.status NOT IN ({ph}))")
        params.extend(sorted(CANCELLED_STATUSES))
    if market:
        curs = MARKET_CURRENCIES.get(market.upper())
        if curs:
            ph = ",".join("?" * len(curs))
            where.append(f"LOWER(o.currency) IN ({ph})")
            params.extend(sorted(curs))
    where_sql = " WHERE " + " AND ".join(where)

    aggs = ", ".join(f"{metric_sql[m]} AS {m}" for m in metrics)
    sql = (f"SELECT oi.product_id, MAX(NULLIF(oi.product_name,'')) AS name, {aggs} "
           f"FROM order_items oi JOIN orders o ON oi.order_id = o.id"
           f"{where_sql} GROUP BY oi.product_id "
           f"ORDER BY {sort_by} DESC LIMIT ?")
    c.execute(sql, params + [limit])
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "Žiadne dáta pre zadané parametre."

    head = f"Top {len(rows)} produktov podľa {sort_by}"
    if start_date or end_date:
        head += f" ({start_date or '...'} – {end_date or '...'})"
    out = [head + ":"]
    for r in rows:
        pid, name = r[0], r[1] or f"#{r[0]}"
        parts = [_fmt(metrics[i], r[i + 2]) for i in range(len(metrics))]
        out.append(f"- {name} (id {pid}): " + ", ".join(parts))
    return "\n".join(out)


def _fmt(metric, value):
    if value is None:
        value = 0
    if metric in ("orders", "customers"):
        return f"{metric}={int(value):,}"
    if metric == "quantity":
        return f"{metric}={float(value):,.0f}"
    return f"{metric}={float(value):,.2f}"


# --- voľný SQL „mozog" (read-only) ----------------------------------------------

import re as _re

# Zakázané operácie (čokoľvek okrem čítania) a prístup k osobným údajom.
_SQL_FORBIDDEN = _re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|"
    r"vacuum|reindex|truncate)\b", _re.I)
_SQL_PII = _re.compile(r"\b(email|customer_key)\b", _re.I)
_EMAIL_RE = _re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# Popis schémy do system promptu nástroja (čo má Claude k dispozícii).
SCHEMA_DOC = (
    "TABUĽKY (views) pre SQL:\n"
    "  o  = objednávky: id, order_number, created_at, order_date('YYYY-MM-DD'), "
    "year, month('YYYY-MM'), customer_id, payment, delivery, currency, "
    "total_raw, total_eur, status, flag\n"
    "  oi = položky: item_id, order_id(→o.id), product_id, product_name, qty, "
    "unit_price, line_eur\n"
    "KONVENCIE:\n"
    "  • Tržby VŽDY cez total_eur / line_eur (už prepočítané na EUR).\n"
    "  • Trh: SK = currency IN ('EUR','€','Sk','sk'); CZ='CZK'; HU='HUF'; RO='RON'.\n"
    "  • Reálne objednávky = status NOT IN (0,3,12,15,16).\n"
    "  • Statusy: 4=zaplatená(hlavný), 2=akcept.dobierka, 3=zrušená, 8=dokončená…\n"
    "  • Analýzu zákazníkov podľa emailu rob cez nástroj orders_customers "
    "(email v SQL nie je dostupný)."
)


def query(sql, max_rows=200, db_path=DB_PATH) -> str:
    """Spustí read-only SELECT nad orders.db a vráti výsledok ako text.

    Mantinely: len jeden SELECT/WITH príkaz, žiadne zápisové operácie, žiadny
    prístup k osobným údajom (email), limit riadkov + redakcia e-mailových reťazcov.
    """
    if not os.path.exists(db_path):
        return "Databáza objednávok ešte neexistuje (spusti import z CSV)."

    s = (sql or "").strip().rstrip(";").strip()
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return "Povolené sú len SELECT dotazy (read-only)."
    if ";" in s:
        return "Povolený je len jeden príkaz (žiadne ';')."
    if _SQL_FORBIDDEN.search(s):
        return "Dotaz obsahuje zakázanú operáciu — povolené je len čítanie (SELECT)."
    if _SQL_PII.search(s):
        return ("Prístup k osobným údajom (email) nie je povolený. Použi views 'o'/'oi', "
                "alebo na zákazníkov nástroj orders_customers.")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(s)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        conn.close()
    except Exception as e:
        return f"SQL chyba: {e}"

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    if not rows:
        return "Dotaz nevrátil žiadne riadky."

    def _cellfmt(v):
        if isinstance(v, str) and _EMAIL_RE.search(v):
            return "[skryté]"   # poistka proti úniku emailu
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    out = [" | ".join(cols)]
    for r in rows:
        out.append(" | ".join(_cellfmt(v) for v in r))
    if truncated:
        out.append(f"… (orezané na {max_rows} riadkov)")
    return "\n".join(out)


# --- lokálny test ----------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if "--import" in sys.argv:
        print(import_csv())
        print(import_items())
    print(products_report(metrics=["revenue", "quantity", "orders"], limit=15))

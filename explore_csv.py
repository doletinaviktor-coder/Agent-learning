"""
Dočasný profilovač surových BUXUS CSV exportov (bez hlavičky).

Cieľ: pochopiť štruktúru stĺpcov BEZ toho, aby sme do výstupu ťahali osobné
údaje (mená, emaily, adresy). Pre každý stĺpec uhádne typ a vypíše len bezpečné
agregáty: počty, min/max/sum pri číslach, rozsah pri dátumoch a zoznam hodnôt
LEN pri nízkopočetných enumoch (napr. spôsob platby). Voľný text (mená/adresy)
nikdy nevypisuje.
"""
import csv
import re
import sys
from collections import Counter

csv.field_size_limit(10_000_000)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")
INT = re.compile(r"^-?\d+$")
FLOAT = re.compile(r"^-?\d+\.\d+$")

SAMPLE = 20000  # koľko riadkov vzorkujeme na profil


def classify(v):
    if v == "":
        return "empty"
    if EMAIL.match(v):
        return "email"
    if DATETIME.match(v):
        return "datetime"
    if INT.match(v):
        return "int"
    if FLOAT.match(v):
        return "float"
    return "text"


def profile(path):
    print(f"\n{'='*60}\n{path}\n{'='*60}")
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        ncols = 0
        col_types = []      # list[Counter]
        col_distinct = []   # list[set] (capped)
        col_nummin = []
        col_nummax = []
        col_numsum = []
        col_datemin = []
        col_datemax = []
        total_rows = 0
        for row in reader:
            total_rows += 1
            if len(row) > ncols:
                # rozšír štruktúry na nový max počet stĺpcov
                for _ in range(len(row) - ncols):
                    col_types.append(Counter())
                    col_distinct.append(set())
                    col_nummin.append(None)
                    col_nummax.append(None)
                    col_numsum.append(0.0)
                    col_datemin.append(None)
                    col_datemax.append(None)
                ncols = len(row)
            if total_rows <= SAMPLE:
                for i, v in enumerate(row):
                    v = v.strip()
                    t = classify(v)
                    col_types[i][t] += 1
                    if len(col_distinct[i]) <= 30:
                        col_distinct[i].add(v)
                    if t in ("int", "float"):
                        fv = float(v)
                        col_numsum[i] += fv
                        if col_nummin[i] is None or fv < col_nummin[i]:
                            col_nummin[i] = fv
                        if col_nummax[i] is None or fv > col_nummax[i]:
                            col_nummax[i] = fv
                    elif t == "datetime":
                        if col_datemin[i] is None or v < col_datemin[i]:
                            col_datemin[i] = v
                        if col_datemax[i] is None or v > col_datemax[i]:
                            col_datemax[i] = v

    print(f"riadkov spolu: {total_rows}   stĺpcov: {ncols}   (profil zo vzorky {min(total_rows,SAMPLE)})\n")
    for i in range(ncols):
        types = col_types[i]
        dom = types.most_common(1)[0][0] if types else "empty"
        nonempty = sum(c for t, c in types.items() if t != "empty")
        empties = types.get("empty", 0)
        label = f"[{i:2}] dom={dom:8} nonempty={nonempty:6} empty={empties:6}"
        # bezpečný detail podľa typu
        if dom in ("int", "float"):
            label += f"  min={col_nummin[i]} max={col_nummax[i]} sum={col_numsum[i]:,.2f}"
        elif dom == "datetime":
            label += f"  range={col_datemin[i]} .. {col_datemax[i]}"
        elif dom == "email":
            label += "  (EMAIL — hodnoty skryté)"
        elif dom == "text":
            distinct = len(col_distinct[i])
            if distinct <= 15:
                # nízka kardinalita = enum, bezpečné ukázať
                vals = sorted(x for x in col_distinct[i] if x)
                label += f"  ENUM{distinct}: {vals}"
            else:
                label += "  (voľný text — hodnoty skryté)"
        print(label)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        profile(p)

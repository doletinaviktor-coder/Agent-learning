"""
Google Analytics 4 — Data API (čítanie reportov) + Admin API (discovery property).

Filozofia: namiesto desiatok úzkych nástrojov máme JEDEN flexibilný report
(run_report), kde si agent sám zvolí metriky, dimenzie, obdobie a YoY. Tým
vytiahne prakticky čokoľvek z GA4 — rovnaký prístup ako "GAQL" u Jožka.

Autentifikácia: service account JSON kľúč (GOOGLE_APPLICATION_CREDENTIALS v .env).
Service account email musí byť v GA4 pridaný ako Viewer na danú property.
"""

import os
import warnings
from datetime import date, timedelta

# Stíšime FutureWarning hlášky google knižníc o Python 3.9 (len šum v logu).
warnings.filterwarnings("ignore")

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.analytics.admin import AnalyticsAdminServiceClient


def list_properties() -> str:
    """Vylistuje GA4 property, ku ktorým má service account prístup (Admin API)."""
    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        return "GA4 nie je nakonfigurované (chýba kľúč v .env)."

    client = AnalyticsAdminServiceClient.from_service_account_file(key_path)
    lines = []
    for account in client.list_account_summaries():
        for prop in account.property_summaries:
            pid = prop.property.split("/")[-1]  # "properties/123" -> "123"
            lines.append(f"- {prop.display_name} (ID: {pid})")

    if not lines:
        return ("Service account nemá prístup k žiadnej GA4 property. "
                "Pridaj jeho email ako Viewer v GA4 → Admin → Property Access Management.")
    return "Dostupné GA4 property:\n" + "\n".join(lines)


# --- pomocníci na dátumy (kvôli YoY porovnaniu) ---------------------------------

def _resolve_date(token) -> date:
    """Preloží GA4 dátumový token na konkrétny dátum.
    Akceptuje 'YYYY-MM-DD', 'today', 'yesterday', 'NdaysAgo'."""
    t = str(token).strip().lower()
    today = date.today()
    if t == "today":
        return today
    if t == "yesterday":
        return today - timedelta(days=1)
    if t.endswith("daysago"):
        return today - timedelta(days=int(t.replace("daysago", "")))
    return date.fromisoformat(t)


def _prior_year(d: date) -> date:
    """Rovnaký deň pred rokom (s ošetrením 29.2.)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d - timedelta(days=365)


def _fmt_num(value: str) -> str:
    """Pekné formátovanie čísla (tisícky), nečíselné nechá tak."""
    try:
        f = float(value)
        return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"
    except (ValueError, TypeError):
        return str(value)


# --- hlavný report --------------------------------------------------------------

def run_report(metrics, dimensions=None, start_date="30daysAgo", end_date="today",
               property_id=None, limit=25, compare_yoy=False) -> str:
    """Flexibilný GA4 report.

    metrics      = zoznam GA4 metrík, napr. ['sessions', 'purchaseRevenue']
    dimensions   = voliteľný rozpad, napr. ['country'] alebo ['sessionDefaultChannelGroup']
    start/end    = obdobie ('YYYY-MM-DD' alebo 'NdaysAgo'/'today'/'yesterday')
    compare_yoy  = ak True, porovná s rovnakým obdobím pred rokom
    """
    property_id = property_id or os.environ.get("GA4_PROPERTY_ID")
    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        return "GA4 nie je nakonfigurované (chýba kľúč v .env)."
    if not property_id:
        return "Nemám property_id — najprv zavolaj ga4_list_properties."
    if not metrics:
        return "Treba aspoň jednu metriku (napr. sessions, purchaseRevenue)."

    client = BetaAnalyticsDataClient.from_service_account_file(key_path)
    mets = [Metric(name=m) for m in metrics]

    # --- YoY porovnanie (totals, bez dimenzií) ---
    if compare_yoy:
        cur_s, cur_e = _resolve_date(start_date), _resolve_date(end_date)
        prev_s, prev_e = _prior_year(cur_s), _prior_year(cur_e)
        request = RunReportRequest(
            property=f"properties/{property_id}",
            metrics=mets,
            date_ranges=[
                DateRange(start_date=cur_s.isoformat(), end_date=cur_e.isoformat(), name="current"),
                DateRange(start_date=prev_s.isoformat(), end_date=prev_e.isoformat(), name="previous_year"),
            ],
        )
        resp = client.run_report(request)
        met_names = [h.name for h in resp.metric_headers]
        data = {}  # {'current': {metric: val}, 'previous_year': {...}}
        for row in resp.rows:
            rng = row.dimension_values[0].value if row.dimension_values else "current"
            data[rng] = {met_names[i]: row.metric_values[i].value for i in range(len(met_names))}

        cur, prev = data.get("current", {}), data.get("previous_year", {})
        lines = [f"GA4 YoY ({cur_s} – {cur_e} vs {prev_s} – {prev_e}):"]
        for m in metrics:
            c = float(cur.get(m, 0) or 0)
            p = float(prev.get(m, 0) or 0)
            change = f"{(c - p) / p * 100:+.1f}%" if p else "n/a"
            lines.append(f"- {m}: {_fmt_num(c)} vs {_fmt_num(p)} ({change} YoY)")
        return "\n".join(lines)

    # --- bežný report za jedno obdobie ---
    dims = [Dimension(name=d) for d in (dimensions or [])]
    request = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=mets,
        dimensions=dims,
        date_ranges=[DateRange(start_date=str(start_date), end_date=str(end_date))],
        limit=limit,
    )
    resp = client.run_report(request)
    if not resp.rows:
        return "Žiadne dáta pre zadané parametre."

    met_names = [h.name for h in resp.metric_headers]
    dim_names = [h.name for h in resp.dimension_headers]
    out = [f"GA4 {start_date} – {end_date}:"]
    for row in resp.rows:
        dpart = ", ".join(f"{dim_names[i]}={row.dimension_values[i].value}"
                          for i in range(len(dim_names)))
        mpart = ", ".join(f"{met_names[i]}={_fmt_num(row.metric_values[i].value)}"
                          for i in range(len(met_names)))
        out.append(f"- {dpart + ' | ' if dpart else ''}{mpart}")
    return "\n".join(out)

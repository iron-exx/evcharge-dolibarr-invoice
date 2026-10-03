#!/usr/bin/env python3
"""
Ingress Web-Server — Wallbox Dolibarr Addon

Seiten:
  GET  /              Manuelle Session-Erfassung + letzte Sessions
  POST /              Speichert neue manuelle Session
  POST /transmit      Löst sofortige Übertragung an Dolibarr aus
  GET  /history       Monatliche Verlaufsansicht
  GET  /export        CSV-Export für einen Monat
"""
import asyncio
import csv
import io
import logging
import os
import calendar
import html
import sqlite3
from datetime import datetime, timedelta

from aiohttp import web

from admin import validate
from admin import web as admin_web
from ocpp_server.id_tags import normalize_id_tag

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Design-System — ExpenseCharge (dark slate / green-teal)
# ---------------------------------------------------------------------------

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  /* Markenpalette systemwerk, abgelesen an www.systemwerk.de — und zwar an dem,
     was die Seite tatsächlich ANWENDET, nicht an der Preset-Liste des Themes:
       body { background: #ffffff; color: #01171F }
       weiße Flächen 43x · Orange als Flächenfarbe 15x · hellgrau #F2F6F6 13x
     Also hell: Weiß und Hellgrau als Grund, Petrol-Schwarz als Text, Orange
     als Signaturfarbe, Cyan für alles Interaktive.
     Statusfarben sind für Text auf Weiß abgedunkelt — das helle Orange der
     Marke erreicht als Textfarbe keinen ausreichenden Kontrast. */
  --bg:       #F8F9F9; --surface:  #FFFFFF; --surface2: #F2F6F6;
  --border:   #E3E7E9; --border-l: #CFD6D9;
  --text:     #01171F; --muted:    #545A5B; --dim:      #7C8B91;
  /* Interaktiv-Cyan: #1B7EAC traegt dunklen Text mit 4.54:1; Buttons nutzen
     das dunklere #157399, damit WEISSE Schrift darauf 5.32:1 erreicht.
     Orange bleibt bei #F19021 — aber nur als FLAECHE mit Petrol-Text (7.65:1),
     genau wie auf systemwerk.de. Als Schriftfarbe auf Weiss schafft es
     keinen Wert ueber 2.4:1, dafuer gibt es --accent-t. */
  --primary:  #1B7EAC; --primary-d:#157399; --primary-i:#FFFFFF;
  --accent:   #F19021; --accent-t: #AF630B; --accent-i: #01171F;
  --success:  #008756; --warn:     #AF630B; --error:    #B32424;
}
html, body {
  background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 14px; line-height: 1.5; min-height: 100vh;
}
/* ── Header ── */
.hdr {
  background: var(--surface);
  border-bottom: 2px solid var(--accent);
  box-shadow: 0 1px 3px rgba(1,23,31,.05);
  padding: 0 20px; height: 54px;
  display: flex; align-items: center; justify-content: space-between;
  position: sticky; top: 0; z-index: 50;
}
.hdr-left { display: flex; align-items: center; gap: 11px; }
.hdr-logo {
  width: 32px; height: 32px;
  background: linear-gradient(140deg, #22A2DC 0%, #0EB1D2 42%, #F19021 100%);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.hdr-name { font-size: 15px; font-weight: 700; letter-spacing: -.01em; }
.hdr-sub  { font-size: 11px; color: var(--muted); }
.hdr-right { display: flex; align-items: center; gap: 14px; min-width: 0; }
.acct { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); margin: 0; }
.acct a, .acct button { background: none; border: none; padding: 0; font: inherit; font-weight: 600;
  color: var(--primary-d); cursor: pointer; text-decoration: none; }
@media (max-width: 560px) { .hdr { padding: 0 14px; } .hdr-sub { display: none; } .nav { padding: 0 8px; }
  .nav a { padding: 11px 11px; } }
.chip { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 500; color: var(--muted); }
.dot  { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
.dot-ok  { background: var(--success); box-shadow: 0 0 0 3px rgba(34,197,94,.2); }
.dot-err { background: var(--error);   box-shadow: 0 0 0 3px rgba(239,68,68,.2); }
/* ── Diagramm ── */
.chart { margin: 4px 0 18px; }
.chart-svg { width: 100%; height: 190px; display: block; overflow: visible; }
.chart-svg .bar rect { transition: opacity .12s; }
.chart-svg .bar:hover rect { opacity: .72; }
.chart-svg .bar { cursor: default; }
.chart-legend {
  display: flex; gap: 16px; flex-wrap: wrap;
  margin-top: 8px; font-size: 11.5px; color: var(--muted);
}
.chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
.chart-legend i { width: 9px; height: 9px; border-radius: 2px; flex-shrink: 0; }
.chart-empty {
  padding: 26px 16px; margin: 4px 0 16px; text-align: center;
  font-size: 13px; color: var(--dim);
  border: 1px dashed var(--border); border-radius: 9px;
}
/* ── Hero + Streifen ── */
.hero-val {
  font-size: 42px; font-weight: 800; line-height: 1; letter-spacing: -.025em;
  font-variant-numeric: tabular-nums; color: var(--text);
  /* Oranger Balken links: die Signaturfarbe als Flaeche, nicht als Schrift —
     als Text erreicht sie auf Weiss nur 2.4:1. */
  padding-left: 13px; border-left: 4px solid var(--accent);
}
.hero-unit { font-size: 15px; font-weight: 600; color: var(--muted); margin-left: 7px; }
.hero-lbl { font-size: 12px; color: var(--muted); margin-top: 5px; }
.hero-side { font-size: 12px; color: var(--muted); text-align: right; line-height: 1.7; }
.hero-side strong { color: var(--text); font-variant-numeric: tabular-nums; }
.kpi-warn, .hero-side .kpi-warn strong { color: var(--warn); }
.spark-wrap { margin-top: 4px; }
.spark { width: 100%; height: 44px; display: block; }
.spark .spark-bar rect { transition: opacity .12s; }
.spark .spark-bar:hover rect { opacity: .7; }
.spark-foot {
  display: flex; justify-content: space-between;
  font-size: 10.5px; color: var(--dim); margin-top: 5px;
}
.spark-empty { font-size: 12.5px; color: var(--dim); padding: 10px 0 2px; }
/* ── System-Tabelle ── */
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.row-changed td:first-child { box-shadow: inset 2px 0 0 var(--primary); padding-left: 9px; }
.tbl-wrap { overflow-x: auto; }
/* ── Kennzahlen ── */
.kpis {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
  background: var(--border); border: 1px solid var(--border);
  border-radius: 9px; overflow: hidden; margin-bottom: 16px;
}
.kpis-3 { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 560px) { .kpis, .kpis-3 { grid-template-columns: repeat(2, 1fr); } }
.kpi { background: var(--surface2); padding: 12px 14px; }
.kpi-lbl {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: var(--muted); margin-bottom: 4px;
}
.kpi-val {
  font-size: 22px; font-weight: 700; line-height: 1.15; letter-spacing: -.015em;
  font-variant-numeric: tabular-nums;
}
.kpi-unit { font-size: 11px; font-weight: 600; color: var(--muted); margin-left: 5px; }
.kpi-sub { font-size: 10.5px; color: var(--dim); margin-top: 2px; }
/* ── Nav tabs ── */
.nav {
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 0 20px; display: flex; gap: 2px;
  overflow-x: auto; scrollbar-width: none;   /* Handy: Reiter wischen statt Seite verbreitern */
}
.nav::-webkit-scrollbar { display: none; }
.nav a { flex-shrink: 0; white-space: nowrap; }
.nav a {
  display: inline-flex; align-items: center; gap: 7px; padding: 11px 15px;
  text-decoration: none; font-size: 13px; font-weight: 600; color: var(--muted);
  border-bottom: 2px solid transparent; transition: all .15s;
}
.nav a.active { color: var(--primary); border-bottom-color: var(--primary); }
.nav a.active::after {
  content: ""; width: 5px; height: 5px; border-radius: 50%;
  background: var(--accent); margin-left: 2px;
}
.nav a:hover:not(.active) { color: var(--text); }
/* ── Layout ── */
.page { max-width: 820px; margin: 22px auto; padding: 0 16px; }
/* ── Stats grid ── */
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; margin-bottom: 13px; }
@media (max-width: 560px) { .stats { grid-template-columns: 1fr; } }
.stat {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 9px; padding: 15px 17px;
}
.stat-lbl {
  font-size: 10.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: var(--muted);
  display: flex; align-items: center; gap: 6px; margin-bottom: 7px;
}
.stat-val { font-size: 24px; font-weight: 700; letter-spacing: -.02em; }
.stat-sub { font-size: 11px; color: var(--dim); margin-top: 3px; }
.stat-blue { border-left: 3px solid var(--primary); }
.stat-ok   { border-left: 3px solid var(--success); }
.stat-warn { border-left: 3px solid var(--warn); }
/* ── Card ── */
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px; margin-bottom: 14px;
  box-shadow: 0 1px 2px rgba(1,23,31,.04);
}
.card-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: var(--muted);
  display: flex; align-items: center; gap: 7px; margin-bottom: 16px;
}
/* ── Form ── */
.flabel {
  display: block; font-size: 11px; font-weight: 700; color: var(--muted);
  text-transform: uppercase; letter-spacing: .05em; margin: 14px 0 5px;
}
.flabel:first-of-type { margin-top: 0; }
input, select {
  width: 100%; padding: 10px 12px;
  background: var(--bg); border: 1.5px solid var(--border);
  border-radius: 7px; font-size: 14px; color: var(--text);
  outline: none; transition: border-color .15s; -webkit-appearance: none;
}
input:focus, select:focus { border-color: var(--primary); }
input::placeholder { color: var(--dim); }
select option { background: var(--surface); color: var(--text); }
.kwh-wrap { position: relative; }
.kwh-wrap input { padding-right: 44px; }
.kwh-unit { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); color: var(--dim); font-size: 13px; }
/* ── Buttons ── */
.btn-save {
  display: flex; align-items: center; justify-content: center; gap: 7px;
  width: 100%; padding: 11px; margin-top: 17px;
  background: var(--primary-d); color: var(--primary-i);
  border: none; border-radius: 8px; letter-spacing: .01em;
  font-size: 14px; font-weight: 600; cursor: pointer; transition: background .15s;
}
.btn-save:hover { background: #116485; }
.btn-transmit {
  display: flex; align-items: center; justify-content: center; gap: 7px;
  width: 100%; padding: 9px; margin-top: 8px;
  background: transparent; color: var(--muted);
  border: 1.5px solid var(--border); border-radius: 7px;
  font-size: 13px; font-weight: 600; cursor: pointer; transition: all .15s;
}
.btn-transmit:hover { color: var(--accent); border-color: var(--accent);
                      background: rgba(241,144,33,.07); }
.btn-dl {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 6px 11px; background: transparent;
  border: 1px solid var(--border); border-radius: 6px;
  color: var(--muted); font-size: 12px; font-weight: 600;
  text-decoration: none; transition: all .15s;
}
.btn-dl:hover { color: var(--success); border-color: var(--success); }
/* ── Messages ── */
.msg { padding: 11px 14px; border-radius: 7px; font-size: 13px; line-height: 1.5; margin-bottom: 12px; }
.ok  { background: rgba(0,121,77,.08);   color: var(--success); border: 1px solid rgba(0,121,77,.22); }
.err { background: rgba(179,36,36,.07);  color: var(--error); border: 1px solid rgba(179,36,36,.2);  }
.warn{ background: rgba(241,144,33,.12); color: var(--warn); border: 1px solid rgba(241,144,33,.35); }
/* ── Session rows ── */
.s-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 9px 0; border-bottom: 1px solid var(--border);
}
.s-row:last-child { border-bottom: none; }
.s-kwh  { font-size: 15px; font-weight: 700; color: var(--primary); }
.s-meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
/* ── Badges ── */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 20px;
  font-size: 11px; font-weight: 700; white-space: nowrap;
}
.b-ok   { background: rgba(0,121,77,.09);    color: var(--success); }
.b-pend { background: rgba(241,144,33,.16);  color: var(--warn); }
.b-disc { background: rgba(124,139,145,.12); color: #596368; }
.b-inc  { background: rgba(241,144,33,.09);  color: var(--warn); }
/* ── Table ── */
.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  padding: 8px 12px; text-align: left;
  font-size: 10.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: var(--muted);
  border-bottom: 1px solid var(--border); white-space: nowrap;
}
tbody tr { border-bottom: 1px solid var(--border); transition: background .1s; }
tbody tr:hover { background: var(--surface2); }
tbody tr:last-child { border-bottom: none; }
td { padding: 9px 12px; vertical-align: middle; }
.td-dim  { color: var(--dim); font-size: 11px; }
.td-bold { font-weight: 700; color: var(--primary); }
.total-row td { border-top: 2px solid var(--border); font-weight: 700; background: var(--surface2); }
/* ── Month tabs ── */
.month-tabs { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 13px; }
.m-tab {
  padding: 5px 12px; border-radius: 20px;
  font-size: 12px; font-weight: 600; text-decoration: none;
  background: var(--surface2); color: var(--muted);
  border: 1px solid var(--border); transition: all .15s;
}
.m-tab.active  { background: var(--primary); color: #fff; border-color: var(--primary); }
.m-tab:hover:not(.active) { color: var(--text); }
/* ── Misc ── */
.empty { text-align: center; padding: 32px 16px; color: var(--muted); font-size: 13px; }
#live-card { display: none; }
.live-item {
  border-left: 3px solid var(--primary); padding: 10px 14px;
  background: var(--surface2); border-radius: 0 6px 6px 0; margin-bottom: 8px;
}
.live-item:last-child { margin-bottom: 0; }
"""

# ---------------------------------------------------------------------------
# Inline SVG icons (no external deps)
# ---------------------------------------------------------------------------
_ICO_BOLT = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>'
)
_ICO_HIST = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>'
)
_ICO_UP = (
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/>'
    '<path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/></svg>'
)
_ICO_DL = (
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="8 17 12 21 16 17"/><line x1="12" y1="12" x2="12" y2="21"/>'
    '<path d="M20.88 18.09A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/></svg>'
)
_ICO_CHECK = (
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="20 6 9 17 4 12"/></svg>'
)
_ICO_GEAR = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="3"/>'
    '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 '
    '1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 '
    '19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 '
    '15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 '
    '0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6 1.65 1.65 0 0 0 '
    '10 3.09V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 '
    '2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.14.31.22.65.22 1v.09c0 .36-.08.7-.22 1z"/>'
    '</svg>'
)
_ICO_CARD = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>'
    '<path d="M6 15h4"/></svg>'
)
_ICO_BOLT_WHITE = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff"'
    ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>'
)


# ---------------------------------------------------------------------------
# HTML scaffolding
# ---------------------------------------------------------------------------

def note_transmit_result(api_state, result) -> None:
    """Letzten Übertragungslauf für die Oberfläche merken."""
    if api_state is not None:
        api_state['last_transmit'] = {
            'time': datetime.now().isoformat(timespec='seconds'),
            'transmitted': result.get('transmitted', 0), 'failed': result.get('failed', 0),
            'error': (result.get('errors') or [''])[0]}


def _base(active, content, base_href=''):
    base_tag = f'  <base href="{base_href}/">\n' if base_href else ''
    nav_form = (
        f'<a href="./" class="{"active" if active == "form" else ""}">'
        f'{_ICO_BOLT} Erfassen</a>'
    )
    nav_hist = (
        f'<a href="history" class="{"active" if active == "history" else ""}">'
        f'{_ICO_HIST} Verlauf</a>'
    )
    nav_tags = (
        f'<a href="tags" class="{"active" if active == "tags" else ""}">'
        f'{_ICO_CARD} Karten</a>'
    )
    nav_sys = (
        f'<a href="system" class="{"active" if active == "system" else ""}">'
        f'{_ICO_GEAR} System</a>'
    )
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{base_tag}  <title>ExpenseCharge</title>
  <style>{_CSS}</style>
</head>
<body>
<header class="hdr">
  <div class="hdr-left">
    <div class="hdr-logo">{_ICO_BOLT_WHITE}</div>
    <div>
      <div class="hdr-name">ExpenseCharge</div>
      <div class="hdr-sub">Ladevorgänge · Spesen · Abgerechnet</div>
    </div>
  </div>
  <div class="hdr-right">
  <!--ec-account-->
  <div class="chip">
    <span class="dot" id="conn-dot"></span>
    <span id="conn-lbl">—</span>
  </div>
  </div>
</header>
<nav class="nav">{nav_form}{nav_hist}{nav_tags}{nav_sys}<!--ec-nav-extra--></nav>
<div class="page">{content}</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# DB-Hilfsfunktionen
# ---------------------------------------------------------------------------

def _db_month(db_path, year, month):
    """Alle Sessions eines Monats aus SQLite"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, rfid_hash, wallbox_id, start_time, end_time,
               total_kwh, status, transmitted_at
        FROM sessions
        WHERE strftime('%Y', start_time) = ?
          AND strftime('%m', start_time) = ?
        ORDER BY start_time DESC
    """, (str(year), str(month).zfill(2)))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _db_months(db_path):
    """Verfügbare Monate (max. 12) absteigend"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT strftime('%Y', start_time) AS y,
                        strftime('%m', start_time) AS m
        FROM sessions
        WHERE start_time IS NOT NULL
        ORDER BY y DESC, m DESC
        LIMIT 12
    """)
    rows = [(int(y), int(m)) for y, m in cur.fetchall()]
    conn.close()
    return rows


def _month_name(month):
    names = ['Jan','Feb','Mär','Apr','Mai','Jun','Jul','Aug','Sep','Okt','Nov','Dez']
    return names[month - 1]


def _charge_points_out(api_state):
    """OCPP-Live-Zustand je Wallbox für live.json (leer im HA-Sensor-Betrieb)."""
    out = []
    for cp_id, st in sorted(((api_state or {}).get('charge_points') or {}).items()):
        connectors = st.get('connectors') or {}
        main_conn = connectors.get('1') or next(iter(connectors.values()), {})
        out.append({
            'id':                  cp_id,
            'wallbox_id':          st.get('wallbox_id'),
            'connected':           bool(st.get('connected')),
            'vendor':              st.get('vendor'),
            'model':               st.get('model'),
            'status':              main_conn.get('status'),
            'energy_kwh':          main_conn.get('energy_kwh'),
            'last_seen':           st.get('last_seen'),
            'last_rejected_id_tag': st.get('last_rejected_id_tag'),
        })
    return out


def _db_active_sessions(db_path):
    """Alle laufenden Sessions (status='active') aus SQLite"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, rfid_hash, wallbox_id, start_time, start_energy_kwh,
               charge_point_id, last_meter_kwh
        FROM sessions
        WHERE status = 'active'
        ORDER BY start_time ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _db_recent(db_path, days=14):
    """Abgeschlossene Sessions der letzten Tage — Grundlage des Tagesstreifens."""
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT start_time, total_kwh, status FROM sessions "
            "WHERE start_time >= ? AND status IN ('completed','private')",
            (since,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _db_stats_month(db_path):
    """Statistiken für den aktuellen Monat"""
    now = datetime.now()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(
                CASE WHEN status NOT IN ('active','discarded') THEN COALESCE(total_kwh, 0)
                     ELSE 0 END), 0) AS kwh,
            COALESCE(SUM(
                CASE WHEN status = 'private' THEN COALESCE(total_kwh, 0)
                     ELSE 0 END), 0) AS private_kwh,
            -- 'private' wird absichtlich NIE übertragen (transmitted_at bleibt
            -- NULL) und darf daher nicht als ausstehend gelten — sonst stünde
            -- dauerhaft eine Warnung da, die sich nie auflösen lässt.
            SUM(CASE WHEN transmitted_at IS NULL
                      AND status NOT IN ('active','discarded','incomplete','private') THEN 1
                     ELSE 0 END) AS pending
        FROM sessions
        WHERE strftime('%Y', start_time) = ?
          AND strftime('%m', start_time) = ?
          AND status != 'active'
    """, (str(now.year), str(now.month).zfill(2)))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'total': row[0] or 0, 'kwh': float(row[1] or 0.0),
                'private_kwh': float(row[2] or 0.0), 'pending': row[3] or 0}
    return {'total': 0, 'kwh': 0.0, 'private_kwh': 0.0, 'pending': 0}


# ---------------------------------------------------------------------------
# Seiten-Builder
# ---------------------------------------------------------------------------

def _build_form_page(session_manager, config, message_html='', base_href='', api_state=None):
    today     = datetime.now().date().isoformat()
    now       = datetime.now()

    # Stats des laufenden Monats
    try:
        stats = _db_stats_month(session_manager.db_path)
    except Exception:
        stats = {'total': 0, 'kwh': 0.0, 'private_kwh': 0.0, 'pending': 0}

    month_lbl = f'{_month_name(now.month)} {now.year}'
    warn_cls  = 'stat-warn' if stats['pending'] > 0 else 'stat-ok'
    try:
        trend_days = int((config or {}).get('trend_days') or 14)
        recent = _db_recent(session_manager.db_path, days=trend_days)
    except Exception:
        recent = []
    trend_html = _build_trend_strip(recent, days=int((config or {}).get('trend_days') or 14))

    billed_kwh = max(0.0, stats['kwh'] - stats.get('private_kwh', 0.0))
    pending_cls = 'kpi-warn' if stats['pending'] > 0 else ''
    pending_sub = ('wird beim nächsten Durchlauf übertragen'
                   if stats['pending'] > 0 else 'alles übertragen')

    stats_html = f"""
<div class="card">
  <div style="display:flex;align-items:baseline;justify-content:space-between;
              gap:12px;flex-wrap:wrap;margin-bottom:12px">
    <div>
      <div class="hero-val">{billed_kwh:.1f}<span class="hero-unit">kWh</span></div>
      <div class="hero-lbl">abgerechnet im {month_lbl}</div>
    </div>
    <div class="hero-side">
      <div><strong>{stats['total']}</strong> Ladevorgänge</div>
      <div class="{pending_cls}"><strong>{stats['pending']}</strong> ausstehend</div>
    </div>
  </div>
  {trend_html}
</div>

<div class="kpis kpis-3">
  <div class="kpi">
    <div class="kpi-lbl">{_ICO_UP} Energie gesamt</div>
    <div class="kpi-val">{stats['kwh']:.1f}<span class="kpi-unit">kWh</span></div>
    <div class="kpi-sub">inklusive privater Ladungen</div>
  </div>
  <div class="kpi">
    <div class="kpi-lbl">Privat</div>
    <div class="kpi-val">{stats.get('private_kwh', 0.0):.1f}<span class="kpi-unit">kWh</span></div>
    <div class="kpi-sub">bleibt lokal</div>
  </div>
  <div class="kpi">
    <div class="kpi-lbl">{_ICO_HIST} Ausstehend</div>
    <div class="kpi-val {pending_cls}">{stats['pending']}</div>
    <div class="kpi-sub">{pending_sub}</div>
  </div>
</div>"""

    # Mitarbeiter-Optionen — aus Dolibarr geladen (Name statt rohem RFID-Code).
    # SEC-01: employees.php liefert nie den rfid_hash, nur login + Name.
    employees = []
    client = api_state.get('client') if api_state else None
    if client:
        try:
            employees = client.list_employees()
        except Exception:
            employees = []

    if employees:
        rfid_opts = '\n'.join(
            f'<option value="{e["login"]}">{e["name"] or e["login"]}</option>'
            for e in employees
        )
    else:
        rfid_opts = '<option value="">— keine Mitarbeiter mit RFID-Tag in Dolibarr gefunden —</option>'

    # Letzte 5 Sessions
    rows_html = ''
    try:
        rows = session_manager.get_completed_sessions(limit=5)
        for s in rows:
            kwh    = s.get('total_kwh') or 0
            date   = (s.get('start_time') or '')[:10]
            rid    = (s.get('rfid_hash') or '')[:8]
            manual = ' · manuell' if (s.get('start_time') or '').endswith('T12:00:00') else ''
            st     = (s.get('status') or '').lower()
            if st == 'discarded':
                tag = '<span class="badge b-disc">⊘ verworfen</span>'
            elif st == 'incomplete':
                tag = '<span class="badge b-inc">⚠ unvollst.</span>'
            elif s.get('transmitted_at'):
                tag = f'<span class="badge b-ok">{_ICO_CHECK} übertragen</span>'
            else:
                tag = '<span class="badge b-pend">ausstehend</span>'
            rows_html += (
                f'<div class="s-row">'
                f'<span>'
                f'<div class="s-kwh">{kwh:.3f} kWh</div>'
                f'<div class="s-meta">{date} · {rid}…{manual}</div>'
                f'</span>'
                f'{tag}</div>'
            )
    except Exception:
        pass

    sessions_block = (
        f'<div class="card">'
        f'<div class="card-title">{_ICO_HIST} Letzte Sessions</div>'
        f'{rows_html}</div>'
    ) if rows_html else ''

    # Live-Karte (JS-gesteuert)
    live_block = """
<div class="card" id="live-card">
  <div class="card-title">
    <span id="live-dot" style="width:8px;height:8px;border-radius:50%;
          background:var(--muted);display:inline-block;flex-shrink:0"></span>
    Aktiver Ladevorgang
  </div>
  <div id="live-banner" style="font-size:12.5px;color:var(--muted);margin-bottom:10px"></div>
  <div id="live-content"></div>
</div>"""

    js_polling = """
<script>
(function(){
  function fmtDuration(s){
    s=Math.max(0,Math.floor(s));
    var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;
    return (h?h+'h ':'')+(m<10?'0'+m:m)+'m '+(sec<10?'0'+sec:sec)+'s';
  }
  function esc(t){
    return (t||'').replace(/[&<>"']/g,function(c){
      return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function render(data){
    var card=document.getElementById('live-card');
    if(!card) return;
    var sessions=data.sessions||[];
    var sensor=data.sensor||{};
    var hasSessions=sessions.length>0;
    var hasSensor=sensor.current_energy!==null && sensor.current_energy!==undefined;
    // OCPP-Betrieb: Wallbox-Liste statt HA-Sensor
    var cps=data.charge_points||[];
    if(cps.length){ hasSensor=true; }

    // Verbindungs-Status im Header
    var cDot=document.getElementById('conn-dot');
    var cLbl=document.getElementById('conn-lbl');
    if(cDot && cLbl){
      if(hasSensor){
        cDot.className='dot dot-ok'; cLbl.textContent='Verbunden';
      } else {
        cDot.className='dot'; cLbl.textContent='Kein Sensor';
      }
    }

    if(!hasSessions && !hasSensor){ card.style.display='none'; return; }
    card.style.display='block';

    var state=sensor.wallbox_state||(cps.length?(cps[0].status||''):'');
    var sl=state.toLowerCase();
    var stateColor='var(--muted)';
    if(sl.indexOf('charging')>=0 && sl.indexOf('stopped')<0) stateColor='var(--success)';
    else if(['faulted','unavailable','stopped'].some(function(k){return sl.indexOf(k)>=0;}))
      stateColor='var(--error)';
    else if(state) stateColor='var(--warn)';

    document.getElementById('live-dot').style.background=stateColor;

    var banner='';
    if(cps.length){
      banner=cps.map(function(c){
        var st=c.connected?(c.status||'verbunden'):'getrennt';
        var col=!c.connected?'var(--error)'
          :(String(c.status||'').toLowerCase()==='charging'?'var(--success)':'var(--warn)');
        var kwh=(c.energy_kwh!==null && c.energy_kwh!==undefined)?c.energy_kwh.toFixed(3)+' kWh':'\u2014';
        var rej=c.last_rejected_id_tag
          ? '<div style="font-size:11px;color:var(--error)">Abgelehnte Karte: <code>'
            +esc(c.last_rejected_id_tag)+'</code> \u2014 in rfid_whitelist und Dolibarr eintragen</div>'
          : '';
        return '<div>'+esc(c.wallbox_id||c.id)+' \u00b7 Z\u00e4hler: <strong>'+kwh+'</strong>'
          +'<span style="background:'+col+';color:var(--accent-i);padding:1px 7px;border-radius:3px;'
          +'font-size:11px;font-weight:700;margin-left:6px">'+esc(st)+'</span>'
          +'<span style="float:right;color:var(--dim);font-size:11px">'+esc(c.last_seen||'')+'</span>'
          +rej+'</div>';
      }).join('');
    } else if(hasSensor){
      var chip=state
        ? '<span style="background:'+stateColor+';color:var(--accent-i);padding:1px 7px;'
          +'border-radius:3px;font-size:11px;font-weight:700;margin-left:6px">'
          +esc(state)+'</span>'
        : '';
      banner='Zähler: <strong>'+sensor.current_energy.toFixed(3)+' kWh</strong>'+chip
        +'<span style="float:right;color:var(--dim);font-size:11px">'
        +(sensor.last_update||'')+'</span>';
    } else {
      banner='<span style="color:var(--muted)">Kein Zähler-Wert vom HA-Sensor</span>';
    }
    document.getElementById('live-banner').innerHTML=banner;

    var html='';
    if(hasSessions){
      sessions.forEach(function(s){
        var kwhStr=(s.current_kwh!==null && s.current_kwh!==undefined)
          ? s.current_kwh.toFixed(3)+' kWh' : '—';
        html+='<div class="live-item">'
          +'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
          +'<div>'
          +'<div style="font-size:11px;color:var(--muted)">#'+s.id+' · '+esc(s.wallbox_id)+'</div>'
          +'<code style="font-size:12px;color:var(--muted)">'+esc(s.rfid_prefix)+'</code>'
          +'</div>'
          +'<div style="font-size:18px;font-weight:700;color:var(--primary)">'+kwhStr+'</div>'
          +'</div>'
          +'<div style="font-size:11.5px;color:var(--dim)">'
          +'Start: '+esc(s.start_time_fmt)+' · Dauer: '+fmtDuration(s.elapsed_seconds)
          +'</div></div>';
      });
    } else if(hasSensor){
      html='<div style="color:var(--muted);font-size:13px;text-align:center;padding:8px">'
        +'Kein aktiver Ladevorgang.</div>';
    }
    document.getElementById('live-content').innerHTML=html;
  }
  function poll(){
    fetch('live.json',{cache:'no-store'})
      .then(function(r){return r.json();})
      .then(render)
      .catch(function(){/* Netz weg — nächster Poll */});
  }
  poll();
  setInterval(poll, 5000);
})();
</script>"""

    content = f"""
{message_html}
{stats_html}
{live_block}
<div class="card">
  <div class="card-title">{_ICO_BOLT} Manueller Ladevorgang</div>
  <form method="POST" action="./">
    <label class="flabel">Mitarbeiter</label>
    <select name="employee_login" required>{rfid_opts}</select>
    <label class="flabel">Geladene Energie</label>
    <div class="kwh-wrap">
      <input type="number" name="kwh" min="0.001" step="0.001"
             placeholder="z.B. 12.500" required>
      <span class="kwh-unit">kWh</span>
    </div>
    <label class="flabel">Datum</label>
    <input type="date" name="date" value="{today}" required>
    <button type="submit" class="btn-save">{_ICO_BOLT} Ladevorgang speichern</button>
  </form>
  <form method="POST" action="transmit"
        onsubmit="this.querySelector('button').textContent='Übertrage…'">
    <button type="submit" class="btn-transmit">{_ICO_UP} Jetzt an Dolibarr übertragen</button>
  </form>
</div>
{sessions_block}
{js_polling}"""

    return _base('form', content, base_href)


_TAG_MODE_LABELS = {
    'business': ('Geschäftlich', 'var(--success)', 'wird an Dolibarr übertragen'),
    'private': ('Privat', 'var(--warn)', 'bleibt lokal, keine Übertragung'),
    'unknown': ('Nicht eingeordnet', 'var(--error)', 'kann nicht laden'),
}


def _tags_out(session_manager):
    """Tag-Liste für die Oberfläche — ohne den vollen Hash."""
    out = []
    for t in session_manager.list_tags():
        label, color, hint = _TAG_MODE_LABELS.get(t['mode'], _TAG_MODE_LABELS['unknown'])
        out.append({
            'hash_prefix': (t['rfid_hash'] or '')[:16],
            'label': t['label'],
            'mode': t['mode'],
            'mode_label': label,
            'mode_color': color,
            'mode_hint': hint,
            'first_seen': t['first_seen'],
            'last_seen': t['last_seen'],
            'seen_count': t['seen_count'],
        })
    return out


def _learn_out(api_state):
    learn = (api_state or {}).get('learn')
    if learn is None:
        return {'enabled': False, 'detected': []}
    return {'enabled': bool(learn.enabled), 'detected': learn.detected()}


def _build_tags_page(session_manager, config, api_state=None, base_href='', message_html='',
                     employees=None, extra_html=''):
    learn = _learn_out(api_state)
    tags = _tags_out(session_manager)

    toggle_label = 'Lernmodus beenden' if learn['enabled'] else 'Lernmodus starten'
    toggle_value = 'off' if learn['enabled'] else 'on'
    toggle_style = 'background:var(--error)' if learn['enabled'] else ''

    if learn['enabled']:
        hint = ('<div style="color:var(--muted);font-size:13px;margin-bottom:10px">'
                'Halte jetzt eine Karte an die Wallbox. Sie erscheint hier und kann '
                'benannt werden. Der Klartext der Karten-ID liegt nur im Arbeitsspeicher '
                'und verschwindet beim Beenden des Lernmodus.</div>')
    else:
        hint = ('<div style="color:var(--muted);font-size:13px;margin-bottom:10px">'
                'Im Lernmodus wird jede vorgehaltene Karte hier angezeigt — auch eine '
                'bisher unbekannte. Danach benennen und einordnen.</div>')

    detected_html = ''
    if learn['enabled']:
        if learn['detected']:
            rows = []
            for d in learn['detected']:
                rows.append(
                    '<form method="POST" action="tags" class="tag-row">'
                    f'<input type="hidden" name="tag" value="{html.escape(d["tag"])}">'
                    f'<code style="font-size:15px;font-weight:700">{html.escape(d["tag"])}</code>'
                    f'<span style="color:var(--dim);font-size:11px">vor {d["seconds_ago"]:.0f}s'
                    f' · {d["count"]}x</span>'
                    '<input name="label" placeholder="Name, z.B. Firmenwagen 1" required>'
                    '<select name="mode">'
                    '<option value="business">Geschäftlich — wird abgerechnet</option>'
                    '<option value="private">Privat — bleibt lokal</option>'
                    '</select>'
                    '<button type="submit" class="btn">Speichern</button>'
                    '</form>')
            detected_html = ''.join(rows)
        else:
            detected_html = ('<div style="color:var(--dim);font-size:13px;padding:8px 0">'
                             'Noch keine Karte erkannt — jetzt eine an die Wallbox halten.</div>')

    def mode_select(selected):
        return ('<select name="mode">' + ''.join(
            f'<option value="{m}"{" selected" if m == selected else ""}>{html.escape(_TAG_MODE_LABELS[m][0])}</option>'
            for m in ('business', 'private', 'unknown')) + '</select>')

    if tags:
        rows = []
        for t in tags:
            prefix = html.escape(t['hash_prefix'])
            rows.append(
                '<div class="tag-row">'
                '<form method="POST" action="tags/update" class="tag-edit">'
                f'<input type="hidden" name="hash_prefix" value="{prefix}">'
                f'<input name="label" value="{html.escape(t["label"] or "")}" placeholder="(ohne Namen)" '
                'list="ec-employees" maxlength="60">'
                f'{mode_select(t["mode"])}'
                '<button type="submit" class="btn-dl">Speichern</button></form>'
                f'<span style="background:{t["mode_color"]};color:var(--accent-i);padding:2px 8px;'
                f'border-radius:3px;font-size:11px;font-weight:700">'
                f'{html.escape(t["mode_label"])}</span>'
                '<form method="POST" action="tags/delete" style="display:inline">'
                f'<input type="hidden" name="hash_prefix" value="{prefix}">'
                '<button type="submit" class="btn-dl" '
                'onclick="return confirm(\'Karte wirklich entfernen? Sie kann danach nur noch '
                'laden, wenn sie in der Konfigurations-Whitelist steht.\')">Entfernen</button>'
                '</form>'
                f'<div style="color:var(--dim);font-size:11px;width:100%">'
                f'Hash {prefix}… · {t["seen_count"]}x gesehen · '
                f'zuletzt {html.escape(t["last_seen"] or "—")} · {html.escape(t["mode_hint"])}</div>'
                '</div>')
        tags_html = ''.join(rows)
    else:
        tags_html = ('<div style="color:var(--dim);font-size:13px;padding:8px 0">'
                     'Noch keine Karten eingetragen.</div>')

    add_html = (
        '<form method="POST" action="tags" class="tag-row tag-edit">'
        '<input type="hidden" name="manual" value="1">'
        '<input name="tag" placeholder="Karten-ID, z.B. EFCD083E" required autocomplete="off" '
        'maxlength="20" style="max-width:200px">'
        '<input name="label" placeholder="Name, z.B. Firmenwagen 1" list="ec-employees" maxlength="60">'
        f'{mode_select("business")}'
        '<button type="submit" class="btn-dl">Hinzufügen</button></form>')

    datalist = ''.join(f'<option value="{html.escape(e.get("name") or e.get("login") or "")}">'
                       f'{html.escape(e.get("login") or "")}</option>' for e in (employees or []))
    if employees is None:
        employees_html = ''
    elif employees:
        employees_html = (
            '<div class="card"><div class="card-title">' + _ICO_CARD + ' Mitarbeiter in Dolibarr</div>'
            '<div style="color:var(--muted);font-size:13px;margin-bottom:10px">Mitarbeiter mit '
            'zugeordneter Karte (Dolibarr → Wallbox-Billing → RFID). <strong>Wem</strong> eine Ladung '
            'gehört, entscheidet Dolibarr; hier wird nur festgelegt, <strong>ob</strong> sie übertragen '
            'wird. Die Namen stehen beim Benennen als Vorschlag bereit.</div>' +
            ''.join(f'<div class="tag-row"><strong>{html.escape(e.get("name") or e.get("login") or "")}</strong>'
                    f'<span style="color:var(--dim);font-size:11px">{html.escape(e.get("login") or "")}</span></div>'
                    for e in employees) + '</div>')
    else:
        employees_html = (
            '<div class="card"><div class="card-title">' + _ICO_CARD + ' Mitarbeiter in Dolibarr</div>'
            '<div style="color:var(--dim);font-size:13px">Keine Mitarbeiter mit Karte gefunden – oder '
            'Dolibarr gerade nicht erreichbar. Karten werden in Dolibarr unter Wallbox-Billing → RFID '
            'einem Mitarbeiter zugeordnet.</div></div>')

    content = f"""{message_html}
<div class="card">
  <div class="card-title">{_ICO_CARD} Lernmodus</div>
  {hint}
  <form method="POST" action="learn" style="margin-bottom:12px">
    <input type="hidden" name="enabled" value="{toggle_value}">
    <button type="submit" class="btn" style="{toggle_style}">{toggle_label}</button>
  </form>
  {detected_html}
</div>

<div class="card">
  <div class="card-title">{_ICO_CARD} Karten</div>
  <div style="color:var(--muted);font-size:13px;margin-bottom:10px">
    <strong>Geschäftlich</strong> wird als Spesenposition an Dolibarr übertragen.
    <strong>Privat</strong> wird lokal protokolliert und erreicht Dolibarr nie —
    die Ladung bleibt im Verlauf und im CSV-Export sichtbar.
    Nicht eingeordnete Karten können nicht laden.
  </div>
  {tags_html}
  <div class="card-title" style="margin-top:14px">Karte von Hand eintragen</div>
  {add_html}
</div>
{extra_html}
{employees_html}
<datalist id="ec-employees">{datalist}</datalist>
<style>
.tag-row {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap;
            padding:10px 0; border-bottom:1px solid var(--border); }}
.tag-row:last-child {{ border-bottom:none; }}
.tag-row input[name=label] {{ flex:1; min-width:160px; }}
.tag-edit {{ display:flex; gap:8px; flex:1; flex-wrap:wrap; align-items:center; min-width:240px; }}
.tag-edit select {{ width:auto; }}
.btn {{ padding:9px 16px; border:none; border-radius:8px; background:var(--primary-d); color:#fff;
        font-size:14px; font-weight:600; cursor:pointer; }}
</style>"""
    return _base('tags', content, base_href=base_href)


# ── Diagrammfarben ─────────────────────────────────────────────────────────
# Gewählt für die HELLE Fläche (#FFFFFF) und mit dem Paletten-Validator
# geprüft: Helligkeitsband L 0.43–0.77, Chroma ≥ 0.1, CVD-Trennung
# ΔE 26.1 (Deutan) / 20.8 (Tritan), Normalsicht ok, Kontrast ≥ 3:1.
# Bewusst NICHT die Statusfarben (warn/error) — die sind für Zustände
# reserviert und dürfen keine Datenserie einfärben.
SERIES_BUSINESS = '#00a86a'      # Grün, abgedunkeltes #00d084 der Marke
SERIES_PRIVATE = '#7a3fc4'       # Violett, abgedunkeltes #9b51e0 der Marke
_CHART_GRID = 'rgba(1,23,31,.10)'
_CHART_AXIS = '#7C8B91'
_STACK_GAP = 2.0          # Flächenspalt zwischen gestapelten Segmenten


def _daily_buckets(rows, year, month):
    """Sessions zu Tageseimern verrechnen.

    Nur Ladungen mit belastbarer Energie: 'discarded' ist ~0 kWh und
    'incomplete' hat einen unbekannten Zählerstand — beides würde das
    Diagramm verfälschen.
    """
    days = calendar.monthrange(year, month)[1]
    buckets = [{'day': d, 'business': 0.0, 'private': 0.0, 'total': 0.0}
               for d in range(1, days + 1)]

    for row in rows:
        status = (row.get('status') or '').lower()
        if status not in ('completed', 'private'):
            continue
        raw = row.get('start_time') or ''
        try:
            dt = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        if dt.year != year or dt.month != month:
            continue
        try:
            kwh = float(row.get('total_kwh') or 0.0)
        except (TypeError, ValueError):
            continue
        if kwh <= 0:
            continue
        bucket = buckets[dt.day - 1]
        key = 'private' if status == 'private' else 'business'
        bucket[key] += kwh
        bucket['total'] += kwh
    return buckets


# Achsen-Höchstwerte. Feiner als 1/2/5, damit der höchste Balken die Plothöhe
# auch ausnutzt: bei 59 kWh wäre 1/2/5 auf 100 gesprungen und hätte 40 % der
# Fläche verschenkt.
_NICE_FACTORS = (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)


def _nice_ceiling(value):
    """Nächster runder Achsen-Höchstwert über value."""
    if value <= 0:
        return 1.0
    import math
    base = 10 ** math.floor(math.log10(value))
    for factor in _NICE_FACTORS:
        candidate = factor * base
        if value <= candidate + 1e-9:
            return float(candidate)
    return float(10 * base)


def _build_trend_strip(rows, days=14, today=None):
    """Kompakter Tagesstreifen für die Hauptseite ("die letzten 14 Tage").

    Eine Serie, also keine Legende — der Titel darüber benennt sie. Kein
    Achsenkreuz: der Streifen beantwortet "wann wurde geladen", nicht
    "wie viel genau". Die Zahl steht in den Kennzahlen darüber.
    """
    today = (today or datetime.now()).date() if hasattr(today or datetime.now(), 'date') \
        else (today or datetime.now())
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    totals = {d: 0.0 for d in window}

    for row in rows:
        if (row.get('status') or '').lower() not in ('completed', 'private'):
            continue
        try:
            d = datetime.fromisoformat(str(row.get('start_time') or '')).date()
            kwh = float(row.get('total_kwh') or 0.0)
        except (TypeError, ValueError):
            continue
        if d in totals and kwh > 0:
            totals[d] += kwh

    if not any(totals.values()):
        return ('<div class="spark-empty">In den letzten '
                f'{days} Tagen noch keine Ladung erfasst.</div>')

    peak = max(totals.values())
    w, h = 320.0, 44.0
    slot = w / days
    bar_w = max(4.0, slot - 3.0)
    bars = []
    for i, d in enumerate(window):
        value = totals[d]
        if value <= 0:
            continue
        bar_h = max(2.0, (value / peak) * (h - 4))
        x = i * slot + (slot - bar_w) / 2
        bars.append(
            f'<g class="spark-bar"><title>{d.strftime("%d.%m.")}: {value:.3f} kWh</title>'
            f'<rect x="{x:.1f}" y="{h - bar_h:.1f}" width="{bar_w:.1f}" '
            f'height="{bar_h:.1f}" rx="2" fill="{SERIES_BUSINESS}"/></g>')

    active = sum(1 for v in totals.values() if v > 0)
    svg = (f'<svg viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" class="spark" '
           f'role="img" aria-label="Geladene Energie der letzten {days} Tage, '
           f'{active} Tage mit Ladung">' + ''.join(bars) + '</svg>')
    return (f'<div class="spark-wrap">{svg}'
            f'<div class="spark-foot"><span>vor {days} Tagen</span>'
            f'<span>{active} von {days} Tagen geladen</span><span>heute</span></div></div>')


# Konfigurationsschlüssel, deren WERT niemals in die Oberfläche darf.
_SECRET_KEYS = ('api_token', 'ha_token', 'password', 'token', 'secret')


def _diagnostics(session_manager, config, api_state):
    """Betriebszustand für den System-Tab. Enthält bewusst keine Geheimnisse."""
    import os
    import platform
    import sqlite3

    db = session_manager.db_path
    counts = {}
    total = 0
    try:
        conn = sqlite3.connect(db)
        for status, n in conn.execute("SELECT status, COUNT(*) FROM sessions GROUP BY status"):
            counts[status or '—'] = n
            total += n
        untransmitted = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE transmitted_at IS NULL "
            "AND status NOT IN ('active','discarded','incomplete','private')").fetchone()[0]
        tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        conn.close()
    except Exception as exc:
        counts, untransmitted, tags = {'Fehler': str(exc)[:60]}, 0, 0

    try:
        size_mb = os.path.getsize(db) / (1024 * 1024)
    except OSError:
        size_mb = 0.0

    api = (config or {}).get('api') or {}
    return {
        'session_source': (config or {}).get('session_source', 'ha_sensors'),
        'wallbox_profile': (config or {}).get('wallbox_profile', 'alfen_eve'),
        'wallbox_id': (config or {}).get('wallbox_id', '—'),
        'python': platform.python_version(),
        'database': db,
        'database_mb': round(size_mb, 2),
        'sessions': total,
        'sessions_by_status': counts,
        'untransmitted': untransmitted,
        'tags': tags,
        # Nur die URL — das Token wird NIE ausgegeben.
        'dolibarr_url': api.get('dolibarr_url') or '—',
        'dolibarr_connected': bool((api_state or {}).get('client')),
        'charge_points': len((config or {}).get('ocpp_charge_points') or []),
        'learn_enabled': bool(getattr((api_state or {}).get('learn'), 'enabled', False)),
    }


def _settings_rows(api_state):
    settings = (api_state or {}).get('settings')
    if settings is None:
        from app_settings import resolve_app_settings
        settings = resolve_app_settings({})
    return settings.as_rows()


def _build_system_page(session_manager, config, api_state=None, base_href=''):
    rows = _settings_rows(api_state)
    diag = _diagnostics(session_manager, config, api_state)
    changed = sum(1 for r in rows if r['changed'])

    def fmt(value):
        if isinstance(value, float):
            return f'{value:g}'
        return html.escape(str(value))

    setting_rows = ''.join(
        '<tr{cls}><td>{label}<div class="td-dim mono">{key}</div></td>'
        '<td class="td-bold mono">{value}</td>'
        '<td class="td-dim mono">{default}</td>'
        '<td>{badge}</td></tr>'.format(
            cls=' class="row-changed"' if r['changed'] else '',
            label=html.escape(r['label']), key=html.escape(r['key']),
            value=fmt(r['value']), default=fmt(r['default']),
            badge=('<span class="badge b-pend">abweichend</span>' if r['changed']
                   else '<span class="td-dim">Standard</span>'))
        for r in rows)

    status_rows = ''.join(
        f'<tr><td>{html.escape(str(k))}</td><td class="td-bold">{v}</td></tr>'
        for k, v in sorted(diag['sessions_by_status'].items()))

    dol = ('<span class="badge b-ok">verbunden</span>' if diag['dolibarr_connected']
           else '<span class="badge b-pend">nicht verbunden</span>')

    content = f"""
<div class="card">
  <div class="card-title">{_ICO_GEAR} Betrieb</div>
  <div class="kpis kpis-3">
    <div class="kpi"><div class="kpi-lbl">Datenquelle</div>
      <div class="kpi-val" style="font-size:16px">{html.escape(diag['session_source'])}</div>
      <div class="kpi-sub">Profil {html.escape(diag['wallbox_profile'])}</div></div>
    <div class="kpi"><div class="kpi-lbl">Sessions</div>
      <div class="kpi-val">{diag['sessions']}</div>
      <div class="kpi-sub">{diag['untransmitted']} noch nicht übertragen</div></div>
    <div class="kpi"><div class="kpi-lbl">Karten</div>
      <div class="kpi-val">{diag['tags']}</div>
      <div class="kpi-sub">Lernmodus {'an' if diag['learn_enabled'] else 'aus'}</div></div>
  </div>

  <table>
    <tbody>
      <tr><td>Dolibarr</td><td class="td-bold">{html.escape(diag['dolibarr_url'])}</td>
          <td>{dol}</td></tr>
      <tr><td>Wallbox-ID in Dolibarr</td>
          <td class="td-bold mono">{html.escape(diag['wallbox_id'])}</td><td></td></tr>
      <tr><td>OCPP-Wallboxen konfiguriert</td>
          <td class="td-bold">{diag['charge_points']}</td><td></td></tr>
      <tr><td>Datenbank</td><td class="td-bold mono">{html.escape(diag['database'])}</td>
          <td class="td-dim">{diag['database_mb']} MB</td></tr>
      <tr><td>Python</td><td class="td-bold mono">{html.escape(diag['python'])}</td>
          <td></td></tr>
    </tbody>
  </table>
</div>

<div class="card">
  <div class="card-title">{_ICO_HIST} Sessions nach Status</div>
  <table><tbody>{status_rows or '<tr><td class="td-dim">noch keine</td><td></td></tr>'}</tbody></table>
</div>

<div class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;
              gap:12px;flex-wrap:wrap;margin-bottom:11px">
    <div class="card-title" style="margin:0">{_ICO_GEAR} Wirksame Einstellungen</div>
    <span class="td-dim" style="font-size:12px">{changed} von {len(rows)} abweichend</span>
  </div>
  <div style="color:var(--muted);font-size:13px;margin-bottom:11px">
    Hier steht, was <strong>tatsächlich gilt</strong> — inklusive Werten, die aus einem
    unzulässigen Bereich zurechtgezogen wurden. Geändert wird die Konfiguration nicht
    hier, sondern in der Addon-Konfiguration bzw. in <code class="mono">data/options.json</code>.
    Nach einer Änderung ist ein Neustart nötig.
  </div>
  <div class="tbl-wrap">
  <table>
    <thead><tr><th>Einstellung</th><th>Wirksam</th><th>Standard</th><th></th></tr></thead>
    <tbody>{setting_rows}</tbody>
  </table>
  </div>
</div>"""
    return _base('system', content, base_href=base_href)


def _build_daily_chart(rows, year, month):
    """Gestapeltes Tagesbalken-Diagramm als Inline-SVG.

    Inline und ohne Bibliothek, weil die Oberfläche im HA-Ingress auch ohne
    Internetzugang funktionieren muss.
    """
    buckets = _daily_buckets(rows, year, month)
    filled = [b for b in buckets if b['total'] > 0]
    if not filled:
        return ('<div class="chart-empty">Keine abgerechneten Ladungen in diesem Monat — '
                'sobald welche vorliegen, erscheint hier der Tagesverlauf.</div>')

    has_private = any(b['private'] > 0 for b in filled)
    peak = max(filled, key=lambda b: b['total'])
    y_max = _nice_ceiling(max(b['total'] for b in filled))

    # Geometrie: feste Höhe, Breite über viewBox skaliert (responsiv ohne JS).
    w, h = 720.0, 190.0
    pad_l, pad_r, pad_t, pad_b = 42.0, 10.0, 16.0, 26.0
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    days = len(buckets)
    slot = plot_w / days
    bar_w = max(3.0, min(14.0, slot - 2.0))      # 2px Fläche zwischen Balken

    def y_of(value):
        return pad_t + plot_h - (value / y_max) * plot_h

    parts = []

    # Gitter und Achsenbeschriftung — bewusst zurücktretend.
    for i in range(5):
        value = y_max * i / 4
        y = y_of(value)
        parts.append(f'<line x1="{pad_l:.1f}" y1="{y:.1f}" x2="{w - pad_r:.1f}" y2="{y:.1f}" '
                     f'stroke="{_CHART_GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 7:.1f}" y="{y + 3.5:.1f}" text-anchor="end" '
                     f'font-size="9.5" fill="{_CHART_AXIS}">{value:.0f}</text>')

    # Tagesachse: nur jeder 5. Tag, sonst kollidieren die Zahlen.
    for b in buckets:
        if b['day'] == 1 or b['day'] % 5 == 0:
            x = pad_l + (b['day'] - 0.5) * slot
            parts.append(f'<text x="{x:.1f}" y="{h - 9:.1f}" text-anchor="middle" '
                         f'font-size="9.5" fill="{_CHART_AXIS}">{b["day"]}</text>')

    for b in buckets:
        if b['total'] <= 0:
            continue
        x = pad_l + (b['day'] - 0.5) * slot - bar_w / 2
        base_y = pad_t + plot_h
        tip = f'{b["day"]}. {_month_name(month)}: {b["total"]:.3f} kWh'
        if b['private'] > 0 and b['business'] > 0:
            tip += f' (geschäftlich {b["business"]:.3f} · privat {b["private"]:.3f})'
        elif b['private'] > 0:
            tip += ' (privat)'

        parts.append(f'<g class="bar"><title>{html.escape(tip)}</title>')
        cursor = base_y
        # Von unten stapeln: geschäftlich zuerst, privat darüber.
        # STACK_GAP liegt ÜBER jedem Segment, damit sich zwei Farben nicht
        # berühren — ohne den Spalt verschmelzen sie optisch zu einem Balken.
        segments = [('business', SERIES_BUSINESS), ('private', SERIES_PRIVATE)]
        drawn = [(k, c) for k, c in segments if b[k] > 0]
        for idx, (key, color) in enumerate(drawn):
            raw_h = (b[key] / y_max) * plot_h
            # Das obere Segment gibt den Spalt aus seiner eigenen Höhe her,
            # damit die Gesamthöhe des Stapels maßstabsgetreu bleibt.
            seg_h = max(1.0, raw_h - (_STACK_GAP if idx > 0 else 0.0))
            top = cursor - seg_h
            is_top = idx == len(drawn) - 1
            radius = 3.0 if is_top else 0.0
            parts.append(
                f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{seg_h:.1f}" '
                f'rx="{radius}" fill="{color}"/>')
            cursor = top - _STACK_GAP
        parts.append('</g>')

    # Direkte Beschriftung nur für den Spitzentag — nicht auf jedem Balken.
    peak_x = pad_l + (peak['day'] - 0.5) * slot
    peak_y = y_of(peak['total'])
    anchor = 'start' if peak['day'] <= 3 else ('end' if peak['day'] >= days - 2 else 'middle')
    parts.append(f'<text class="bar-label" x="{peak_x:.1f}" y="{max(pad_t + 8, peak_y - 6):.1f}" '
                 f'text-anchor="{anchor}" font-size="10.5" font-weight="700" '
                 f'fill="var(--text)">{peak["total"]:.1f} kWh</text>')

    legend = ''
    if has_private:
        legend = (
            '<div class="chart-legend">'
            f'<span><i style="background:{SERIES_BUSINESS}"></i>Geschäftlich</span>'
            f'<span><i style="background:{SERIES_PRIVATE}"></i>Privat (nicht übertragen)</span>'
            '</div>')

    svg = (f'<svg viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
           f'class="chart-svg" role="img" '
           f'aria-label="Tagesverlauf {_month_name(month)} {year} in Kilowattstunden">'
           + ''.join(parts) + '</svg>')
    return f'<div class="chart">{svg}{legend}</div>'


def _build_history_page(session_manager, year, month, base_href=''):
    months = _db_months(session_manager.db_path)

    now = datetime.now()
    if not months:
        months = [(now.year, now.month)]
    if year == 0 or month == 0:
        year, month = months[0]

    # Monats-Tabs
    tabs_html = ''
    for y, m in months:
        active = 'active' if (y == year and m == month) else ''
        tabs_html += (
            f'<a href="history?year={y}&month={m}" class="m-tab {active}">'
            f'{_month_name(m)} {y}</a>'
        )

    # Sessions des gewählten Monats
    rows      = _db_month(session_manager.db_path, year, month)
    total_kwh = sum(s.get('total_kwh') or 0 for s in rows)
    n_total   = len(rows)
    n_sent    = sum(1 for s in rows if s.get('transmitted_at'))
    # 'private' wird absichtlich nie übertragen und ist daher nicht ausstehend.
    n_pending = sum(
        1 for s in rows
        if not s.get('transmitted_at')
        and (s.get('status') or '').lower() not in ('discarded', 'incomplete',
                                                    'active', 'private')
    )

    if rows:
        table_rows = ''
        for s in rows:
            kwh      = s.get('total_kwh') or 0
            date     = (s.get('start_time') or '')[:10]
            time_str = (s.get('start_time') or '')[11:16]
            rid      = (s.get('rfid_hash') or '')[:12] + '…'
            wbx      = s.get('wallbox_id') or '—'
            status   = (s.get('status') or '').lower()
            if status == 'discarded':
                tag = ('<span class="badge b-disc" '
                       'title="Karte gelesen, aber zu wenig kWh — nicht übertragen">'
                       '⊘ verworfen</span>')
                row_style = ' style="opacity:0.55"'
            elif status == 'incomplete':
                tag = ('<span class="badge b-inc" '
                       'title="Zählerstand unbekannt — bitte manuell nachtragen">'
                       '⚠ unvollständig</span>')
                row_style = ' style="opacity:0.7"'
            elif s.get('transmitted_at'):
                tag = f'<span class="badge b-ok">{_ICO_CHECK} übertragen</span>'
                row_style = ''
            else:
                tag = '<span class="badge b-pend">ausstehend</span>'
                row_style = ''
            table_rows += (
                f'<tr{row_style}>'
                f'<td>{date}<div class="td-dim">{time_str}</div></td>'
                f'<td class="td-dim">{rid}</td>'
                f'<td>{wbx}</td>'
                f'<td class="td-bold">{kwh:.3f}</td>'
                f'<td>{tag}</td></tr>'
            )
        table_rows += (
            f'<tr class="total-row">'
            f'<td colspan="3">Gesamt ({n_total} Sessions)</td>'
            f'<td class="td-bold">{total_kwh:.3f} kWh</td>'
            f'<td><span style="font-size:11px;color:var(--muted)">'
            f'{n_sent} übertr. · {n_pending} ausst.</span></td></tr>'
        )
        table_html = (
            f'<div class="tbl-wrap">'
            f'<table>'
            f'<thead><tr>'
            f'<th>Datum</th><th>RFID</th><th>Wallbox</th><th>kWh</th><th>Status</th>'
            f'</tr></thead>'
            f'<tbody>{table_rows}</tbody>'
            f'</table></div>'
        )
    else:
        table_html = '<div class="empty">Keine Sessions in diesem Monat</div>'

    export_url = f'export?year={year}&month={month}'
    chart_html = _build_daily_chart(rows, year, month)
    buckets = _daily_buckets(rows, year, month)
    private_kwh = sum(b['private'] for b in buckets)
    billed_kwh = sum(b['business'] for b in buckets)
    active_days = sum(1 for b in buckets if b['total'] > 0)
    best = max(buckets, key=lambda b: b['total']) if active_days else None

    content = f"""
<div class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:13px">
    <div class="card-title" style="margin:0">{_ICO_HIST} Verlauf</div>
    <a href="{export_url}" class="btn-dl">{_ICO_DL} CSV exportieren</a>
  </div>
  <div class="month-tabs">{tabs_html}</div>

  <div class="kpis">
    <div class="kpi">
      <div class="kpi-lbl">Abgerechnet</div>
      <div class="kpi-val">{billed_kwh:.1f}<span class="kpi-unit">kWh</span></div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Privat</div>
      <div class="kpi-val">{private_kwh:.1f}<span class="kpi-unit">kWh</span></div>
      <div class="kpi-sub">nicht übertragen</div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Ladetage</div>
      <div class="kpi-val">{active_days}<span class="kpi-unit">von {len(buckets)}</span></div>
    </div>
    <div class="kpi">
      <div class="kpi-lbl">Stärkster Tag</div>
      <div class="kpi-val">{(f'{best["total"]:.1f}' if best else '—')}<span class="kpi-unit">kWh</span></div>
      <div class="kpi-sub">{(f'{best["day"]}. {_month_name(month)}' if best else '—')}</div>
    </div>
  </div>

  {chart_html}
  {table_html}
</div>"""

    return _base('history', content, base_href)


# ---------------------------------------------------------------------------
# App-Factory
# ---------------------------------------------------------------------------

def create_app(session_manager, config, api_state):
    """
    api_state: dict mit key 'client' → WallboxApiClient oder None.
    Wird von main.py befüllt und kann sich zur Laufzeit ändern.
    """

    # -- GET / ---------------------------------------------------------------
    async def handle_get(request):
        base_href = request.headers.get('X-Ingress-Path', '').rstrip('/')
        msg = request.rel_url.query.get('msg', '')
        t   = request.rel_url.query.get('t', '')
        msg_html = f'<div class="msg {t}">{msg}</div>' if msg else ''
        return web.Response(
            text=_build_form_page(session_manager, config, msg_html, base_href=base_href, api_state=api_state),
            content_type='text/html'
        )

    # -- POST / --------------------------------------------------------------
    async def handle_post(request):
        base_href = request.headers.get('X-Ingress-Path', '').rstrip('/')
        msg_html = ''
        try:
            data           = await request.post()
            employee_login = data.get('employee_login', '').strip()
            kwh_str        = data.get('kwh', '').strip()
            date_str       = data.get('date', datetime.now().date().isoformat()).strip()

            if not employee_login:
                raise ValueError("Kein Mitarbeiter ausgewählt.")
            kwh = float(kwh_str) if kwh_str else 0.0
            if kwh <= 0:
                raise ValueError("kWh muss größer als 0 sein.")

            wallbox_id = config.get('wallbox_id', 'wallbox')
            sid        = session_manager.add_manual_session(
                kwh, wallbox_id, date_str, login=employee_login
            )

            if sid:
                msg_html = (f'<div class="msg ok">Session #{sid} gespeichert: '
                            f'<strong>{kwh:.3f} kWh</strong> am {date_str}</div>')
            else:
                raise ValueError("Session konnte nicht gespeichert werden.")
        except Exception as exc:
            msg_html = f'<div class="msg err">Fehler: {exc}</div>'

        return web.Response(
            text=_build_form_page(session_manager, config, msg_html, base_href=base_href, api_state=api_state),
            content_type='text/html'
        )

    # -- POST /transmit -------------------------------------------------------
    async def handle_transmit(request):
        base_href = request.headers.get('X-Ingress-Path', '').rstrip('/')
        client = api_state.get('client')

        if not client:
            from api_client import WallboxApiClient
            api_cfg = config.get('api', {})
            url     = api_cfg.get('dolibarr_url', '')
            token   = api_cfg.get('api_token', '')
            if url and token and url != 'https://dolibarr.example.com':
                try:
                    c = WallboxApiClient(base_url=url, api_token=token, timeout=30)
                    if c.check_connection():
                        client = c
                        api_state['client'] = c
                        _LOGGER.info("API-Client on-demand erstellt")
                except Exception as e:
                    _LOGGER.warning("API-Client Fehler: %s", e)

        if not client:
            msg_html = '<div class="msg err">Dolibarr API nicht erreichbar — bitte URL und Token prüfen.</div>'
        else:
            try:
                # im Thread: synchrones requests mit Retry darf den Event-Loop (und
                # damit die OCPP-Verbindung der Wallbox) nicht anhalten
                result = await asyncio.to_thread(session_manager.transmit_completed_sessions, client)
                note_transmit_result(api_state, result)
                sent   = result.get('transmitted', 0)
                failed = result.get('failed', 0)
                if sent == 0 and failed == 0:
                    msg_html = '<div class="msg warn">Keine ausstehenden Sessions.</div>'
                elif failed > 0:
                    err = result['errors'][0] if result['errors'] else ''
                    msg_html = f'<div class="msg err">{sent} übertragen, {failed} fehlgeschlagen: {err}</div>'
                else:
                    msg_html = f'<div class="msg ok">{sent} Session(s) erfolgreich an Dolibarr übertragen.</div>'
            except Exception as exc:
                msg_html = f'<div class="msg err">Übertragungsfehler: {exc}</div>'

        return web.Response(
            text=_build_form_page(session_manager, config, msg_html, base_href=base_href, api_state=api_state),
            content_type='text/html'
        )

    # -- GET /history --------------------------------------------------------
    async def handle_history(request):
        base_href = request.headers.get('X-Ingress-Path', '').rstrip('/')
        year  = int(request.rel_url.query.get('year',  0))
        month = int(request.rel_url.query.get('month', 0))
        return web.Response(
            text=_build_history_page(session_manager, year, month, base_href=base_href),
            content_type='text/html'
        )

    # -- GET /export ----------------------------------------------------------
    async def handle_export(request):
        now   = datetime.now()
        year  = int(request.rel_url.query.get('year',  now.year))
        month = int(request.rel_url.query.get('month', now.month))
        rows  = _db_month(session_manager.db_path, year, month)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['Datum', 'Uhrzeit', 'RFID (Prefix)', 'Wallbox', 'kWh', 'Status', 'Übertragen am'])
        for s in rows:
            date_s  = (s.get('start_time') or '')[:10]
            time_s  = (s.get('start_time') or '')[11:16]
            rfid_s  = (s.get('rfid_hash') or '')[:16] + '…'
            wbx_s   = s.get('wallbox_id') or ''
            kwh_s   = f"{(s.get('total_kwh') or 0):.3f}".replace('.', ',')
            _st = (s.get('status') or '').lower()
            status  = ('verworfen'      if _st == 'discarded'
                       else 'unvollständig' if _st == 'incomplete'
                       else 'übertragen'    if s.get('transmitted_at')
                       else 'ausstehend')
            tx_time = (s.get('transmitted_at') or '')[:16]
            writer.writerow([date_s, time_s, rfid_s, wbx_s, kwh_s, status, tx_time])

        filename = f'wallbox_{year}_{str(month).zfill(2)}.csv'
        # aiohttp content_type darf KEINE Parameter (z.B. charset) enthalten —
        # sonst ValueError → 500. Mit BOM für korrekte Umlaut-Darstellung in Excel.
        body = '﻿' + output.getvalue()
        return web.Response(
            body=body.encode('utf-8'),
            content_type='text/csv',
            charset='utf-8',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )

    # -- GET /live.json (JSON-Endpoint für JS-Polling, flackerfrei) ----------
    async def handle_live_json(request):
        active         = _db_active_sessions(session_manager.db_path)
        current_energy = api_state.get('current_energy') if api_state else None
        wallbox_state  = api_state.get('wallbox_state')  if api_state else None
        last_update    = api_state.get('last_update')    if api_state else None

        now = datetime.now()
        sessions_out = []
        for s in active:
            try:
                start_dt = datetime.fromisoformat(s['start_time'])
            except (ValueError, TypeError):
                start_dt = now
            elapsed      = (now - start_dt).total_seconds()
            start_energy = float(s.get('start_energy_kwh') or 0.0)
            current_kwh  = None
            if s.get('charge_point_id'):
                # OCPP: eigener Zählerstand je Session (letzter MeterValue)
                if s.get('last_meter_kwh') is not None:
                    current_kwh = max(0.0, float(s['last_meter_kwh']) - start_energy)
            elif current_energy is not None and current_energy >= start_energy:
                current_kwh = current_energy - start_energy
            sessions_out.append({
                'id':              s['id'],
                'rfid_prefix':     ((s.get('rfid_hash') or '')[:16] + '…'),
                'wallbox_id':      s.get('wallbox_id') or '—',
                'start_time_fmt':  start_dt.strftime('%d.%m.%Y %H:%M:%S'),
                'elapsed_seconds': elapsed,
                'start_energy_kwh': start_energy,
                'current_kwh':     current_kwh,
            })

        return web.json_response({
            'sensor': {
                'current_energy': current_energy,
                'wallbox_state':  wallbox_state,
                'last_update':    last_update,
            },
            'sessions': sessions_out,
            'charge_points': _charge_points_out(api_state),
        })


    async def _body(request):
        """Nimmt JSON und Formulardaten gleichermaßen an."""
        if (request.content_type or '').startswith('application/json'):
            try:
                data = await request.json()
            except Exception:
                return {}
            return data if isinstance(data, dict) else {}
        return dict(await request.post())

    def _wants_json(request):
        return (request.content_type or '').startswith('application/json')

    async def handle_system_page(request):
        base_href = request.headers.get('X-Ingress-Path', '')
        return web.Response(
            content_type='text/html', charset='utf-8',
            text=_build_system_page(session_manager, config, api_state=api_state,
                                    base_href=base_href))

    async def handle_system_json(request):
        return web.json_response({
            'settings': _settings_rows(api_state),
            'diagnostics': _diagnostics(session_manager, config, api_state),
        })

    def _audit(request, field, new, note=''):
        admin_ctx = (api_state or {}).get('admin')
        if admin_ctx:
            admin_ctx.audit.record(request.get('user'), field, None, new, note)

    async def _employees():
        """Dolibarr-Mitarbeiter für Namensvorschläge; None ohne Dolibarr-Zugang."""
        client = (api_state or {}).get('client')
        if not client:
            return None
        try:
            return await asyncio.wait_for(asyncio.to_thread(client.list_employees), 5)
        except Exception:
            return []

    def _whitelist_html():
        admin_ctx = (api_state or {}).get('admin')
        entries = [str(x) for x in (config.get('rfid_whitelist') or []) if str(x).strip()]
        if not admin_ctx or not entries:
            return ''
        return ('<div class="card"><div class="card-title">' + _ICO_CARD + ' Alte Whitelist</div>'
                f'<div style="color:var(--muted);font-size:13px;margin-bottom:10px">In der Konfiguration '
                f'stehen noch {len(entries)} Karte(n) unter <code>rfid_whitelist</code>. Sie laden und werden '
                'abgerechnet, tauchen hier aber nicht auf. Übernehmen trägt sie als geschäftlich ein und '
                'leert die Liste.</div><form method="POST" action="tags/import-whitelist">'
                '<button type="submit" class="btn">In die Kartenverwaltung übernehmen</button></form></div>')

    async def _tags_response(request, message_html=''):
        return web.Response(
            content_type='text/html', charset='utf-8',
            text=_build_tags_page(session_manager, config, api_state=api_state,
                                  base_href=request.headers.get('X-Ingress-Path', ''),
                                  message_html=message_html, employees=await _employees(),
                                  extra_html=_whitelist_html()))

    async def handle_tags_page(request):
        return await _tags_response(request)

    async def handle_tags_json(request):
        return web.json_response({
            'learn': _learn_out(api_state),
            'tags': _tags_out(session_manager),
        })

    async def handle_learn(request):
        data = await _body(request)
        raw = data.get('enabled')
        enabled = raw in (True, 'on', 'true', '1', 1)
        learn = (api_state or {}).get('learn')
        if learn is None:
            return web.json_response({'error': 'Lernmodus nicht verfügbar'}, status=503)
        learn.enabled = enabled
        if _wants_json(request):
            return web.json_response({'enabled': learn.enabled})
        raise web.HTTPFound(f"{request.headers.get('X-Ingress-Path', '')}/tags")

    async def handle_tags_save(request):
        data = await _body(request)
        tag = str(data.get('tag') or '').strip()
        mode = str(data.get('mode') or '').strip()
        label = str(data.get('label') or '').strip()
        if not tag:
            return web.json_response({'error': 'Karten-ID fehlt'}, status=400)
        if data.get('manual'):
            # Von Hand getippt: so prüfen und schreiben, wie die Wallbox die ID meldet
            try:
                tag = validate.rfid(tag)
                label = validate.label(label)
            except ValueError as exc:
                return await _tags_response(request, f'<div class="msg err">{html.escape(str(exc))}</div>')
        try:
            saved = session_manager.upsert_tag(tag, label=label, mode=mode)
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400)
        _audit(request, 'karte', f'{saved["rfid_hash"][:16]}… {label}'.strip(), f'eingetragen als {mode}')
        if _wants_json(request):
            return web.json_response({'ok': True, 'mode': saved['mode'],
                                      'label': saved['label']})
        raise web.HTTPFound(f"{request.headers.get('X-Ingress-Path', '')}/tags")

    def _tag_by_prefix(prefix):
        if len(prefix) < 8:
            return None
        return next((t for t in session_manager.list_tags()
                     if (t['rfid_hash'] or '').startswith(prefix)), None)

    async def handle_tags_update(request):
        data = await _body(request)
        tag = _tag_by_prefix(str(data.get('hash_prefix') or '').strip())
        label = str(data.get('label') or '').strip()
        mode = str(data.get('mode') or '').strip()
        if tag is None:
            return web.json_response({'error': 'Karte nicht gefunden'}, status=404)
        if len(label) > 60 or not label.isprintable():
            return await _tags_response(request, '<div class="msg err">Name: höchstens 60 druckbare Zeichen</div>')
        try:
            session_manager.update_tag_by_hash(tag['rfid_hash'], label, mode)
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400)
        _audit(request, 'karte', f'{tag["rfid_hash"][:16]}… {label}'.strip(), f'{tag["mode"]} → {mode}')
        if _wants_json(request):
            return web.json_response({'ok': True})
        raise web.HTTPFound(f"{request.headers.get('X-Ingress-Path', '')}/tags")

    async def handle_whitelist_import(request):
        admin_ctx = api_state['admin']
        entries = [str(x).strip() for x in (config.get('rfid_whitelist') or []) if str(x).strip()]
        for entry in entries:
            tag = normalize_id_tag(entry)
            if not session_manager.get_tag(tag):
                session_manager.upsert_tag(tag, label=None, mode='business')
        diff = admin_ctx.store.update({'rfid_whitelist': []}, config)
        admin_ctx.audit.record_diff(request.get('user'), [(f, f'{len(entries)} Karte(n)', n) for f, _, n in diff],
                                    'in die Kartenverwaltung übernommen')
        return await _tags_response(request, f'<div class="msg ok">{len(entries)} Karte(n) übernommen – '
                                             'jetzt unten benennen.</div>')

    async def handle_tags_delete(request):
        data = await _body(request)
        tag = str(data.get('tag') or '').strip()
        prefix = str(data.get('hash_prefix') or '').strip()
        removed = False
        if tag:
            removed = session_manager.delete_tag(tag)
        elif prefix:
            # Aus der Liste kommt nur der Hash-Präfix — den Klartext kennt die
            # Oberfläche dort bewusst nicht mehr.
            for t in session_manager.list_tags():
                if (t['rfid_hash'] or '').startswith(prefix):
                    removed = session_manager.delete_tag_by_hash(t['rfid_hash'])
                    break
        if not removed:
            if _wants_json(request):
                return web.json_response({'error': 'Karte nicht gefunden'}, status=404)
            raise web.HTTPFound(f"{request.headers.get('X-Ingress-Path', '')}/tags")
        _audit(request, 'karte', None, 'entfernt')
        if _wants_json(request):
            return web.json_response({'ok': True})
        raise web.HTTPFound(f"{request.headers.get('X-Ingress-Path', '')}/tags")

    async def handle_health(request):
        return web.json_response({'status': 'ok'})

    # Standalone: Anmeldung, CSRF, Assistent (admin/). Im HA-Addon gibt es keinen
    # AdminContext — dort schützt der Ingress und die Oberfläche bleibt wie bisher.
    admin_ctx = (api_state or {}).get('admin')
    app = web.Application(middlewares=[admin_web.middleware(admin_ctx)] if admin_ctx else [],
                          client_max_size=admin_web.MAX_UPLOAD if admin_ctx else 1024 ** 2)
    if admin_ctx:
        admin_ctx.render = _base
        admin_ctx.api_state = api_state
        admin_web.register(app, admin_ctx)
    app.router.add_get('/health',    handle_health)
    app.router.add_get('/',          handle_get)
    app.router.add_post('/',         handle_post)
    app.router.add_post('/transmit', handle_transmit)
    app.router.add_get('/live.json', handle_live_json)
    app.router.add_get('/history',   handle_history)
    app.router.add_get('/export',    handle_export)
    app.router.add_get('/system',       handle_system_page)
    app.router.add_get('/system.json',  handle_system_json)
    app.router.add_get('/tags',         handle_tags_page)
    app.router.add_get('/tags.json',    handle_tags_json)
    app.router.add_post('/learn',       handle_learn)
    app.router.add_post('/tags',        handle_tags_save)
    app.router.add_post('/tags/delete', handle_tags_delete)
    app.router.add_post('/tags/update', handle_tags_update)
    if admin_ctx:
        app.router.add_post('/tags/import-whitelist', handle_whitelist_import)
    return app


async def start_web_server(session_manager, config, api_state, port=8099, host='0.0.0.0'):
    app    = create_app(session_manager, config, api_state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    _LOGGER.info("Web-Server gestartet auf %s:%d", host, port)

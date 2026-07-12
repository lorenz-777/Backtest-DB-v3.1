#!/usr/bin/env python3
"""
ohlcv.py – OHLCV Kursdaten Scraper
====================================
Lädt tägliche Kursdaten (Open/High/Low/Close/Volume) über yfinance und
befüllt die `ohlcv`-Tabelle in data.db.

Historie: komplette verfügbare Historie pro Ticker (period="max"),
d.h. z.T. seit Börsengang / IPO.

Installation:
    pip install yfinance

Verwendung:
    python ohlcv.py                      # alle Ticker aus tickers.txt
    python ohlcv.py AAPL
    python ohlcv.py AAPL --debug
    python ohlcv.py --tickers-file tickers.txt --delay 1.0
"""

import sys
import time
import argparse

import yfinance as yf
import pandas as pd

from rich.console import Console
from rich.panel import Panel

from db import DB

console = Console()
TICKERS_FILE = "tickers.txt"

# yfinance: komplette verfügbare Historie
PERIOD = "max"


# ═══════════════════════════════════════════════════════════════════════════════
# TICKERS.TXT LADEN
# ═══════════════════════════════════════════════════════════════════════════════

def load_tickers(path: str) -> list[str]:
    """
    Liest tickers.txt. Akzeptiert TICKER oder TICKER:EXCHANGE pro Zeile
    (Exchange wird für yfinance ignoriert, nur der Ticker wird gebraucht).
    """
    tickers: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                ticker = line.split(":", 1)[0].strip().upper()
                if ticker:
                    tickers.append(ticker)
    except FileNotFoundError:
        console.print(f"[red]❌ Datei nicht gefunden: {path}[/red]")
        sys.exit(1)
    return tickers


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv(ticker: str, debug: bool = False) -> list[dict]:
    """
    Lädt die komplette verfügbare Kurshistorie für einen Ticker via yfinance.
    Gibt eine Liste von Records für db.upsert_ohlcv() zurück.
    """
    try:
        hist = yf.Ticker(ticker).history(
            period=PERIOD,
            interval="1d",
            auto_adjust=False,   # rohe OHLC, nicht dividenden-/split-bereinigt
            actions=False,
        )
    except Exception as e:
        if debug:
            console.print(f"[dim]  yfinance Fehler: {e}[/dim]")
        return []

    if hist is None or hist.empty:
        return []

    hist = hist.reset_index()

    # Spaltenname kann "Date" oder "Datetime" sein, je nach Intervall
    date_col = "Date" if "Date" in hist.columns else "Datetime"

    records: list[dict] = []
    for _, row in hist.iterrows():
        date_val = row[date_col]
        if isinstance(date_val, pd.Timestamp):
            date_str = date_val.strftime("%Y-%m-%d")
        else:
            date_str = str(date_val)[:10]

        # Zeilen mit fehlenden Kernwerten überspringen
        if pd.isna(row.get("Close")):
            continue

        records.append({
            "ticker": ticker,
            "date":   date_str,
            "open":   float(row["Open"])   if not pd.isna(row.get("Open"))   else None,
            "high":   float(row["High"])   if not pd.isna(row.get("High"))   else None,
            "low":    float(row["Low"])    if not pd.isna(row.get("Low"))    else None,
            "close":  float(row["Close"]),
            "volume": int(row["Volume"])   if not pd.isna(row.get("Volume")) else None,
        })

    if debug:
        console.print(f"[dim]  {ticker}: {len(records)} Tage geladen[/dim]")

    return records


# ═══════════════════════════════════════════════════════════════════════════════
# TICKER-VERARBEITUNG
# ═══════════════════════════════════════════════════════════════════════════════

def process_ticker(ticker: str, db: DB, debug: bool = False) -> tuple[int, int]:
    """Lädt OHLCV-Daten für einen Ticker und schreibt sie in die DB."""
    with console.status(f"[bold green]{ticker}: Kursdaten laden …[/bold green]"):
        records = fetch_ohlcv(ticker, debug=debug)

    if not records:
        console.print(f"  [yellow]⚠ Keine Kursdaten für {ticker}[/yellow]")
        return 0, 0

    ins, upd = db.upsert_ohlcv(records)
    first_date = records[0]["date"]
    last_date  = records[-1]["date"]
    console.print(
        f"  [green]✓[/green] {ticker}: {len(records)} Tage "
        f"[dim]({first_date} → {last_date})[/dim]  "
        f"[dim]💾 +{ins} neu  ~{upd} aktualisiert[/dim]"
    )
    return ins, upd


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="OHLCV Kursdaten Scraper (yfinance)")
    p.add_argument("ticker",         nargs="?",          help="Einzelner Ticker (optional)")
    p.add_argument("--tickers-file", "-f", default=TICKERS_FILE,
                   help=f"Ticker-Datei (Standard: {TICKERS_FILE})")
    p.add_argument("--db",           default="data.db", help="DB-Pfad")
    p.add_argument("--delay",        type=float, default=0.5,
                   help="Pause zwischen Tickern in Sekunden (Standard: 0.5)")
    p.add_argument("--debug", "-d",  action="store_true")
    args = p.parse_args()

    console.print()
    console.print(Panel(
        "[bold cyan]OHLCV Kursdaten Scraper[/bold cyan]\n"
        "[dim]Tägliche Open/High/Low/Close/Volume – komplette Historie[/dim]\n"
        "[dim]Quelle: yfinance (Yahoo Finance)[/dim]",
        border_style="cyan", expand=False,
    ))
    console.print()

    db = DB(args.db)
    console.print(f"  [dim]DB: {db.path}[/dim]\n")

    # ── Einzelner Ticker ──────────────────────────────────────────────────────
    if args.ticker:
        ticker = args.ticker.upper()
        console.rule(f"[bold cyan]{ticker}[/bold cyan]")
        console.print()
        process_ticker(ticker, db, debug=args.debug)
        db.close()
        console.print()
        return

    # ── Batch-Modus ───────────────────────────────────────────────────────────
    tickers = load_tickers(args.tickers_file)
    if not tickers:
        console.print(f"[yellow]⚠ Keine Ticker in {args.tickers_file}.[/yellow]")
        db.close()
        sys.exit(0)

    console.print(
        f"[bold]Batch-Modus:[/bold] [cyan]{len(tickers)} Ticker[/cyan] "
        f"aus [dim]{args.tickers_file}[/dim]\n"
    )

    total_ins = total_upd = 0
    failed: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        console.rule(f"[bold cyan]{i}/{len(tickers)}  {ticker}[/bold cyan]")
        console.print()
        try:
            ins, upd = process_ticker(ticker, db, debug=args.debug)
            total_ins += ins
            total_upd += upd
            if ins == 0 and upd == 0:
                failed.append(ticker)
        except Exception as e:
            console.print(f"  [red]❌ {ticker}: {e}[/red]")
            failed.append(ticker)

        console.print()
        if i < len(tickers):
            time.sleep(args.delay)

    db.close()

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    console.rule("[bold]Batch-Ergebnis[/bold]")
    console.print()
    lines = (
        f"[bold]Verarbeitet:[/bold] [green]{len(tickers)-len(failed)} ✅[/green]  "
        f"[red]{len(failed)} ❌[/red]\n"
        f"[bold]DB:[/bold] [green]+{total_ins} neu[/green]  [yellow]~{total_upd} aktualisiert[/yellow]"
    )
    if failed:
        lines += f"\n[red]Keine Daten:[/red] {', '.join(failed)}"
    console.print(Panel(lines, title="📦 Zusammenfassung", border_style="bright_black"))
    console.print()


if __name__ == "__main__":
    main()

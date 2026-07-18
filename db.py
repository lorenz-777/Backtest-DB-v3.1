#!/usr/bin/env python3
"""
db.py – Zentrale SQLite-Datenbankschicht
=========================================
Tabellen:
  - fundamentals (ticker, date, revenue, eps, debt_to_equity, profit_margin)
  - estimates (ticker, period, period_type, eps_estimate, reported_eps, surprise_eps, revenue_estimate, reported_revenue, surprise_revenue)
"""

import sqlite3
from datetime import datetime, timezone

DB_FILE = "data.db"

class DB:
    def __init__(self, path: str = DB_FILE):
        self.path = path
        self.con  = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    # ─── Schema ──────────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS fundamentals (
                ticker TEXT,
                date TEXT,
                revenue REAL,
                eps REAL,
                debt_to_equity REAL,
                profit_margin REAL,
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS estimates (
                ticker TEXT,
                period TEXT,
                period_type TEXT,
                eps_estimate REAL,
                reported_eps REAL,
                surprise_eps REAL,
                revenue_estimate REAL,
                reported_revenue REAL,
                surprise_revenue REAL,
                PRIMARY KEY (ticker, period, period_type)
            );
        """)
        self.con.commit()

    # ─── UPSERT fundamentals ─────────────────────────────────────────────────

    def upsert_fundamentals(self, records: list[dict]) -> tuple[int, int]:
        inserted = updated = 0

        for r in records:
            ticker = r.get("ticker", "")
            date = r.get("period_end", "")
            
            if not ticker or not date:
                continue

            existing = self.con.execute(
                "SELECT ticker FROM fundamentals WHERE ticker=? AND date=?",
                (ticker, date),
            ).fetchone()

            self.con.execute(
                """
                INSERT INTO fundamentals (ticker, date, revenue, eps, debt_to_equity, profit_margin)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, date) DO UPDATE SET
                    revenue = excluded.revenue,
                    eps = excluded.eps,
                    debt_to_equity = excluded.debt_to_equity,
                    profit_margin = excluded.profit_margin
                """,
                (
                    ticker,
                    date,
                    r.get("total_revenue"),
                    r.get("trailing_eps"),
                    r.get("debt_to_equity"),
                    r.get("profit_margins")
                ),
            )
            if existing: updated  += 1
            else:        inserted += 1

        self.con.commit()
        return inserted, updated

    # ─── UPSERT estimates ────────────────────────────────────────────────────
    
    def upsert_estimates(self, ticker: str, data: dict) -> tuple[int, int]:
        inserted = updated = 0
        records = []
        if "quarterly" in data:
            for r in data["quarterly"]:
                r["period_type"] = "quarterly"
                records.append(r)
        if "annual" in data:
            for r in data["annual"]:
                r["period_type"] = "annual"
                records.append(r)

        for r in records:
            period = r.get("period", "")
            period_type = r.get("period_type", "")
            
            if not period:
                continue

            existing = self.con.execute(
                "SELECT ticker FROM estimates WHERE ticker=? AND period=? AND period_type=?",
                (ticker, period, period_type),
            ).fetchone()

            # yfinance names conversion: 
            # eps_est -> eps_estimate
            # eps_act -> reported_eps
            # eps_beat -> surprise_eps
            self.con.execute(
                """
                INSERT INTO estimates (
                    ticker, period, period_type, 
                    eps_estimate, reported_eps, surprise_eps,
                    revenue_estimate, reported_revenue, surprise_revenue
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, period, period_type) DO UPDATE SET
                    eps_estimate = excluded.eps_estimate,
                    reported_eps = excluded.reported_eps,
                    surprise_eps = excluded.surprise_eps,
                    revenue_estimate = excluded.revenue_estimate,
                    reported_revenue = excluded.reported_revenue,
                    surprise_revenue = excluded.surprise_revenue
                """,
                (
                    ticker,
                    period,
                    period_type,
                    r.get("eps_est"),
                    r.get("eps_act"),
                    r.get("eps_beat"),
                    r.get("rev_est"),
                    r.get("rev_act"),
                    r.get("rev_beat")
                ),
            )
            if existing: updated  += 1
            else:        inserted += 1

        self.con.commit()
        return inserted, updated

    # ─── Lesen ───────────────────────────────────────────────────────────────

    def get_fundamentals(self, ticker: str, limit: int = 100) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM fundamentals WHERE ticker=? "
            "ORDER BY date DESC LIMIT ?",
            (ticker.upper(), limit),
        ).fetchall()
        
    def get_estimates(self, ticker: str, limit: int = 100) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM estimates WHERE ticker=? "
            "ORDER BY period DESC LIMIT ?",
            (ticker.upper(), limit),
        ).fetchall()

    def get_all_tickers(self) -> list[str]:
        rows = self.con.execute(
            "SELECT DISTINCT ticker FROM fundamentals ORDER BY ticker"
        ).fetchall()
        return [r[0] for r in rows]

    def summary(self) -> dict:
        f = self.con.execute(
            "SELECT COUNT(*) as total, COUNT(DISTINCT ticker) as tickers FROM fundamentals"
        ).fetchone()
        
        # Falls die Tabelle existiert (z.B. neu erstellt)
        try:
            e = self.con.execute(
                "SELECT COUNT(*) as total, COUNT(DISTINCT ticker) as tickers FROM estimates"
            ).fetchone()
        except sqlite3.OperationalError:
            e = {"total": 0, "tickers": 0}
            
        return {
            "fund_rows":      f["total"],
            "fund_tickers":   f["tickers"],
            "est_rows":       e["total"],
            "est_tickers":    e["tickers"],
        }

    # ─── Lifecycle ───────────────────────────────────────────────────────────

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ─── CLI: DB befüllen + Status anzeigen ──────────────────────────────────────

if __name__ == "__main__":
    import sys
    import time
    import argparse
    from rich.console import Console
    from rich.panel import Panel

    con = Console()

    ap = argparse.ArgumentParser(
        description="DB befüllen (Fundamentals & Estimates) aus tickers.txt"
    )
    ap.add_argument("--db",      default=DB_FILE,      help="DB-Pfad")
    ap.add_argument("--tickers", default="tickers.txt", help="Ticker-Datei")
    ap.add_argument("--delay",   type=float, default=3.0,
                    help="Pause zwischen Tickern in Sekunden (Standard: 3.0)")
    ap.add_argument("--debug",   action="store_true")
    ap.add_argument("--status",  action="store_true",
                    help="Nur DB-Status anzeigen, nichts scrapen")
    args = ap.parse_args()

    # ── Nur Status ────────────────────────────────────────────────────────────
    if args.status:
        with DB(args.db) as db:
            info = db.summary()
        con.print(f"\n[bold cyan]DB:[/bold cyan] [dim]{args.db}[/dim]")
        con.print(f"  [bold]fundamentals[/bold] : {info['fund_rows']} Zeilen "
                  f"| {info['fund_tickers']} Ticker")
        con.print(f"  [bold]estimates[/bold]    : {info['est_rows']} Zeilen "
                  f"| {info['est_tickers']} Ticker\n")
        sys.exit(0)

    # ── Ticker-Liste laden ────────────────────────────────────────────────────
    def _load_tickers(path: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        t, ex = line.split(":", 1)
                        result.append((t.strip().upper(), ex.strip().upper()))
                    else:
                        result.append((line.upper(), ""))
        except FileNotFoundError:
            con.print(f"[red]❌ Ticker-Datei nicht gefunden: {path}[/red]")
            sys.exit(1)
        return result

    tickers = _load_tickers(args.tickers)
    if not tickers:
        con.print(f"[yellow]⚠ Keine Ticker in {args.tickers}.[/yellow]")
        sys.exit(0)

    # ── Scraper importieren ───────────────────────────────────────────────────
    try:
        from fundamentals import process_ticker as fund_process
        from earnings import process_ticker as earn_process
    except ImportError as e:
        con.print(f"[red]❌ Import-Fehler: {e}[/red]")
        con.print("   Stelle sicher, dass fundamentals.py und earnings.py im selben Verzeichnis liegen.")
        sys.exit(1)

    con.print()
    con.print(Panel(
        f"[bold cyan]DB Befüllung[/bold cyan]\n"
        f"[dim]Fundamentals & Estimates für {len(tickers)} Ticker[/dim]\n"
        f"[dim]Quelle: {args.tickers}  →  DB: {args.db}[/dim]",
        border_style="cyan", expand=False,
    ))
    con.print()

    db = DB(args.db)
    total_fund_ins = total_fund_upd = 0
    total_est_ins = total_est_upd = 0
    failed_fund:  list[str] = []
    failed_est:   list[str] = []

    for i, (ticker, exchange) in enumerate(tickers, 1):
        con.rule(f"[bold cyan]{i}/{len(tickers)}  {ticker}[/bold cyan]")
        con.print()

        # ── Fundamentals ──────────────────────────────────────────────────────
        con.print(f"[bold]📆 Fundamentals …[/bold]")
        try:
            fund_records = fund_process(ticker, exchange, debug=args.debug)
            if fund_records:
                ins, upd = db.upsert_fundamentals(fund_records)
                total_fund_ins += ins
                total_fund_upd += upd
                con.print(f"  [green]✓[/green] [dim]+{ins} neu  ~{upd} aktualisiert[/dim]")
            else:
                con.print(f"  [yellow]⚠ Keine Fundamentals-Daten[/yellow]")
                failed_fund.append(ticker)
        except Exception as e:
            con.print(f"  [red]❌ Fundamentals Fehler: {e}[/red]")
            failed_fund.append(ticker)
            
        time.sleep(1.0)
            
        # ── Estimates ────────────────────────────────────────────────────────
        con.print(f"[bold]📊 Estimates …[/bold]")
        try:
            est_data = earn_process(ticker, exchange, debug=args.debug)
            if est_data:
                ins, upd = db.upsert_estimates(ticker, est_data)
                total_est_ins += ins
                total_est_upd += upd
                con.print(f"  [green]✓[/green] [dim]+{ins} neu  ~{upd} aktualisiert[/dim]")
            else:
                con.print(f"  [yellow]⚠ Keine Estimates-Daten[/yellow]")
                failed_est.append(ticker)
        except Exception as e:
            con.print(f"  [red]❌ Estimates Fehler: {e}[/red]")
            failed_est.append(ticker)

        con.print()
        if i < len(tickers):
            time.sleep(args.delay)

    db.close()

    # ── Abschlussstatus ───────────────────────────────────────────────────────
    con.rule("[bold]Ergebnis[/bold]")
    con.print()

    with DB(args.db) as db_check:
        info = db_check.summary()

    lines = (
        f"[bold]Fundamentals:[/bold] "
        f"[green]+{total_fund_ins} neu[/green]  [yellow]~{total_fund_upd} aktualisiert[/yellow]"
        + (f"  [red]| Fehler: {len(failed_fund)}[/red]" if failed_fund else "") + "\n"
        f"[bold]Estimates:[/bold]    "
        f"[green]+{total_est_ins} neu[/green]  [yellow]~{total_est_upd} aktualisiert[/yellow]"
        + (f"  [red]| Fehler: {len(failed_est)}[/red]" if failed_est else "") + "\n\n"
        f"[dim]DB fundamentals: {info['fund_rows']} Zeilen | {info['fund_tickers']} Ticker[/dim]\n"
        f"[dim]DB estimates:    {info['est_rows']} Zeilen | {info['est_tickers']} Ticker[/dim]"
    )
    con.print(Panel(lines, title="📦 Zusammenfassung", border_style="bright_black"))
    con.print()
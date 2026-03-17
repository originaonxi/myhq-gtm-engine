#!/usr/bin/env python3
"""myHQ GTM Engine — Master orchestrator.

Usage:
    python3 agent.py --run full --dry-run          # Full pipeline with synthetic data
    python3 agent.py --run signals --dry-run        # All signal ingestion
    python3 agent.py --run funding --dry-run        # Funding signals only
    python3 agent.py --run sdr --city BLR --dry-run # SDR list for Bengaluru
    python3 agent.py --run ads --dry-run            # Ad intelligence
    python3 agent.py --persona 1 --tier hot         # Hot funded founders only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import box

# ── Local imports ───────────────────────────────────────────────────
from config.settings import CITIES, DRY_RUN, INTENT_TIERS
from pipeline.signals_funding import collect_funding_signals
from pipeline.signals_hiring import collect_hiring_signals
from pipeline.signals_expansion import collect_expansion_signals
from pipeline.signals_intent import collect_intent_signals
from pipeline.enrichment import enrich_signals
from pipeline.scorer import score_leads
from pipeline.persona_matcher import match_personas
from pipeline.outreach_generator import generate_outreach
from pipeline.paid_ads import generate_ad_intelligence
from pipeline.sdr_dashboard import generate_sdr_dashboard, SDRDashboard
from pipeline.whatsapp_formatter import format_whatsapp_messages
from compliance.india import check_compliance

logger = logging.getLogger("myhq-gtm")


HEADER = r"""
╔══════════════════════════════════════════════════════════════╗
║                    myHQ GTM ENGINE v1.0                      ║
║         India's Most Sophisticated Lead Intelligence         ║
║                                                              ║
║  "Find companies the moment they need an office —            ║
║   arm your SDR with everything to close the call."           ║
╚══════════════════════════════════════════════════════════════╝
"""


class GTMEngine:
    """Master orchestrator for myHQ GTM intelligence pipeline."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.dry_run = args.dry_run or DRY_RUN
        self.console = Console()
        self.all_signals: list[dict] = []
        self.all_leads: list[dict] = []
        self.outreach_records: list[dict] = []
        self.start_time = time.time()

    def run(self) -> None:
        """Execute the pipeline based on CLI arguments."""
        self._display_header()

        dispatch = {
            "full": self._run_full_pipeline,
            "signals": self._run_all_signals,
            "funding": self._run_funding,
            "hiring": self._run_hiring,
            "expansion": self._run_expansion,
            "intent": self._run_intent,
            "enrich": self._run_enrichment,
            "outreach": self._run_outreach,
            "sdr": self._run_sdr,
            "ads": self._run_ads,
        }

        handler = dispatch.get(self.args.run)
        if handler:
            handler()
        else:
            self.console.print(f"[red]Unknown run mode: {self.args.run}[/red]")
            sys.exit(1)

        self._display_footer()

    # ── Full pipeline ───────────────────────────────────────────────

    def _run_full_pipeline(self) -> None:
        """Execute complete pipeline: signals → enrich → score → persona → compliance → outreach → SDR list."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            # Step 1: Collect signals
            task = progress.add_task("[cyan]Collecting funding signals…", total=7)
            funding = collect_funding_signals(dry_run=self.dry_run)
            progress.advance(task)
            progress.update(task, description="[cyan]Collecting hiring signals…")
            hiring = collect_hiring_signals(dry_run=self.dry_run, cities=self._get_cities())
            progress.advance(task)
            progress.update(task, description="[cyan]Collecting expansion signals…")
            expansion = collect_expansion_signals(dry_run=self.dry_run, cities=self._get_cities())
            progress.advance(task)
            progress.update(task, description="[cyan]Collecting intent signals…")
            intent = collect_intent_signals(dry_run=self.dry_run, cities=self._get_cities())
            progress.advance(task)

            self.console.print(f"  📡 Signals collected: funding={len(funding)}, hiring={len(hiring)}, expansion={len(expansion)}, intent={len(intent)}")

            # Step 2: Enrich
            progress.update(task, description="[yellow]Enriching leads…")
            leads: list[dict] = []
            leads.extend(enrich_signals(funding, "funding", dry_run=self.dry_run))
            leads.extend(enrich_signals(hiring, "hiring", dry_run=self.dry_run))
            leads.extend(enrich_signals(expansion, "expansion", dry_run=self.dry_run))
            leads.extend(enrich_signals(intent, "intent", dry_run=self.dry_run))
            progress.advance(task)

            # Step 3: Match personas
            progress.update(task, description="[yellow]Matching personas…")
            leads = match_personas(leads)
            progress.advance(task)

            # Step 4: Score
            progress.update(task, description="[yellow]Scoring leads…")
            leads = score_leads(leads)
            progress.advance(task)

        # Step 5: Compliance filter
        self.console.print("  🔒 Running compliance checks…")
        leads = check_compliance(leads, dry_run=self.dry_run)

        # Apply filters
        leads = self._filter_by_persona(leads)
        leads = self._filter_by_tier(leads)
        self.all_leads = leads

        self.console.print(f"  ✅ {len(leads)} leads ready after enrichment, scoring & compliance\n")

        # Step 6: Generate outreach
        self.console.print("  ✍️  Generating outreach…")
        self.outreach_records = generate_outreach(leads, dry_run=self.dry_run)

        # Attach outreach to leads for SDR display
        outreach_by_lead = {}
        for rec in self.outreach_records:
            lead_ref = rec.get("_lead", {})
            key = lead_ref.get("dedup_hash") or lead_ref.get("company_name", "")
            if key:
                outreach_by_lead[key] = rec
        for lead in leads:
            key = lead.get("dedup_hash") or lead.get("company_name", "")
            if key in outreach_by_lead:
                lead["_outreach"] = outreach_by_lead[key]

        # Step 7: SDR call list + dashboard
        generate_sdr_dashboard(leads, city=self.args.city, dry_run=self.dry_run)

        # Step 8: Ad intelligence
        self.console.print("\n  📊 Generating ad intelligence…")
        ads = generate_ad_intelligence(dry_run=self.dry_run)
        self._save_results(ads, "ad_intelligence")
        self.console.print("  ✅ Ad intelligence generated\n")

    # ── Individual pipeline stages ──────────────────────────────────

    def _run_all_signals(self) -> None:
        self.console.print("[bold]Running all signal collectors…[/bold]\n")
        funding = collect_funding_signals(dry_run=self.dry_run)
        hiring = collect_hiring_signals(dry_run=self.dry_run, cities=self._get_cities())
        expansion = collect_expansion_signals(dry_run=self.dry_run, cities=self._get_cities())
        intent = collect_intent_signals(dry_run=self.dry_run, cities=self._get_cities())
        self.all_signals = funding + hiring + expansion + intent
        self.console.print(f"\n[green]Total signals: {len(self.all_signals)}[/green]")
        self.console.print(f"  Funding: {len(funding)} | Hiring: {len(hiring)} | Expansion: {len(expansion)} | Intent: {len(intent)}")
        self._save_results({"funding": funding, "hiring": hiring, "expansion": expansion, "intent": intent}, "signals")

    def _run_funding(self) -> None:
        self.console.print("[bold]Collecting funding signals…[/bold]\n")
        signals = collect_funding_signals(dry_run=self.dry_run)
        self.all_signals = signals
        self.console.print(f"\n[green]Funding signals: {len(signals)}[/green]")
        self._save_results(signals, "signals_funding")

    def _run_hiring(self) -> None:
        self.console.print("[bold]Collecting hiring signals…[/bold]\n")
        signals = collect_hiring_signals(dry_run=self.dry_run, cities=self._get_cities())
        self.all_signals = signals
        self.console.print(f"\n[green]Hiring signals: {len(signals)}[/green]")
        self._save_results(signals, "signals_hiring")

    def _run_expansion(self) -> None:
        self.console.print("[bold]Collecting expansion signals…[/bold]\n")
        signals = collect_expansion_signals(dry_run=self.dry_run, cities=self._get_cities())
        self.all_signals = signals
        self.console.print(f"\n[green]Expansion signals: {len(signals)}[/green]")
        self._save_results(signals, "signals_expansion")

    def _run_intent(self) -> None:
        self.console.print("[bold]Collecting intent signals…[/bold]\n")
        signals = collect_intent_signals(dry_run=self.dry_run, cities=self._get_cities())
        self.all_signals = signals
        self.console.print(f"\n[green]Intent signals: {len(signals)}[/green]")
        self._save_results(signals, "signals_intent")

    def _run_enrichment(self) -> None:
        self.console.print("[bold]Running enrichment on existing signals…[/bold]\n")
        # Collect all signals first, then enrich
        self._run_all_signals()
        leads: list[dict] = []
        # Group by type
        for sig in self.all_signals:
            sig_type = "funding"  # Default; in production read from signal table
            if sig.get("jobs_count_this_week"):
                sig_type = "hiring"
            elif sig.get("city_entering"):
                sig_type = "expansion"
            elif sig.get("platform"):
                sig_type = "intent"
            enriched = enrich_signals([sig], sig_type, dry_run=self.dry_run)
            leads.extend(enriched)
        self.all_leads = leads
        self.console.print(f"\n[green]Enriched leads: {len(leads)}[/green]")

    def _run_outreach(self) -> None:
        self.console.print("[bold]Generating outreach for leads…[/bold]\n")
        # Need leads first
        if not self.all_leads:
            self._run_enrichment()
            self.all_leads = match_personas(self.all_leads)
            self.all_leads = score_leads(self.all_leads)
        self.outreach_records = generate_outreach(self.all_leads, dry_run=self.dry_run)
        self.console.print(f"\n[green]Outreach records generated: {len(self.outreach_records)}[/green]")

    def _run_sdr(self) -> None:
        self.console.print("[bold]Generating SDR call list…[/bold]\n")
        if not self.all_leads:
            self._run_full_pipeline()
            return
        generate_sdr_dashboard(self.all_leads, city=self.args.city, dry_run=self.dry_run)

    def _run_ads(self) -> None:
        self.console.print("[bold]Generating ad intelligence…[/bold]\n")
        ads = generate_ad_intelligence(dry_run=self.dry_run)
        self._display_ad_summary(ads)
        self._save_results(ads, "ad_intelligence")

    # ── Helpers ─────────────────────────────────────────────────────

    def _get_cities(self) -> list[str] | None:
        return [self.args.city] if self.args.city else None

    def _filter_by_persona(self, leads: list[dict]) -> list[dict]:
        if self.args.persona:
            return [l for l in leads if l.get("persona_id") == self.args.persona]
        return leads

    def _filter_by_tier(self, leads: list[dict]) -> list[dict]:
        if self.args.tier:
            tier = self.args.tier.upper()
            return [l for l in leads if l.get("tier") == tier]
        return leads

    def _display_header(self) -> None:
        self.console.print(f"[bold cyan]{HEADER}[/bold cyan]")
        mode = "[bold red]DRY RUN[/bold red]" if self.dry_run else "[bold green]LIVE[/bold green]"
        city = self.args.city or "All cities"
        persona = f"Persona {self.args.persona}" if self.args.persona else "All personas"
        tier = self.args.tier.upper() if self.args.tier else "All tiers"
        self.console.print(f"  Mode: {mode}  |  Run: {self.args.run}  |  City: {city}  |  {persona}  |  {tier}")
        self.console.print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
        self.console.print()

    def _display_footer(self) -> None:
        elapsed = time.time() - self.start_time
        self.console.print()
        self.console.print(Panel(
            f"[bold green]Pipeline complete in {elapsed:.1f}s[/bold green]\n"
            f"Signals: {len(self.all_signals)} | Leads: {len(self.all_leads)} | Outreach: {len(self.outreach_records)}",
            title="✅ Done",
            border_style="green",
        ))

    def _display_ad_summary(self, ads: dict) -> None:
        """Show ad intelligence summary."""
        self.console.print("\n[bold]📊 Ad Intelligence Summary[/bold]\n")

        # Google keywords
        google = ads.get("google", {})
        kw_table = Table(title="Google Ads Keywords (Top per City)", box=box.SIMPLE)
        kw_table.add_column("City")
        kw_table.add_column("Top Keyword")
        kw_table.add_column("Est. CPC (₹)")
        kw_table.add_column("Trend")
        for city_code, keywords in google.get("keywords_by_city", {}).items():
            if keywords:
                top = keywords[0]
                kw_table.add_row(
                    CITIES.get(city_code, {}).get("name", city_code),
                    top["keyword"],
                    f"₹{top['estimated_cpc_inr']}",
                    top["trend"],
                )
        self.console.print(kw_table)

        # Facebook audiences
        fb = ads.get("facebook", [])
        fb_table = Table(title="Facebook/Instagram Audiences", box=box.SIMPLE)
        fb_table.add_column("Audience")
        fb_table.add_column("Persona")
        fb_table.add_column("Est. Size")
        fb_table.add_column("Daily Budget")
        for aud in fb:
            fb_table.add_row(
                aud["audience_name"],
                str(aud.get("persona_target", "All")),
                aud.get("estimated_audience_size", "N/A"),
                f"₹{aud.get('recommended_daily_budget_inr', 0):,}",
            )
        self.console.print(fb_table)

        # LinkedIn campaigns
        li = ads.get("linkedin", [])
        self.console.print(f"\n  LinkedIn campaigns: {len(li)}")
        for camp in li:
            self.console.print(f"    • {camp['campaign_name']} — ₹{camp.get('recommended_daily_budget_inr', 0):,}/day")

    def _save_results(self, data: dict | list, prefix: str) -> str:
        os.makedirs(self.args.output_dir, exist_ok=True)
        filename = os.path.join(self.args.output_dir, f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(filename, "w") as f:
            json.dump(data, f, indent=2, default=str)
        self.console.print(f"  [dim]Saved: {filename}[/dim]")
        return filename


# ── CLI entry point ─────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="myHQ GTM Engine — India's most sophisticated lead intelligence system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 agent.py --run full --dry-run          Full pipeline with synthetic data
  python3 agent.py --run signals --dry-run        All signal ingestion
  python3 agent.py --run funding --dry-run        Funding signals only
  python3 agent.py --run hiring --dry-run         Hiring signals only
  python3 agent.py --run sdr --city BLR --dry-run SDR list for Bengaluru
  python3 agent.py --run ads --dry-run            Ad intelligence
  python3 agent.py --persona 1 --tier hot         Hot funded founders only
        """,
    )
    parser.add_argument(
        "--run",
        choices=["full", "signals", "funding", "hiring", "expansion", "intent", "enrich", "outreach", "sdr", "ads"],
        default="full",
        help="Pipeline stage to run (default: full)",
    )
    parser.add_argument("--city", choices=["BLR", "MUM", "DEL", "HYD", "PUN"], help="Filter by city")
    parser.add_argument("--persona", type=int, choices=[1, 2, 3], help="Filter by persona (1=Founder, 2=Ops, 3=Enterprise)")
    parser.add_argument("--tier", choices=["hot", "warm", "nurture", "monitor"], help="Filter by intent tier")
    parser.add_argument("--dry-run", action="store_true", help="Run with synthetic data, zero API calls")
    parser.add_argument("--output-dir", default="results", help="Output directory (default: results)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--json-only", action="store_true", help="Output JSON only, no terminal dashboard")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    engine = GTMEngine(args)
    engine.run()


if __name__ == "__main__":
    main()

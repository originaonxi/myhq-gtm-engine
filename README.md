# myHQ GTM Engine

> The GTM system that finds Indian companies the moment they need an office — and arms your SDR with everything they need to close the call.

## The Insight

**Funding + Hiring + Expansion = Workspace Need.**

When an Indian startup raises its seed round, the founder needs an office within 60 days. When a company posts 10 new jobs in Bengaluru, they need desks there within 30 days. When an enterprise registers a new GST number in Mumbai, they're committed to that city.

myHQ GTM Engine detects these signals in real-time across 23 sources, enriches them into complete lead profiles, and generates a prioritised SDR call list with personalised outreach — so your team calls the right person, at the right time, with the right message.

## Quick Start

```bash
# 1. Clone and install
pip3 install -r requirements.txt

# 2. Copy env file
cp .env.example .env

# 3. Run with synthetic data (zero API calls needed)
python3 agent.py --run full --dry-run
```

The `--dry-run` flag generates realistic synthetic data for 59 Indian companies across all 5 cities, with full enrichment, scoring, and SDR call cards.

## Four Signal Types

| Signal | Sources | Persona | Urgency |
|--------|---------|---------|---------|
| 🏦 **Funding** | Entrackr, Inc42, Tracxn, Crunchbase, LinkedIn, Twitter, Google News | Funded Founder | Call within 48h |
| 📋 **Hiring** | LinkedIn Jobs, Naukri, Foundit, Indeed, Wellfound | Ops Expander | Call within 7 days |
| 🏢 **Expansion** | MCA filings, GST portal, press releases, LinkedIn, business news, real estate | Enterprise Expander | Call within 14 days |
| 🔍 **Intent** | Reddit, Twitter, LinkedIn, Google Trends, IndiaMart | Any persona | Call ASAP |

## Three Buyer Personas

**Persona 1: The Funded Founder** (Seed → Series A, 5-50 employees)
- Just raised money, needs a real office fast
- Decision maker: Founder/CEO themselves
- Product fit: Fixed Desks, Private Cabins, Managed Office (10-30 seats)

**Persona 2: The Ops Expander** (50-300 employees)
- Hiring aggressively in a new city, needs desks
- Decision maker: Ops/Admin/Facilities Manager
- Product fit: Managed Office (30-100 seats), Fixed Desks

**Persona 3: The Enterprise Expander** (300+ employees)
- Entering a new city, needs compliant managed workspace
- Decision maker: VP Sales / BD Director
- Product fit: Managed Office (100+ seats), Commercial Leasing

## SDR Call List

Every lead card shows the SDR exactly what to say:

```
╭────────────────── 🔥 LEAD #1 — CALL NOW ───────────────────╮
│ Company: CleanGrid Energy | City: Mumbai | Score: 98/100    │
│ Contact: Rohan Deshmukh, Founder & CEO                      │
│ Phone: +91 98765 43210 (WhatsApp ✓)                         │
│ ──────────────────────────────────────────────────────────   │
│ TRIGGER: Raised ₹30Cr (seed round) (18h ago)                │
│ INVESTORS: Sequoia Capital India, ADB Ventures               │
│ TEAM SIZE: ~28 people | SECTOR: CleanTech                    │
│ ──────────────────────────────────────────────────────────   │
│ OPENING LINE:                                                │
│ "Hi Rohan, congrats on the ₹30Cr raise! I'm from myHQ —     │
│  we help funded startups get office-ready in 48 hours."      │
│ ──────────────────────────────────────────────────────────   │
│ 3 QUALIFYING QUESTIONS:                                      │
│   1. How many desks do you need initially?                   │
│   2. Any preference on location in Mumbai?                   │
│   3. When do you need to be set up by?                       │
╰─────────────── Persona: The Funded Founder ─────────────────╯
```

## Paid Ads Intelligence

Auto-generated audience segments for:
- **Google Ads**: City-specific keywords with CPC estimates and trend data
- **Facebook/Instagram**: 5 custom audiences (Funded Founders, Ops Professionals, Enterprise Decision Makers, Lookalikes, Competitor Conquesting)
- **LinkedIn**: Account-based targeting campaigns with ad copy variants

## WhatsApp-First Outreach

India is WhatsApp-first for B2B. The engine generates:
1. WhatsApp message (under 100 words, conversational)
2. WhatsApp follow-up day 3 (different angle)
3. Email subject + body (under 150 words)
4. Email follow-up day 5
5. LinkedIn connection message (under 300 chars)
6. SDR call script with opening line, qualifying questions, objection handlers

## TRAI / PDPB Compliance

- **NDNC check**: Every number verified before SDR call
- **Max 3 touches**: WhatsApp + Email + Call per lead
- **7-day cooling period** between outreach attempts
- **Consent tracking**: Per-lead, per-channel
- **Right to erasure**: Full PDPB compliance
- **Suppression list**: DNC, opt-outs, existing customers

## CLI Reference

```bash
python3 agent.py --run full          # Full pipeline
python3 agent.py --run signals       # All signal ingestion
python3 agent.py --run funding       # Funding signals only
python3 agent.py --run hiring        # Hiring signals only
python3 agent.py --run expansion     # Expansion signals only
python3 agent.py --run intent        # Intent signals only
python3 agent.py --run enrich        # Enrich existing signals
python3 agent.py --run outreach      # Generate outreach
python3 agent.py --run sdr           # Generate SDR call list
python3 agent.py --run ads           # Generate ad intelligence
python3 agent.py --city BLR          # Bengaluru only
python3 agent.py --city MUM          # Mumbai only
python3 agent.py --city DEL          # Delhi-NCR only
python3 agent.py --city HYD          # Hyderabad only
python3 agent.py --city PUN          # Pune only
python3 agent.py --persona 1         # Funded founders only
python3 agent.py --persona 2         # Ops expanders only
python3 agent.py --persona 3         # Enterprise expanders only
python3 agent.py --tier hot          # HOT leads only
python3 agent.py --dry-run           # Zero API calls, synthetic data
python3 agent.py --verbose           # Debug logging
```

## APIs Required (Production)

| API | Purpose | Free Tier |
|-----|---------|-----------|
| SerpAPI | Google Search, News, Trends | 100 searches/mo |
| ScraperAPI | LinkedIn, Naukri, websites | 1,000 requests/mo |
| Apollo.io | Contact enrichment | 50 credits/mo |
| Anthropic | Claude for outreach generation | Pay per use |
| Supabase | Database | 500MB free |
| WhatsApp Business | Message delivery | Pay per message |

## Project Structure

```
myhq-gtm-engine/
├── agent.py                    # Master orchestrator + CLI
├── config/
│   └── settings.py             # All configuration
├── pipeline/
│   ├── signals_funding.py      # 7 funding signal sources
│   ├── signals_hiring.py       # 5 hiring signal sources
│   ├── signals_expansion.py    # 6 expansion signal sources
│   ├── signals_intent.py       # 5 real-time intent sources
│   ├── enrichment.py           # Lead enrichment (Apollo + scraping)
│   ├── scorer.py               # 5-dimension intent scoring
│   ├── persona_matcher.py      # 3-persona matching
│   ├── outreach_generator.py   # Claude-powered outreach
│   ├── paid_ads.py             # Google/FB/LinkedIn ad intelligence
│   ├── sdr_dashboard.py        # Rich terminal SDR call list
│   ├── whatsapp_formatter.py   # WhatsApp Business API formatting
│   └── utils.py                # Shared utilities
├── compliance/
│   └── india.py                # TRAI DND + PDPB compliance
├── database/
│   └── schema.sql              # Full Supabase schema + seed data
├── tests/
│   ├── test_scorer.py          # 28 scoring tests
│   ├── test_persona_matcher.py # 15 persona tests
│   ├── test_deduplication.py   # 11 dedup tests
│   └── test_compliance.py      # 12 compliance tests
├── results/                    # Generated SDR lists + reports
├── ARCHITECTURE.md             # System design document
└── README.md                   # This file
```

## Target Cities

Bengaluru · Mumbai · Delhi-NCR · Hyderabad · Pune

---

Built for myHQ (myhq.in) — India's leading assisted workspace marketplace.

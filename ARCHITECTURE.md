# myHQ GTM Engine — Architecture

> The system that finds Indian companies the moment they need an office — and arms your SDR with everything to close the call.

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    SIGNAL COLLECTION                         │
│  Funding │ Hiring │ Expansion │ Intent                       │
│  (7 src) │ (5 src)│ (6 src)   │ (5 src)                     │
└────────────────────────┬────────────────────────────────────┘
                         │
                    ┌────▼────┐
                    │  DEDUP  │  MD5(company + type + city)
                    └────┬────┘
                         │
                ┌────────▼────────┐
                │   ENRICHMENT    │  Apollo.io + ScraperAPI + SerpAPI
                │  Company data   │  Decision maker contact
                │  Funding history│  News + competitor intel
                │  WhatsApp detect│
                └────────┬────────┘
                         │
            ┌────────────▼────────────┐
            │   PERSONA MATCHING      │
            │  1. Funded Founder      │
            │  2. Ops Expander        │
            │  3. Enterprise Expander │
            └────────────┬────────────┘
                         │
                ┌────────▼────────┐
                │  INTENT SCORING │  5 dimensions × 0-20 = 0-100
                │  HOT/WARM/      │
                │  NURTURE/MONITOR│
                └────────┬────────┘
                         │
               ┌─────────▼─────────┐
               │  COMPLIANCE CHECK  │  TRAI DND + PDPB + limits
               └─────────┬─────────┘
                         │
          ┌──────────────▼──────────────┐
          │      OUTREACH GENERATION    │  Claude API
          │  WhatsApp × 2 │ Email × 2   │
          │  LinkedIn │ SDR call script  │
          └──────────────┬──────────────┘
                         │
          ┌──────────────▼──────────────┐
          │      SDR CALL LIST          │  Prioritised by score
          │  Rich terminal dashboard    │  JSON + Markdown output
          └─────────────────────────────┘
```

## Why These Signal Sources

### Funding Signals (Persona 1: The Funded Founder)

Startups that just raised Seed to Series A need office space within 60-90 days. The **48-hour contact window** is critical — after that, competitors and brokers reach them first.

- **Entrackr + Inc42**: India's top two startup news outlets. Cover 90%+ of Indian funding announcements.
- **Tracxn + Crunchbase**: Database cross-references for completeness and data enrichment.
- **LinkedIn News**: Founders post "thrilled to announce" — catches deals before press coverage.
- **Twitter/X**: Real-time founder announcements, often 12-24 hours before news articles.
- **Google News**: Catch-all for press releases and regional media coverage.

### Hiring Signals (Persona 2: The Ops Expander)

A company posting **5+ jobs in a city where they had <2 jobs last week** = city expansion. They need desks in 30-60 days.

- **LinkedIn Jobs**: Largest professional job board in India. Company attribution is reliable.
- **Naukri.com**: India's #1 job portal by volume. Critical for mid-market companies.
- **Foundit (Monster India)**: Strong in Tier 1 cities, good for enterprise IT hiring.
- **Indeed India**: Broad coverage, good for operations/support roles.
- **Wellfound**: Startup-specific. Often shows company size and funding info alongside jobs.

### Expansion Signals (Persona 3: The Enterprise Expander)

Enterprises entering new cities need managed offices with compliance documentation. The sales cycle is 60-120 days, but early contact gives myHQ a trusted-advisor position.

- **MCA Filings**: New subsidiary/branch registrations = committed city entry.
- **GST Portal**: New GST number in a city = definitely setting up operations there.
- **Press Releases**: "Opens office in" announcements — company has committed but may not have workspace yet.
- **LinkedIn Company Updates**: Location additions often precede press announcements.
- **Business News (ET/BS/Mint)**: Coverage of major expansion decisions.
- **Real Estate Signals (JLL/CBRE/Colliers)**: Companies lease BEFORE they fit out. myHQ intercepts during the fit-out search.

### Intent Signals (All Personas)

People actively searching for workspace RIGHT NOW are the warmest leads.

- **Reddit India**: Founders and ops managers ask for recommendations in city-specific subreddits.
- **Twitter/X**: Real-time "need office space" and competitor mentions.
- **LinkedIn Posts**: Decision makers asking their network for workspace recommendations.
- **Google Trends**: Search volume spikes indicate city-level demand waves.
- **IndiaMart**: B2B marketplace where businesses list office space requirements.

## WhatsApp-First Strategy

India is a WhatsApp-first market for B2B communication:
- 500M+ WhatsApp users in India
- B2B decision makers check WhatsApp before email
- Response rates: WhatsApp 45-60% vs Email 15-20% vs Cold call 5-8%
- Indian business culture: informal, direct, relationship-driven

## Scoring Algorithm

Five dimensions, each 0-20 points:

| Dimension | What it measures | Max score |
|-----------|-----------------|-----------|
| Trigger Recency | How fresh is the signal | 20 |
| Trigger Strength | How strong is the buying indicator | 20 |
| Company Fit | Does company match ICP size/type | 20 |
| Reachability | Can we reach the decision maker | 20 |
| City + Product Fit | Is the city/sector alignment strong | 20 |

Tier thresholds: HOT ≥80, WARM ≥60, NURTURE ≥40, MONITOR <40.

## Adding New Cities

1. Add city to `config/settings.py` → `CITIES` dict with aliases, priority, sector strengths
2. Add city to `database/schema.sql` → `cities` table INSERT
3. All signal collectors automatically include new city via `CITIES` config
4. Update `pipeline/paid_ads.py` → `GOOGLE_KEYWORDS` with city-specific keywords
5. Run `python3 agent.py --city NEW --dry-run` to verify

## Scaling to 100+ Leads/Day

1. **Hourly cron**: `python3 agent.py --run full` via crontab
2. **City parallelism**: Run each city as a separate process
3. **Rate limiting**: SerpAPI (100 searches/month free), ScraperAPI (1000/month), Apollo (50/month free)
4. **Supabase**: Handles concurrent writes via upsert with dedup_hash conflict resolution
5. **SDR assignment**: `sdr_call_list.assigned_to` field — assign by city for specialisation

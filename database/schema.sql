-- ═══════════════════════════════════════════════════════════════════
-- myHQ GTM Engine — Supabase / PostgreSQL Schema
-- ═══════════════════════════════════════════════════════════════════

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Reference tables ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS personas (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    keywords      JSONB DEFAULT '[]',
    signal_sources JSONB DEFAULT '[]',
    product_fit   JSONB DEFAULT '[]',
    urgency       TEXT,
    sdr_script_angle TEXT,
    min_company_size INT DEFAULT 0,
    max_company_size INT DEFAULT 999999
);
COMMENT ON TABLE personas IS 'Three buyer personas from myHQ ICP document.';

CREATE TABLE IF NOT EXISTS cities (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    aliases       JSONB DEFAULT '[]',
    timezone      TEXT DEFAULT 'Asia/Kolkata',
    priority_rank INT DEFAULT 99
);
COMMENT ON TABLE cities IS 'Five target cities with priority rankings.';

CREATE TABLE IF NOT EXISTS competitors (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    website TEXT,
    cities_present JSONB DEFAULT '[]'
);
COMMENT ON TABLE competitors IS 'Top 10 competitor workspace brands.';

-- ── Signal tables ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signals_funding (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source              TEXT,
    company_name        TEXT NOT NULL,
    founder_name        TEXT,
    amount_raised       TEXT,
    round_type          TEXT CHECK (round_type IN ('seed','pre_seed','series_a','series_b','pre_series_a','bridge')),
    city                TEXT,
    sector              TEXT,
    investor_names      JSONB DEFAULT '[]',
    announcement_date   TIMESTAMPTZ,
    linkedin_company_url TEXT,
    founder_linkedin    TEXT,
    company_website     TEXT,
    employee_count_est  INT,
    raw_data            JSONB DEFAULT '{}',
    intent_score        INT DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT now(),
    processed           BOOLEAN DEFAULT false,
    dedup_hash          TEXT UNIQUE NOT NULL
);
COMMENT ON TABLE signals_funding IS 'Funding round announcements — Persona 1 triggers.';

CREATE INDEX idx_funding_city ON signals_funding (city);
CREATE INDEX idx_funding_round ON signals_funding (round_type);
CREATE INDEX idx_funding_date ON signals_funding (announcement_date DESC);
CREATE INDEX idx_funding_processed ON signals_funding (processed);
CREATE INDEX idx_funding_score ON signals_funding (intent_score DESC);

CREATE TABLE IF NOT EXISTS signals_hiring (
    id                      UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source                  TEXT,
    company_name            TEXT NOT NULL,
    city                    TEXT,
    jobs_count_this_week    INT DEFAULT 0,
    jobs_count_last_week    INT DEFAULT 0,
    delta                   INT DEFAULT 0,
    job_titles              JSONB DEFAULT '[]',
    company_size_est        INT,
    company_linkedin_url    TEXT,
    company_website         TEXT,
    is_new_to_city          BOOLEAN DEFAULT false,
    hiring_roles_senior_count INT DEFAULT 0,
    raw_data                JSONB DEFAULT '{}',
    intent_score            INT DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT now(),
    processed               BOOLEAN DEFAULT false,
    dedup_hash              TEXT UNIQUE NOT NULL
);
COMMENT ON TABLE signals_hiring IS 'Hiring velocity signals — Persona 2 triggers.';

CREATE INDEX idx_hiring_city ON signals_hiring (city);
CREATE INDEX idx_hiring_delta ON signals_hiring (delta DESC);
CREATE INDEX idx_hiring_new_city ON signals_hiring (is_new_to_city);
CREATE INDEX idx_hiring_score ON signals_hiring (intent_score DESC);

CREATE TABLE IF NOT EXISTS signals_expansion (
    id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source            TEXT,
    company_name      TEXT NOT NULL,
    company_size_est  INT,
    city_entering     TEXT,
    current_cities    JSONB DEFAULT '[]',
    announcement_date TIMESTAMPTZ,
    source_url        TEXT,
    contact_name      TEXT,
    contact_title     TEXT,
    company_website   TEXT,
    employee_count    INT,
    raw_data          JSONB DEFAULT '{}',
    intent_score      INT DEFAULT 0,
    created_at        TIMESTAMPTZ DEFAULT now(),
    processed         BOOLEAN DEFAULT false,
    dedup_hash        TEXT UNIQUE NOT NULL
);
COMMENT ON TABLE signals_expansion IS 'Enterprise city-expansion signals — Persona 3 triggers.';

CREATE INDEX idx_expansion_city ON signals_expansion (city_entering);
CREATE INDEX idx_expansion_size ON signals_expansion (company_size_est);
CREATE INDEX idx_expansion_score ON signals_expansion (intent_score DESC);

CREATE TABLE IF NOT EXISTS signals_intent (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source          TEXT,
    platform        TEXT,
    content_snippet TEXT,
    username        TEXT,
    company_mention TEXT,
    city            TEXT,
    urgency_level   TEXT CHECK (urgency_level IN ('high','medium','low')),
    contact_hint    TEXT,
    raw_data        JSONB DEFAULT '{}',
    intent_score    INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    processed       BOOLEAN DEFAULT false,
    dedup_hash      TEXT UNIQUE NOT NULL
);
COMMENT ON TABLE signals_intent IS 'Real-time workspace-search intent signals.';

CREATE INDEX idx_intent_city ON signals_intent (city);
CREATE INDEX idx_intent_urgency ON signals_intent (urgency_level);
CREATE INDEX idx_intent_platform ON signals_intent (platform);
CREATE INDEX idx_intent_score ON signals_intent (intent_score DESC);

-- ── Master lead table ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS leads (
    id                          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    signal_id                   UUID,
    signal_type                 TEXT CHECK (signal_type IN ('funding','hiring','expansion','intent')),
    persona_id                  INT REFERENCES personas(id),
    company_name                TEXT NOT NULL,
    company_size                INT,
    company_revenue             TEXT,
    company_funding_total       TEXT,
    company_last_funding_date   TIMESTAMPTZ,
    company_last_funding_amount TEXT,
    company_investors           JSONB DEFAULT '[]',
    contact_name                TEXT,
    contact_title               TEXT,
    contact_email               TEXT,
    contact_phone               TEXT,
    contact_whatsapp            TEXT,
    contact_linkedin            TEXT,
    company_linkedin            TEXT,
    company_website             TEXT,
    city                        TEXT,
    current_workspace           TEXT DEFAULT 'unknown',
    pain_points                 JSONB DEFAULT '[]',
    intent_score                INT DEFAULT 0,
    enrichment_score            INT DEFAULT 0,
    tier                        TEXT CHECK (tier IN ('HOT','WARM','NURTURE','MONITOR')),
    sdr_notes                   TEXT,
    created_at                  TIMESTAMPTZ DEFAULT now(),
    assigned_to                 TEXT,
    called_at                   TIMESTAMPTZ,
    call_outcome                TEXT,
    follow_up_date              DATE,
    dedup_hash                  TEXT UNIQUE NOT NULL
);
COMMENT ON TABLE leads IS 'Master enriched lead table — the SDR's source of truth.';

CREATE INDEX idx_leads_city ON leads (city);
CREATE INDEX idx_leads_persona ON leads (persona_id);
CREATE INDEX idx_leads_score ON leads (intent_score DESC);
CREATE INDEX idx_leads_tier ON leads (tier);
CREATE INDEX idx_leads_signal_type ON leads (signal_type);
CREATE INDEX idx_leads_created ON leads (created_at DESC);
CREATE INDEX idx_leads_assigned ON leads (assigned_to);

-- ── Outreach ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS outreach (
    id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id           UUID REFERENCES leads(id),
    persona_id        INT,
    whatsapp_touch_1  TEXT,
    whatsapp_touch_2  TEXT,
    email_subject     TEXT,
    email_touch_1     TEXT,
    email_touch_2     TEXT,
    linkedin_connect  TEXT,
    sdr_call_script   JSONB DEFAULT '{}',
    generated_at      TIMESTAMPTZ DEFAULT now(),
    whatsapp_sent_at  TIMESTAMPTZ,
    email_sent_at     TIMESTAMPTZ,
    replied_at        TIMESTAMPTZ,
    meeting_booked_at TIMESTAMPTZ,
    conversion_status TEXT DEFAULT 'pending'
);

CREATE INDEX idx_outreach_lead ON outreach (lead_id);
CREATE INDEX idx_outreach_status ON outreach (conversion_status);

-- ── SDR call list ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sdr_call_list (
    id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id       UUID REFERENCES leads(id),
    city          TEXT,
    persona_id    INT,
    priority_rank INT,
    intent_score  INT,
    tier          TEXT,
    call_date     DATE DEFAULT CURRENT_DATE,
    assigned_to   TEXT,
    status        TEXT DEFAULT 'pending',
    called_at     TIMESTAMPTZ,
    outcome       TEXT,
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_sdr_city ON sdr_call_list (city);
CREATE INDEX idx_sdr_date ON sdr_call_list (call_date);
CREATE INDEX idx_sdr_priority ON sdr_call_list (priority_rank);
CREATE INDEX idx_sdr_status ON sdr_call_list (status);

-- ── Ad intelligence ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ad_intelligence (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    platform            TEXT,
    audience_name       TEXT,
    audience_definition JSONB DEFAULT '{}',
    ad_copy_variants    JSONB DEFAULT '[]',
    keyword_list        JSONB DEFAULT '[]',
    city                TEXT,
    recommended_budget  NUMERIC,
    generated_at        TIMESTAMPTZ DEFAULT now(),
    performance_notes   TEXT
);

-- ── WhatsApp templates ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS whatsapp_templates (
    id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    template_name TEXT NOT NULL,
    category      TEXT CHECK (category IN ('utility','marketing')),
    language      TEXT DEFAULT 'en',
    body_text     TEXT,
    variables     JSONB DEFAULT '[]',
    status        TEXT DEFAULT 'draft',
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ── Suppression list ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS suppression_list (
    id      UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    phone   TEXT,
    email   TEXT,
    reason  TEXT CHECK (reason IN ('dnc','opt_out','existing_customer')),
    added_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_suppress_phone ON suppression_list (phone);
CREATE INDEX idx_suppress_email ON suppression_list (email);

-- ── Performance metrics ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS performance_metrics (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    date            DATE,
    city            TEXT,
    signals_collected INT DEFAULT 0,
    leads_enriched    INT DEFAULT 0,
    calls_made        INT DEFAULT 0,
    meetings_booked   INT DEFAULT 0,
    conversion_rate   NUMERIC DEFAULT 0,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════════
-- SEED DATA
-- ═══════════════════════════════════════════════════════════════════

-- 3 Personas
INSERT INTO personas (id, name, description, keywords, signal_sources, product_fit, urgency, sdr_script_angle, min_company_size, max_company_size)
VALUES
(1, 'The Funded Founder',
 'Seed to Series A startups that just raised funding and need real office space within 60 days.',
 '["founder","co-founder","CEO","CTO","raised","funding","seed","series-a","startup"]',
 '["entrackr","inc42","tracxn","crunchbase","linkedin_news","twitter","google_news"]',
 '["Fixed Desks","Private Cabins","Managed Office (10-30 seats)"]',
 'HIGH',
 'Congratulations on the raise — most founders we work with need a real office within 60 days. We can have you set up in a week.',
 1, 50),

(2, 'The Ops Expander',
 'Mid-size companies (50-300 employees) hiring aggressively in a new city.',
 '["operations","admin","facilities","office manager","procurement","vendor","workspace","team","GST","invoice"]',
 '["linkedin_jobs","naukri","foundit","indeed","wellfound"]',
 '["Managed Office (30-100 seats)","Fixed Desks"]',
 'MEDIUM',
 'We handle everything — shortlisting, site visits, documentation, GST invoicing. You present one vetted recommendation to your leadership.',
 50, 300),

(3, 'The Enterprise Expander',
 'Large companies (300+ employees) entering a new city via MCA filings, GST registrations, or press announcements.',
 '["VP","Director","expansion","new city","satellite","hub","compliance","legal","enterprise","pan-India"]',
 '["mca_filings","gst_portal","press_releases","linkedin_company","economic_times","real_estate_signals"]',
 '["Managed Office (100+ seats)","Commercial Leasing"]',
 'LOW-MEDIUM',
 'We work with JLL and CBRE-level clients. Full compliance documentation, dedicated account manager, references from similar companies.',
 300, 999999)

ON CONFLICT (id) DO NOTHING;

-- 5 Target Cities
INSERT INTO cities (code, name, aliases, timezone, priority_rank) VALUES
('BLR', 'Bengaluru', '["Bangalore","Bengaluru"]', 'Asia/Kolkata', 1),
('MUM', 'Mumbai',    '["Mumbai","Bombay"]',        'Asia/Kolkata', 2),
('DEL', 'Delhi-NCR', '["Delhi","Gurgaon","Gurugram","Noida","New Delhi","NCR"]', 'Asia/Kolkata', 3),
('HYD', 'Hyderabad', '["Hyderabad","Secunderabad"]','Asia/Kolkata', 4),
('PUN', 'Pune',      '["Pune"]',                    'Asia/Kolkata', 5)
ON CONFLICT (code) DO NOTHING;

-- 10 Competitor workspace brands
INSERT INTO competitors (name, website, cities_present) VALUES
('WeWork India',           'wework.co.in',         '["BLR","MUM","DEL","HYD","PUN"]'),
('Awfis',                  'awfis.com',            '["BLR","MUM","DEL","HYD","PUN"]'),
('IndiQube',               'indiqube.com',         '["BLR","HYD","PUN"]'),
('Smartworks',             'smartworks.co',        '["BLR","MUM","DEL","HYD","PUN"]'),
('Regus India',            'regus.co.in',          '["BLR","MUM","DEL","HYD","PUN"]'),
('91springboard',          '91springboard.com',    '["BLR","MUM","DEL","HYD"]'),
('CoWrks',                 'cowrks.com',           '["BLR","MUM","DEL"]'),
('Innov8',                 'innov8.work',          '["DEL","MUM","BLR"]'),
('The Executive Centre',   'executivecentre.com',  '["BLR","MUM","DEL","HYD"]'),
('Simpliwork',             'simpliwork.com',       '["BLR","MUM","DEL","HYD","PUN"]')
ON CONFLICT DO NOTHING;

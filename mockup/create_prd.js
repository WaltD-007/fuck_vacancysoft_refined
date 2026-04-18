const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageBreak, PageNumber, LevelFormat,
  TabStopType, TabStopPosition,
} = require("docx");

// ── Colours ──
const PURPLE = "6C5CE7";
const DARK = "1A1A2E";
const GREY = "6B7280";
const WHITE = "FFFFFF";
const BORDER_COL = "D1D5DB";

// ── Helpers ──
const border = { style: BorderStyle.SINGLE, size: 1, color: BORDER_COL };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };
const TABLE_W = 9360;
const COL2 = [3200, 6160];
const COL3 = [2200, 3580, 3580];
const COL4 = [1800, 2520, 2520, 2520];

function headerCell(text, width) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    shading: { fill: DARK, type: ShadingType.CLEAR },
    margins: cellMargins,
    children: [new Paragraph({ children: [new TextRun({ text, bold: true, color: WHITE, font: "Arial", size: 20 })] })],
  });
}
function cell(text, width, opts = {}) {
  const runs = Array.isArray(text) ? text : [new TextRun({ text, font: "Arial", size: 20, ...(opts.bold ? { bold: true } : {}), ...(opts.color ? { color: opts.color } : {}) })];
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    shading: opts.shading ? { fill: opts.shading, type: ShadingType.CLEAR } : undefined,
    margins: cellMargins,
    children: [new Paragraph({ children: runs })],
  });
}
function makeTable(cols, rows) {
  return new Table({
    width: { size: TABLE_W, type: WidthType.DXA }, columnWidths: cols,
    rows: rows.map((r, i) =>
      new TableRow({ children: r.map((t, j) => i === 0 ? headerCell(t, cols[j]) : cell(t, cols[j], j === 0 ? { bold: true } : {})) })
    ),
  });
}

function h1(text) { return new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 200 }, children: [new TextRun({ text, bold: true, font: "Arial", size: 36, color: DARK })] }); }
function h2(text) { return new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 280, after: 160 }, children: [new TextRun({ text, bold: true, font: "Arial", size: 28, color: PURPLE })] }); }
function h3(text) { return new Paragraph({ heading: HeadingLevel.HEADING_3, spacing: { before: 200, after: 120 }, children: [new TextRun({ text, bold: true, font: "Arial", size: 24, color: DARK })] }); }
function p(text) { return new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text, font: "Arial", size: 20 })] }); }
function pbold(label, text) { return new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: label, font: "Arial", size: 20, bold: true }), new TextRun({ text, font: "Arial", size: 20 })] }); }
function bullet(text, ref = "bullets") { return new Paragraph({ numbering: { reference: ref, level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text, font: "Arial", size: 20 })] }); }
function bulletBold(label, text) { return new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: label, font: "Arial", size: 20, bold: true }), new TextRun({ text, font: "Arial", size: 20 })] }); }
function num(text) { return new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text, font: "Arial", size: 20 })] }); }
function spacer() { return new Paragraph({ spacing: { after: 80 }, children: [] }); }
function pageBreak() { return new Paragraph({ children: [new PageBreak()] }); }
function divider() { return new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [new TextRun({ text: "\u2501".repeat(40), font: "Arial", size: 20, color: PURPLE })] }); }

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 20 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 36, bold: true, font: "Arial", color: DARK }, paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 28, bold: true, font: "Arial", color: PURPLE }, paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 24, bold: true, font: "Arial", color: DARK }, paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [
      { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "\u25E6", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
    ]},
    { reference: "numbers", levels: [
      { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
    ]},
  ]},
  sections: [
    // ════════════ TITLE PAGE ════════════
    {
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      children: [
        spacer(), spacer(), spacer(), spacer(), spacer(), spacer(), spacer(), spacer(),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [new TextRun({ text: "PROSPERO", font: "Arial", size: 72, bold: true, color: PURPLE })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 }, children: [new TextRun({ text: "Recruitment Intelligence Platform", font: "Arial", size: 32, color: GREY })] }),
        spacer(), spacer(), divider(), spacer(),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 }, children: [new TextRun({ text: "Product Requirements Document", font: "Arial", size: 28, bold: true, color: DARK })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 }, children: [new TextRun({ text: "Version 1.0  |  April 2026", font: "Arial", size: 22, color: GREY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 }, children: [new TextRun({ text: "CONFIDENTIAL", font: "Arial", size: 20, bold: true, color: "DC2626" })] }),
        spacer(), spacer(), spacer(), spacer(), spacer(), spacer(), spacer(), spacer(), spacer(),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 }, children: [new TextRun({ text: "Prepared for: Development Consultancy Partner", font: "Arial", size: 20, color: GREY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Prepared by: Prospero Ltd", font: "Arial", size: 20, color: GREY })] }),
      ],
    },

    // ════════════ MAIN CONTENT ════════════
    {
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      headers: { default: new Header({ children: [new Paragraph({ children: [
        new TextRun({ text: "PROSPERO", font: "Arial", size: 16, bold: true, color: PURPLE }),
        new TextRun({ text: "  |  Product Requirements Document  |  Confidential", font: "Arial", size: 16, color: GREY }),
      ], tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }] })] }) },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
        new TextRun({ text: "CONFIDENTIAL  |  Page ", font: "Arial", size: 16, color: GREY }),
        new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: GREY }),
      ]})] }) },
      children: [

        // ══════════ 1. EXECUTIVE SUMMARY ══════════
        h1("1. Executive Summary"),

        h2("1.1 The Problem"),
        p("Recruitment agencies waste significant time manually monitoring job boards, identifying hiring managers, and crafting outreach. A typical agency recruiter checks dozens of career sites daily, copies job details into spreadsheets, researches contacts on LinkedIn, and writes individualised emails. This process is slow, inconsistent, and unscalable."),
        p("The firms that win mandates are the ones that reach hiring managers first with intelligent, relevant outreach. Speed and insight are the differentiators, not headcount."),

        spacer(),
        h2("1.2 The Solution"),
        p("Prospero is a SaaS platform that automates the entire recruitment lead lifecycle: discovery, qualification, intelligence gathering, and outreach. It continuously monitors hundreds of job board sources, classifies and scores each vacancy, identifies the likely hiring manager, and executes personalised multi-step email campaigns on behalf of the recruiter."),
        p("The platform is vertical-agnostic. While the initial implementation focuses on financial services recruitment, the architecture is designed so that any recruitment agency, in any sector, in any country, can configure their own sources, categories, scoring criteria, and outreach templates."),

        spacer(),
        h2("1.3 What Already Exists"),
        p("A fully functional scraping and enrichment engine has been built and battle-tested against 800+ live sources. The following components are production-ready and should be wrapped by the web application, not rebuilt:"),
        bullet("29 platform-specific adapters (Workday, Greenhouse, iCIMS, Lever, Oracle Cloud, SuccessFactors, and 23 others)"),
        bullet("A generic browser scraper (Playwright) that handles unknown career sites with Cloudflare bypass, iframe detection, and SPA support"),
        bullet("Location normalisation engine (650+ city rules, 39 countries, 200+ company HQ fallbacks)"),
        bullet("Recruiter agency filtering (556 named agencies + keyword detection)"),
        bullet("Configurable classification and scoring engine"),
        bullet("URL auto-detection module that identifies the correct adapter from any careers page URL"),
        bullet("n8n workflow for AI-powered hiring intelligence dossier generation"),
        bullet("Interactive UI mockups covering all screens"),
        spacer(),
        p("The consultancy is expected to build the web application layer, campaign engine, integrations, and multi-tenant infrastructure around this existing engine."),

        pageBreak(),

        // ══════════ 2. TARGET MARKET ══════════
        h1("2. Target Market"),

        h2("2.1 Primary Audience"),
        p("Agency recruiters at specialist and generalist recruitment firms globally. The platform serves:"),
        spacer(),
        makeTable(COL3, [
          ["Segment", "Firm Size", "Value Proposition"],
          ["Boutique Specialists", "5-50 consultants", "Replaces manual job board monitoring. Levels the playing field against larger firms with dedicated research teams."],
          ["Mid-Market Agencies", "50-500 consultants", "Scalable lead generation across multiple desks/verticals. Centralised source management and campaign analytics."],
          ["Enterprise Staffing", "500+ consultants", "White-labelled deployment per division. API integration with existing tech stack (Bullhorn, Salesforce, Vincere)."],
        ]),

        spacer(),
        h2("2.2 Vertical Flexibility"),
        p("The platform must support any recruitment vertical, not just financial services. The architecture must allow each tenant to define:"),
        bullet("Their own job categories (e.g. a legal recruiter would use 'Partner', 'Associate', 'Paralegal', 'In-House Counsel' rather than 'Risk', 'Quant', 'Compliance')"),
        bullet("Their own scoring weights (a tech recruiter values different signals than a finance recruiter)"),
        bullet("Their own source lists (each firm monitors different companies and geographies)"),
        bullet("Their own outreach templates and tone preferences"),
        bullet("Their own geographic focus and allowed countries"),
        spacer(),
        p("The category system, scoring engine, location rules, and campaign templates must all be configurable per tenant through the UI. Nothing should be hardcoded to a specific vertical."),

        spacer(),
        h2("2.3 Geographic Scope"),
        p("The platform must support global operations from day one:"),
        bullet("Location normalisation already covers 39 countries across North America, Europe, Middle East, and Asia-Pacific"),
        bullet("Aggregator adapters (Adzuna, Reed, eFinancialCareers) cover 15+ country-specific job markets"),
        bullet("UI must support configurable allowed-country lists per tenant"),
        bullet("Email campaigns must respect timezone differences for send scheduling"),
        bullet("GDPR, CAN-SPAM, and PECR compliance for outreach in relevant jurisdictions"),

        pageBreak(),

        // ══════════ 3. PRODUCT ARCHITECTURE ══════════
        h1("3. Product Architecture"),

        h2("3.1 System Overview"),
        p("Prospero consists of four subsystems operating as a pipeline:"),
        spacer(),
        makeTable(COL2, [
          ["Subsystem", "Description"],
          ["Discovery Engine", "Continuously scrapes job board sources on a configurable schedule. Uses platform-specific API adapters where available, falling back to browser automation for unknown sites. Outputs raw job records."],
          ["Enrichment Pipeline", "Normalises locations, extracts employer data, filters recruitment agencies, resolves missing fields via company HQ mappings and detail page fetching. Produces qualified, structured lead records."],
          ["Intelligence Layer", "Classifies jobs into tenant-defined categories, scores against configurable criteria, identifies hiring managers via AI and CRM lookup, generates hiring intelligence dossiers on demand."],
          ["Campaign Engine", "Manages multi-step email sequences with per-step tone, timing, and template controls. Tracks opens, replies, bounces, and meeting bookings. Integrates with CRM for activity logging."],
        ]),

        spacer(),
        h2("3.2 User Roles"),
        makeTable(COL3, [
          ["Role", "Permissions", "Use Case"],
          ["Owner / Admin", "Full platform access, user management, billing, tenant settings, API keys", "Agency owner, operations director"],
          ["Recruiter", "View and filter leads, create and manage campaigns, add sources, configure personal scoring preferences", "Consultant working a desk"],
          ["Team Lead", "Everything Recruiter has, plus team-level analytics, campaign approval, source management", "Desk head, team manager"],
          ["Viewer", "Read-only access to leads, campaigns, and analytics", "Stakeholder, board reporting"],
        ]),

        pageBreak(),

        // ══════════ 4. DISCOVERY ENGINE ══════════
        h1("4. Discovery Engine"),
        p("The discovery engine is the core competitive advantage. It must reliably extract job listings from hundreds of sources daily, across dozens of different platforms."),

        h2("4.1 Supported Platforms"),
        p("The existing engine supports 29 adapter types. Each adapter encapsulates the platform-specific logic for extracting job listings:"),
        spacer(),
        makeTable(COL4, [
          ["Category", "Platforms", "Method", "Coverage"],
          ["Enterprise ATS", "Workday, Oracle Cloud, SuccessFactors, Taleo", "JSON API + Browser", "~400 boards"],
          ["Modern ATS", "Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Pinpoint, HiBob", "REST API", "~100 boards"],
          ["Specialist ATS", "iCIMS, Eightfold, Beamery, Phenom, SelectMinds, Silkroad, ClearCompany, ADP, Infor, NeoGov", "API + Browser", "~50 boards"],
          ["Aggregators", "Adzuna, Reed, eFinancialCareers, Google Jobs", "REST API", "~20k jobs across 15+ countries"],
          ["Generic", "Playwright browser (Chromium + Firefox)", "Browser automation", "Unlimited (any careers page)"],
        ]),

        spacer(),
        h2("4.2 Add Source (URL Auto-Detection)"),
        p("This is a critical user-facing feature. A recruiter pastes any careers page URL and the system automatically:"),
        spacer(),
        num("Pattern-matches the URL against known ATS platforms (e.g. *.myworkdayjobs.com = Workday, *.icims.com = iCIMS, boards.greenhouse.io/* = Greenhouse)"),
        num("Extracts the platform-specific identifier (slug, tenant ID) automatically"),
        num("Runs a live validation request to confirm the board is reachable and active"),
        num("Returns the detected platform, estimated job count, and a company name guess within 3 seconds"),
        num("User confirms or edits the company name, then the source is added to the database and included in the next scheduled run"),
        spacer(),
        p("If no known platform is detected, the source is assigned to the generic browser scraper which can handle most career sites."),
        p("A working Python implementation of this detection module exists (source_detector.py) and should be used as the reference implementation."),

        spacer(),
        h2("4.3 Generic Browser Scraper"),
        p("For sites without a dedicated adapter, the system uses Playwright-based browser automation. This is the most complex adapter and handles:"),
        bullet("Cloudflare bot detection with automatic Firefox fallback (Chromium is tried first, Firefox launched only when Cloudflare is detected)"),
        bullet("Single-page application (SPA) support including hash routing (e.g. #/job/details/224)"),
        bullet("AJAX-loaded content with intelligent retry (if zero jobs found on first scan, wait 5 seconds, scroll, and re-scan)"),
        bullet("Iframe scanning for embedded job boards (common with iCIMS and Salesforce)"),
        bullet("CTA link resolution (when link text is generic like 'Learn more' or 'Apply now', extract the job title from the URL slug)"),
        bullet("Batch mode: 15 boards share a single Playwright browser instance with fresh isolated context per board, preventing memory bloat"),
        bullet("Configurable job-URL patterns and non-job title filters to maximise accuracy"),

        spacer(),
        h2("4.4 Operational Requirements"),
        makeTable(COL2, [
          ["Requirement", "Specification"],
          ["Scheduling", "Configurable per tenant. Default daily. Manual trigger available via UI ('Run Now')."],
          ["Timeouts", "120s standard adapters, 300s browser-heavy adapters (SuccessFactors, Oracle, iCIMS, generic), 900s aggregators. Configurable per source."],
          ["Error Isolation", "A failing source must never block other sources. Each source runs independently with its own error handling."],
          ["Status Tracking", "Per-source status after each run: OK, FAIL (with error message), PARTIAL (some jobs persisted before timeout), EMPTY (reachable but no jobs found)."],
          ["Deduplication", "Within-run dedup by URL. Cross-run dedup for incremental delivery (only new leads sent to campaigns)."],
          ["Pagination Guards", "Server-reported totals used to prevent infinite loops. URL dedup to catch API wrap-around. Configurable max pages per source."],
          ["Rate Limiting", "Per-adapter request delays (e.g. 1s between Adzuna API calls). Exponential backoff on 429/503 responses."],
        ]),

        pageBreak(),

        // ══════════ 5. ENRICHMENT PIPELINE ══════════
        h1("5. Enrichment Pipeline"),
        p("Raw discovered jobs go through a multi-stage enrichment process before they are presented to users."),

        h2("5.1 Location Normalisation"),
        p("Every job must be mapped to a standardised country and city. The existing engine handles:"),
        bullet("Country-aware structured parsing: split location string on commas, resolve country from the trailing token first, then identify city constrained by country"),
        bullet("650+ city-to-country mapping rules (loaded from YAML configuration, editable per tenant)"),
        bullet("US state code resolution, UK county detection (91 counties), Canadian province detection (71 regions)"),
        bullet("Ambiguous city handling: cities like Birmingham, Cambridge, and London are resolved using country context rather than defaulting"),
        bullet("Company HQ fallback: 200+ company-to-country mappings for boards that return empty location fields"),
        bullet("Configurable allowed-country list per tenant (jobs outside allowed countries are filtered out)"),

        spacer(),
        h2("5.2 Employer Extraction"),
        bullet("Platform-specific employer parsing from aggregator API payloads"),
        bullet("Provenance fallback: employer name inherited from source configuration when not available in the listing"),
        bullet("Employer normalisation: variant names mapped to canonical company name"),

        spacer(),
        h2("5.3 Agency Filtering"),
        p("Jobs posted by recruitment agencies rather than direct employers must be automatically filtered:"),
        bullet("Named agency list (currently 556 entries, must be editable per tenant)"),
        bullet("Keyword fallback: employer names containing 'recruitment', 'recruiting', 'staffing', 'headhunt', 'talent acquisition agency', etc."),
        bullet("Filtered jobs are tagged (not deleted) so they can be reviewed if needed"),

        spacer(),
        h2("5.4 Detail Fetching"),
        p("For leads that pass initial filtering, the full job description is fetched from the detail page URL. This provides the rich text needed for AI dossier generation and improves classification accuracy."),

        pageBreak(),

        // ══════════ 6. INTELLIGENCE LAYER ══════════
        h1("6. Intelligence Layer"),

        h2("6.1 Classification"),
        p("Each enriched job is classified into one or more tenant-defined categories. The classification engine must support:"),
        bullet("Title-based keyword matching (configurable keyword lists per category)"),
        bullet("Description-based analysis for ambiguous titles"),
        bullet("Sub-specialisms (e.g. a job classified as 'Risk' with 'quant' in the title gets a 'Quant Risk' sub-tag)"),
        bullet("Multi-label classification (a job can belong to multiple categories)"),
        bullet("Per-tenant category definitions: the system ships with a default set, but each tenant can create, rename, or remove categories"),
        spacer(),
        p("Example category configurations for different verticals:"),
        makeTable(COL2, [
          ["Vertical", "Example Categories"],
          ["Financial Services", "Risk, Quant, Compliance, Audit, Cyber, Legal, Front Office, Operations"],
          ["Technology", "Engineering, Product, Design, Data Science, DevOps, Security, QA, Management"],
          ["Legal", "Partner, Associate, Counsel, Paralegal, Compliance, Regulatory, In-House"],
          ["Life Sciences", "R&D, Clinical, Regulatory Affairs, Quality, Medical Affairs, Commercial"],
        ]),

        spacer(),
        h2("6.2 Scoring"),
        p("Every classified lead receives a score (1-10) based on configurable weighted criteria:"),
        bullet("Category relevance to the tenant's focus areas"),
        bullet("Seniority level (extracted from title keywords)"),
        bullet("Location (priority markets score higher, configurable per tenant)"),
        bullet("Company tier (configurable company groupings: e.g. 'Tier 1', 'Tier 2', 'Target Account')"),
        bullet("Role specificity (niche roles score higher than generic titles)"),
        bullet("Recency (recently posted jobs score higher)"),
        spacer(),
        p("Scoring weights must be adjustable through the UI with slider controls. Changes take effect on the next pipeline run."),

        spacer(),
        h2("6.3 Hiring Manager Identification"),
        p("For each qualified lead, the system identifies the most likely hiring manager(s) through multiple channels:"),
        spacer(),
        makeTable(COL3, [
          ["Source", "Method", "Output"],
          ["AI Analysis", "LLM analyses the job description, company structure, and seniority level to identify up to 3 probable hiring managers with names, titles, and LinkedIn URLs", "Primary for new leads"],
          ["CRM Lookup", "Searches the tenant's connected CRM (Bullhorn, Salesforce, Vincere) by company name and relevant seniority/title keywords", "Preferred when CRM data exists"],
          ["Manual Entry", "User enters name, job title, email, phone, and LinkedIn URL directly", "Override for known contacts"],
          ["CSV Upload", "Bulk import of contact lists with company, name, and email columns", "Batch operations"],
        ]),
        spacer(),
        p("Each AI-suggested contact displays a confidence score (percentage). The user selects which contact to target, or enters their own. The selected contact populates the {hiring_manager} merge variable in campaign emails."),

        spacer(),
        h2("6.4 AI Dossier Generation"),
        p("On demand (or automatically for leads above a configurable score threshold), the system generates a Hiring Intelligence Dossier using an LLM. The dossier provides the recruiter with:"),
        bullet("Company and market context beyond the job description"),
        bullet("The core business problem driving the hire"),
        bullet("Gap analysis: what the JD asks for vs what the business likely needs (two-column table)"),
        bullet("Specification and execution risk: over-specification, conflicting expectations, pay mismatches"),
        bullet("Two ideal candidate profiles with sourcing guidance"),
        bullet("Copy-paste Boolean search strings for LinkedIn/job boards"),
        bullet("Lead score with justification"),
        bullet("Hiring manager identification"),
        bullet("Draft outreach sequence (5 steps, tone-matched to campaign settings)"),
        spacer(),
        p("The dossier prompt is approximately 2000 words and is highly engineered. An existing n8n workflow implementation (Workflow C) exists and should be used as the reference for prompt structure. The prompt must be templatised so that different verticals can customise the research focus (e.g. a tech recruiter's dossier emphasises tech stack and engineering culture rather than credit risk)."),

        pageBreak(),

        // ══════════ 7. CAMPAIGN ENGINE ══════════
        h1("7. Campaign Engine"),
        p("The campaign engine automates multi-step email outreach to hiring managers for qualified leads. This is a new-build component."),

        h2("7.1 Campaign Builder"),
        p("Users design campaigns through a dedicated builder interface (separate page in the UI). Configuration at two levels:"),

        spacer(),
        h3("Campaign-Level Settings"),
        makeTable(COL2, [
          ["Setting", "Description"],
          ["Campaign Name", "User-defined label (e.g. 'Risk Managers UK Q2', 'VP Engineering FAANG')"],
          ["Target Category", "Filter leads by category, or 'All Categories'"],
          ["Minimum Score", "Slider (1-10). Only leads at or above this score are enrolled."],
          ["Geography Filter", "Optional country/region filter"],
          ["Hiring Manager Source", "AI Auto-Match (recommended), CRM Lookup (Bullhorn/Salesforce/Vincere), Manual Only, or CSV Upload"],
          ["Auto-Enrol", "Toggle: automatically add matching leads as they are discovered, or manual enrolment only"],
        ]),

        spacer(),
        h3("Per-Step Settings (5 steps, expandable)"),
        makeTable(COL2, [
          ["Setting", "Options"],
          ["Message Tone", "Formal, Informal, Technical, Candidate Spec, Consultative, Direct (dropdown per step)"],
          ["Wait Period", "Configurable days before sending: 1, 2, 3, 4, 5, 7, 10, 14, 21, 30, 45, 60 (dropdown per step)"],
          ["Trigger Condition", "If no reply (default), If no reply and role still open (for later steps), Always send"],
          ["Email Template", "Editable rich text with merge variable insertion"],
        ]),

        spacer(),
        h3("Merge Variables"),
        p("Templates support dynamic fields populated from lead data:"),
        bullet("{hiring_manager} - Selected contact's first name"),
        bullet("{hiring_manager_full} - Full name"),
        bullet("{company} - Employer name"),
        bullet("{job_title} - Role title"),
        bullet("{location} - Normalised city, country"),
        bullet("{category} - Lead classification"),
        bullet("{similar_company} - AI-suggested comparable firm (from dossier)"),
        bullet("{sender_name}, {sender_company}, {sender_title}, {sender_phone} - User profile fields"),
        bullet("{unsubscribe_link} - Required for compliance"),

        spacer(),
        h2("7.2 Campaign Execution"),
        num("Leads enter Step 1 when they match campaign filters (automatically or via manual selection from Lead Explorer)"),
        num("A daily scheduler advances leads to the next step if the configured wait period has elapsed and no reply has been received"),
        num("Campaign auto-pauses for a lead if: contact replies, contact unsubscribes, job posting is detected as no longer active, or email hard-bounces"),
        num("Open tracking via pixel, click tracking via redirect URLs, reply detection via inbox webhook or IMAP polling"),
        num("Meeting booking detection via calendar link clicks or configurable reply keyword matching"),
        num("All campaign activity is logged to the connected CRM (Bullhorn, Salesforce, Vincere)"),

        spacer(),
        h2("7.3 Campaign Analytics"),
        p("Two views: aggregate metrics and per-lead tracking."),
        spacer(),
        h3("Aggregate Dashboard"),
        bullet("Total emails sent, open rate, reply rate, meetings booked, bounce rate, unsubscribe rate"),
        bullet("Per-step funnel: Enrolled > Opened > Replied > Interested > Meeting Booked"),
        bullet("Trend charts over time"),
        spacer(),
        h3("Lead-Level Table"),
        p("Each row represents one lead being pursued:"),
        bullet("Company and job title"),
        bullet("Assigned hiring manager with name and email"),
        bullet("Current step (e.g. '3 of 5')"),
        bullet("Status: Sent / Opened / Replied / Meeting Booked / No Response / Paused"),
        bullet("Open tracking ratio (e.g. '4/4' = opened all sent emails)"),
        bullet("Last activity timestamp"),
        bullet("Filter by: All / In Sequence / Replied / Meeting Booked / No Response"),

        pageBreak(),

        // ══════════ 8. USER INTERFACE ══════════
        h1("8. User Interface"),
        p("Interactive HTML/CSS mockups are provided with this document (mockup/index.html). The mockups are fully navigable and demonstrate the design language, data layout, and interaction patterns for all screens."),

        spacer(),
        h2("8.1 Dashboard"),
        bullet("KPI stat cards: Total Leads, Scored & Qualified, Sources Active, Campaigns Active, Avg Score"),
        bullet("Leads discovered over time (chart with 7D / 30D / 90D toggle)"),
        bullet("Category breakdown with proportional bars"),
        bullet("Live feed of incoming leads with real-time score badges and category tags"),
        bullet("Source health panel: per-source status, adapter type, job count, response time"),

        spacer(),
        h2("8.2 Lead Explorer"),
        bullet("Filterable data table with category chip filters across the top"),
        bullet("Columns: Title, Company, Location, Category, Score, Source, Discovered"),
        bullet("Bulk select with checkbox for campaign enrolment or export"),
        bullet("Export to CSV/Excel"),
        bullet("Direct 'Create Campaign' action from selected leads"),

        spacer(),
        h2("8.3 Source Management"),
        bullet("'Add Source' panel: expandable URL input with live detection, validation result, editable company name, one-click confirm"),
        bullet("Source card grid: company name, platform badge, job count, qualified count, response time"),
        bullet("Failing sources highlighted with red border and error details"),
        bullet("'Run Now' button for on-demand pipeline execution"),
        bullet("Toggle active/inactive per source"),

        spacer(),
        h2("8.4 Campaigns"),
        bullet("Lead-level table showing all active pursuits across campaigns"),
        bullet("Per-lead: company, job title, hiring manager (name + email), category tag, current step, status, open ratio, last activity"),
        bullet("Aggregate metrics bar: emails sent, open rate, reply rate, meetings booked"),

        spacer(),
        h2("8.5 Campaign Builder (Separate Page)"),
        bullet("Campaign-level config panel: name, target category, min score slider, hiring manager source selector"),
        bullet("5-step sequence builder with visual connector lines showing wait periods"),
        bullet("Per-step: tone dropdown, wait-days dropdown, editable email template"),
        bullet("Live email preview with merge variable highlighting in accent colour"),
        bullet("Hiring manager selector: AI suggestions (up to 3 with confidence %), CRM match, manual entry form (name, title, email, phone, LinkedIn), CSV upload"),
        bullet("'+ Add Step' for extending sequences beyond 5"),
        bullet("Actions: Save Draft, Send Test, Launch Campaign"),

        spacer(),
        h2("8.6 Design System"),
        bullet("Dark theme, purple (#6C5CE7) accent, card-based layout"),
        bullet("Inter font for UI, JetBrains Mono for metrics and data"),
        bullet("Colour-coded categories (configurable per tenant)"),
        bullet("Score badges: green (high), amber (medium), grey (low)"),
        bullet("Source status: green dot (OK), red dot (FAIL), amber dot (PARTIAL), grey dot (EMPTY)"),
        bullet("Responsive: desktop-first, with tablet breakpoint for key screens"),

        pageBreak(),

        // ══════════ 9. INTEGRATIONS ══════════
        h1("9. Integrations"),

        h2("9.1 CRM Integration"),
        p("CRM integration is critical for hiring manager matching and activity logging. The platform must support:"),
        spacer(),
        makeTable(COL3, [
          ["CRM", "Priority", "Capabilities Required"],
          ["Bullhorn", "Launch (primary)", "Contact search by company + title, activity/note creation, candidate record sync, OAuth2 authentication"],
          ["Salesforce", "Phase 2", "Contact/lead search, task creation, opportunity tracking"],
          ["Vincere", "Phase 2", "Contact search, activity logging, placement tracking"],
        ]),
        spacer(),
        p("All CRM integrations must support:"),
        bullet("Two-way contact matching: Prospero leads matched to existing CRM contacts, new contacts from AI discovery pushed to CRM"),
        bullet("Activity logging: each campaign step (sent, opened, replied, meeting booked) recorded against the CRM contact"),
        bullet("OAuth2 authentication flow managed per tenant"),

        spacer(),
        h2("9.2 Email Delivery"),
        bullet("SendGrid or Mailgun for transactional delivery with pixel tracking and webhook callbacks"),
        bullet("Alternatively, direct SMTP for firms using their own mail infrastructure"),
        bullet("Open tracking (pixel), click tracking (redirect), reply detection (inbox webhook or IMAP)"),
        bullet("Bounce handling: hard bounces auto-pause, soft bounces retry"),
        bullet("Unsubscribe handling: one-click unsubscribe link, automatic suppression"),
        bullet("Domain authentication: SPF, DKIM, DMARC per tenant sending domain"),

        spacer(),
        h2("9.3 AI / LLM"),
        bullet("OpenAI GPT-4 (existing implementation via n8n) and/or Anthropic Claude API"),
        bullet("Used for: hiring manager identification, dossier generation, outreach email tone adaptation"),
        bullet("Prompt templates stored per tenant with vertical-specific customisation"),
        bullet("Token usage metering per tenant for billing"),

        spacer(),
        h2("9.4 Future Integrations"),
        bullet("SourceWhale: direct campaign sync for firms already using SourceWhale for email sequencing"),
        bullet("LinkedIn Sales Navigator: contact enrichment and InMail integration"),
        bullet("Slack / Microsoft Teams: real-time notifications for high-score leads, campaign replies, and meeting bookings"),
        bullet("Zapier / Make: webhook-based integration point for custom workflows"),
        bullet("Calendar (Google Calendar, Outlook): meeting booking links in email templates with auto-detection"),

        pageBreak(),

        // ══════════ 10. DATA MODEL ══════════
        h1("10. Data Model"),
        p("The existing SQLite schema must be migrated to PostgreSQL. All tables must include tenant_id for multi-tenant isolation."),
        spacer(),
        makeTable(COL3, [
          ["Table", "Purpose", "Key Fields"],
          ["tenants", "Tenant configuration", "name, domain, plan, settings_json, branding"],
          ["users", "User accounts", "tenant_id, email, role, name, sender_profile"],
          ["sources", "Board configurations", "tenant_id, adapter_name, job_board_url, employer_name, active, last_run_status"],
          ["source_runs", "Execution audit log", "source_id, status, jobs_found, duration_ms, error_message, ran_at"],
          ["raw_jobs", "Discovered listings", "source_id, title_raw, location_raw, discovered_url, posted_at_raw, description_raw"],
          ["enriched_jobs", "Normalised leads", "raw_job_id, title_clean, country, city, employer, detail_fetch_status"],
          ["classifications", "Category assignments", "enriched_job_id, category, sub_category, confidence"],
          ["scores", "Scoring results", "enriched_job_id, score, decision, weights_snapshot"],
          ["campaigns", "Campaign definitions", "tenant_id, user_id, name, target_category, min_score, hm_source, status, auto_enrol"],
          ["campaign_steps", "Per-step config", "campaign_id, step_number, tone, wait_days, template_html, subject_line"],
          ["campaign_leads", "Enrolled leads", "campaign_id, enriched_job_id, hm_name, hm_email, hm_source, current_step, status"],
          ["campaign_events", "Activity tracking", "campaign_lead_id, step_number, event_type (sent/opened/replied/bounced/unsubscribed/meeting), timestamp"],
          ["categories", "Tenant-defined categories", "tenant_id, name, colour, keywords_json, sort_order"],
          ["scoring_profiles", "Tenant scoring config", "tenant_id, weights_json, thresholds_json"],
        ]),

        pageBreak(),

        // ══════════ 11. TECHNICAL REQUIREMENTS ══════════
        h1("11. Technical Requirements"),

        h2("11.1 Recommended Stack"),
        makeTable(COL2, [
          ["Component", "Technology"],
          ["Frontend", "Next.js (React), TypeScript, Tailwind CSS"],
          ["Backend API", "FastAPI (Python) wrapping the existing scraping engine, or Node.js with Python microservice"],
          ["Database", "PostgreSQL (migrated from SQLite)"],
          ["Cache / Queue", "Redis + Celery (or BullMQ) for scheduled scraping, campaign step advancement, and background tasks"],
          ["Browser Automation", "Playwright (Chromium + Firefox) in containerised environment"],
          ["Email Delivery", "SendGrid / Mailgun with webhook callbacks"],
          ["AI / LLM", "OpenAI GPT-4 and/or Anthropic Claude API"],
          ["CRM", "Bullhorn REST API (launch), Salesforce / Vincere (phase 2)"],
          ["Hosting", "Azure or AWS, containerised with Docker, orchestrated via Kubernetes or Azure Container Apps"],
          ["Authentication", "Multi-tenant auth with role-based access. OAuth2 / SSO / Magic link."],
          ["Billing", "Stripe for subscription management and usage metering"],
        ]),

        spacer(),
        h2("11.2 Performance"),
        bullet("Full pipeline run (800+ sources) completes within 4 hours"),
        bullet("Source auto-detection returns results within 3 seconds"),
        bullet("Dashboard loads within 2 seconds with 50,000+ leads in database"),
        bullet("API endpoints respond within 500ms for standard CRUD"),
        bullet("Campaign engine processes 10,000+ daily emails across all tenants with proper rate limiting and domain warming"),

        spacer(),
        h2("11.3 Reliability"),
        bullet("99.5% uptime SLA for the web application"),
        bullet("Per-source error isolation (one failing board never blocks others)"),
        bullet("Automatic retry with exponential backoff for rate-limited APIs"),
        bullet("Exactly-once email delivery semantics (no duplicate sends)"),
        bullet("Daily automated database backups with 30-day retention"),
        bullet("Health check endpoints for all services"),

        spacer(),
        h2("11.4 Security & Compliance"),
        bullet("Data encrypted at rest (AES-256) and in transit (TLS 1.3)"),
        bullet("API keys and credentials in Azure Key Vault or AWS Secrets Manager"),
        bullet("Strict tenant data isolation (no cross-tenant data leakage at any layer)"),
        bullet("GDPR compliance: right to erasure, data export, consent management, DPA templates"),
        bullet("CAN-SPAM / PECR compliance: unsubscribe mechanism, suppression lists, sender identification"),
        bullet("SOC 2 Type II readiness (design controls from the start)"),
        bullet("Rate limiting on all public endpoints, API key authentication for programmatic access"),
        bullet("Full audit log: user actions, data access, configuration changes"),

        pageBreak(),

        // ══════════ 12. MULTI-TENANCY ══════════
        h1("12. Multi-Tenancy & White-Labelling"),

        h2("12.1 Tenant Isolation"),
        p("Every tenant operates in a logically isolated environment:"),
        bullet("Own source list, categories, scoring weights, and allowed countries"),
        bullet("Own campaign templates, sequences, and outreach history"),
        bullet("Own CRM connection with separate OAuth credentials"),
        bullet("Own email sending domain and credentials"),
        bullet("Own user accounts with role-based access"),
        bullet("No shared data between tenants; all database queries scoped by tenant_id"),

        spacer(),
        h2("12.2 Shared Infrastructure"),
        bullet("Scraping engine (Playwright cluster) shared across tenants with per-tenant job queuing"),
        bullet("Adapter code and enrichment logic shared (updates benefit all tenants)"),
        bullet("LLM API calls shared but metered per tenant for billing"),
        bullet("Aggregator API keys may be shared or per-tenant (configurable)"),

        spacer(),
        h2("12.3 White-Labelling"),
        p("Each tenant can customise:"),
        bullet("Logo, colour scheme (primary accent, backgrounds)"),
        bullet("Custom domain (e.g. leads.acmerecruiting.com) with SSL"),
        bullet("Branded campaign emails (sender name, domain, signature)"),
        bullet("No Prospero branding visible to end users or email recipients unless the tenant chooses to display it"),

        pageBreak(),

        // ══════════ 13. BILLING ══════════
        h1("13. Billing Model"),
        p("Tiered subscription with usage-based overage. Managed via Stripe."),
        spacer(),
        makeTable(COL4, [
          ["Tier", "Sources", "Leads/month", "Campaigns"],
          ["Starter", "Up to 100", "5,000", "3 active"],
          ["Professional", "Up to 500", "25,000", "10 active"],
          ["Enterprise", "Unlimited", "Unlimited", "Unlimited"],
        ]),
        spacer(),
        p("Metered dimensions:"),
        bullet("Active sources count"),
        bullet("Leads discovered per month"),
        bullet("Campaign emails sent per month"),
        bullet("AI dossier generations per month (token usage)"),
        bullet("User seats"),
        spacer(),
        p("Enterprise tier includes: dedicated support, custom SLA, SSO/SAML, priority scraping queue, custom adapter development."),

        pageBreak(),

        // ══════════ 14. DELIVERABLES ══════════
        h1("14. Deliverables & Expectations"),

        h2("14.1 What We Provide"),
        makeTable(COL2, [
          ["Artefact", "Description"],
          ["This PRD", "Full product requirements and specifications (this document)"],
          ["UI Mockups", "Interactive HTML/CSS/JS mockups covering all screens (mockup/index.html)"],
          ["Scraping Engine", "Complete Python codebase: 29 adapters, enrichment pipeline, classification, scoring, export"],
          ["Source Detector", "Working URL auto-detection module (source_detector.py)"],
          ["Configuration", "Board configs, location rules (YAML), scoring config (TOML), routing rules (YAML)"],
          ["n8n Workflows", "3 workflow definitions: lead intake, queue processing, AI dossier generation"],
          ["Database Schema", "SQLAlchemy ORM models for all existing tables"],
        ]),

        spacer(),
        h2("14.2 What We Expect"),
        num("Review the existing codebase, validate architecture assumptions, and propose a technical plan with milestones"),
        num("Build the web application frontend (Next.js) based on provided mockups and design system"),
        num("Build the REST API layer that wraps the existing Python scraping/enrichment engine"),
        num("Build the campaign engine (new component: builder, scheduler, email delivery, tracking)"),
        num("Integrate Bullhorn CRM (contact matching, activity logging, OAuth flow)"),
        num("Integrate email delivery provider (SendGrid/Mailgun with tracking webhooks)"),
        num("Migrate database from SQLite to PostgreSQL with multi-tenant schema"),
        num("Implement authentication, authorisation, and tenant management"),
        num("Deploy to Azure with CI/CD pipeline, staging environment, and production environment"),
        num("Deliver documentation: API docs, deployment runbook, admin guide"),
        num("Provide 30-day post-launch support and bug fixing"),

        spacer(),
        h2("14.3 Phasing (Suggested)"),
        makeTable(COL3, [
          ["Phase", "Scope", "Duration (Est.)"],
          ["Phase 1: Foundation", "Frontend shell, API layer, database migration, auth, source management with auto-detection, basic dashboard", "6-8 weeks"],
          ["Phase 2: Intelligence", "Lead Explorer, classification UI, scoring UI with tenant config, AI dossier generation, hiring manager identification", "4-6 weeks"],
          ["Phase 3: Campaigns", "Campaign builder, email delivery integration, tracking (opens/replies/bounces), campaign analytics", "6-8 weeks"],
          ["Phase 4: CRM & Polish", "Bullhorn integration, white-labelling, billing (Stripe), mobile responsive, performance optimisation", "4-6 weeks"],
          ["Phase 5: Launch", "Staging testing, security audit, documentation, production deployment, onboarding first tenants", "2-4 weeks"],
        ]),

        pageBreak(),

        // ══════════ 15. OPEN QUESTIONS ══════════
        h1("15. Open Questions"),
        p("The following decisions should be resolved during technical planning:"),
        spacer(),
        makeTable(COL2, [
          ["Question", "Context"],
          ["Email provider", "SendGrid vs Mailgun vs Postmark. Depends on volume expectations, deliverability requirements, and cost."],
          ["AI provider", "GPT-4 (existing) vs Claude. Consider offering both with tenant-level selection. Cost and quality trade-offs."],
          ["SourceWhale integration", "Some target clients already use SourceWhale. Build a native campaign engine AND offer SourceWhale sync, or pick one?"],
          ["Mobile experience", "Mockups are desktop-focused. Define scope: responsive web, or mobile app (phase 2)?"],
          ["Real-time updates", "WebSocket for live feed and campaign events, or polling? WebSocket is better UX but adds infrastructure complexity."],
          ["Data retention policy", "How long to store raw job data, enriched leads, campaign events? GDPR implications. Suggest: raw 90 days, enriched 2 years, events 2 years."],
          ["Scraping infrastructure", "Shared Playwright cluster vs per-tenant containers. Shared is cheaper but requires fair scheduling."],
          ["Custom adapter development", "Enterprise clients may need adapters for proprietary ATS platforms. Define the process and pricing for custom adapter builds."],
        ]),

        spacer(), spacer(), divider(),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 }, children: [new TextRun({ text: "End of Document", font: "Arial", size: 20, color: GREY, italics: true })] }),
      ],
    },
  ],
});

Packer.toBuffer(doc).then(buffer => {
  const out = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/Prospero_PRD_v1.0.docx";
  fs.writeFileSync(out, buffer);
  console.log("Written to " + out);
});

const pptxgen = require("pptxgenjs");
const pres = new pptxgen();

pres.layout = "LAYOUT_16x9";
pres.author = "Prospero";
pres.title = "Prospero - Seed Investment Pitch";

// ── Palette ──
const BG_DARK = "0A0A1A";
const BG_MID = "12121F";
const PURPLE = "6C5CE7";
const PURPLE_LIGHT = "A29BFE";
const GREEN = "00D2A0";
const WHITE = "F0F0F8";
const GREY = "8888A0";
const MUTED = "555570";
const AMBER = "FFD93D";
const RED = "FF6B6B";
const BLUE = "4DABF7";

function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: BG_DARK };
  return s;
}

function midSlide() {
  const s = pres.addSlide();
  s.background = { color: BG_MID };
  // Subtle top line
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.03, fill: { color: PURPLE, transparency: 50 } });
  return s;
}

function slideTitle(slide, title, subtitle) {
  slide.addText(title, { x: 0.7, y: 0.35, w: 8.6, h: 0.5, fontSize: 28, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
  if (subtitle) {
    slide.addText(subtitle, { x: 0.7, y: 0.85, w: 8.6, h: 0.35, fontSize: 13, fontFace: "Arial", color: GREY, margin: 0 });
  }
}

function statCard(slide, x, y, w, value, label, color = WHITE) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 1.1, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: "2A2A3A", width: 0.75 } });
  slide.addText(value, { x, y: y + 0.1, w, h: 0.55, fontSize: 28, fontFace: "Arial", bold: true, color, align: "center", margin: 0 });
  slide.addText(label, { x, y: y + 0.6, w, h: 0.35, fontSize: 10, fontFace: "Arial", color: GREY, align: "center", margin: 0 });
}

function bulletList(slide, x, y, w, items) {
  const textItems = items.map((item, i) => ({
    text: item,
    options: { bullet: true, color: WHITE, fontSize: 14, fontFace: "Arial", breakLine: i < items.length - 1 }
  }));
  slide.addText(textItems, { x, y, w, h: items.length * 0.38, valign: "top", margin: 0, paraSpaceAfter: 6 });
}


// ═══════════════════════════════════════════════════════
// SLIDE 1: TITLE
// ═══════════════════════════════════════════════════════
let s1 = darkSlide();
// Purple glow circle (background decoration)
s1.addShape(pres.shapes.OVAL, { x: 3.5, y: 0.8, w: 3, h: 3, fill: { color: PURPLE, transparency: 85 } });
s1.addText("PROSPERO", { x: 0, y: 1.5, w: 10, h: 1.0, fontSize: 54, fontFace: "Arial", bold: true, color: WHITE, align: "center", charSpacing: 8, margin: 0 });
s1.addText("Recruitment Intelligence Platform", { x: 0, y: 2.45, w: 10, h: 0.5, fontSize: 20, fontFace: "Arial", color: PURPLE_LIGHT, align: "center", margin: 0 });
s1.addShape(pres.shapes.LINE, { x: 3.5, y: 3.15, w: 3, h: 0, line: { color: PURPLE, width: 1.5 } });
s1.addText("Every vacancy. Every hiring manager. Every outreach.\nAutomated.", { x: 1.5, y: 3.4, w: 7, h: 0.8, fontSize: 14, fontFace: "Arial", color: GREY, align: "center", margin: 0 });
s1.addText("Seed Investment Pitch  |  April 2026  |  Confidential", { x: 0, y: 4.8, w: 10, h: 0.4, fontSize: 11, fontFace: "Arial", color: MUTED, align: "center", margin: 0 });


// ═══════════════════════════════════════════════════════
// SLIDE 2: THE PROBLEM
// ═══════════════════════════════════════════════════════
let s2 = midSlide();
slideTitle(s2, "The Problem", "Recruitment agencies are losing deals they should be winning");

// Left column - pain points
const pains = [
  ["150+", "career sites a recruiter\nmonitors manually per day"],
  ["72hrs", "average time to spot a new\nvacancy vs competitors"],
  ["85%", "of outreach emails are\ngeneric and ignored"],
  ["4.2hrs", "spent daily on admin\ninstead of selling"],
];
pains.forEach((p, i) => {
  const yy = 1.5 + i * 1.0;
  s2.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.7, y: yy, w: 4.2, h: 0.85, fill: { color: "1A1A2E" }, rectRadius: 0.06, line: { color: "2A2A3A", width: 0.75 } });
  s2.addText(p[0], { x: 0.9, y: yy + 0.05, w: 1.3, h: 0.75, fontSize: 26, fontFace: "Arial", bold: true, color: RED, margin: 0, valign: "middle" });
  s2.addText(p[1], { x: 2.2, y: yy + 0.05, w: 2.5, h: 0.75, fontSize: 11, fontFace: "Arial", color: GREY, margin: 0, valign: "middle" });
});

// Right column - quote
s2.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 5.3, y: 1.5, w: 4.2, h: 3.5, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: PURPLE, width: 1 } });
s2.addText([
  { text: "The recruiter who reaches the\nhiring manager first with a\nrelevant, intelligent message\nwins the mandate.\n\n", options: { fontSize: 15, fontFace: "Arial", color: WHITE, italic: true, breakLine: true } },
  { text: "Everyone else is noise.", options: { fontSize: 15, fontFace: "Arial", color: PURPLE_LIGHT, bold: true, breakLine: true } },
  { text: "\n\nThe problem is that doing this\nmanually across hundreds of\ncompanies is impossible.", options: { fontSize: 12, fontFace: "Arial", color: GREY } },
], { x: 5.6, y: 1.7, w: 3.6, h: 3.1, valign: "middle", margin: 0 });


// ═══════════════════════════════════════════════════════
// SLIDE 3: THE SOLUTION
// ═══════════════════════════════════════════════════════
let s3 = midSlide();
slideTitle(s3, "Prospero", "An AI-powered platform that automates the entire recruitment lead lifecycle");

// Pipeline flow
const steps = [
  { label: "DISCOVER", desc: "800+ sources\nscraped daily", color: PURPLE, icon: "01" },
  { label: "QUALIFY", desc: "AI classification\n& scoring", color: BLUE, icon: "02" },
  { label: "IDENTIFY", desc: "Hiring manager\ndetection", color: GREEN, icon: "03" },
  { label: "ENGAGE", desc: "5-step email\ncampaigns", color: AMBER, icon: "04" },
];
steps.forEach((st, i) => {
  const xx = 0.7 + i * 2.35;
  // Card
  s3.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: xx, y: 1.5, w: 2.05, h: 2.2, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: "2A2A3A", width: 0.75 } });
  // Number circle
  s3.addShape(pres.shapes.OVAL, { x: xx + 0.65, y: 1.7, w: 0.7, h: 0.7, fill: { color: st.color } });
  s3.addText(st.icon, { x: xx + 0.65, y: 1.7, w: 0.7, h: 0.7, fontSize: 16, fontFace: "Arial", bold: true, color: BG_DARK, align: "center", valign: "middle", margin: 0 });
  // Label
  s3.addText(st.label, { x: xx + 0.15, y: 2.55, w: 1.75, h: 0.35, fontSize: 14, fontFace: "Arial", bold: true, color: st.color, align: "center", margin: 0 });
  // Desc
  s3.addText(st.desc, { x: xx + 0.15, y: 2.9, w: 1.75, h: 0.6, fontSize: 11, fontFace: "Arial", color: GREY, align: "center", margin: 0 });
  // Arrow between cards
  if (i < 3) {
    s3.addText("\u25B6", { x: xx + 2.05, y: 2.25, w: 0.3, h: 0.5, fontSize: 12, color: MUTED, align: "center", valign: "middle", margin: 0 });
  }
});

// Bottom tagline
s3.addText("From job posted to hiring manager contacted in hours, not days. Fully automated.", { x: 0.7, y: 4.1, w: 8.6, h: 0.4, fontSize: 13, fontFace: "Arial", color: PURPLE_LIGHT, italic: true, margin: 0 });
s3.addText("No manual monitoring. No spreadsheet tracking. No missed opportunities.", { x: 0.7, y: 4.5, w: 8.6, h: 0.35, fontSize: 12, fontFace: "Arial", color: GREY, margin: 0 });


// ═══════════════════════════════════════════════════════
// SLIDE 4: MARKET OPPORTUNITY
// ═══════════════════════════════════════════════════════
let s4 = midSlide();
slideTitle(s4, "Market Opportunity", "The global recruitment industry is massive, fragmented, and underserved by technology");

// TAM / SAM / SOM
const markets = [
  { label: "TAM", value: "$500B+", desc: "Global staffing &\nrecruitment market", size: 2.4, color: PURPLE },
  { label: "SAM", value: "$45B", desc: "Agency recruiters in\nwhite-collar verticals", size: 1.9, color: BLUE },
  { label: "SOM", value: "$2.5B", desc: "Specialist agencies willing\nto pay for automation", size: 1.4, color: GREEN },
];
markets.forEach((m, i) => {
  const cx = 2.8 + i * 0.3;
  const cy = 3.0 - i * 0.3;
  s4.addShape(pres.shapes.OVAL, { x: cx - m.size / 2, y: cy - m.size / 2, w: m.size, h: m.size, fill: { color: m.color, transparency: 75 }, line: { color: m.color, width: 1.5 } });
});
// Labels to the right
s4.addText("TAM: $500B+", { x: 5.0, y: 1.5, w: 4.0, h: 0.35, fontSize: 16, fontFace: "Arial", bold: true, color: PURPLE_LIGHT, margin: 0 });
s4.addText("Global staffing and recruitment industry revenue", { x: 5.0, y: 1.85, w: 4.0, h: 0.3, fontSize: 11, fontFace: "Arial", color: GREY, margin: 0 });
s4.addText("SAM: $45B", { x: 5.0, y: 2.4, w: 4.0, h: 0.35, fontSize: 16, fontFace: "Arial", bold: true, color: BLUE, margin: 0 });
s4.addText("Agency recruiters in professional/white-collar verticals globally", { x: 5.0, y: 2.75, w: 4.0, h: 0.3, fontSize: 11, fontFace: "Arial", color: GREY, margin: 0 });
s4.addText("SOM: $2.5B", { x: 5.0, y: 3.3, w: 4.0, h: 0.35, fontSize: 16, fontFace: "Arial", bold: true, color: GREEN, margin: 0 });
s4.addText("Specialist agencies actively investing in tech-driven lead generation", { x: 5.0, y: 3.65, w: 4.0, h: 0.3, fontSize: 11, fontFace: "Arial", color: GREY, margin: 0 });

// Bottom stats
statCard(s4, 0.7, 4.35, 2.8, "170,000+", "Recruitment firms globally", WHITE);
statCard(s4, 3.7, 4.35, 2.8, "12%", "Annual tech spend growth", GREEN);
statCard(s4, 6.7, 4.35, 2.8, "<5%", "Currently use AI lead gen", AMBER);


// ═══════════════════════════════════════════════════════
// SLIDE 5: HOW IT WORKS
// ═══════════════════════════════════════════════════════
let s5 = midSlide();
slideTitle(s5, "How It Works", "A recruiter adds a source in seconds. The platform does the rest.");

// Step-by-step with numbered boxes
const howSteps = [
  { n: "1", title: "Paste a URL", desc: "Recruiter pastes any careers page URL. System auto-detects the platform (Workday, Greenhouse, iCIMS, etc.) and validates it in under 3 seconds.", color: PURPLE },
  { n: "2", title: "Discover Jobs", desc: "Daily automated scraping across all configured sources. 29 dedicated adapters + a universal browser scraper that handles Cloudflare, SPAs, and iframes.", color: BLUE },
  { n: "3", title: "Qualify & Score", desc: "Jobs are classified into tenant-defined categories, scored against configurable criteria, and filtered by geography. Only qualified leads surface.", color: GREEN },
  { n: "4", title: "Identify Decision Makers", desc: "AI identifies up to 3 probable hiring managers per lead with confidence scores. CRM integration (Bullhorn) cross-references existing contacts.", color: AMBER },
  { n: "5", title: "Automated Outreach", desc: "5-step email campaigns with per-step tone control (formal, technical, candidate spec, etc.), configurable timing, open/reply tracking, and meeting detection.", color: PURPLE_LIGHT },
];
howSteps.forEach((st, i) => {
  const yy = 1.35 + i * 0.82;
  // Number
  s5.addShape(pres.shapes.OVAL, { x: 0.7, y: yy + 0.08, w: 0.5, h: 0.5, fill: { color: st.color } });
  s5.addText(st.n, { x: 0.7, y: yy + 0.08, w: 0.5, h: 0.5, fontSize: 16, fontFace: "Arial", bold: true, color: BG_DARK, align: "center", valign: "middle", margin: 0 });
  // Title + desc
  s5.addText(st.title, { x: 1.4, y: yy, w: 2.0, h: 0.65, fontSize: 14, fontFace: "Arial", bold: true, color: st.color, valign: "middle", margin: 0 });
  s5.addText(st.desc, { x: 3.4, y: yy, w: 6.0, h: 0.65, fontSize: 11, fontFace: "Arial", color: GREY, valign: "middle", margin: 0 });
  // Separator
  if (i < 4) s5.addShape(pres.shapes.LINE, { x: 0.7, y: yy + 0.72, w: 8.6, h: 0, line: { color: "2A2A3A", width: 0.5 } });
});


// ═══════════════════════════════════════════════════════
// SLIDE 6: COMPETITIVE ADVANTAGE
// ═══════════════════════════════════════════════════════
let s6 = midSlide();
slideTitle(s6, "Why Prospero Wins", "The only platform that covers the full lifecycle: discovery to outreach");

// Comparison table
const compHeaders = [
  { text: "", options: { fill: { color: BG_MID } } },
  { text: "Prospero", options: { fill: { color: PURPLE }, color: WHITE, bold: true, fontSize: 11, fontFace: "Arial", align: "center" } },
  { text: "LinkedIn\nRecruiter", options: { fill: { color: "1A1A2E" }, color: GREY, bold: true, fontSize: 10, fontFace: "Arial", align: "center" } },
  { text: "Job Board\nAlerts", options: { fill: { color: "1A1A2E" }, color: GREY, bold: true, fontSize: 10, fontFace: "Arial", align: "center" } },
  { text: "Outreach\nTools", options: { fill: { color: "1A1A2E" }, color: GREY, bold: true, fontSize: 10, fontFace: "Arial", align: "center" } },
];
const tick = "\u2713";
const cross = "\u2717";
const compRows = [
  ["Multi-source discovery (800+)", tick, cross, cross, cross],
  ["Auto-detect any ATS platform", tick, cross, cross, cross],
  ["AI classification & scoring", tick, cross, cross, cross],
  ["Hiring manager identification", tick, "Partial", cross, cross],
  ["Multi-step email campaigns", tick, cross, cross, tick],
  ["CRM integration", tick, tick, cross, "Partial"],
  ["Open/reply/meeting tracking", tick, cross, cross, tick],
  ["Full lifecycle in one platform", tick, cross, cross, cross],
];

const tableData = [compHeaders];
compRows.forEach(row => {
  tableData.push([
    { text: row[0], options: { fontSize: 10, fontFace: "Arial", color: WHITE, fill: { color: "1A1A2E" } } },
    { text: row[1], options: { fontSize: 12, fontFace: "Arial", color: row[1] === tick ? GREEN : RED, fill: { color: "1E1E2E" }, align: "center", bold: true } },
    { text: row[2], options: { fontSize: 11, fontFace: "Arial", color: row[2] === tick ? GREEN : row[2] === "Partial" ? AMBER : RED, fill: { color: "1A1A2E" }, align: "center" } },
    { text: row[3], options: { fontSize: 11, fontFace: "Arial", color: row[3] === tick ? GREEN : row[3] === "Partial" ? AMBER : RED, fill: { color: "1A1A2E" }, align: "center" } },
    { text: row[4], options: { fontSize: 11, fontFace: "Arial", color: row[4] === tick ? GREEN : row[4] === "Partial" ? AMBER : RED, fill: { color: "1A1A2E" }, align: "center" } },
  ]);
});

s6.addTable(tableData, { x: 0.5, y: 1.35, w: 9, colW: [3.2, 1.45, 1.45, 1.45, 1.45], border: { pt: 0.5, color: "2A2A3A" }, rowH: [0.45, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38] });


// ═══════════════════════════════════════════════════════
// SLIDE 7: TRACTION & VALIDATION
// ═══════════════════════════════════════════════════════
let s7 = midSlide();
slideTitle(s7, "Traction & Validation", "Built and battle-tested against live production data");

// Stats row
statCard(s7, 0.5, 1.4, 2.1, "800+", "Live sources", GREEN);
statCard(s7, 2.8, 1.4, 2.1, "29", "ATS adapters", PURPLE_LIGHT);
statCard(s7, 5.1, 1.4, 2.1, "18,000+", "Jobs discovered", BLUE);
statCard(s7, 7.4, 1.4, 2.1, "39", "Countries", AMBER);

// Proof points
s7.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.5, y: 2.85, w: 9, h: 2.5, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: "2A2A3A", width: 0.75 } });
s7.addText("What we have built and proven:", { x: 0.8, y: 2.95, w: 8.4, h: 0.35, fontSize: 14, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });

const proofs = [
  "Working scraping engine tested against Goldman Sachs, Citadel, Barclays, HSBC, and 700+ other sources",
  "Adapters for every major ATS: Workday, Greenhouse, iCIMS, Lever, Oracle Cloud, SuccessFactors, and 23 more",
  "Generic browser scraper that defeats Cloudflare, handles SPAs, scans iframes, and resolves AJAX-loaded content",
  "AI-powered hiring intelligence dossier generation with 5-step outreach sequences (live n8n workflow)",
  "Location normalisation across 650+ city rules and 39 countries with recruiter agency filtering",
  "Interactive UI mockups and a complete Product Requirements Document ready for development",
];
proofs.forEach((pr, i) => {
  s7.addText([{ text: pr, options: { bullet: true, color: GREY, fontSize: 11, fontFace: "Arial", breakLine: i < proofs.length - 1 } }], { x: 0.8, y: 3.35 + i * 0.32, w: 8.4, h: 0.3, margin: 0 });
});


// ═══════════════════════════════════════════════════════
// SLIDE 8: BUSINESS MODEL
// ═══════════════════════════════════════════════════════
let s8 = midSlide();
slideTitle(s8, "Business Model", "Tiered SaaS subscription with usage-based expansion revenue");

// Pricing tiers
const tiers = [
  { name: "STARTER", price: "\u00A3299/mo", sources: "100 sources", leads: "5,000 leads/mo", campaigns: "3 campaigns", users: "2 users", color: GREY },
  { name: "PROFESSIONAL", price: "\u00A3799/mo", sources: "500 sources", leads: "25,000 leads/mo", campaigns: "10 campaigns", users: "10 users", color: PURPLE },
  { name: "ENTERPRISE", price: "Custom", sources: "Unlimited", leads: "Unlimited", campaigns: "Unlimited", users: "Unlimited", color: GREEN },
];
tiers.forEach((t, i) => {
  const xx = 0.5 + i * 3.15;
  const isPro = i === 1;
  s8.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: xx, y: 1.4, w: 2.85, h: 3.2, fill: { color: isPro ? "1E1E35" : "1A1A2E" }, rectRadius: 0.08, line: { color: isPro ? PURPLE : "2A2A3A", width: isPro ? 1.5 : 0.75 } });
  if (isPro) s8.addText("MOST POPULAR", { x: xx, y: 1.5, w: 2.85, h: 0.25, fontSize: 8, fontFace: "Arial", bold: true, color: PURPLE, align: "center", margin: 0 });
  s8.addText(t.name, { x: xx, y: 1.75, w: 2.85, h: 0.3, fontSize: 12, fontFace: "Arial", bold: true, color: t.color, align: "center", charSpacing: 3, margin: 0 });
  s8.addText(t.price, { x: xx, y: 2.05, w: 2.85, h: 0.45, fontSize: 24, fontFace: "Arial", bold: true, color: WHITE, align: "center", margin: 0 });
  const features = [t.sources, t.leads, t.campaigns, t.users];
  features.forEach((f, j) => {
    s8.addText(f, { x: xx + 0.3, y: 2.65 + j * 0.35, w: 2.25, h: 0.3, fontSize: 11, fontFace: "Arial", color: GREY, margin: 0 });
  });
});

// Unit economics
s8.addText("Target unit economics at scale:", { x: 0.5, y: 4.85, w: 9, h: 0.3, fontSize: 12, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
s8.addText("LTV:CAC > 5:1   |   Gross margin > 80%   |   Net revenue retention > 120%   |   Payback < 6 months", { x: 0.5, y: 5.1, w: 9, h: 0.25, fontSize: 11, fontFace: "Arial", color: PURPLE_LIGHT, margin: 0 });


// ═══════════════════════════════════════════════════════
// SLIDE 9: GO-TO-MARKET
// ═══════════════════════════════════════════════════════
let s9 = midSlide();
slideTitle(s9, "Go-to-Market", "Land and expand through vertical specialisation");

// Phase boxes
const gtmPhases = [
  { phase: "PHASE 1", timeline: "Months 1-6", title: "Prove with One Vertical", items: ["Deploy internally at founding team's recruitment firm", "Financial services vertical: Risk, Quant, Compliance, Audit", "Validate product-market fit with real billing data", "Refine AI dossier quality and campaign conversion rates"], color: PURPLE },
  { phase: "PHASE 2", timeline: "Months 6-12", title: "Expand Verticals", items: ["Onboard 10-20 pilot agencies across Tech, Legal, Life Sciences", "Build vertical-specific category templates and prompt libraries", "Launch self-serve onboarding for Starter tier", "Integrate Salesforce and Vincere alongside Bullhorn"], color: BLUE },
  { phase: "PHASE 3", timeline: "Months 12-24", title: "Scale Globally", items: ["Enterprise white-label deals with large staffing firms", "API marketplace for custom integrations", "International expansion (US, EU, APAC)", "Build partner channel with CRM vendors"], color: GREEN },
];
gtmPhases.forEach((ph, i) => {
  const xx = 0.5 + i * 3.15;
  s9.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: xx, y: 1.4, w: 2.85, h: 3.8, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: ph.color, width: 1 } });
  s9.addText(ph.phase, { x: xx, y: 1.55, w: 2.85, h: 0.25, fontSize: 9, fontFace: "Arial", bold: true, color: ph.color, align: "center", charSpacing: 3, margin: 0 });
  s9.addText(ph.timeline, { x: xx, y: 1.78, w: 2.85, h: 0.22, fontSize: 10, fontFace: "Arial", color: GREY, align: "center", margin: 0 });
  s9.addText(ph.title, { x: xx + 0.2, y: 2.1, w: 2.45, h: 0.35, fontSize: 13, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
  ph.items.forEach((item, j) => {
    s9.addText([{ text: item, options: { bullet: true, fontSize: 10, fontFace: "Arial", color: GREY, breakLine: j < ph.items.length - 1 } }], { x: xx + 0.2, y: 2.55 + j * 0.38, w: 2.45, h: 0.35, margin: 0 });
  });
});


// ═══════════════════════════════════════════════════════
// SLIDE 10: FINANCIAL PROJECTIONS
// ═══════════════════════════════════════════════════════
let s10 = midSlide();
slideTitle(s10, "Financial Projections", "Conservative assumptions based on specialist recruitment agency market sizing");

// Bar chart - ARR growth
s10.addChart(pres.charts.BAR, [{
  name: "ARR",
  labels: ["Year 1", "Year 2", "Year 3", "Year 4", "Year 5"],
  values: [120, 580, 1800, 4200, 8500],
}], {
  x: 0.5, y: 1.4, w: 5.5, h: 3.2, barDir: "col",
  chartColors: [PURPLE],
  chartArea: { fill: { color: "1A1A2E" }, roundedCorners: true },
  catAxisLabelColor: GREY, valAxisLabelColor: GREY,
  valGridLine: { color: "2A2A3A", size: 0.5 }, catGridLine: { style: "none" },
  showValue: true, dataLabelPosition: "outEnd", dataLabelColor: WHITE,
  valAxisNumFmt: "\u00A30,K",
  showLegend: false,
});

// Key metrics table
s10.addText("Key Assumptions", { x: 6.3, y: 1.4, w: 3.2, h: 0.35, fontSize: 14, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
const assumptions = [
  ["Year 1 clients", "15"],
  ["Year 3 clients", "200"],
  ["Year 5 clients", "800"],
  ["Avg. revenue/client", "\u00A3750/mo"],
  ["Gross margin", "82%"],
  ["Monthly churn", "<3%"],
  ["CAC", "\u00A32,500"],
  ["LTV", "\u00A318,000"],
];
assumptions.forEach((a, i) => {
  const yy = 1.85 + i * 0.38;
  s10.addText(a[0], { x: 6.3, y: yy, w: 1.8, h: 0.32, fontSize: 10, fontFace: "Arial", color: GREY, margin: 0 });
  s10.addText(a[1], { x: 8.3, y: yy, w: 1.2, h: 0.32, fontSize: 11, fontFace: "Arial", bold: true, color: GREEN, align: "right", margin: 0 });
});

s10.addText("Year 5 target: \u00A38.5M ARR", { x: 0.5, y: 4.85, w: 9, h: 0.35, fontSize: 14, fontFace: "Arial", bold: true, color: PURPLE_LIGHT, margin: 0 });


// ═══════════════════════════════════════════════════════
// SLIDE 11: THE TEAM
// ═══════════════════════════════════════════════════════
let s11 = midSlide();
slideTitle(s11, "The Team", "Domain expertise meets technical execution");

// Founder card
s11.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.5, y: 1.5, w: 4.3, h: 3.2, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: PURPLE, width: 1 } });
s11.addText("FOUNDER & CEO", { x: 0.5, y: 1.65, w: 4.3, h: 0.25, fontSize: 9, fontFace: "Arial", bold: true, color: PURPLE, align: "center", charSpacing: 3, margin: 0 });
s11.addText("[Founder Name]", { x: 0.7, y: 2.05, w: 3.9, h: 0.4, fontSize: 18, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
const founderBullets = [
  "Deep domain expertise in specialist recruitment",
  "Built and validated the entire scraping engine (800+ sources, 29 adapters)",
  "Designed the AI dossier generation workflow and outreach automation",
  "Understands the recruiter workflow from years of hands-on experience",
  "Technical enough to architect, commercial enough to sell",
];
founderBullets.forEach((b, i) => {
  s11.addText([{ text: b, options: { bullet: true, fontSize: 10, fontFace: "Arial", color: GREY, breakLine: i < founderBullets.length - 1 } }], { x: 0.8, y: 2.55 + i * 0.38, w: 3.8, h: 0.35, margin: 0 });
});

// Hiring plan
s11.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 5.2, y: 1.5, w: 4.3, h: 3.2, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: "2A2A3A", width: 0.75 } });
s11.addText("HIRING WITH SEED FUNDING", { x: 5.2, y: 1.65, w: 4.3, h: 0.25, fontSize: 9, fontFace: "Arial", bold: true, color: GREEN, align: "center", charSpacing: 3, margin: 0 });
const hires = [
  { role: "CTO / Lead Engineer", when: "Month 1", desc: "Full-stack, SaaS experience" },
  { role: "Senior Backend Dev", when: "Month 2", desc: "Python, APIs, scraping infra" },
  { role: "Frontend Developer", when: "Month 2", desc: "React/Next.js, design system" },
  { role: "Head of Sales", when: "Month 4", desc: "Recruitment tech sales" },
  { role: "Customer Success", when: "Month 6", desc: "Onboarding, support" },
];
hires.forEach((h, i) => {
  const yy = 2.1 + i * 0.5;
  s11.addText(h.role, { x: 5.4, y: yy, w: 2.4, h: 0.2, fontSize: 11, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
  s11.addText(h.when, { x: 8.0, y: yy, w: 1.3, h: 0.2, fontSize: 10, fontFace: "Arial", color: GREEN, align: "right", margin: 0 });
  s11.addText(h.desc, { x: 5.4, y: yy + 0.2, w: 3.9, h: 0.2, fontSize: 9, fontFace: "Arial", color: GREY, margin: 0 });
});


// ═══════════════════════════════════════════════════════
// SLIDE 12: THE ASK
// ═══════════════════════════════════════════════════════
let s12 = midSlide();
slideTitle(s12, "The Ask", "Seed round to take Prospero from validated engine to commercial SaaS product");

// Big number
s12.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.5, y: 1.5, w: 9, h: 1.4, fill: { color: "1A1A2E" }, rectRadius: 0.08, line: { color: PURPLE, width: 1.5 } });
s12.addText("\u00A3500,000", { x: 0.5, y: 1.55, w: 9, h: 0.75, fontSize: 48, fontFace: "Arial", bold: true, color: GREEN, align: "center", margin: 0 });
s12.addText("Seed Round  |  18-month runway  |  SEIS/EIS eligible", { x: 0.5, y: 2.3, w: 9, h: 0.35, fontSize: 13, fontFace: "Arial", color: GREY, align: "center", margin: 0 });

// Use of funds
s12.addText("Use of Funds", { x: 0.5, y: 3.2, w: 4.3, h: 0.35, fontSize: 14, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
const funds = [
  ["Engineering (team of 3)", "55%", "\u00A3275K"],
  ["Sales & Marketing", "20%", "\u00A3100K"],
  ["Infrastructure & APIs", "10%", "\u00A350K"],
  ["Operations & Legal", "10%", "\u00A350K"],
  ["Reserve", "5%", "\u00A325K"],
];
funds.forEach((f, i) => {
  const yy = 3.6 + i * 0.35;
  s12.addText(f[0], { x: 0.5, y: yy, w: 2.5, h: 0.3, fontSize: 11, fontFace: "Arial", color: GREY, margin: 0 });
  s12.addText(f[1], { x: 3.0, y: yy, w: 0.8, h: 0.3, fontSize: 11, fontFace: "Arial", bold: true, color: PURPLE_LIGHT, align: "right", margin: 0 });
  s12.addText(f[2], { x: 3.9, y: yy, w: 0.9, h: 0.3, fontSize: 11, fontFace: "Arial", color: GREY, align: "right", margin: 0 });
});

// Milestones
s12.addText("18-Month Milestones", { x: 5.2, y: 3.2, w: 4.3, h: 0.35, fontSize: 14, fontFace: "Arial", bold: true, color: WHITE, margin: 0 });
const milestones = [
  "Product launch (Month 4)",
  "15 paying clients (Month 8)",
  "100 clients (Month 14)",
  "\u00A3120K ARR (Month 12)",
  "Series A ready (Month 18)",
];
milestones.forEach((m, i) => {
  s12.addText([{ text: m, options: { bullet: true, fontSize: 11, fontFace: "Arial", color: GREY, breakLine: i < milestones.length - 1 } }], { x: 5.4, y: 3.6 + i * 0.35, w: 3.9, h: 0.3, margin: 0 });
});


// ═══════════════════════════════════════════════════════
// SLIDE 13: CLOSING
// ═══════════════════════════════════════════════════════
let s13 = darkSlide();
s13.addShape(pres.shapes.OVAL, { x: 3.5, y: 0.8, w: 3, h: 3, fill: { color: PURPLE, transparency: 85 } });
s13.addText("PROSPERO", { x: 0, y: 1.6, w: 10, h: 0.8, fontSize: 48, fontFace: "Arial", bold: true, color: WHITE, align: "center", charSpacing: 6, margin: 0 });
s13.addText("The recruitment industry runs on relationships.\nWe make sure you start them first.", { x: 1.5, y: 2.6, w: 7, h: 0.8, fontSize: 16, fontFace: "Arial", color: PURPLE_LIGHT, align: "center", italic: true, margin: 0 });
s13.addShape(pres.shapes.LINE, { x: 3.5, y: 3.6, w: 3, h: 0, line: { color: PURPLE, width: 1.5 } });
s13.addText("[founder@prospero.io]  |  [prospero.io]", { x: 0, y: 4.0, w: 10, h: 0.4, fontSize: 13, fontFace: "Arial", color: GREY, align: "center", margin: 0 });
s13.addText("Thank you.", { x: 0, y: 4.6, w: 10, h: 0.4, fontSize: 18, fontFace: "Arial", bold: true, color: WHITE, align: "center", margin: 0 });


// ── Write ──
const outPath = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/Prospero_Seed_Pitch.pptx";
pres.writeFile({ fileName: outPath }).then(() => console.log("Written to " + outPath));

const pptxgen = require("pptxgenjs");
const pres = new pptxgen();

pres.layout = "LAYOUT_16x9";
pres.author = "Antony Berou";
pres.title = "Prospero — The Journey";

const BS_ORANGE = "FF6E02";
const BS_TEAL = "105856";
const BS_PEACH = "FEE4D6";
const BS_DARK_GREY = "60686E";
const WHITE = "FFFFFF";
const BLACK = "000000";

const logoPath = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/bs_logo.png";
const mockupDir = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/";

function footer(s) {
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.25, w: 10, h: 0.38, fill: { color: BS_ORANGE } });
  s.addText("\u00A9 2026 Barclay Simpson Associates Ltd.", {
    x: 0.5, y: 5.25, w: 9, h: 0.38,
    fontSize: 9, fontFace: "Arial", color: WHITE, valign: "middle", margin: 0,
  });
}
function topBar(s) {
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.06, fill: { color: BS_ORANGE } });
}


// ═══════════════════════════════════════════════════════
// SLIDE 1: TITLE
// ═══════════════════════════════════════════════════════
let s1 = pres.addSlide();
s1.background = { color: BS_ORANGE };
s1.addImage({ path: logoPath, x: 0.5, y: 0.35, w: 1.4, h: 1.4 });
s1.addText("PROSPERO", {
  x: 0.5, y: 2.0, w: 9, h: 0.9,
  fontSize: 54, fontFace: "Arial", bold: true, color: WHITE, margin: 0, charSpacing: 5,
});
s1.addText("From idea to intelligence platform", {
  x: 0.5, y: 2.85, w: 8, h: 0.4,
  fontSize: 20, fontFace: "Arial", color: WHITE, margin: 0,
});
s1.addText("The journey so far, and where we're going", {
  x: 0.5, y: 3.3, w: 8, h: 0.35,
  fontSize: 14, fontFace: "Arial", color: WHITE, margin: 0, italic: true,
});
s1.addText("Antony Berou  |  Head of Risk  |  April 2026", {
  x: 0.5, y: 5.1, w: 9, h: 0.3,
  fontSize: 10, fontFace: "Arial", color: WHITE, margin: 0,
});


// ═══════════════════════════════════════════════════════
// SLIDE 2: THE PROBLEM
// ═══════════════════════════════════════════════════════
let s2 = pres.addSlide();
s2.background = { color: WHITE };
topBar(s2);
s2.addText("The Problem", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});
const pains = [
  { icon: "150+", text: "career sites to monitor manually" },
  { icon: "72hrs", text: "before we spot a new vacancy" },
  { icon: "85%", text: "of outreach is generic and ignored" },
  { icon: "30min", text: "per lead to research and write emails" },
];
pains.forEach((p, i) => {
  const yy = 1.2 + i * 1.0;
  s2.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: yy, w: 9, h: 0.8,
    fill: { color: "F8F9FA" }, rectRadius: 0.06, line: { color: "E8E8E8", width: 0.5 },
  });
  s2.addText(p.icon, {
    x: 0.7, y: yy + 0.05, w: 1.3, h: 0.7,
    fontSize: 26, fontFace: "Arial", bold: true, color: BS_ORANGE, valign: "middle", margin: 0,
  });
  s2.addText(p.text, {
    x: 2.1, y: yy + 0.05, w: 7.2, h: 0.7,
    fontSize: 16, fontFace: "Arial", color: BS_DARK_GREY, valign: "middle", margin: 0,
  });
});
footer(s2);


// ═══════════════════════════════════════════════════════
// SLIDE 3: WHAT I BUILT — The Engine
// ═══════════════════════════════════════════════════════
let s3 = pres.addSlide();
s3.background = { color: WHITE };
topBar(s3);
s3.addText("What I Built", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});
s3.addText("The scraping engine — working today", {
  x: 0.5, y: 0.85, w: 9, h: 0.3,
  fontSize: 13, fontFace: "Arial", italic: true, color: BS_DARK_GREY, margin: 0,
});
const stats = [
  { value: "780+", label: "Company career\nsites scraped", color: BS_ORANGE },
  { value: "30", label: "ATS platform\nadapters built", color: BS_TEAL },
  { value: "39", label: "Countries\ncovered", color: BS_ORANGE },
  { value: "<3min", label: "Per lead\nend to end", color: BS_TEAL },
];
stats.forEach((st, i) => {
  const col = i % 2;
  const row = Math.floor(i / 2);
  const xx = 0.5 + col * 4.7;
  const yy = 1.4 + row * 1.7;
  s3.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: yy, w: 4.4, h: 1.4,
    fill: { color: "F8F9FA" }, rectRadius: 0.08, line: { color: st.color, width: 1.5 },
  });
  s3.addText(st.value, {
    x: xx + 0.3, y: yy + 0.15, w: 2.0, h: 1.1,
    fontSize: 36, fontFace: "Arial", bold: true, color: st.color, valign: "middle", margin: 0,
  });
  s3.addText(st.label, {
    x: xx + 2.3, y: yy + 0.15, w: 1.8, h: 1.1,
    fontSize: 13, fontFace: "Arial", color: BS_DARK_GREY, valign: "middle", margin: 0,
  });
});
footer(s3);


// ═══════════════════════════════════════════════════════
// SLIDE 4: PROOF — Results
// ═══════════════════════════════════════════════════════
let s4 = pres.addSlide();
s4.background = { color: BS_TEAL };
s4.addText("It Works", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});
s4.addText("First 10 weeks — just the AI prompt and SourceWhale", {
  x: 0.5, y: 0.9, w: 9, h: 0.3,
  fontSize: 13, fontFace: "Arial", color: BS_PEACH, margin: 0, italic: true,
});
const proof = [
  { value: "30", label: "Campaigns" },
  { value: "4", label: "Jobs Won" },
  { value: "1", label: "Placement" },
  { value: "\u00A326K", label: "Revenue" },
];
proof.forEach((p, i) => {
  const xx = 0.5 + i * 2.35;
  s4.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.5, w: 2.05, h: 1.6,
    fill: { color: WHITE, transparency: 10 }, rectRadius: 0.08,
  });
  s4.addText(p.value, {
    x: xx, y: 1.55, w: 2.05, h: 0.85,
    fontSize: 44, fontFace: "Arial", bold: true, color: i >= 2 ? BS_ORANGE : WHITE,
    align: "center", margin: 0,
  });
  s4.addText(p.label, {
    x: xx, y: 2.4, w: 2.05, h: 0.4,
    fontSize: 14, fontFace: "Arial", color: WHITE, align: "center", margin: 0,
  });
});
s4.addText("We wouldn't have found the job without it.", {
  x: 0.5, y: 3.5, w: 9, h: 0.45,
  fontSize: 20, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});
s4.addText("Campaigns still running. More will convert.", {
  x: 0.5, y: 3.95, w: 9, h: 0.3,
  fontSize: 13, fontFace: "Arial", color: BS_PEACH, margin: 0, italic: true,
});


// ═══════════════════════════════════════════════════════
// SLIDE 5: WHERE I AM NOW — Teams Integration
// Two placeholder boxes for Teams screenshots
// ═══════════════════════════════════════════════════════
let s5 = pres.addSlide();
s5.background = { color: "0A0A1A" };
topBar(s5);
s5.addText("Where I Am Now", {
  x: 0.5, y: 0.25, w: 6, h: 0.45,
  fontSize: 26, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});
s5.addText("Leads served to Teams automatically. Outreach campaigns created on demand.", {
  x: 0.5, y: 0.65, w: 9, h: 0.25,
  fontSize: 11, fontFace: "Arial", color: BS_DARK_GREY, margin: 0, italic: true,
});

// Left: Teams lead feed
s5.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x: 0.3, y: 1.05, w: 4.55, h: 4.0,
  fill: { color: "1A1A2E" }, rectRadius: 0.06,
  shadow: { type: "outer", color: "000000", blur: 8, offset: 3, angle: 135, opacity: 0.4 },
});
s5.addImage({
  path: "/Users/antonyberou/Desktop/teams_leads.png",
  x: 0.4, y: 1.15, w: 4.35, h: 3.8,
  sizing: { type: "contain", w: 4.35, h: 3.8 },
});
s5.addText("Daily Lead Feed", {
  x: 0.3, y: 4.6, w: 4.55, h: 0.25,
  fontSize: 10, fontFace: "Arial", bold: true, color: BS_ORANGE, align: "center", margin: 0,
});

// Right: Teams outreach
s5.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x: 5.15, y: 1.05, w: 4.55, h: 4.0,
  fill: { color: "1A1A2E" }, rectRadius: 0.06,
  shadow: { type: "outer", color: "000000", blur: 8, offset: 3, angle: 135, opacity: 0.4 },
});
s5.addImage({
  path: "/Users/antonyberou/Desktop/teams_outreach.png",
  x: 5.25, y: 1.15, w: 4.35, h: 3.8,
  sizing: { type: "contain", w: 4.35, h: 3.8 },
});
s5.addText("AI-Generated Outreach", {
  x: 5.15, y: 4.6, w: 4.55, h: 0.25,
  fontSize: 10, fontFace: "Arial", bold: true, color: BS_ORANGE, align: "center", margin: 0,
});


// ═══════════════════════════════════════════════════════
// SLIDE 6: HOW IT WORKS
// ═══════════════════════════════════════════════════════
let s6 = pres.addSlide();
s6.background = { color: WHITE };
topBar(s6);
s6.addText("How It Works", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});
const steps = [
  { num: "1", title: "PASTE A URL", desc: "Any company careers page", time: "3 seconds", color: BS_ORANGE },
  { num: "2", title: "AUTO-DETECT", desc: "Platform identified,\njobs counted", time: "Instant", color: BS_TEAL },
  { num: "3", title: "SCRAPE & SCORE", desc: "Every job classified\ninto our categories", time: "30 seconds", color: BS_ORANGE },
  { num: "4", title: "INTELLIGENCE", desc: "AI dossier, hiring\nmanager, outreach", time: "2 minutes", color: BS_TEAL },
];
steps.forEach((st, i) => {
  const xx = 0.35 + i * 2.45;
  s6.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.2, w: 2.15, h: 3.4,
    fill: { color: "F8F9FA" }, rectRadius: 0.08, line: { color: st.color, width: 1.5 },
  });
  s6.addShape(pres.shapes.OVAL, {
    x: xx + 0.65, y: 1.45, w: 0.85, h: 0.85,
    fill: { color: st.color },
  });
  s6.addText(st.num, {
    x: xx + 0.65, y: 1.45, w: 0.85, h: 0.85,
    fontSize: 28, fontFace: "Arial", bold: true, color: WHITE,
    align: "center", valign: "middle", margin: 0,
  });
  s6.addText(st.title, {
    x: xx + 0.15, y: 2.5, w: 1.85, h: 0.35,
    fontSize: 13, fontFace: "Arial", bold: true, color: st.color,
    align: "center", margin: 0, charSpacing: 1,
  });
  s6.addText(st.desc, {
    x: xx + 0.15, y: 2.95, w: 1.85, h: 0.7,
    fontSize: 11, fontFace: "Arial", color: BS_DARK_GREY,
    align: "center", margin: 0,
  });
  s6.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx + 0.4, y: 3.85, w: 1.35, h: 0.35,
    fill: { color: st.color }, rectRadius: 0.04,
  });
  s6.addText(st.time, {
    x: xx + 0.4, y: 3.85, w: 1.35, h: 0.35,
    fontSize: 10, fontFace: "Arial", bold: true, color: WHITE,
    align: "center", valign: "middle", margin: 0,
  });
  if (i < 3) {
    s6.addText("\u25B6", {
      x: xx + 2.15, y: 2.3, w: 0.3, h: 0.5,
      fontSize: 14, color: "CCCCCC", align: "center", valign: "middle", margin: 0,
    });
  }
});
footer(s6);


// ═══════════════════════════════════════════════════════
// SLIDE 7: THE VISION — Platform UI (Live App)
// ═══════════════════════════════════════════════════════
let s7 = pres.addSlide();
s7.background = { color: "0A0A1A" };
topBar(s7);
s7.addText("The Platform", {
  x: 0.5, y: 0.25, w: 6, h: 0.45,
  fontSize: 26, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});
s7.addText("Live web application — built and running", {
  x: 0.5, y: 0.65, w: 6, h: 0.25,
  fontSize: 11, fontFace: "Arial", color: BS_DARK_GREY, margin: 0, italic: true,
});
// Live app screenshot
s7.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x: 0.35, y: 1.05, w: 9.3, h: 4.05,
  fill: { color: "1A1A2E" }, rectRadius: 0.06,
  shadow: { type: "outer", color: "000000", blur: 10, offset: 4, angle: 135, opacity: 0.5 },
});
s7.addImage({
  path: mockupDir + "screenshot_live_sources.png",
  x: 0.45, y: 1.1, w: 9.1, h: 3.9,
});


// ═══════════════════════════════════════════════════════
// SLIDE 8: THE VISION — Mockup Screens
// ═══════════════════════════════════════════════════════
let s8 = pres.addSlide();
s8.background = { color: "0A0A1A" };
topBar(s8);
s8.addText("What's Coming", {
  x: 0.5, y: 0.2, w: 6, h: 0.45,
  fontSize: 26, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});
s8.addText("Dashboard, lead management, campaign automation — all designed", {
  x: 0.5, y: 0.6, w: 9, h: 0.25,
  fontSize: 11, fontFace: "Arial", color: BS_DARK_GREY, margin: 0, italic: true,
});

const mockups = [
  { file: "screenshot_mockup_dashboard.png", label: "Live Dashboard" },
  { file: "screenshot_mockup_leads.png", label: "Lead List" },
  { file: "screenshot_mockup_campaigns.png", label: "Campaign Tracking" },
  { file: "screenshot_mockup_builder.png", label: "Campaign Builder" },
];
mockups.forEach((sc, i) => {
  const col = i % 2;
  const row = Math.floor(i / 2);
  const xx = 0.35 + col * 4.8;
  const yy = 1.0 + row * 2.25;
  s8.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx - 0.05, y: yy - 0.05, w: 4.55, h: 2.1,
    fill: { color: "1A1A2E" }, rectRadius: 0.06,
    shadow: { type: "outer", color: "000000", blur: 8, offset: 3, angle: 135, opacity: 0.4 },
  });
  s8.addImage({
    path: mockupDir + sc.file,
    x: xx, y: yy, w: 4.45, h: 1.8,
  });
  s8.addText(sc.label, {
    x: xx, y: yy + 1.82, w: 4.45, h: 0.22,
    fontSize: 10, fontFace: "Arial", bold: true, color: BS_ORANGE,
    align: "center", margin: 0,
  });
});


// ═══════════════════════════════════════════════════════
// SLIDE 9: ROADMAP — Three phases
// ═══════════════════════════════════════════════════════
let s9 = pres.addSlide();
s9.background = { color: WHITE };
topBar(s9);
s9.addText("The Roadmap", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});
const phases = [
  {
    title: "NOW", subtitle: "Working Today",
    items: ["780+ boards scraped daily", "AI dossiers + 5-step outreach", "Leads served to Teams", "Web UI for source management"],
    color: BS_ORANGE,
  },
  {
    title: "NEXT", subtitle: "Full Platform",
    items: ["Live lead feed + queue to campaign", "Email automation via Outlook", "Hiring manager ID + CRM sync", "Consultant dashboard"],
    color: BS_TEAL,
  },
  {
    title: "FUTURE", subtitle: "Commercial Product",
    items: ["SaaS for any recruitment agency", "Any vertical, any country", "White-labelled per client", "Contact discovery engine"],
    color: BS_DARK_GREY,
  },
];
phases.forEach((ph, i) => {
  const xx = 0.35 + i * 3.2;
  s9.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.1, w: 2.9, h: 3.8,
    fill: { color: "F8F9FA" }, rectRadius: 0.08, line: { color: ph.color, width: 1.5 },
  });
  s9.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.1, w: 2.9, h: 0.7,
    fill: { color: ph.color }, rectRadius: 0.08,
  });
  s9.addShape(pres.shapes.RECTANGLE, {
    x: xx, y: 1.5, w: 2.9, h: 0.3,
    fill: { color: ph.color },
  });
  s9.addText(ph.title, {
    x: xx + 0.15, y: 1.15, w: 2.6, h: 0.35,
    fontSize: 16, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
  });
  s9.addText(ph.subtitle, {
    x: xx + 0.15, y: 1.48, w: 2.6, h: 0.25,
    fontSize: 10, fontFace: "Arial", color: WHITE, margin: 0,
  });
  ph.items.forEach((item, j) => {
    s9.addText([{ text: item, options: { bullet: true, fontSize: 11, fontFace: "Arial", color: BS_DARK_GREY, breakLine: j < ph.items.length - 1 } }], {
      x: xx + 0.2, y: 2.0 + j * 0.55, w: 2.5, h: 0.5, margin: 0,
    });
  });
});
footer(s9);


// ═══════════════════════════════════════════════════════
// SLIDE 10: THE ASK
// ═══════════════════════════════════════════════════════
let s10 = pres.addSlide();
s10.background = { color: WHITE };
topBar(s10);
s10.addText("The Ask", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});
const asks = [
  { value: "5", label: "Consultants", sub: "3-month pilot", color: BS_ORANGE },
  { value: "90", label: "Days", sub: "to prove ROI", color: BS_TEAL },
  { value: "1", label: "Decision", sub: "roll out or walk away", color: BS_ORANGE },
];
asks.forEach((a, i) => {
  const xx = 0.5 + i * 3.15;
  s10.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.2, w: 2.85, h: 2.0,
    fill: { color: "F8F9FA" }, rectRadius: 0.08, line: { color: a.color, width: 1.5 },
  });
  s10.addText(a.value, {
    x: xx, y: 1.3, w: 2.85, h: 0.8,
    fontSize: 48, fontFace: "Arial", bold: true, color: a.color, align: "center", margin: 0,
  });
  s10.addText(a.label, {
    x: xx, y: 2.1, w: 2.85, h: 0.35,
    fontSize: 16, fontFace: "Arial", bold: true, color: BLACK, align: "center", margin: 0,
  });
  s10.addText(a.sub, {
    x: xx, y: 2.45, w: 2.85, h: 0.3,
    fontSize: 12, fontFace: "Arial", color: BS_DARK_GREY, align: "center", margin: 0,
  });
});

s10.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x: 0.5, y: 3.6, w: 9, h: 1.4,
  fill: { color: BS_TEAL }, rectRadius: 0.08,
});
s10.addText("The bigger picture:", {
  x: 0.7, y: 3.7, w: 8.6, h: 0.3,
  fontSize: 13, fontFace: "Arial", bold: true, color: BS_ORANGE, margin: 0,
});
s10.addText([
  { text: "If the pilot works, every consultant gets a daily pipeline of scored, qualified leads\n", options: { fontSize: 14, fontFace: "Arial", color: WHITE } },
  { text: "with AI intelligence and automated outreach. From their Outlook. Hands-free.\n\n", options: { fontSize: 14, fontFace: "Arial", color: WHITE } },
  { text: "And beyond Barclay Simpson, this becomes a product we can sell to every agency in the market.", options: { fontSize: 12, fontFace: "Arial", color: BS_PEACH, italic: true } },
], { x: 0.7, y: 4.0, w: 8.6, h: 0.9, margin: 0 });

s10.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.25, w: 10, h: 0.38, fill: { color: BS_ORANGE } });
s10.addText("ab@barclaysimpson.com  |  Head of Risk  |  +44 (0) 207 936 2601", {
  x: 0.5, y: 5.25, w: 9, h: 0.38,
  fontSize: 9, fontFace: "Arial", color: WHITE, valign: "middle", align: "center", margin: 0,
});


const outPath = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/Prospero_Journey.pptx";
pres.writeFile({ fileName: outPath }).then(() => console.log("Written to " + outPath));

const pptxgen = require("pptxgenjs");
const pres = new pptxgen();

pres.layout = "LAYOUT_16x9";
pres.author = "Antony Berou";
pres.title = "Prospero";

const BS_ORANGE = "FF6E02";
const BS_TEAL = "105856";
const BS_PEACH = "FEE4D6";
const BS_DARK_GREY = "60686E";
const WHITE = "FFFFFF";
const BLACK = "000000";

const logoPath = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/bs_logo.png";


// ═══════════════════════════════════════════════════════
// SLIDE 1: TITLE
// ═══════════════════════════════════════════════════════
let s1 = pres.addSlide();
s1.background = { color: BS_ORANGE };
s1.addImage({ path: logoPath, x: 0.5, y: 0.35, w: 1.4, h: 1.4 });

s1.addText("PROSPERO", {
  x: 0.5, y: 2.2, w: 9, h: 0.9,
  fontSize: 54, fontFace: "Arial", bold: true, color: WHITE, margin: 0, charSpacing: 5,
});
s1.addText("Find every vacancy. Reach every hiring manager. First.", {
  x: 0.5, y: 3.1, w: 8, h: 0.4,
  fontSize: 18, fontFace: "Arial", color: WHITE, margin: 0,
});

s1.addText("Antony Berou  |  April 2026", {
  x: 0.5, y: 5.1, w: 9, h: 0.3,
  fontSize: 10, fontFace: "Arial", color: WHITE, margin: 0,
});


// ═══════════════════════════════════════════════════════
// SLIDE 2: WHAT IT IS — stats only
// ═══════════════════════════════════════════════════════
let s2 = pres.addSlide();
s2.background = { color: WHITE };
s2.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.06, fill: { color: BS_ORANGE } });

s2.addText("What I've Built", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});

// 6 big stat cards in 2x3 grid
const stats = [
  { value: "800+", label: "Company career sites\nscraped daily", color: BS_ORANGE },
  { value: "29", label: "ATS platforms\nautomatically handled", color: BS_TEAL },
  { value: "18,000+", label: "Jobs discovered\nin the last run", color: BS_ORANGE },
  { value: "39", label: "Countries\ncovered", color: BS_TEAL },
  { value: "556", label: "Recruitment agencies\nauto-filtered out", color: BS_ORANGE },
  { value: "<3 min", label: "Per lead, end to end\n(was 30-45 min)", color: BS_TEAL },
];

stats.forEach((st, i) => {
  const col = i % 3;
  const row = Math.floor(i / 3);
  const xx = 0.5 + col * 3.1;
  const yy = 1.2 + row * 1.85;

  s2.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: yy, w: 2.8, h: 1.55,
    fill: { color: "F8F9FA" }, rectRadius: 0.08,
    line: { color: st.color, width: 1.5 },
  });
  s2.addText(st.value, {
    x: xx, y: yy + 0.15, w: 2.8, h: 0.7,
    fontSize: 38, fontFace: "Arial", bold: true, color: st.color,
    align: "center", margin: 0,
  });
  s2.addText(st.label, {
    x: xx, y: yy + 0.9, w: 2.8, h: 0.5,
    fontSize: 11, fontFace: "Arial", color: BS_DARK_GREY,
    align: "center", margin: 0,
  });
});

s2.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.25, w: 10, h: 0.38, fill: { color: BS_ORANGE } });
s2.addText("Live today. Not a prototype.", {
  x: 0.5, y: 5.25, w: 9, h: 0.38,
  fontSize: 11, fontFace: "Arial", bold: true, color: WHITE, valign: "middle", align: "center", margin: 0,
});


// ═══════════════════════════════════════════════════════
// SLIDE 3: HOW — the flow
// ═══════════════════════════════════════════════════════
let s3 = pres.addSlide();
s3.background = { color: WHITE };
s3.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.06, fill: { color: BS_ORANGE } });

s3.addText("How It Works", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});

// 4 steps, big and clean
const steps = [
  { num: "1", title: "PASTE A URL", desc: "Any company careers page", color: BS_ORANGE },
  { num: "2", title: "AUTO-SCRAPE", desc: "Finds every job, daily", color: BS_TEAL },
  { num: "3", title: "AI DOSSIER", desc: "Why the role exists,\nwho to contact, what to say", color: BS_ORANGE },
  { num: "4", title: "OUTREACH", desc: "5-step email sequence\nfrom your Outlook", color: BS_TEAL },
];

steps.forEach((st, i) => {
  const xx = 0.35 + i * 2.45;

  s3.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.2, w: 2.15, h: 3.2,
    fill: { color: "F8F9FA" }, rectRadius: 0.08,
    line: { color: st.color, width: 1.5 },
  });

  // Number
  s3.addShape(pres.shapes.OVAL, {
    x: xx + 0.65, y: 1.5, w: 0.85, h: 0.85,
    fill: { color: st.color },
  });
  s3.addText(st.num, {
    x: xx + 0.65, y: 1.5, w: 0.85, h: 0.85,
    fontSize: 28, fontFace: "Arial", bold: true, color: WHITE,
    align: "center", valign: "middle", margin: 0,
  });

  s3.addText(st.title, {
    x: xx + 0.15, y: 2.55, w: 1.85, h: 0.35,
    fontSize: 13, fontFace: "Arial", bold: true, color: st.color,
    align: "center", margin: 0, charSpacing: 1.5,
  });

  s3.addText(st.desc, {
    x: xx + 0.15, y: 3.0, w: 1.85, h: 0.8,
    fontSize: 12, fontFace: "Arial", color: BS_DARK_GREY,
    align: "center", margin: 0,
  });

  // Arrow
  if (i < 3) {
    s3.addText("\u25B6", {
      x: xx + 2.15, y: 2.4, w: 0.3, h: 0.5,
      fontSize: 14, color: "CCCCCC", align: "center", valign: "middle", margin: 0,
    });
  }
});

s3.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.25, w: 10, h: 0.38, fill: { color: BS_ORANGE } });
s3.addText("\u00A9 2026 Barclay Simpson Associates Ltd.", {
  x: 0.5, y: 5.25, w: 9, h: 0.38,
  fontSize: 9, fontFace: "Arial", color: WHITE, valign: "middle", margin: 0,
});


// ═══════════════════════════════════════════════════════
// SLIDE 4: PROOF
// ═══════════════════════════════════════════════════════
let s4 = pres.addSlide();
s4.background = { color: BS_TEAL };

s4.addText("First 10 Weeks", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});
s4.addText("Just the AI prompt and SourceWhale. No scraper running yet.", {
  x: 0.5, y: 0.9, w: 9, h: 0.3,
  fontSize: 13, fontFace: "Arial", color: BS_PEACH, margin: 0, italic: true,
});

// Four huge stats
const proof = [
  { value: "30", label: "Campaigns", color: WHITE },
  { value: "4", label: "Jobs Won", color: BS_ORANGE },
  { value: "1", label: "Placement", color: BS_ORANGE },
  { value: "\u00A326K", label: "Revenue", color: BS_ORANGE },
];

proof.forEach((p, i) => {
  const xx = 0.5 + i * 2.35;
  s4.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.5, w: 2.05, h: 1.8,
    fill: { color: WHITE, transparency: 10 }, rectRadius: 0.08,
  });
  s4.addText(p.value, {
    x: xx, y: 1.6, w: 2.05, h: 1.0,
    fontSize: 48, fontFace: "Arial", bold: true, color: p.color,
    align: "center", margin: 0,
  });
  s4.addText(p.label, {
    x: xx, y: 2.6, w: 2.05, h: 0.4,
    fontSize: 14, fontFace: "Arial", color: WHITE,
    align: "center", margin: 0,
  });
});

// One killer line
s4.addText("We wouldn't have found the job without it.", {
  x: 0.5, y: 3.7, w: 9, h: 0.5,
  fontSize: 20, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});

s4.addText("Campaigns still running. More will convert.", {
  x: 0.5, y: 4.2, w: 9, h: 0.35,
  fontSize: 13, fontFace: "Arial", color: BS_PEACH, margin: 0, italic: true,
});


// ═══════════════════════════════════════════════════════
// SLIDE 5: THE ASK
// ═══════════════════════════════════════════════════════
let s5 = pres.addSlide();
s5.background = { color: WHITE };
s5.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.06, fill: { color: BS_ORANGE } });

s5.addText("The Ask", {
  x: 0.5, y: 0.4, w: 9, h: 0.5,
  fontSize: 30, fontFace: "Arial", bold: true, color: BS_TEAL, margin: 0,
});

// Three simple cards
const asks = [
  { value: "5", label: "Consultants", sub: "3-month pilot", color: BS_ORANGE },
  { value: "90", label: "Days", sub: "to prove ROI", color: BS_TEAL },
  { value: "1", label: "Decision", sub: "roll out or walk away", color: BS_ORANGE },
];

asks.forEach((a, i) => {
  const xx = 0.5 + i * 3.15;
  s5.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx, y: 1.2, w: 2.85, h: 2.2,
    fill: { color: "F8F9FA" }, rectRadius: 0.08,
    line: { color: a.color, width: 1.5 },
  });
  s5.addText(a.value, {
    x: xx, y: 1.35, w: 2.85, h: 0.9,
    fontSize: 54, fontFace: "Arial", bold: true, color: a.color,
    align: "center", margin: 0,
  });
  s5.addText(a.label, {
    x: xx, y: 2.25, w: 2.85, h: 0.35,
    fontSize: 16, fontFace: "Arial", bold: true, color: BLACK,
    align: "center", margin: 0,
  });
  s5.addText(a.sub, {
    x: xx, y: 2.6, w: 2.85, h: 0.3,
    fontSize: 12, fontFace: "Arial", color: BS_DARK_GREY,
    align: "center", margin: 0,
  });
});

// Bottom: what success looks like
s5.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x: 0.5, y: 3.8, w: 9, h: 1.2,
  fill: { color: BS_TEAL }, rectRadius: 0.08,
});
s5.addText("If it works:", {
  x: 0.7, y: 3.9, w: 8.6, h: 0.3,
  fontSize: 13, fontFace: "Arial", bold: true, color: BS_ORANGE, margin: 0,
});
s5.addText([
  { text: "Every consultant gets a daily pipeline of scored, qualified leads with AI intelligence", options: { fontSize: 13, fontFace: "Arial", color: WHITE, breakLine: true } },
  { text: "and personalised outreach sent automatically from their Outlook.", options: { fontSize: 13, fontFace: "Arial", color: WHITE, breakLine: true } },
  { text: "\nIf it doesn't: we've lost nothing but time.", options: { fontSize: 11, fontFace: "Arial", color: BS_PEACH, italic: true } },
], { x: 0.7, y: 4.2, w: 8.6, h: 0.7, margin: 0 });

s5.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.25, w: 10, h: 0.38, fill: { color: BS_ORANGE } });
s5.addText("ab@barclaysimpson.com", {
  x: 0.5, y: 5.25, w: 9, h: 0.38,
  fontSize: 9, fontFace: "Arial", color: WHITE, valign: "middle", align: "center", margin: 0,
});


// ═══════════════════════════════════════════════════════
// SLIDE 6: WHAT IT LOOKS LIKE
// ═══════════════════════════════════════════════════════
let s6 = pres.addSlide();
s6.background = { color: "0A0A1A" };
s6.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.06, fill: { color: BS_ORANGE } });

s6.addText("What It Looks Like", {
  x: 0.5, y: 0.2, w: 9, h: 0.45,
  fontSize: 26, fontFace: "Arial", bold: true, color: WHITE, margin: 0,
});
s6.addText("UI mockups — built, interactive, ready for development", {
  x: 0.5, y: 0.6, w: 9, h: 0.25,
  fontSize: 11, fontFace: "Arial", color: BS_DARK_GREY, margin: 0, italic: true,
});

const mockupDir = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/";

const screenshots = [
  { file: "screenshot_dashboard.png", label: "Dashboard" },
  { file: "screenshot_leads.png", label: "Lead List" },
  { file: "screenshot_campaigns.png", label: "Campaigns" },
  { file: "screenshot_builder.png", label: "Campaign Builder" },
];

screenshots.forEach((sc, i) => {
  const col = i % 2;
  const row = Math.floor(i / 2);
  const xx = 0.4 + col * 4.7;
  const yy = 1.05 + row * 2.2;

  // Shadow card
  s6.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: xx - 0.05, y: yy - 0.05, w: 4.5, h: 2.05,
    fill: { color: "1A1A2E" }, rectRadius: 0.06,
    shadow: { type: "outer", color: "000000", blur: 8, offset: 3, angle: 135, opacity: 0.4 },
  });

  // Screenshot
  s6.addImage({
    path: mockupDir + sc.file,
    x: xx, y: yy, w: 4.4, h: 1.75,
    rounding: false,
  });

  // Label
  s6.addText(sc.label, {
    x: xx, y: yy + 1.78, w: 4.4, h: 0.22,
    fontSize: 10, fontFace: "Arial", bold: true, color: BS_ORANGE,
    align: "center", margin: 0,
  });
});

s6.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.25, w: 10, h: 0.38, fill: { color: BS_ORANGE } });
s6.addText("\u00A9 2026 Barclay Simpson Associates Ltd.", {
  x: 0.5, y: 5.25, w: 9, h: 0.38,
  fontSize: 9, fontFace: "Arial", color: WHITE, valign: "middle", margin: 0,
});


const outPath = "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/mockup/Prospero_Internal_Pitch.pptx";
pres.writeFile({ fileName: outPath }).then(() => console.log("Written to " + outPath));

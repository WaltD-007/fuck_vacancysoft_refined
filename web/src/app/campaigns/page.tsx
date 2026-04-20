"use client";
import Sidebar from "../components/Sidebar";
import { FEATURES } from "../lib/features";

const catColors: Record<string, string> = { Risk: "#a29bfe", Quant: "#4dabf7", Compliance: "#00d2a0", Audit: "#ffd93d", Cyber: "#ff6b6b", Legal: "#fd79a8", "Quant Risk": "#4dabf7", "Front Office": "#ffa500" };

const leads = [
  { company: "Goldman Sachs", title: "VP, Market Risk Analytics", hm: "Sarah Chen", email: "sarah.chen@gs.com", cat: "Risk", step: "4 of 5", status: "Opened", statusColor: "#00d2a0", opens: "4/4", opensColor: "#00d2a0", time: "2h ago" },
  { company: "Barclays", title: "Head of Compliance, EMEA", hm: "James Wright", email: "j.wright@barclays.com", cat: "Compliance", step: "3 of 5", status: "✉ Replied", statusColor: "#4dabf7", opens: "3/3", opensColor: "#00d2a0", time: "4h ago" },
  { company: "Citadel", title: "Quantitative Analyst, Derivatives", hm: "David Kim", email: "d.kim@citadel.com", cat: "Quant", step: "2 of 5", status: "Opened", statusColor: "#00d2a0", opens: "2/2", opensColor: "#00d2a0", time: "6h ago" },
  { company: "HSBC", title: "Senior Cyber Security Engineer", hm: "Rachel Patel", email: "rachel.patel@hsbc.com", cat: "Cyber", step: "5 of 5", status: "No response", statusColor: "#555570", opens: "0/5", opensColor: "#555570", time: "3d ago" },
  { company: "Man Group", title: "Quantitative Risk Analyst", hm: "Tom Harding", email: "t.harding@man.com", cat: "Quant Risk", step: "1 of 5", status: "Sent", statusColor: "#00d2a0", opens: "0/1", opensColor: "#555570", time: "12m ago" },
  { company: "Marathon AM", title: "Compliance Senior VP", hm: "Lisa Morgan", email: "l.morgan@marathonfund.com", cat: "Compliance", step: "3 of 5", status: "📅 Meeting", statusColor: "#ffd93d", opens: "3/3", opensColor: "#00d2a0", time: "1d ago" },
  { company: "Deutsche Bank", title: "Internal Audit Manager, Tech", hm: "Klaus Weber", email: "klaus.weber@db.com", cat: "Audit", step: "2 of 5", status: "Opened", statusColor: "#00d2a0", opens: "1/2", opensColor: "#00d2a0", time: "8h ago" },
  { company: "BNP Paribas", title: "Legal Counsel, Structured Finance", hm: "Marie Dupont", email: "m.dupont@bnpparibas.com", cat: "Legal", step: "3 of 5", status: "✉ Replied", statusColor: "#4dabf7", opens: "3/3", opensColor: "#00d2a0", time: "5h ago" },
  { company: "Santander", title: "Credit Risk Modeller", hm: "Andrew Mills", email: "a.mills@santander.co.uk", cat: "Risk", step: "1 of 5", status: "Sent", statusColor: "#00d2a0", opens: "0/1", opensColor: "#555570", time: "25m ago" },
];

const filters = ["All (47)", "In Sequence (32)", "Replied (8)", "Meeting Booked (4)", "No Response (3)"];

export default function CampaignsPage() {
  // While FEATURES.campaignsManager is false, the rows below are static
  // mock data — flag this loudly at the top of the page so colleagues
  // don't mistake it for a live dashboard.
  const isMock = !FEATURES.campaignsManager;
  return (
    <div className="min-h-screen" style={{ background: "#0a0a0f", color: "#e8e8f0", fontFamily: "'Inter', sans-serif" }}>
      <Sidebar />
      <main className="ml-60">
        <div className="flex items-center justify-between px-8 h-14" style={{ background: "rgba(10,10,15,0.8)", borderBottom: "1px solid #1f1f2f" }}>
          <div className="font-bold text-base">Campaigns</div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#555570", minWidth: 240 }}>
              <span style={{ fontSize: 14 }}>&#128269;</span>Search leads, sources, campaigns...
              <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded" style={{ background: "#1e1e2a", border: "1px solid #2a2a3a" }}>⌘K</span>
            </div>
            <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#8888a0" }}>🔔</div>
            <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#8888a0" }}>⚙</div>
          </div>
        </div>

        <div className="p-7">
          {/* Coming-soon banner — shown while the Campaign Manager is still */}
          {/* a mock. Flip FEATURES.campaignsManager to true to hide. */}
          {isMock && (
            <div className="mb-6 px-5 py-3 rounded-xl flex items-center gap-3" style={{ background: "rgba(255,217,61,0.06)", border: "1px solid rgba(255,217,61,0.25)" }}>
              <span className="text-[14px]">⚠</span>
              <div>
                <div className="text-[13px] font-semibold" style={{ color: "#ffd93d" }}>Preview only — not live data</div>
                <div className="text-[11px]" style={{ color: "#8888a0" }}>Shows what the Campaigns page will look like. The rows, counts, and stats below are hardcoded placeholders. Real tracking ships with the email-send feature.</div>
              </div>
            </div>
          )}

          {/* Header */}
          <div className="flex justify-between items-center mb-6">
            <div>
              <div className="text-xl font-bold">Campaigns</div>
              <div className="text-sm mt-1" style={{ color: "#555570" }}>
                {isMock ? "Placeholder data — see banner above" : "5 campaigns · 1,247 contacts enrolled · 68% avg open rate"}
              </div>
            </div>
            <button
              disabled={isMock}
              title={isMock ? "Coming soon" : undefined}
              className="px-5 py-2.5 rounded-lg text-sm font-semibold text-white"
              style={isMock
                ? { background: "#1e1e2a", color: "#555570", border: "1px solid #2a2a3a", cursor: "not-allowed" }
                : { background: "linear-gradient(135deg, #6c5ce7, #8b7cf7)", boxShadow: "0 2px 12px rgba(108,92,231,0.3)" }
              }
            >+ New Campaign</button>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-5 gap-4 mb-6">
            {[
              { label: "EMAILS SENT", value: "4,812", color: "#e8e8f0", sub: "▲ 342 today", subColor: "#00d2a0" },
              { label: "OPEN RATE", value: "68.4%", color: "#00d2a0", sub: "▲ 2.1% vs last week", subColor: "#00d2a0" },
              { label: "REPLY RATE", value: "14.2%", color: "#a29bfe", sub: "▲ 1.8% vs last week", subColor: "#00d2a0" },
              { label: "MEETINGS BOOKED", value: "37", color: "#ffd93d", sub: "▲ 8 this week", subColor: "#00d2a0" },
              { label: "BOUNCE RATE", value: "2.1%", color: "#8888a0", sub: "▼ 0.3%", subColor: "#00d2a0" },
            ].map((s) => (
              <div key={s.label} className="p-5 rounded-xl" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="text-[11px] font-medium uppercase tracking-wider mb-2" style={{ color: "#555570", letterSpacing: "0.8px" }}>{s.label}</div>
                <div className="text-[28px] font-extrabold tracking-tight" style={{ color: s.color }}>{s.value}</div>
                <div className="text-xs mt-1.5" style={{ color: s.subColor }}>{s.sub}</div>
              </div>
            ))}
          </div>

          {/* Table */}
          <div className="rounded-xl overflow-hidden" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
            <div className="flex items-center justify-between px-5 py-3" style={{ borderBottom: "1px solid #1f1f2f" }}>
              <div className="flex gap-1.5">
                {filters.map((f, i) => (
                  <span key={f} className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer" style={{
                    background: i === 0 ? "rgba(108,92,231,0.15)" : "#1e1e2a",
                    color: i === 0 ? "#a29bfe" : "#8888a0",
                    border: `1px solid ${i === 0 ? "rgba(108,92,231,0.3)" : "#2a2a3a"}`,
                  }}>{f}</span>
                ))}
              </div>
              <button className="px-3 py-1.5 rounded-lg text-xs font-semibold" style={{ background: "transparent", color: "#8888a0", border: "1px solid #2a2a3a" }}>↓ Export</button>
            </div>

            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #1f1f2f" }}>
                  {["", "COMPANY", "JOB TITLE", "HIRING MANAGER", "CATEGORY", "STEP", "STATUS", "OPENS", "LAST ACTIVITY"].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider" style={{ color: "#555570", background: "#12121a", letterSpacing: "0.8px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {leads.map((l, i) => (
                  <tr key={i} className="cursor-pointer" style={{ borderBottom: "1px solid #1f1f2f" }}
                    onMouseOver={(e) => (e.currentTarget.style.background = "#1a1a25")}
                    onMouseOut={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <td className="px-4 py-3"><input type="checkbox" style={{ accentColor: "#6c5ce7" }} /></td>
                    <td className="px-4 py-3 text-[13px] font-semibold">{l.company}</td>
                    <td className="px-4 py-3 text-[13px] font-semibold">{l.title}</td>
                    <td className="px-4 py-3">
                      <div className="text-xs font-medium">{l.hm}</div>
                      <div className="text-[11px]" style={{ color: "#555570" }}>{l.email}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded uppercase tracking-wide" style={{
                        background: `${catColors[l.cat] || "#a29bfe"}15`,
                        color: catColors[l.cat] || "#a29bfe",
                      }}>{l.cat}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-[11px] px-2 py-0.5 rounded" style={{
                        background: l.step === "5 of 5" ? "rgba(255,217,61,0.08)" : "#1e1e2a",
                        color: l.step === "5 of 5" ? "#ffd93d" : "#8888a0",
                      }}>{l.step}</span>
                    </td>
                    <td className="px-4 py-3 text-xs font-semibold" style={{ color: l.statusColor }}>{l.status}</td>
                    <td className="px-4 py-3 text-xs font-bold" style={{ color: l.opensColor, fontFamily: "'JetBrains Mono', monospace" }}>{l.opens}</td>
                    <td className="px-4 py-3 text-xs" style={{ color: "#555570" }}>{l.time}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}

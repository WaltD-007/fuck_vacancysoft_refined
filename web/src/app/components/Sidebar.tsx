"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import { fetcher } from "../lib/swr";

const sections = ["OVERVIEW", "PIPELINE", "SETTINGS"];

export default function Sidebar() {
  const pathname = usePathname();
  // Shares the same SWR cache key ("/queue") as the Leads page — when
  // both are mounted there's exactly one HTTP request every 5s, not two.
  const { data: queueData } = useSWR<Array<unknown>>("/queue", fetcher, {
    refreshInterval: 5000,
  });
  const queueCount = queueData?.length ?? 0;

  const nav = [
    { label: "Dashboard", href: "/", icon: "▣", section: "OVERVIEW" },
    { label: "Lead List", href: "/leads", icon: "★", section: "OVERVIEW", badge: queueCount > 0 ? queueCount.toString() : undefined, badgeColor: "#00d2a0" },
    { label: "Sources", href: "/sources", icon: "⊙", section: "PIPELINE", badge: undefined, badgeColor: "#6c5ce7" },
    { label: "Campaign Builder", href: "/builder", icon: "✎", section: "PIPELINE" },
    { label: "Campaigns", href: "/campaigns", icon: "✉", section: "PIPELINE", badge: undefined, badgeColor: "#ffd93d" },
    { label: "Scoring Rules", href: "/settings/scoring", icon: "⚙", section: "SETTINGS" },
    { label: "Integrations", href: "/settings/integrations", icon: "⇶", section: "SETTINGS" },
    { label: "Team", href: "/settings/team", icon: "☷", section: "SETTINGS" },
  ];

  return (
    <aside className="fixed left-0 top-0 bottom-0 w-60 flex flex-col z-50" style={{ background: "#12121a", borderRight: "1px solid #1f1f2f" }}>
      {/* Logo */}
      <div className="p-5 flex items-center gap-3" style={{ borderBottom: "1px solid #1f1f2f" }}>
        <div className="w-9 h-9 rounded-[10px] flex items-center justify-center font-extrabold text-white text-lg" style={{ background: "linear-gradient(135deg, #6c5ce7, #a29bfe)" }}>P</div>
        <span className="font-bold text-lg tracking-tight" style={{ letterSpacing: "-0.5px" }}>Prospero</span>
        <span className="ml-auto text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded" style={{ background: "#6c5ce7", color: "white", letterSpacing: "0.5px" }}>Beta</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 pt-2 overflow-y-auto">
        {sections.map((section) => (
          <div key={section} className="mb-5">
            <div className="text-[10px] font-semibold uppercase px-2 mb-2" style={{ color: "#555570", letterSpacing: "1.2px" }}>{section}</div>
            {nav.filter((n) => n.section === section).map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className="flex items-center gap-2.5 px-3 py-[9px] rounded-lg text-[13.5px] font-medium mb-0.5"
                  style={active
                    ? { background: "rgba(108,92,231,0.15)", color: "#a29bfe", border: "1px solid rgba(108,92,231,0.2)" }
                    : { color: "#8888a0", border: "1px solid transparent" }
                  }
                >
                  <span className="w-[18px] text-center text-[15px]">{item.icon}</span>
                  {item.label}
                  {item.badge && (
                    <span className="ml-auto text-[10px] font-semibold px-[7px] py-[1px] rounded-[10px]" style={{ background: item.badgeColor, color: item.badgeColor === "#ffd93d" ? "#000" : "white" }}>
                      {item.badge}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {/* User */}
      <div className="px-4 py-4 flex items-center gap-2.5" style={{ borderTop: "1px solid #1f1f2f" }}>
        <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold text-white" style={{ background: "linear-gradient(135deg, #6c5ce7, #fd79a8)" }}>AB</div>
        <div className="flex-1">
          <div className="text-[13px] font-semibold">Antony B.</div>
          <div className="text-[11px]" style={{ color: "#555570" }}>Pro Plan</div>
        </div>
      </div>
    </aside>
  );
}

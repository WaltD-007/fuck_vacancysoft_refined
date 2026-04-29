"use client";
// Slide-over panel that opens from the right edge when a row is clicked.
// Shows the per-step timeline (5 SentMessage rows + their open + click
// events) and the reply log. Stop button calls /api/campaigns/{id}/cancel.
//
// Auto-refresh every 30s while any step is `pending` so operators can
// leave it open and watch sends fire.

import { useEffect, useState } from "react";
import useSWR, { mutate as swrMutate } from "swr";
import { API, fetcher } from "../lib/swr";

type OpenDetail = {
  opened_at: string;
  user_agent: string | null;
  likely_apple_mpp: boolean;
};

type ClickDetail = {
  clicked_at: string;
  original_url: string;
  user_agent: string | null;
  likely_scanner: boolean;
};

type SequenceStep = {
  sequence_index: number;
  tone: string;
  status: string;
  scheduled_for: string | null;
  sent_at: string | null;
  subject: string | null;
  error_message: string | null;
  opens: OpenDetail[];
  clicks: ClickDetail[];
};

type Reply = {
  received_at: string;
  from_email: string;
  subject: string | null;
};

type CampaignDetail = {
  campaign_output_id: string;
  title: string | null;
  company: string | null;
  location_city: string | null;
  location_country: string | null;
  category: string | null;
  hiring_manager: { email: string | null; name: string | null };
  sender: { sender_user_id: string; display_name: string | null; email: string | null };
  status: string;
  counts: { opens: number; clicks: number; replies: number };
  launched_at: string | null;
  last_activity: string | null;
  steps: SequenceStep[];
  replies: Reply[];
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  sent: "Sent",
  cancelled_replied: "Cancelled (replied)",
  cancelled_manual: "Cancelled",
  failed: "Failed",
};

const STATUS_COLOR: Record<string, string> = {
  pending: "#8888a0",
  sent: "#00d2a0",
  cancelled_replied: "#4dabf7",
  cancelled_manual: "#ff6b6b",
  failed: "#ff6b6b",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

export default function CampaignDetailPanel({
  campaignId,
  onClose,
}: {
  campaignId: string;
  onClose: () => void;
}) {
  const swrKey = `/campaigns/${campaignId}/detail`;
  const { data, error, isLoading } = useSWR<CampaignDetail>(
    swrKey, fetcher,
    {
      // Auto-refresh every 30s when any step is pending; otherwise only
      // on focus. SWR's refreshInterval is per-hook so we set it
      // dynamically based on the current data.
      refreshInterval: (latest) => {
        if (!latest) return 0;
        return latest.steps.some((s) => s.status === "pending") ? 30_000 : 0;
      },
      revalidateOnFocus: true,
    },
  );

  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);

  // Close on Escape — small affordance, big UX win
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const onStop = async () => {
    if (!data || stopping) return;
    setStopping(true);
    setStopError(null);
    try {
      const res = await fetch(`${API}/campaigns/${campaignId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "",
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setStopError(detail?.detail || `Cancel failed (${res.status}).`);
        return;
      }
      // Force a re-fetch of both the detail (this panel) and the list
      // (the page behind it) so the row's status chip updates.
      await swrMutate(swrKey);
      await swrMutate((key: string) => typeof key === "string" && key.startsWith("/campaigns?"));
      setConfirmStop(false);
    } catch (e: unknown) {
      setStopError(e instanceof Error ? e.message : "Network error");
    } finally {
      setStopping(false);
    }
  };

  const anyPending = data?.steps.some((s) => s.status === "pending") ?? false;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          top: 0, left: 0, right: 0, bottom: 0,
          background: "rgba(0,0,0,0.5)",
          zIndex: 50,
        }}
      />
      {/* Panel */}
      <aside
        role="dialog"
        aria-label="Campaign detail"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(640px, 90vw)",
          background: "#0a0a0f",
          borderLeft: "1px solid #1f1f2f",
          zIndex: 51,
          overflowY: "auto",
          color: "#e8e8f0",
          fontFamily: "'Inter', sans-serif",
        }}
      >
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: "1px solid #1f1f2f" }}>
          <div>
            <div className="text-base font-bold">{data?.title || "Campaign"}</div>
            <div className="text-xs" style={{ color: "#8888a0" }}>
              {data?.company ? `${data.company}` : "—"}
              {data?.location_city ? ` · ${data.location_city}` : ""}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="w-8 h-8 rounded flex items-center justify-center"
            style={{ background: "transparent", color: "#8888a0", border: "1px solid #2a2a3a" }}
          >×</button>
        </div>

        {isLoading && (
          <div className="p-6 text-sm" style={{ color: "#8888a0" }}>Loading…</div>
        )}

        {error && (
          <div className="p-6 text-sm" style={{ color: "#ff6b6b" }}>
            Failed to load: {(error as Error).message}
          </div>
        )}

        {data && (
          <div className="p-6 flex flex-col gap-5">
            {/* Header info */}
            <section className="flex flex-col gap-1.5">
              <Row label="Hiring manager" value={[
                data.hiring_manager.name,
                data.hiring_manager.email,
              ].filter(Boolean).join(" · ") || "—"} />
              <Row label="Operator" value={data.sender.display_name || data.sender.email || data.sender.sender_user_id || "—"} />
              <Row label="Launched" value={fmtDate(data.launched_at)} />
              <Row label="Last activity" value={fmtDate(data.last_activity)} />
              <Row label="Counts" value={
                `${data.counts.opens} opens · ${data.counts.clicks} clicks · ${data.counts.replies} replies`
              } />
            </section>

            {/* Sequence steps */}
            <section>
              <div className="text-[11px] uppercase tracking-wider mb-3" style={{ color: "#555570", letterSpacing: "0.8px" }}>
                Sequence
              </div>
              <div className="flex flex-col gap-2">
                {data.steps.map((step) => (
                  <div key={step.sequence_index}
                    className="rounded-lg p-3"
                    style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-semibold">
                        Step {step.sequence_index}
                        <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider"
                          style={{
                            background: "rgba(108,92,231,0.15)",
                            border: "1px solid rgba(108,92,231,0.3)",
                            color: "#a29bfe",
                          }}>{step.tone.replace("_", " ")}</span>
                      </div>
                      <span className="text-xs font-semibold" style={{ color: STATUS_COLOR[step.status] || "#8888a0" }}>
                        {STATUS_LABEL[step.status] || step.status}
                      </span>
                    </div>
                    <div className="text-[11px] mt-1" style={{ color: "#8888a0" }}>
                      {step.subject ? `“${step.subject}”` : ""}
                    </div>
                    <div className="text-[11px] mt-1.5" style={{ color: "#555570" }}>
                      {step.sent_at ? `sent ${fmtDate(step.sent_at)}` : `scheduled ${fmtDate(step.scheduled_for)}`}
                    </div>
                    {step.error_message && (
                      <div className="text-[11px] mt-1" style={{ color: "#ff6b6b" }}>
                        Error: {step.error_message}
                      </div>
                    )}
                    {step.opens.length > 0 && (
                      <div className="mt-2 pl-3" style={{ borderLeft: "2px solid #2a2a3a" }}>
                        {step.opens.map((o, i) => (
                          <div key={`o${i}`} className="text-[11px]" style={{
                            color: o.likely_apple_mpp ? "#555570" : "#00d2a0",
                          }}>
                            ▢ opened {fmtDate(o.opened_at)}
                            {o.likely_apple_mpp ? " (pre-fetched)" : ""}
                          </div>
                        ))}
                      </div>
                    )}
                    {step.clicks.length > 0 && (
                      <div className="mt-1 pl-3" style={{ borderLeft: "2px solid #2a2a3a" }}>
                        {step.clicks.map((c, i) => (
                          <div key={`c${i}`} className="text-[11px]" style={{
                            color: c.likely_scanner ? "#555570" : "#a29bfe",
                          }}>
                            🔗 clicked {fmtDate(c.clicked_at)}
                            {c.likely_scanner ? " (scanner)" : ""}
                            <span className="ml-1" style={{ color: "#3a3a4a" }}>{c.original_url}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {/* Replies */}
            {data.replies.length > 0 && (
              <section>
                <div className="text-[11px] uppercase tracking-wider mb-3" style={{ color: "#555570", letterSpacing: "0.8px" }}>
                  Replies ({data.replies.length})
                </div>
                <div className="flex flex-col gap-1.5">
                  {data.replies.map((r, i) => (
                    <div key={i} className="rounded-lg p-3" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                      <div className="text-xs font-semibold">{r.from_email}</div>
                      <div className="text-[11px]" style={{ color: "#8888a0" }}>{fmtDate(r.received_at)}</div>
                      {r.subject && <div className="text-[11px] mt-1" style={{ color: "#a29bfe" }}>{r.subject}</div>}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Stop button */}
            <section className="pt-4" style={{ borderTop: "1px solid #1f1f2f" }}>
              {stopError && (
                <div className="text-xs mb-3" style={{ color: "#ff6b6b" }}>
                  {stopError}
                </div>
              )}
              {!confirmStop ? (
                <button
                  onClick={() => setConfirmStop(true)}
                  disabled={!anyPending}
                  title={!anyPending ? "No pending sends to cancel" : undefined}
                  className="px-4 py-2 rounded-lg text-sm font-semibold"
                  style={{
                    background: anyPending ? "rgba(255,107,107,0.12)" : "#1e1e2a",
                    color: anyPending ? "#ff6b6b" : "#3a3a4a",
                    border: `1px solid ${anyPending ? "rgba(255,107,107,0.3)" : "#2a2a3a"}`,
                    cursor: anyPending ? "pointer" : "not-allowed",
                  }}
                >Stop campaign</button>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-xs" style={{ color: "#ff6b6b" }}>Cancel all pending sends?</span>
                  <button
                    onClick={() => void onStop()}
                    disabled={stopping}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{
                      background: "#ff6b6b",
                      color: "white",
                      border: "1px solid rgba(255,107,107,0.3)",
                    }}
                  >{stopping ? "Cancelling…" : "Yes, stop"}</button>
                  <button
                    onClick={() => setConfirmStop(false)}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{
                      background: "transparent",
                      color: "#8888a0",
                      border: "1px solid #2a2a3a",
                    }}
                  >No</button>
                </div>
              )}
            </section>
          </div>
        )}
      </aside>
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-3">
      <span className="text-[11px] uppercase tracking-wider" style={{
        color: "#555570", letterSpacing: "0.8px", minWidth: 110,
      }}>{label}</span>
      <span className="text-xs" style={{ color: "#e8e8f0" }}>{value}</span>
    </div>
  );
}

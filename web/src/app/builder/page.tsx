"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import Sidebar from "../components/Sidebar";
import { API, fetcher } from "../lib/swr";
import { FEATURES } from "../lib/features";

type ToneKey = "formal" | "informal" | "consultative" | "direct" | "candidate_spec" | "technical";

type Variant = { subject: string; body: string };

type EmailStep = {
  num: number;
  title: string;
  tone: ToneKey;
  desc: string;
  day: string;
  active: boolean;
  wait: number;
  variants: Record<ToneKey, Variant>;
};

type QueuedLead = {
  id: string;
  status: string;
  title: string;
  company: string;
};

type CampaignEmail = {
  sequence?: number;
  variants?: Partial<Record<ToneKey, Variant>>;
  // Back-compat with earlier response shape (subject/body at top level):
  subject?: string;
  body?: string;
};

const TONE_LABELS: Record<ToneKey, string> = {
  formal: "FORMAL",
  informal: "INFORMAL",
  consultative: "CONSULTATIVE",
  direct: "DIRECT",
  candidate_spec: "CANDIDATE SPEC",
  technical: "TECHNICAL",
};

const TONE_ORDER: ToneKey[] = ["formal", "informal", "consultative", "direct", "candidate_spec", "technical"];

const emptyVariants = (): Record<ToneKey, Variant> =>
  TONE_ORDER.reduce((acc, t) => {
    acc[t] = { subject: "", body: "" };
    return acc;
  }, {} as Record<ToneKey, Variant>);

const defaultSteps = (): EmailStep[] => [
  { num: 1, title: "Initial Outreach",          tone: "formal",         desc: "Sent immediately on lead discovery",    day: "Day 0",  active: true,  wait: 3,  variants: emptyVariants() },
  { num: 2, title: "Candidate Spec",            tone: "candidate_spec", desc: "Reference a live candidate profile",    day: "Day 3",  active: true,  wait: 4,  variants: emptyVariants() },
  { num: 3, title: "Technical Angle",           tone: "technical",      desc: "Domain-specific follow-up",             day: "Day 7",  active: true,  wait: 7,  variants: emptyVariants() },
  { num: 4, title: "Market Observation",        tone: "consultative",   desc: "Share a wider-market view",             day: "Day 14", active: true,  wait: 16, variants: emptyVariants() },
  { num: 5, title: "Re-engagement — New Angle", tone: "informal",       desc: "Fresh approach, new candidates",        day: "Day 30", active: true,  wait: 0,  variants: emptyVariants() },
];

const waitOptions = [1, 2, 3, 4, 5, 7, 10, 14, 16, 21, 30, 45, 60];

// Next.js 16+ requires any client component that calls useSearchParams()
// to sit inside a <Suspense> boundary so the prerender step can bail out
// to client-side rendering cleanly. The page's default export is the
// wrapper; BuilderPageInner holds all the real state + side-effects.
export default function BuilderPage() {
  return (
    <Suspense
      fallback={
        <div style={{ minHeight: "100vh", background: "#0a0a0f", color: "#8888a0", padding: 40 }}>
          Loading Campaign Builder…
        </div>
      }
    >
      <BuilderPageInner />
    </Suspense>
  );
}

function BuilderPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const leadId = searchParams.get("lead") || "";

  // Shared SWR cache — dedupes with Sidebar + Leads page.
  const { data: rawLeads } = useSWR<QueuedLead[]>("/queue", fetcher, {
    keepPreviousData: true,
  });
  const leads = useMemo(
    () => (Array.isArray(rawLeads) ? rawLeads.filter((l) => l.status === "ready") : []),
    [rawLeads],
  );
  const [steps, setSteps] = useState<EmailStep[]>(defaultSteps);
  const [activeStep, setActiveStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verifiedHmEmail, setVerifiedHmEmail] = useState("");
  // "Save as training sample" button state. Separate from the main
  // loading/error so the save request doesn't block the preview.
  const [trainSaving, setTrainSaving] = useState(false);
  const [trainFeedback, setTrainFeedback] = useState<{kind: "ok" | "err"; text: string} | null>(null);

  // When leadId changes, fetch the campaign and populate variants
  useEffect(() => {
    if (!leadId) {
      setSteps(defaultSteps());
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API}/leads/${leadId}/campaign`, { method: "POST" })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
        return r.json();
      })
      .then((data: { emails?: CampaignEmail[] }) => {
        if (cancelled) return;
        const emails = Array.isArray(data.emails) ? data.emails : [];
        setSteps((prev) =>
          prev.map((step) => {
            const match = emails.find((e) => e.sequence === step.num);
            const variants = emptyVariants();
            if (match?.variants) {
              for (const t of TONE_ORDER) {
                const v = match.variants[t];
                if (v) variants[t] = { subject: v.subject ?? "", body: v.body ?? "" };
              }
            } else if (match?.subject || match?.body) {
              // Back-compat: older campaigns had a single subject/body.
              // Seed it into the step's default tone so something shows.
              variants[step.tone] = { subject: match.subject ?? "", body: match.body ?? "" };
            }
            return { ...step, variants };
          })
        );
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message || "Failed to load campaign");
        setSteps(defaultSteps());
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [leadId]);

  const current = useMemo(
    () => steps.find((s) => s.num === activeStep) ?? steps[0],
    [steps, activeStep]
  );
  const currentVariant = current?.variants?.[current.tone] ?? { subject: "", body: "" };

  const updateVariant = (patch: Partial<Variant>) => {
    // TODO: persist edits
    setSteps((prev) =>
      prev.map((s) =>
        s.num === activeStep
          ? {
              ...s,
              variants: {
                ...s.variants,
                [s.tone]: { ...s.variants[s.tone], ...patch },
              },
            }
          : s
      )
    );
  };

  // Save the currently-active variant as a voice training sample.
  // Writes to voice_training_samples (migration 0012); the resolver
  // unions these rows with real SentMessage rows on the next campaign
  // regeneration, so the voice layer starts imitating the operator's
  // voice without waiting for the Graph send flow.
  const saveAsTrainingSample = async () => {
    if (trainSaving) return;
    if (!current || !currentVariant) return;
    const subject = (currentVariant.subject || "").trim();
    const body = (currentVariant.body || "").trim();
    if (!subject || !body) {
      setTrainFeedback({ kind: "err", text: "Subject and body must both be non-empty." });
      return;
    }
    setTrainSaving(true);
    setTrainFeedback(null);
    try {
      const res = await fetch(`${API}/users/me/voice-training-samples`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sequence_index: activeStep,
          tone: current.tone,
          subject,
          body,
          source_enriched_job_id: leadId || null,
        }),
      });
      if (res.ok) {
        setTrainFeedback({ kind: "ok", text: `Saved · ${current.tone} / step ${activeStep}` });
      } else {
        const detail = await res.json().catch(() => null);
        setTrainFeedback({
          kind: "err",
          text: detail?.detail || `Save failed (${res.status}).`,
        });
      }
    } catch {
      setTrainFeedback({ kind: "err", text: "Network error — sample not saved." });
    } finally {
      setTrainSaving(false);
      // Auto-clear the feedback pill after 4s so it doesn't linger.
      setTimeout(() => setTrainFeedback(null), 4000);
    }
  };

  const updateStepTone = (stepNum: number, tone: ToneKey) => {
    setSteps((prev) => prev.map((s) => (s.num === stepNum ? { ...s, tone } : s)));
    // Also activate the step whose tone we just changed, so the preview
    // immediately reflects the new variant rather than silently updating
    // a different step's dropdown.
    setActiveStep(stepNum);
  };

  const onLeadChange = (id: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (id) params.set("lead", id);
    else params.delete("lead");
    router.replace(`/builder${params.toString() ? `?${params.toString()}` : ""}`);
  };

  return (
    <div className="min-h-screen" style={{ background: "#0a0a0f", color: "#e8e8f0", fontFamily: "'Inter', sans-serif" }}>
      <Sidebar />
      <main className="ml-60">
        <div className="flex items-center justify-between px-8 h-14" style={{ background: "rgba(10,10,15,0.8)", borderBottom: "1px solid #1f1f2f" }}>
          <div className="font-bold text-base">Campaign Builder</div>
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
          {/* Header */}
          <div className="flex justify-between items-center mb-6 gap-4">
            <div className="shrink-0">
              <div className="text-xl font-bold">Campaign Builder</div>
              <div className="text-sm mt-1" style={{ color: "#555570" }}>Design your outreach sequence, choose tone per step, preview with merge variables</div>
            </div>
            <div className="flex-1 max-w-md">
              <div className="text-[11px] uppercase tracking-wider mb-1" style={{ color: "#555570", letterSpacing: "0.8px" }}>LEAD (READY)</div>
              <select
                value={leadId}
                onChange={(e) => onLeadChange(e.target.value)}
                className="w-full px-3 py-2 rounded-lg text-sm outline-none cursor-pointer"
                style={{ background: "#1e1e2a", border: "1px solid #2a2a3a", color: "#e8e8f0" }}
              >
                <option value="">— Select a lead —</option>
                {leads.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.company} · {l.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                disabled={!FEATURES.campaignSaveDraft}
                title={!FEATURES.campaignSaveDraft ? "Coming soon" : undefined}
                className="px-4 py-2.5 rounded-lg text-sm font-semibold"
                style={!FEATURES.campaignSaveDraft
                  ? { background: "transparent", color: "#3a3a4a", border: "1px solid #1f1f2f", cursor: "not-allowed" }
                  : { background: "transparent", color: "#8888a0", border: "1px solid #2a2a3a" }
                }
              >Save Draft</button>
              <button
                disabled={!FEATURES.campaignLaunch}
                title={!FEATURES.campaignLaunch ? "Coming soon" : undefined}
                className="px-5 py-2.5 rounded-lg text-sm font-semibold text-white"
                style={!FEATURES.campaignLaunch
                  ? { background: "#1e1e2a", color: "#555570", border: "1px solid #2a2a3a", boxShadow: "none", cursor: "not-allowed" }
                  : { background: "linear-gradient(135deg, #6c5ce7, #8b7cf7)", boxShadow: "0 2px 12px rgba(108,92,231,0.3)" }
                }
              >Launch Campaign</button>
            </div>
          </div>

          {/* Loading / error banner */}
          {(loading || error) && (
            <div className="mb-4 px-4 py-2.5 rounded-lg text-xs" style={{
              background: error ? "rgba(255,107,107,0.08)" : "rgba(108,92,231,0.1)",
              border: `1px solid ${error ? "rgba(255,107,107,0.25)" : "rgba(108,92,231,0.25)"}`,
              color: error ? "#ff6b6b" : "#a29bfe",
            }}>
              {error ? `Error loading campaign: ${error}` : "Loading campaign…"}
            </div>
          )}

          {/* Sequence + Preview */}
          <div className="grid gap-5" style={{ gridTemplateColumns: "1fr 1fr" }}>
            {/* Steps */}
            <div>
              <div className="text-sm font-semibold mb-1 flex items-center gap-2">
                ✉ Sequence Steps
                <span className="text-[11px] font-normal" style={{ color: "#555570" }}>Click a step, choose a tone to swap the variant</span>
              </div>
              <div className="mt-3">
                {steps.map((step, i) => {
                  const isActive = step.num === activeStep;
                  return (
                    <div key={step.num}>
                      <div
                        onClick={() => setActiveStep(step.num)}
                        className="p-4 rounded-xl mb-1 cursor-pointer"
                        style={{
                          background: "#16161f",
                          border: isActive ? "2px solid #6c5ce7" : "1px solid #1f1f2f",
                        }}
                      >
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0" style={{ background: step.active ? "#6c5ce7" : "#1e1e2a", border: step.active ? "none" : "1px solid #2a2a3a", color: step.active ? "white" : "#555570" }}>
                            {step.num}
                          </div>
                          <div className="flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-semibold">Step {step.num}</span>
                              <select
                                onClick={(e) => e.stopPropagation()}
                                value={step.tone}
                                onChange={(e) => updateStepTone(step.num, e.target.value as ToneKey)}
                                className="text-[10px] font-semibold px-2 py-0.5 rounded-md outline-none cursor-pointer uppercase tracking-wider"
                                style={{ background: "rgba(108,92,231,0.15)", border: "1px solid rgba(108,92,231,0.3)", color: "#a29bfe" }}
                              >
                                {TONE_ORDER.map((t) => (
                                  <option key={t} value={t}>{TONE_LABELS[t]}</option>
                                ))}
                              </select>
                            </div>
                            <div className="text-[11px] mt-0.5" style={{ color: "#555570" }}>{step.desc}</div>
                          </div>
                          <div className="text-[11px]" style={{ color: "#555570" }}>{step.day}</div>
                        </div>
                      </div>
                      {i < steps.length - 1 && (
                        <div className="flex items-center py-1 pl-3.5">
                          <div className="w-px h-5" style={{ background: "#2a2a3a" }} />
                          <span className="text-[10px] ml-3" style={{ color: "#555570" }}>
                            Wait <select
                              onClick={(e) => e.stopPropagation()}
                              defaultValue={step.wait}
                              className="px-1 py-0.5 rounded text-[10px] outline-none cursor-pointer mx-1"
                              style={{ background: "#1e1e2a", border: "1px solid #2a2a3a", color: "#a29bfe" }}
                            >
                              {waitOptions.map((w) => <option key={w} value={w}>{w}</option>)}
                            </select> days · if no reply{i === 3 ? " & role still open" : ""}
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Preview */}
            <div>
              <div className="text-sm font-semibold mb-4 flex items-center gap-2">
                <span>📄 Preview — Step {activeStep}</span>
                {current && (
                  <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md uppercase tracking-wider" style={{ background: "rgba(108,92,231,0.15)", border: "1px solid rgba(108,92,231,0.3)", color: "#a29bfe" }}>
                    {TONE_LABELS[current.tone]}
                  </span>
                )}
              </div>
              <div className="p-5 rounded-xl mb-4" style={{ background: "#0a0a0f", border: "1px solid #1f1f2f", lineHeight: 1.8 }}>
                {activeStep === 1 && (
                  <div className="pb-3 mb-3" style={{ borderBottom: "1px solid #1f1f2f" }}>
                    <input
                      type="text"
                      value={currentVariant.subject}
                      onChange={(e) => updateVariant({ subject: e.target.value })}
                      placeholder="Subject"
                      className="w-full font-semibold text-[14px] outline-none"
                      style={{ background: "transparent", color: "#e8e8f0", border: "none" }}
                    />
                  </div>
                )}
                <textarea
                  value={currentVariant.body}
                  onChange={(e) => updateVariant({ body: e.target.value })}
                  placeholder={leadId ? "Body" : "Select a lead to populate messages"}
                  className="w-full text-[13px] outline-none resize-y"
                  style={{
                    background: "transparent",
                    color: "#8888a0",
                    border: "none",
                    minHeight: 320,
                    lineHeight: 1.8,
                    fontFamily: "inherit",
                  }}
                />
                {/* "Train model" action — saves the currently-edited
                    Subject + Body as a voice training sample so the
                    next campaign regeneration picks it up via the
                    voice layer. Sits inside the preview card, below
                    the body textarea, so it reads as "save what you
                    just wrote". */}
                <div className="mt-3 pt-3 flex items-center gap-2" style={{ borderTop: "1px solid #1f1f2f" }}>
                  <button
                    onClick={() => void saveAsTrainingSample()}
                    disabled={trainSaving || !currentVariant.body.trim() || !currentVariant.subject.trim()}
                    className="px-3 py-1.5 rounded-md text-[11px] font-semibold cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                    style={{
                      background: "rgba(108,92,231,0.12)",
                      color: "#a29bfe",
                      border: "1px solid rgba(108,92,231,0.3)",
                    }}
                    title="Save the edited subject + body as a voice training sample. The voice layer picks it up on the next campaign regeneration so future output imitates your voice."
                  >
                    {trainSaving ? "Saving…" : "⚡ Train model on this"}
                  </button>
                  {trainFeedback && (
                    <span
                      className="text-[11px] font-semibold px-2 py-0.5 rounded"
                      style={{
                        background: trainFeedback.kind === "ok"
                          ? "rgba(0,210,160,0.1)"
                          : "rgba(255,107,107,0.1)",
                        color: trainFeedback.kind === "ok" ? "#00d2a0" : "#ff6b6b",
                        border: `1px solid ${trainFeedback.kind === "ok" ? "rgba(0,210,160,0.3)" : "rgba(255,107,107,0.3)"}`,
                      }}
                    >
                      {trainFeedback.text}
                    </span>
                  )}
                  <span
                    className="ml-auto text-[10px]"
                    style={{ color: "#555570" }}
                    title="The voice layer injects these as few-shot examples on the next regeneration. Cold start is zero samples; 3-5 samples per step is the sweet spot."
                  >
                    ← captures what you&apos;ve written above
                  </span>
                </div>
              </div>

              {/* Verified Hiring Manager Email */}
              <div className="p-4 rounded-xl" style={{ background: "#0a0a0f", border: "1px solid #1f1f2f" }}>
                <div className="text-[11px] uppercase tracking-wider mb-3" style={{ color: "#555570", letterSpacing: "0.8px" }}>VERIFIED HIRING MANAGER EMAIL</div>
                <input
                  type="email"
                  value={verifiedHmEmail}
                  onChange={(e) => setVerifiedHmEmail(e.target.value)}
                  placeholder="enter verified hiring manager email here"
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: "#1e1e2a", border: "1px solid #2a2a3a", color: "#e8e8f0" }}
                />
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

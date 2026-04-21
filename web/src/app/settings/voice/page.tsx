"use client";

// Settings → Voice page.
//
// One card per campaign tone (6 total). Each card carries:
//   - a collapsed "Default guidance" panel (paraphrase of the
//     tone->source mapping in CAMPAIGN_TEMPLATE_V2) so operators
//     know what they're overriding
//   - a textarea bound to the SWR cache via useVoicePrompts
//   - a character counter
//   - a small "Saved" / "Saving..." indicator that reflects the
//     debounced PUT's lifecycle
//
// Typing is debounced by the hook at 1s; mid-typing the page
// reflects the text optimistically so it feels instant. A route
// change flushes any pending write so navigating away doesn't
// drop the last keystroke.
//
// When no user has been bootstrapped (401) or the backend's voice
// endpoints aren't deployed yet (404), the page renders a friendly
// degradation banner instead of empty textareas that silently
// discard input. See useVoicePrompts's `status` for the 401 vs 404
// distinction.

import { useState } from "react";

import Sidebar from "../../components/Sidebar";
import {
  CAMPAIGN_TONES,
  type CampaignTone,
  TONE_DEFAULT_HINTS,
  useVoicePrompts,
} from "../../lib/useVoicePrompts";

// Display labels for each tone key. The DB stores snake_case
// (`candidate_spec`); operators read Title Case.
const TONE_LABELS: Record<CampaignTone, string> = {
  formal: "Formal",
  informal: "Informal",
  consultative: "Consultative",
  direct: "Direct",
  candidate_spec: "Candidate-led",
  technical: "Technical",
};

// Rough maximum visible hint. Anything longer than this and the LLM
// starts ignoring instructions anyway; surface that as a visual
// nudge to keep voice guidance punchy rather than essay-length.
const TONE_TEXT_SOFT_LIMIT = 600;

export default function VoiceSettingsPage() {
  const { prompts, isLoading, error, updatePrompt, status } = useVoicePrompts();

  // Track which tone the operator last typed into — used to show
  // the "Saved" pill only next to the most-recently-edited card
  // without lighting up all six on every keystroke.
  const [lastEdited, setLastEdited] = useState<CampaignTone | null>(null);
  // Which cards have their Default-guidance accordion open. Each
  // card starts collapsed; click to reveal.
  const [openDefaults, setOpenDefaults] = useState<Set<CampaignTone>>(new Set());

  const toggleDefault = (tone: CampaignTone) => {
    setOpenDefaults((prev) => {
      const next = new Set(prev);
      if (next.has(tone)) next.delete(tone);
      else next.add(tone);
      return next;
    });
  };

  const handleChange = (tone: CampaignTone, text: string) => {
    setLastEdited(tone);
    updatePrompt(tone, text);
  };

  // Degradation banner: 401 = no user bootstrapped (friendly
  // actionable hint), 404 = backend not deployed (developer hint),
  // other errors = generic sad-face.
  let banner: { tone: "info" | "warn" | "error"; text: string } | null = null;
  if (error) {
    if (status === 401) {
      banner = {
        tone: "info",
        text:
          "No user resolved yet — on a fresh dev env, bootstrap with `prospero user add --email you@firm.com --display-name \"You\"` and refresh.",
      };
    } else if (status === 404) {
      banner = {
        tone: "warn",
        text:
          "The voice endpoints aren't available on this backend. Make sure the voice-layer backend (PR #38) has been deployed and alembic upgrade head has run.",
      };
    } else {
      banner = {
        tone: "error",
        text: "Failed to load voice prompts. Check the API logs and refresh.",
      };
    }
  }

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-primary)" }}>
      <Sidebar />

      <main className="ml-60 h-screen flex flex-col overflow-hidden">
        <div
          className="flex items-center px-8 h-14 shrink-0"
          style={{
            background: "rgba(10,10,15,0.8)",
            borderBottom: "1px solid var(--border-subtle)",
          }}
        >
          <div className="font-bold text-base">Settings · Voice</div>
        </div>

        <div className="flex-1 overflow-y-auto px-7 py-6">
          <div className="max-w-4xl mx-auto">
            <div className="mb-6">
              <div className="text-xl font-bold mb-1">Voice</div>
              <div
                className="text-sm leading-relaxed max-w-2xl"
                style={{ color: "var(--text-muted)" }}
              >
                Write a short note on how you want each tone to sound. This gets injected into every campaign prompt Prospero sends to the LLM on your behalf. Keep it punchy — one or two sentences per tone. Leave any tone blank to use Prospero&apos;s default guidance.
              </div>
              <div
                className="text-xs mt-2"
                style={{ color: "var(--text-muted)" }}
              >
                Saves automatically as you type (1 second after the last keystroke).
              </div>
            </div>

            {banner && (
              <div
                className="mb-5 px-4 py-3 rounded-lg text-sm"
                style={{
                  background:
                    banner.tone === "info"
                      ? "var(--accent-glow)"
                      : banner.tone === "warn"
                      ? "var(--amber-bg)"
                      : "var(--red-bg)",
                  color:
                    banner.tone === "info"
                      ? "var(--accent-light)"
                      : banner.tone === "warn"
                      ? "var(--amber)"
                      : "var(--red)",
                  border:
                    banner.tone === "info"
                      ? "1px solid rgba(108,92,231,0.3)"
                      : banner.tone === "warn"
                      ? "1px solid rgba(255,179,64,0.3)"
                      : "1px solid rgba(255,107,107,0.3)",
                }}
              >
                {banner.text}
              </div>
            )}

            {isLoading && !error ? (
              <div
                className="text-sm text-center py-20"
                style={{ color: "var(--text-muted)" }}
              >
                Loading voice prompts…
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {CAMPAIGN_TONES.map((tone) => {
                  const value = prompts[tone] ?? "";
                  const open = openDefaults.has(tone);
                  const overLimit = value.length > TONE_TEXT_SOFT_LIMIT;
                  const edited = lastEdited === tone && !error;
                  return (
                    <div
                      key={tone}
                      className="rounded-xl p-4"
                      style={{
                        background: "var(--bg-card)",
                        border: "1px solid var(--border-subtle)",
                      }}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="font-semibold text-sm">
                          {TONE_LABELS[tone]}
                        </div>
                        <div className="flex items-center gap-2">
                          {edited && (
                            <span
                              className="text-[10px] px-1.5 py-0.5 rounded"
                              style={{
                                background: "rgba(0,210,160,0.08)",
                                color: "var(--green)",
                                border: "1px solid rgba(0,210,160,0.2)",
                              }}
                              title="Local edit captured. Server save happens 1s after your last keystroke."
                            >
                              ● Saving
                            </span>
                          )}
                          <button
                            onClick={() => toggleDefault(tone)}
                            className="text-[10px] font-medium px-2 py-0.5 rounded cursor-pointer"
                            style={{
                              background: "var(--bg-elevated)",
                              color: "var(--text-muted)",
                              border: "1px solid var(--border)",
                            }}
                            title="Show the default guidance Prospero uses when this box is blank"
                          >
                            {open ? "Hide default" : "Show default"}
                          </button>
                        </div>
                      </div>

                      {open && (
                        <div
                          className="mb-3 px-3 py-2 rounded text-[11px] leading-relaxed"
                          style={{
                            background: "var(--bg-elevated)",
                            color: "var(--text-muted)",
                            border: "1px solid var(--border-subtle)",
                          }}
                        >
                          {TONE_DEFAULT_HINTS[tone]}
                        </div>
                      )}

                      <textarea
                        value={value}
                        onChange={(e) => handleChange(tone, e.target.value)}
                        placeholder={`e.g. ${placeholderFor(tone)}`}
                        rows={5}
                        disabled={!!error}
                        className="w-full p-2 text-sm rounded resize-y"
                        style={{
                          background: "var(--bg-elevated)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border)",
                          fontFamily: "inherit",
                          minHeight: 100,
                        }}
                      />

                      <div className="flex items-center justify-between mt-1.5">
                        <div
                          className="text-[10px]"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {value.trim()
                            ? "Overrides the default guidance above."
                            : "Uses Prospero's default guidance."}
                        </div>
                        <div
                          className="text-[10px]"
                          style={{
                            color: overLimit
                              ? "var(--amber)"
                              : "var(--text-muted)",
                          }}
                          title={
                            overLimit
                              ? "Longer guidance starts getting ignored by the model. Consider trimming."
                              : undefined
                          }
                        >
                          {value.length} / {TONE_TEXT_SOFT_LIMIT}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

// Per-tone placeholder examples — shown in an empty textarea as
// `e.g. ...` so operators see one concrete shape of what to write.
// Deliberately short; long placeholders look like already-filled
// boxes.
function placeholderFor(tone: CampaignTone): string {
  switch (tone) {
    case "formal":
      return "Measured, no contractions. Name the regulatory pressure directly.";
    case "informal":
      return "Estuary English. Short. 'Cheers' is fine. Never 'touching base'.";
    case "consultative":
      return "Open with a market observation, not a pitch.";
    case "direct":
      return "First line lands the point. No adjectives in the subject.";
    case "candidate_spec":
      return "Lead with a real profile. Name what they can do.";
    case "technical":
      return "Names the domain tension in role-specific language.";
  }
}

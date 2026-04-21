// SWR hook for the per-user campaign voice prompts.
//
// Wraps GET / PUT /api/users/me/campaign-prompts so the Settings →
// Voice page can:
//   - initialise six tone text areas from the user's saved prompts
//   - push edits back to the backend on a 1s debounce
//   - share its cache with any other component that also reads the
//     same SWR key (none today, but future audit / preview panels
//     will dedupe automatically — e.g. a "what's active" pill on
//     the Campaign Builder)
//
// Cold-start / missing-backend behaviour mirrors useCurrentUser:
//   * 401 (no user bootstrapped) or 404 (PR A not deployed) → hook
//     leaves all six tones as empty strings and `updatePrompt` is
//     a best-effort no-op. The Settings page renders a friendly
//     "Bootstrap a user first" hint in that case.
//   * network error → silent; next successful PUT resyncs.
//
// PUT semantics match the backend (see src/vacancysoft/api/routes/
// voice.py):
//   * missing key  → leave that tone alone
//   * present key  → upsert that tone
//   * empty string → clear that tone (row kept, text emptied)
//
// The hook only ever PUTs one tone at a time — whichever field the
// operator just typed into. That lets the debounce coalesce rapid
// typing on a single tone without blocking edits on other tones.

"use client";

import useSWR from "swr";
import { useCallback, useEffect, useRef } from "react";
import { API, fetcher } from "./swr";

// Must match CAMPAIGN_TONES in src/vacancysoft/intelligence/voice.py.
// Centralising here means a new tone requires both a frontend and
// backend change — a deliberate choice to keep the two in lockstep.
export const CAMPAIGN_TONES = [
  "formal",
  "informal",
  "consultative",
  "direct",
  "candidate_spec",
  "technical",
] as const;

export type CampaignTone = (typeof CAMPAIGN_TONES)[number];

export type VoicePrompts = Record<CampaignTone, string>;

const EMPTY_PROMPTS: VoicePrompts = {
  formal: "",
  informal: "",
  consultative: "",
  direct: "",
  candidate_spec: "",
  technical: "",
};

// Human-readable defaults shown in the "Default guidance" panel of
// each tone card, so operators know what they're overriding. These
// MIRROR (not verbatim) the tone->source mapping in
// src/vacancysoft/intelligence/prompts/base_campaign.py's
// CAMPAIGN_TEMPLATE_V2. Keeping them paraphrased means a minor edit
// to the Python prompt (e.g. softening an adjective) doesn't require
// a frontend change; the overall intent stays in sync and the UI
// warns operators when drift is worth a re-read.
export const TONE_DEFAULT_HINTS: Record<CampaignTone, string> = {
  formal:
    "Source: Company Context. Measured institutional framing. Polished British business English, minimal contractions, third-person where natural.",
  informal:
    "Source: Stated Need vs Actual Need. An experienced FS recruiter in their own words — approachable, gender neutral, positive, estuary English, avoid jargon.",
  consultative:
    "Source: Company Context + Core Business Problem. Market-observation led, positions sender as a trusted partner with a view on the wider market.",
  direct:
    "Source: Core Business Problem stripped to one line. Concise and outcome-focused, cuts to the point, light on adjectives.",
  candidate_spec:
    "Source: Ideal Candidate Profiles. Leads with a live candidate or pipeline; references a specific profile, their background, and why they fit.",
  technical:
    "Source: Specification Risk OR Stated vs Actual. Names the domain tension using role-specific language (risk frameworks, quant terms, compliance regs) without jargon-heavy.",
};

export function useVoicePrompts() {
  const { data, error, mutate } = useSWR<VoicePrompts>(
    "/users/me/campaign-prompts",
    fetcher,
    {
      // Prompts don't change out from under us; poll on focus is
      // overkill.
      revalidateOnFocus: false,
      dedupingInterval: 30_000,
      // 401 / 404 → no user bootstrapped or PR A not deployed.
      // Leave hook state empty and let the page render its
      // degradation hint instead of retry-spamming.
      shouldRetryOnError: false,
    },
  );

  // Single debounce timer shared across all six tones. Latest PUT
  // per tone wins within the window. If two different tones are
  // edited inside one debounce window, both are coalesced into one
  // PUT body (the backend's upsert accepts any subset).
  const pending = useRef<Partial<VoicePrompts>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    const payload = { ...pending.current };
    pending.current = {};
    if (Object.keys(payload).length === 0) return;
    try {
      const res = await fetch(`${API}/users/me/campaign-prompts`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) return; // silent — hook is best-effort
      const merged = (await res.json()) as VoicePrompts;
      // Reconcile with the server-authoritative merged state.
      mutate(() => merged, { revalidate: false });
    } catch {
      // Network error — silent. Local optimistic state stays correct
      // for the session; the next successful PUT resyncs.
    }
  }, [mutate]);

  const updatePrompt = useCallback(
    (tone: CampaignTone, text: string) => {
      pending.current = { ...pending.current, [tone]: text };
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, 1000);
      // Optimistic client-side merge — UI reflects the typed text
      // immediately without waiting for the round-trip.
      mutate(
        (curr) => ({ ...(curr ?? EMPTY_PROMPTS), [tone]: text }),
        { revalidate: false },
      );
    },
    [flush, mutate],
  );

  // Flush on unmount so a route change doesn't drop the most-recent
  // keystroke's write. Matches useCurrentUser's pattern.
  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
        void flush();
      }
    },
    [flush],
  );

  return {
    prompts: data ?? EMPTY_PROMPTS,
    isLoading: !data && !error,
    error,
    updatePrompt,
    // Exposed so the page can show a friendly degradation hint
    // when the identity resolver is empty (401) vs when PR A isn't
    // deployed (404) vs actual errors.
    status: error ? (error as { status?: number }).status ?? "error" : null,
  };
}

// SWR hook that exposes the current Prospero user + a debounced
// preferences updater. Wraps GET /api/users/me + PATCH
// /api/users/me/preferences so every page can:
//   - initialise local filter state from the user's saved preferences
//   - push changes back to the backend on a 500ms debounce
//
// The hook handles the two degradation modes cleanly:
//   1. Backend is missing PR 1 entirely (404 on /users/me) — `user`
//      stays undefined, `updatePreferences` is a no-op.
//   2. Backend is there but no user row exists yet (401) — same
//      behaviour. Bootstrap with `prospero user add --email X
//      --display-name Y` to light up persistence.
//
// Preferences merge is shallow at the top level — the backend does
// `{...existing, ...patch}`. So pages that want to persist their
// filter state should PATCH the whole section (e.g.
// `{ dashboard_feed: {category, country, sub_specialism,
// employment_type} }`) rather than partial keys.
//
// The SWR key "/users/me" is stable, so multiple components on the
// same page (Dashboard, Sidebar, any future settings panel) share a
// single fetch / cache entry automatically.

"use client";

import useSWR from "swr";
import { useCallback, useEffect, useRef } from "react";
import { API, fetcher } from "./swr";

// Known preference sections — keep this type loose so new pages can
// add their own without widening the hook.
export type DashboardFeedPrefs = {
  category?: string;
  country?: string;
  sub_specialism?: string;
  employment_type?: string;
};

export type Preferences = {
  dashboard_feed?: DashboardFeedPrefs;
  [key: string]: unknown;
};

export type CurrentUser = {
  id: string;
  email: string;
  display_name: string;
  role: string;
  active: boolean;
  entra_object_id: string | null;
  preferences: Preferences;
};

export function useCurrentUser() {
  const { data, error, mutate } = useSWR<CurrentUser>("/users/me", fetcher, {
    // Preferences don't change out from under us (only this browser
    // mutates them); no need to poll on focus.
    revalidateOnFocus: false,
    dedupingInterval: 30_000,
    // On 401/404 (no user bootstrapped yet, or PR 1 not deployed),
    // don't retry — just leave `user` undefined and fall back to
    // hardcoded defaults. Avoids a flood of failed requests on a
    // stack that never had a user backend.
    shouldRetryOnError: false,
  });

  // Debounced PATCH. One timer, latest payload wins. `pending`
  // accumulates the shallow-merged patches between timer fires.
  const pending = useRef<Preferences | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(async () => {
    const payload = pending.current;
    pending.current = null;
    if (!payload) return;
    try {
      const res = await fetch(`${API}/users/me/preferences`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) return;    // silent — hook is best-effort
      const merged = (await res.json()) as Preferences;
      // Reconcile the cache with the server-authoritative merged prefs.
      mutate(
        (curr) => (curr ? { ...curr, preferences: merged } : curr),
        { revalidate: false },
      );
    } catch {
      // Network error — also silent. User's local state is still
      // correct for this session; the next successful PATCH will
      // resync.
    }
  }, [mutate]);

  const updatePreferences = useCallback(
    (patch: Preferences) => {
      // Accumulate at the top level. Callers always send a full
      // section blob (e.g. whole `dashboard_feed`), so a shallow
      // merge here is correct — it just coalesces patches from
      // multiple setters within the debounce window.
      pending.current = { ...(pending.current ?? {}), ...patch };
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, 500);
      // Optimistic client-side merge so the UI reflects the intended
      // state immediately, before the server round-trip lands.
      mutate(
        (curr) =>
          curr
            ? { ...curr, preferences: { ...curr.preferences, ...patch } }
            : curr,
        { revalidate: false },
      );
    },
    [flush, mutate],
  );

  // On unmount, flush any pending write synchronously-ish so a route
  // change doesn't drop a user's most-recent selection.
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
    user: data,
    preferences: data?.preferences ?? ({} as Preferences),
    isLoading: !data && !error,
    error,
    updatePreferences,
  };
}

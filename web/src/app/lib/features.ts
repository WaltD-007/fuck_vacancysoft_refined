/**
 * Central feature flags for the Prospero frontend.
 *
 * Each flag is either `true` (feature is live and should render normally)
 * or `false` (feature is a work-in-progress; UI surfaces it as "Coming soon"
 * and non-clickable).
 *
 * Flip to `true` once the backing work is landed. The build-time grep-target
 * is `FEATURES.<name>` — searching for that string surfaces every place a
 * feature's UI is gated.
 */

export const FEATURES = {
  // /settings/* pages don't exist yet. The Sidebar links go nowhere today.
  scoringRules: false,
  integrations: false,
  team: false,

  // /settings/voice — per-user campaign voice prompts. Lights up once
  // the voice-layer backend (PR #38) is deployed. The hook degrades
  // gracefully on 401 / 404 so flipping this flag early is safe; the
  // page renders a "bootstrap a user" hint when the backend isn't
  // ready yet. Default ON to match the backend's default-wired-in
  // status.
  voicePrompts: true,

  // The /campaigns page is a hardcoded mock — no real data behind it yet.
  // When the Campaign Manager feature is built, flip to true and the page
  // link will light up.
  campaignsManager: false,

  // The Campaign Builder has a Launch button but no backend to launch to.
  // Email send + multi-step scheduling tranche flips this on.
  campaignLaunch: false,

  // "Save Draft" next to Launch — same tranche, but a separate subswitch
  // so we can ship draft persistence without wiring send.
  campaignSaveDraft: false,

  // The top-right cogs/bell/search in each page header are decorative
  // today. Global search, notifications, and per-user settings are all
  // future work.
  headerBell: false,
  headerSettingsCog: false,
  headerGlobalSearch: false,

  // "Mark as Agency" button on lead cards. Endpoint is deploy-safe as of
  // 2026-04-20 but the button is still considered beta — flip to true when
  // the change-request review flow lands (or immediately if you want to
  // expose the current "DB cascade only" behaviour to colleagues).
  markAgencyButton: true,

  // "×" remove button on Source cards. Default OFF — when a colleague
  // is clicking around via the review tunnel (or any read-only demo
  // scenario) an accidental delete is a real risk: the endpoint
  // cascades and removes the source + its raw_jobs + downstream rows.
  // Flip to true when you're operating the directory yourself and
  // want the button back.
  //
  // When OFF, the button still renders but is visually greyed and
  // non-clickable. The confirm-delete popover flow remains in code;
  // it's just unreachable until the flag flips.
  removeSourceButton: false,
} as const;

export type FeatureFlag = keyof typeof FEATURES;

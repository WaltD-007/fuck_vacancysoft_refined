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
} as const;

export type FeatureFlag = keyof typeof FEATURES;

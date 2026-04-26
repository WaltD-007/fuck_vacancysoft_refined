"use client";

type Props = {
  companySearch: string;
  onCompanySearchChange: (value: string) => void;
  countryFilter: string;
  onCountryFilterChange: (value: string) => void;
  countries: { country: string; count: number }[];
  employmentTypeFilter: string;
  onEmploymentTypeFilterChange: (value: string) => void;
  sectorFilter: string;
  onSectorFilterChange: (value: string) => void;
  sectorCounts: Record<string, number>;
};

/**
 * Header filter controls: company search, country, employment type,
 * sector dropdowns. Rendered inline inside the parent's header flex
 * row (the parent keeps the "Add Company" button alongside).
 *
 * Sector filter renders only sectors actually present in the loaded
 * source list, so the dropdown reflects what the operator can filter
 * to today (no dead options for empty buckets). Counts surface in
 * each option label.
 *
 * All inputs are controlled — no internal state. The change callbacks
 * are wired by the parent to also clear `sourceJobs` and collapse any
 * expanded card (filter changes invalidate the job cache).
 */

// Display order chosen so the dropdown reads top-down from the most
// commercially-relevant buckets to the niche / cosmetic ones. Operators
// scanning for "the right thing to filter to" find their bucket faster.
const SECTOR_LABELS: Record<string, string> = {
  retail_bank: "Retail Bank",
  investment_bank: "Investment Bank",
  building_society: "Building Society",
  hedge_fund: "Hedge Fund",
  asset_manager: "Asset Manager",
  private_equity: "Private Equity",
  private_credit: "Private Credit",
  wealth_manager: "Wealth Manager",
  insurance: "Insurance",
  reinsurance: "Reinsurance",
  insurance_broker: "Insurance Broker",
  pension_fund: "Pension Fund",
  sovereign: "Sovereign / SWF",
  fintech: "Fintech",
  payments: "Payments",
  crypto: "Crypto / Digital Assets",
  hft_market_maker: "HFT / Market Maker",
  commodity_trading: "Commodity Trading",
  regtech: "Regtech",
  insurtech: "Insurtech",
  wealthtech: "Wealthtech",
  lending: "Lending",
  consultancy: "Consultancy",
  law_firm: "Law Firm",
  audit_firm: "Audit Firm",
  regulator: "Regulator",
  custodian: "Custodian",
  market_infrastructure: "Market Infrastructure",
  data_provider: "Data Provider",
  aggregator: "Aggregator",
  recruitment: "Recruitment",
  unknown: "Unclassified",
};
const SECTOR_ORDER = [
  "hedge_fund",
  "asset_manager",
  "private_equity",
  "private_credit",
  "wealth_manager",
  "investment_bank",
  "retail_bank",
  "building_society",
  "hft_market_maker",
  "commodity_trading",
  "insurance",
  "reinsurance",
  "insurance_broker",
  "pension_fund",
  "sovereign",
  "custodian",
  "market_infrastructure",
  "data_provider",
  "fintech",
  "payments",
  "crypto",
  "regtech",
  "insurtech",
  "wealthtech",
  "lending",
  "consultancy",
  "law_firm",
  "audit_firm",
  "regulator",
  "aggregator",
  "recruitment",
  "unknown",
];

export default function SourceFilters({
  companySearch,
  onCompanySearchChange,
  countryFilter,
  onCountryFilterChange,
  countries,
  employmentTypeFilter,
  onEmploymentTypeFilterChange,
  sectorFilter,
  onSectorFilterChange,
  sectorCounts,
}: Props) {
  // Only show sectors that have at least one source loaded — keeps the
  // dropdown tight and reflects today's data, not the full enum.
  const visibleSectors = SECTOR_ORDER.filter((k) => (sectorCounts[k] || 0) > 0);

  return (
    <>
      <input
        type="text"
        value={companySearch}
        onChange={(e) => onCompanySearchChange(e.target.value)}
        placeholder="Search companies..."
        className="px-3 py-2 rounded-lg text-sm outline-none w-48"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
      />
      <select
        value={countryFilter}
        onChange={(e) => onCountryFilterChange(e.target.value)}
        className="px-3 py-2 rounded-lg text-sm cursor-pointer outline-none"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
      >
        <option value="">All Countries</option>
        {countries.map((c) => (
          <option key={c.country} value={c.country}>{c.country} ({c.count})</option>
        ))}
      </select>
      <select
        value={sectorFilter}
        onChange={(e) => onSectorFilterChange(e.target.value)}
        className="px-3 py-2 rounded-lg text-sm cursor-pointer outline-none"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
        title="Filter by industry sector"
      >
        <option value="">All Sectors</option>
        {visibleSectors.map((k) => (
          <option key={k} value={k}>
            {SECTOR_LABELS[k] || k} ({sectorCounts[k] || 0})
          </option>
        ))}
      </select>
      <select
        value={employmentTypeFilter}
        onChange={(e) => onEmploymentTypeFilterChange(e.target.value)}
        className="px-3 py-2 rounded-lg text-sm cursor-pointer outline-none"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
      >
        <option value="Permanent">Permanent</option>
        <option value="Contract">Contract</option>
      </select>
    </>
  );
}

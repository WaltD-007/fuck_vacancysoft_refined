"use client";

type Props = {
  companySearch: string;
  onCompanySearchChange: (value: string) => void;
  countryFilter: string;
  onCountryFilterChange: (value: string) => void;
  countries: { country: string; count: number }[];
  employmentTypeFilter: string;
  onEmploymentTypeFilterChange: (value: string) => void;
};

/**
 * The three header filter controls: company search, country dropdown,
 * employment-type dropdown. Rendered inline inside the parent's header
 * flex row (the parent keeps the "Add Company" button alongside).
 *
 * All three are controlled inputs — no internal state. The change
 * callbacks are wired by the parent to also clear `sourceJobs` and
 * collapse any expanded card (filter changes invalidate the job cache).
 *
 * Extracted verbatim from `sources/page.tsx` during the Week 3 split.
 */
export default function SourceFilters({
  companySearch,
  onCompanySearchChange,
  countryFilter,
  onCountryFilterChange,
  countries,
  employmentTypeFilter,
  onEmploymentTypeFilterChange,
}: Props) {
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

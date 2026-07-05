/*
 * format.js — pure presentation helpers (no DOM, no app state).
 *
 * Value formatters and small HTML-string builders shared by the app. Kept
 * side-effect free so they can be unit-tested in isolation.
 */

// ISO country codes that appear in the dataset -> display names, for the
// compact country tag. Flag emoji don't render on Windows, so we tag instead.
export const COUNTRY_NAME = {
  US: "United States",
  MX: "Mexico",
  PR: "Puerto Rico",
  DO: "Dominican Republic",
  JM: "Jamaica",
  CR: "Costa Rica",
  GT: "Guatemala",
  HN: "Honduras",
  SV: "El Salvador",
  SX: "Sint Maarten",
  CA: "Canada",
};

export const fmtMoneyRound = (v) => "$" + Math.round(v).toLocaleString("en-US");
export const fmtMoneyExact = (v) =>
  "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const fmtMiles = (v) => v.toLocaleString("en-US") + " mi";
export const parseDate = (s) => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
};
export const fmtDate = (s) =>
  parseDate(s).toLocaleDateString("en-US", { month: "short", day: "numeric" });

export function escapeHtml(s) {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
}

export function highlight(text, q) {
  if (!q) return escapeHtml(text);
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return escapeHtml(text);
  return (
    escapeHtml(text.slice(0, i)) +
    "<mark>" +
    escapeHtml(text.slice(i, i + q.length)) +
    "</mark>" +
    escapeHtml(text.slice(i + q.length))
  );
}

// Domestic US is the common case, so we omit it to keep rows quiet.
export const countryTag = (cc) =>
  !cc || cc === "US"
    ? ""
    : `<span class="cc-tag" title="${escapeHtml(COUNTRY_NAME[cc] || cc)}">${escapeHtml(cc)}</span>`;

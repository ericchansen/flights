import { describe, it, expect } from "vitest";
import {
  fmtMoneyRound,
  fmtMoneyExact,
  fmtMiles,
  parseDate,
  fmtDate,
  escapeHtml,
  highlight,
  countryTag,
} from "../public/format.js";

describe("money + miles formatters", () => {
  it("rounds cash to whole dollars with grouping", () => {
    expect(fmtMoneyRound(27)).toBe("$27");
    expect(fmtMoneyRound(1234.6)).toBe("$1,235");
  });

  it("renders exact cash with two decimals", () => {
    expect(fmtMoneyExact(27)).toBe("$27.00");
    expect(fmtMoneyExact(1234.5)).toBe("$1,234.50");
  });

  it("formats miles with grouping and a unit", () => {
    expect(fmtMiles(5000)).toBe("5,000 mi");
  });
});

describe("date helpers", () => {
  it("parses an ISO date into a local Date", () => {
    const d = parseDate("2026-07-22");
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(6); // July is 0-based
    expect(d.getDate()).toBe(22);
  });

  it("formats a short month/day label", () => {
    expect(fmtDate("2026-07-22")).toBe("Jul 22");
  });
});

describe("escapeHtml", () => {
  it("escapes the four HTML-sensitive characters", () => {
    expect(escapeHtml('<a title="x">&')).toBe("&lt;a title=&quot;x&quot;&gt;&amp;");
  });
});

describe("highlight", () => {
  it("wraps the first match in <mark> and escapes the rest", () => {
    expect(highlight("Denver", "env")).toBe("D<mark>env</mark>er");
  });

  it("escapes but does not mark when the query is empty", () => {
    expect(highlight("<b>", "")).toBe("&lt;b&gt;");
  });

  it("returns escaped text when there is no match", () => {
    expect(highlight("Denver", "xyz")).toBe("Denver");
  });
});

describe("countryTag", () => {
  it("is empty for domestic US and blank codes", () => {
    expect(countryTag("US")).toBe("");
    expect(countryTag("")).toBe("");
    expect(countryTag(null)).toBe("");
  });

  it("renders a titled tag for known countries", () => {
    expect(countryTag("MX")).toBe('<span class="cc-tag" title="Mexico">MX</span>');
  });

  it("falls back to the raw code when the country is unknown", () => {
    expect(countryTag("ZZ")).toBe('<span class="cc-tag" title="ZZ">ZZ</span>');
  });
});

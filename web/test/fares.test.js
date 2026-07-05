import { describe, it, expect } from "vitest";
import { metricArrayFor, bestInWindow } from "../public/fares.js";

const route = {
  cashByDate: [null, 27, 40, 30],
  milesByDate: [5000, null, 9000, 8000],
};

describe("metricArrayFor", () => {
  it("selects the cash series for 'cash'", () => {
    expect(metricArrayFor(route, "cash")).toBe(route.cashByDate);
  });

  it("selects the miles series for anything else", () => {
    expect(metricArrayFor(route, "miles")).toBe(route.milesByDate);
  });
});

describe("bestInWindow", () => {
  it("finds the cheapest priced entry and its index across the window", () => {
    expect(bestInWindow(route.cashByDate, 0, 3)).toEqual({ value: 27, dateIdx: 1 });
  });

  it("respects the window bounds", () => {
    expect(bestInWindow(route.cashByDate, 2, 3)).toEqual({ value: 30, dateIdx: 3 });
  });

  it("skips null / undefined entries", () => {
    expect(bestInWindow(route.milesByDate, 0, 1)).toEqual({ value: 5000, dateIdx: 0 });
  });

  it("returns null when nothing is priced in the window", () => {
    expect(bestInWindow([null, null], 0, 1)).toBeNull();
    // Index 0 of the cash series is null.
    expect(bestInWindow(route.cashByDate, 0, 0)).toBeNull();
  });

  it("keeps the earliest index on ties", () => {
    expect(bestInWindow([10, 10, 5, 5], 0, 3)).toEqual({ value: 5, dateIdx: 2 });
  });
});

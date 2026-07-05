import { describe, it, expect } from "vitest";
import { findTrips } from "../public/trips.js";

// A tiny 5-date calendar with a DEN hub and two destinations. Every route is
// flat-priced across dates so expected totals are easy to reason about.
function makeDataset() {
  const flat = (p) => [p, p, p, p, p];
  return {
    dates: ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"],
    routesByPair: {
      DEN: {
        SLC: { cashByDate: flat(50), milesByDate: [], nonstop: 1 },
        LAX: { cashByDate: flat(80), milesByDate: [], nonstop: 0 },
      },
      SLC: {
        DEN: { cashByDate: flat(50), milesByDate: [], nonstop: 1 },
        LAX: { cashByDate: flat(40), milesByDate: [], nonstop: 1 },
      },
      LAX: {
        DEN: { cashByDate: flat(80), milesByDate: [], nonstop: 0 },
        SLC: { cashByDate: flat(40), milesByDate: [], nonstop: 1 },
      },
    },
  };
}

const base = {
  home: "DEN",
  metric: "cash",
  maxStops: 1,
  minStay: 1,
  minTrip: 1,
  maxTrip: 5,
};

describe("findTrips", () => {
  it("returns nothing for an unknown home or empty calendar", () => {
    expect(findTrips({ routesByPair: {}, dates: [] }, base).trips).toEqual([]);
    expect(findTrips(makeDataset(), { ...base, home: "ZZZ" }).trips).toEqual([]);
  });

  it("finds round-trips ranked cheapest-first", () => {
    const res = findTrips(makeDataset(), base);
    expect(res.trips.length).toBeGreaterThan(0);
    const cheapest = res.trips[0];
    expect(cheapest.cities).toEqual(["SLC"]); // 50 + 50 = 100, beats LAX's 160
    expect(cheapest.total).toBe(100);
    expect(cheapest.legs.map((l) => `${l.o}-${l.d}`)).toEqual(["DEN-SLC", "SLC-DEN"]);
  });

  it("excludes trips over budget", () => {
    // Cheapest possible round-trip is 100, so a budget of 90 yields nothing.
    expect(findTrips(makeDataset(), { ...base, budget: 90 }).trips).toEqual([]);
  });

  it("honors nonstopOnly by dropping any itinerary with a connecting leg", () => {
    const res = findTrips(makeDataset(), { ...base, maxStops: 2, nonstopOnly: true });
    expect(res.trips.length).toBeGreaterThan(0);
    for (const trip of res.trips) {
      expect(trip.nonstop).toBe(true);
    }
    // DEN-LAX and LAX-DEN are connecting, so no trip may touch LAX.
    for (const trip of res.trips) {
      expect(trip.cities).not.toContain("LAX");
    }
  });

  it("builds multi-city itineraries when the stop budget allows", () => {
    const res = findTrips(makeDataset(), { ...base, maxStops: 2 });
    const multi = res.trips.find((t) => t.cities.length === 2);
    expect(multi).toBeTruthy();
    expect(multi.cities).toEqual(["SLC", "LAX"]);
  });

  it("enforces the trip-length window", () => {
    // Only 5 dates (indices 0-4) so the longest span is 4; minTrip 5 is unreachable.
    expect(findTrips(makeDataset(), { ...base, minTrip: 5 }).trips).toEqual([]);
  });

  it("truncates to the limit and flags it", () => {
    const res = findTrips(makeDataset(), { ...base, maxStops: 2, limit: 1 });
    expect(res.trips).toHaveLength(1);
    expect(res.truncated).toBe(true);
  });
});

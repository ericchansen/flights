/*
 * trips.js — pure, UI-agnostic cheap-vacation itinerary search.
 *
 * Given the fare calendar already exported in data.json, build whole trips from
 * a home airport and rank them by total price:
 *
 *   • Round-trips     home → X → home
 *   • Multi-city hops home → X → Y → home  (up to `maxStops` intermediate cities)
 *
 * The exporter gives, per route, the cheapest cash/miles fare **per departure
 * date** (`cashByDate[]` / `milesByDate[]` aligned to `meta.dates`). That is all
 * the search needs — multi-day vacations only require date-level timing.
 *
 * Modelling assumptions (documented, since they bound what "a trip" means here):
 *  - A leg departing on date index `i` is treated as arriving the same day; the
 *    low-fare calendar has no clock times, so same-day connections are out of
 *    scope. Nights spent in a city = nextDepartIdx − arriveIdx.
 *  - Trip length ("days") = span from the outbound departure to the return
 *    departure (returnIdx − outboundIdx). price-per-day = total / max(days, 1).
 *  - `minStay` nights are required in every intermediate city.
 *  - A trip never repeats a city and never touches home mid-trip.
 *  - Fares are single-airline and the single cheapest per route-date (whatever
 *    the exporter recorded); a market may still be sold as a connection.
 *
 * Pure: no DOM, no globals. Native ES module — `import { findTrips }`.
 */

/**
 * @param {Object} dataset
 *   @param {Object} dataset.routesByPair  { [origin]: { [dest]: route } } where
 *     route has cashByDate[], milesByDate[], and (optional) nonstop.
 *   @param {string[]} dataset.dates       meta.dates (ISO strings), indexable.
 * @param {Object} params
 *   @param {string} params.home           home airport code (required)
 *   @param {"cash"|"miles"} [params.metric="cash"]
 *   @param {number} [params.maxStops=2]   max intermediate cities (>=1)
 *   @param {number} [params.minStay=2]    min nights per intermediate city
 *   @param {number} [params.minTrip=3]    min total trip days
 *   @param {number} [params.maxTrip=10]   max total trip days
 *   @param {number} [params.budget]       max total price (metric units)
 *   @param {boolean}[params.nonstopOnly=false]
 *   @param {number} [params.rangeStart=0] earliest outbound date index
 *   @param {number} [params.rangeEnd=T-1] latest return date index
 *   @param {number} [params.topK=6]       cheapest onward hops kept per city
 *   @param {number} [params.limit=60]     max ranked itineraries returned
 * @returns {{trips: Array, truncated: boolean, considered: number}}
 */
function findTrips(dataset, params) {
  const routesByPair = dataset.routesByPair || {};
  const dates = dataset.dates || [];
  const T = dates.length;

  const home = params.home;
  const metricArr = params.metric === "miles" ? "milesByDate" : "cashByDate";
  const maxStops = Math.max(1, params.maxStops == null ? 2 : params.maxStops);
  const minStay = params.minStay == null ? 2 : params.minStay;
  const minTrip = params.minTrip == null ? 3 : params.minTrip;
  const maxTrip = params.maxTrip == null ? 10 : params.maxTrip;
  const budget = params.budget == null ? Infinity : params.budget;
  const nonstopOnly = !!params.nonstopOnly;
  const lo = clamp(params.rangeStart == null ? 0 : params.rangeStart, 0, Math.max(0, T - 1));
  const hi = clamp(params.rangeEnd == null ? T - 1 : params.rangeEnd, 0, Math.max(0, T - 1));
  const topK = params.topK == null ? 6 : params.topK;
  const limit = params.limit == null ? 60 : params.limit;

  const result = { trips: [], truncated: false, considered: 0 };
  if (!home || !routesByPair[home] || T === 0 || hi < lo) return result;

  function usable(route) {
    if (!route) return false;
    if (nonstopOnly && route.nonstop !== 1) return false;
    return true;
  }
  function priceOn(route, idx) {
    const v = route[metricArr][idx];
    return v == null ? null : v;
  }

  // Cheapest onward hops from `city`, departing within [earliest, latest],
  // excluding home and any already-visited city. Returns up to `k` cheapest.
  function hops(city, earliest, latest, visited, k) {
    const out = [];
    const dests = routesByPair[city];
    if (!dests) return out;
    for (const d in dests) {
      if (d === home || visited.has(d)) continue;
      const route = dests[d];
      if (!usable(route)) continue;
      let best = null;
      for (let idx = earliest; idx <= latest; idx++) {
        const pr = priceOn(route, idx);
        if (pr == null) continue;
        if (best === null || pr < best.price) best = { d, idx, price: pr };
      }
      if (best) out.push(best);
    }
    out.sort((a, b) => a.price - b.price);
    return k == null ? out : out.slice(0, k);
  }

  // Keep only the cheapest itinerary per ordered city sequence.
  const bestBySeq = new Map();
  function record(startIdx, legs, cities, total, days) {
    const key = cities.join(">");
    const prev = bestBySeq.get(key);
    if (prev && prev.total <= total) return;
    bestBySeq.set(key, buildTrip(startIdx, legs, cities, total, days, dates, routesByPair, metricArr));
  }

  // Depth-first extension of a partial chain currently sitting in `city`,
  // having arrived on `arriveIdx`, with the trip having left home on `startIdx`.
  function extend(city, arriveIdx, startIdx, cities, legs, cost) {
    result.considered++;

    // Option 1: close the trip by flying city → home.
    const back = routesByPair[city][home];
    if (usable(back)) {
      const earliestReturn = arriveIdx + minStay;
      for (let idx = earliestReturn; idx <= hi; idx++) {
        const days = idx - startIdx;
        if (days < minTrip) continue;
        if (days > maxTrip) break;
        const pr = priceOn(back, idx);
        if (pr == null) continue;
        const total = cost + pr;
        if (total > budget) continue;
        record(startIdx, legs.concat([{ o: city, d: home, idx: idx, price: pr }]), cities, total, days);
      }
    }

    // Option 2: hop to another intermediate city (if we have stop budget and
    // room to still return within maxTrip).
    if (cities.length < maxStops) {
      const earliest = arriveIdx + minStay;
      const latest = Math.min(hi, startIdx + maxTrip - minStay);
      if (earliest <= latest) {
        const visited = new Set(cities);
        const onward = hops(city, earliest, latest, visited, topK);
        for (let i = 0; i < onward.length; i++) {
          const h = onward[i];
          const newCost = cost + h.price;
          if (newCost > budget) continue;
          extend(
            h.d, h.idx, startIdx,
            cities.concat([h.d]),
            legs.concat([{ o: city, d: h.d, idx: h.idx, price: h.price }]),
            newCost
          );
        }
      }
    }
  }

  // Seed with every first leg home → X on every outbound date in the window.
  // The first leg is not top-K pruned: destination variety is the primary axis
  // travelers care about, and this stays cheap (≈ dates × destinations seeds).
  const firstDests = routesByPair[home];
  for (let d0 = lo; d0 <= hi; d0++) {
    for (const x in firstDests) {
      if (x === home) continue;
      const route = firstDests[x];
      if (!usable(route)) continue;
      const pr = priceOn(route, d0);
      if (pr == null || pr > budget) continue;
      extend(x, d0, d0, [x], [{ o: home, d: x, idx: d0, price: pr }], pr);
    }
  }

  let trips = Array.from(bestBySeq.values());
  trips.sort((a, b) =>
    a.total - b.total ||
    a.pricePerDay - b.pricePerDay ||
    a.days - b.days
  );
  if (trips.length > limit) {
    result.truncated = true;
    trips = trips.slice(0, limit);
  }
  result.trips = trips;
  return result;
}

function buildTrip(startIdx, legs, cities, total, days, dates, routesByPair, metricArr) {
  const nights = [];
  for (let i = 0; i < legs.length - 1; i++) {
    nights.push({ city: legs[i].d, nights: legs[i + 1].idx - legs[i].idx });
  }
  const nonstop = legs.every((l) => {
    const r = routesByPair[l.o] && routesByPair[l.o][l.d];
    return r && r.nonstop === 1;
  });
  return {
    home: legs[0].o,
    cities: cities.slice(),
    legs: legs.map((l) => ({
      o: l.o, d: l.d, dateIdx: l.idx, date: dates[l.idx], price: l.price,
    })),
    stays: nights,
    total: round2(total),
    days: days,
    pricePerDay: round2(total / Math.max(days, 1)),
    nonstop: nonstop,
    metric: metricArr === "milesByDate" ? "miles" : "cash",
  };
}

function clamp(v, min, max) {
  return v < min ? min : v > max ? max : v;
}
function round2(v) {
  return Math.round(v * 100) / 100;
}

export { findTrips };

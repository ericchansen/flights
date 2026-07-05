/*
 * fares.js — pure fare-selection helpers over the date-indexed fare calendar.
 *
 * Routes carry `cashByDate[]` / `milesByDate[]` aligned to `meta.dates`. These
 * helpers pick the right series for a metric and find the cheapest priced entry
 * within a date-index window. No DOM, no app state — safe to unit-test.
 */

// The price series for a metric. Only "cash" selects cash; anything else
// selects miles (mirrors the app's original ternary exactly).
export function metricArrayFor(route, metric) {
  return metric === "cash" ? route.cashByDate : route.milesByDate;
}

// Cheapest value in arr over the inclusive index window [lo, hi], skipping
// null/undefined entries. Returns { value, dateIdx } or null if none priced.
export function bestInWindow(arr, lo, hi) {
  let best = Infinity,
    bi = -1;
  for (let i = lo; i <= hi; i++) {
    const v = arr[i];
    if (v != null && v < best) {
      best = v;
      bi = i;
    }
  }
  return bi === -1 ? null : { value: best, dateIdx: bi };
}

/* ============================================================================
   Fare Map — app
   Deal-discovery map over the crawled low-fare dataset. Pick a home airport,
   see the cheapest destinations mapped and ranked. Fully client-side.
   ========================================================================== */
(function () {
  "use strict";

  // Fare ramp (cheap -> pricey). Mirrors --fare-1..6 in DESIGN.md / styles.css.
  const FARE_COLORS = ["#0fb39a", "#43b04a", "#9bbf30", "#e0a800", "#ef7d1a", "#dc3d43"];
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const COUNTRY_NAME = {
    US: "United States", MX: "Mexico", PR: "Puerto Rico", DO: "Dominican Republic",
    JM: "Jamaica", CR: "Costa Rica", GT: "Guatemala", HN: "Honduras",
    SV: "El Salvador", SX: "Sint Maarten", CA: "Canada",
  };

  const $ = (sel, root = document) => root.querySelector(sel);

  const state = {
    airports: {},
    routesByOrigin: {},
    origins: [],
    origin: null,
    metric: "cash", // 'cash' | 'miles'
    nonstopOnly: false,
    dates: [],
    range: [0, 0],
    hover: null,
    selected: null,
    color: null,
    deals: [],
    dealByCode: {},
    // Phase 2 — trip planner
    routesByPair: {},
    mode: "explore", // 'explore' | 'plan'
    plan: { maxStops: 1, minStay: 1, tripMin: 4, tripMax: 9, budget: null },
    budgetMax: 0,
    trips: [],
    planTruncated: false,
    selectedTrip: null,
  };

  const el = {
    body: document.body,
    svg: null,
    gRoot: null,
    gLand: null,
    gArcs: null,
    gNodes: null,
    gLabels: null,
    tooltip: $("#tooltip"),
    legend: $("#legend"),
    routeDetail: $("#routeDetail"),
    dealList: $("#dealList"),
    dealSummary: $("#dealSummary"),
    datasetMeta: $("#datasetMeta"),
    providerTag: $("#providerTag"),
    originInput: $("#originInput"),
    originList: $("#originList"),
    originClear: $("#originClear"),
    combobox: $("#combobox"),
    metricToggle: $("#metricToggle"),
    stopsToggle: $("#stopsToggle"),
    rangeMin: $("#rangeMin"),
    rangeMax: $("#rangeMax"),
    rangeFill: $("#rangeFill"),
    windowReadout: $("#windowReadout"),
    sheetHandle: $("#sheetHandle"),
    // Phase 2 — trip planner
    modeToggle: $("#modeToggle"),
    shapeToggle: $("#shapeToggle"),
    nightsToggle: $("#nightsToggle"),
    lengthMin: $("#lengthMin"),
    lengthMax: $("#lengthMax"),
    lengthFill: $("#lengthFill"),
    lengthReadout: $("#lengthReadout"),
    budgetInput: $("#budgetInput"),
    budgetFill: $("#budgetFill"),
    budgetReadout: $("#budgetReadout"),
    planSummary: $("#planSummary"),
    tripList: $("#tripList"),
  };

  let projection, geoPath, zoom, dims = { w: 0, h: 0 }, currentK = 1;

  /* ------------------------------------------------------------------ utils */
  const fmtMoneyRound = (v) => "$" + Math.round(v).toLocaleString("en-US");
  const fmtMoneyExact = (v) =>
    "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtMiles = (v) => v.toLocaleString("en-US") + " mi";
  const parseDate = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const fmtDate = (s) => parseDate(s).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  // Flag emoji don't render on Windows; use a compact country-code tag instead.
  // Domestic US is the common case, so we omit it to keep rows quiet.
  const countryTag = (cc) =>
    (!cc || cc === "US") ? "" : `<span class="cc-tag" title="${escapeHtml(COUNTRY_NAME[cc] || cc)}">${escapeHtml(cc)}</span>`;

  function metricValue(route, i) {
    return state.metric === "cash" ? route.cashByDate[i] : route.milesByDate[i];
  }

  // cheapest value within the current date window + its date index
  function windowBest(route) {
    const [lo, hi] = state.range;
    let best = Infinity, bi = -1;
    for (let i = lo; i <= hi; i++) {
      const v = metricValue(route, i);
      if (v != null && v < best) { best = v; bi = i; }
    }
    return bi === -1 ? null : { value: best, dateIdx: bi };
  }

  // cheapest of the *other* metric within window (for the detail alt line)
  function windowBestOf(route, metric) {
    const [lo, hi] = state.range;
    const arr = metric === "cash" ? route.cashByDate : route.milesByDate;
    let best = Infinity, bi = -1;
    for (let i = lo; i <= hi; i++) {
      const v = arr[i];
      if (v != null && v < best) { best = v; bi = i; }
    }
    return bi === -1 ? null : { value: best, dateIdx: bi };
  }

  /* ------------------------------------------------------------- data load */
  Promise.all([
    d3.json("data.json"),
    d3.json("vendor/countries-50m.json"),
    d3.json("vendor/states-10m.json"),
  ]).then(([data, world, us]) => {
    boot(data, world, us);
  }).catch((err) => {
    console.error(err);
    el.body.dataset.state = "ready";
    $("#loader").innerHTML = "<span>Couldn't load the dataset. Run <code>python web/build_data.py</code> first.</span>";
  });

  function boot(data, world, us) {
    // Guard against an empty export (no routes / no priced dates): the range
    // slider and date formatting below assume a non-empty dates array, so show
    // a clear message instead of rendering a broken UI.
    const hasFares = Array.isArray(data.routes) && data.routes.length &&
      data.meta && Array.isArray(data.meta.dates) && data.meta.dates.length;
    if (!hasFares) {
      el.providerTag.textContent = (data.meta && data.meta.provider) || "—";
      el.datasetMeta.textContent = "No fares in dataset";
      el.body.dataset.state = "ready";
      const hint = $("#mapHint span");
      if (hint) hint.textContent =
        "This dataset has no fares yet — run build_data.py against a populated database.";
      el.dealList.innerHTML =
        `<div class="deal-empty"><p class="deal-empty-title">No fares in this dataset</p>` +
        `<p class="deal-empty-sub">Run <code>python web/build_data.py &lt;db&gt;</code> against a database that has fares, then reload.</p></div>`;
      return;
    }
    data.airports.forEach((a) => { state.airports[a.code] = a; });
    data.routes.forEach((r) => {
      (state.routesByOrigin[r.o] || (state.routesByOrigin[r.o] = [])).push(r);
      (state.routesByPair[r.o] || (state.routesByPair[r.o] = {}))[r.d] = r;
    });
    state.origins = Object.keys(state.routesByOrigin)
      .map((code) => state.airports[code])
      .filter(Boolean)
      .sort((a, b) => a.city.localeCompare(b.city));
    state.dates = data.meta.dates;
    state.range = [0, state.dates.length - 1];
    state.meta = data.meta;

    buildColorScales(data);
    // Fit the map tightly to the actual airport spread (a lon/lat rectangle
    // fans out under a conic projection and wastes the canvas).
    state.fitGeo = {
      type: "MultiPoint",
      coordinates: Object.values(state.airports)
        .filter((a) => a.lat != null && a.lon != null)
        .map((a) => [a.lon, a.lat]),
    };
    setupMeta(data.meta);
    setupSvg(world, us);
    setupControls();
    renderLegend();
    el.body.dataset.mode = state.mode;

    // Auto-select a sensible hub so the map is useful immediately.
    const defaultOrigin =
      state.airports.DEN && state.routesByOrigin.DEN ? "DEN"
        : Object.entries(state.routesByOrigin).sort((a, b) => b[1].length - a[1].length)[0][0];
    setOrigin(defaultOrigin);

    el.body.dataset.state = "ready";
  }

  /* ----------------------------------------------------------- color scale */
  function buildColorScales(data) {
    const cash = [], miles = [];
    data.routes.forEach((r) => {
      if (r.cash != null) cash.push(r.cash);
      if (r.miles != null) miles.push(r.miles);
    });
    cash.sort(d3.ascending); miles.sort(d3.ascending);
    state.priceExtent = {
      cash: cash.length ? cash[cash.length - 1] : 0,
      miles: miles.length ? miles[miles.length - 1] : 0,
    };
    const robust = (arr, q) => d3.quantileSorted(arr, q);
    state.scales = {
      cash: {
        domain: [cash[0], robust(cash, 0.9)],
        fmtRound: fmtMoneyRound, fmtExact: fmtMoneyExact, unit: "$",
      },
      miles: {
        domain: [miles[0], robust(miles, 0.9)],
        fmtRound: fmtMiles, fmtExact: fmtMiles, unit: "mi",
      },
    };
    const interp = d3.interpolateRgbBasis(FARE_COLORS);
    state.colorFor = (metric, v) => {
      const [a, b] = state.scales[metric].domain;
      const t = Math.max(0, Math.min(1, (v - a) / (b - a || 1)));
      return interp(t);
    };
  }

  /* --------------------------------------------------------------- dataset */
  function setupMeta(meta) {
    el.providerTag.textContent = meta.provider;
    el.datasetMeta.innerHTML =
      `<strong>${meta.n_routes.toLocaleString()}</strong> routes · ` +
      `<strong>${meta.n_origins}</strong> origins · ` +
      `${fmtDate(meta.date_min)}–${fmtDate(meta.date_max)}`;
  }

  /* ------------------------------------------------------------------- svg */
  function setupSvg(world, us) {
    el.svg = d3.select("#map");
    el.gRoot = el.svg.append("g").attr("class", "g-root");
    el.gLand = el.gRoot.append("g").attr("class", "g-land");
    el.gArcs = el.gRoot.append("g").attr("class", "g-arcs");
    el.gNodes = el.gRoot.append("g").attr("class", "g-nodes");
    el.gLabels = el.gRoot.append("g").attr("class", "g-labels");

    projection = d3.geoAlbers().rotate([94, 0]).center([0, 36]).parallels([15, 42]);
    geoPath = d3.geoPath(projection);

    // basemap geometry. The contiguous US is drawn from the states topology as a
    // merged landmass (below) so it gets a real fill + coastline; the world layer
    // supplies only the neighboring context (Canada, Mexico, ...), so we exclude
    // the US (id 840) from it to avoid drawing it twice.
    const countries = topojson.feature(world, world.objects.countries).features
      .filter((f) => {
        if (f.id === "840") return false; // US comes from us.objects.states
        const [[x0, y0], [x1, y1]] = d3.geoBounds(f);
        return x1 >= -125 && x0 <= -58 && y1 >= 7 && y0 <= 50;
      });
    // Contiguous US only: drop Alaska, Hawaii, and the island territories, which a
    // CONUS Albers projection would otherwise fling into the corners.
    const nonConus = new Set(["02", "15", "60", "66", "69", "72", "78"]);
    const nation = topojson.merge(
      us,
      us.objects.states.geometries.filter((g) => !nonConus.has(g.id))
    );
    const statesMesh = topojson.mesh(us, us.objects.states, (a, b) => a !== b);

    state.geo = { countries, nation, statesMesh };

    zoom = d3.zoom().scaleExtent([1, 9]).on("zoom", (ev) => {
      currentK = ev.transform.k;
      el.gRoot.attr("transform", ev.transform);
      rescale();
    });
    el.svg.call(zoom).on("dblclick.zoom", null);

    window.addEventListener("resize", debounce(resize, 150));
    resize();
  }

  function resize() {
    const wrap = $("#mapWrap");
    dims.w = wrap.clientWidth;
    dims.h = wrap.clientHeight;
    el.svg.attr("viewBox", `0 0 ${dims.w} ${dims.h}`);

    projection.fitExtent([[46, 40], [dims.w - 46, dims.h - 40]], state.fitGeo);
    geoPath = d3.geoPath(projection);

    drawBasemap();
    if (state.mode === "plan") {
      drawNodes();
      if (state.selectedTrip != null && state.trips[state.selectedTrip]) {
        drawTripScene(state.trips[state.selectedTrip]);
      }
    } else {
      if (state.origin) drawScene(false);
      drawNodes();
    }
    rescale();
  }

  function drawBasemap() {
    const land = el.gLand.selectAll("path.geo-land").data(state.geo.countries);
    land.join("path").attr("class", "geo-land").attr("d", geoPath);

    const nation = el.gLand.selectAll("path.geo-nation").data([state.geo.nation]);
    nation.join("path").attr("class", "geo-nation").attr("d", geoPath);

    let sm = el.gLand.selectAll("path.geo-state").data([state.geo.statesMesh]);
    sm.join("path").attr("class", "geo-state").attr("d", geoPath);
  }

  // counter-scale marks so they stay crisp when zoomed
  function rescale() {
    const k = currentK;
    el.gLand.selectAll(".geo-land").attr("stroke-width", 0.8 / k);
    el.gLand.selectAll(".geo-nation").attr("stroke-width", 0.8 / k);
    el.gLand.selectAll(".geo-state").attr("stroke-width", 0.5 / k);
    el.gNodes.selectAll(".node").attr("r", (d) => nodeRadius(d) / k);
    el.gNodes.selectAll(".node-origin-ring").attr("r", 9 / k).attr("stroke-width", 2 / k);
    el.gNodes.selectAll(".node-hit").attr("r", 11 / k);
    el.gLabels.selectAll("text").attr("font-size", (10.5 / k) + "px").attr("stroke-width", 3 / k);
    el.gArcs.selectAll(".arc").attr("stroke-width", (d) => arcWidth(d) / k);
    el.gArcs.selectAll(".trip-arc").attr("stroke-width", 2 / k);
    el.gNodes.selectAll(".trip-city").attr("r", 4 / k);
    el.gLabels.selectAll(".trip-city-label").attr("font-size", (11 / k) + "px").attr("stroke-width", 3 / k);
    el.gLabels.selectAll(".trip-seq").attr("font-size", (9 / k) + "px").attr("stroke-width", 2.5 / k);
  }

  /* ----------------------------------------------------------------- nodes */
  function pt(a) { return projection([a.lon, a.lat]); }
  function nodeRadius(d) {
    if (state.origin === d.code) return 0; // origin drawn as ring
    return d._deal ? 3.4 + Math.max(0, 3 - d._deal.rank * 0.05) * 0.4 : 2.1;
  }

  function drawNodes() {
    const arr = Object.values(state.airports).filter((a) => pt(a));
    // hit areas
    const hits = el.gNodes.selectAll(".node-hit").data(arr, (d) => d.code);
    hits.join("circle")
      .attr("class", "node-hit")
      .attr("cx", (d) => pt(d)[0]).attr("cy", (d) => pt(d)[1])
      .attr("r", 11 / currentK)
      .on("mouseenter", (ev, d) => { if (d._deal) setHover(d.code); })
      .on("mouseleave", () => setHover(null))
      .on("click", (ev, d) => { if (d._deal) setSelected(d.code); });

    const nodes = el.gNodes.selectAll(".node").data(arr, (d) => d.code);
    nodes.join("circle")
      .attr("class", "node")
      .attr("cx", (d) => pt(d)[0]).attr("cy", (d) => pt(d)[1])
      .attr("r", (d) => nodeRadius(d) / currentK)
      .attr("fill", (d) => d._deal ? state.colorFor(state.metric, d._deal.value) : "var(--node)")
      .attr("fill-opacity", (d) => d._deal ? 1 : 0.42)
      .style("pointer-events", "none");

    // origin ring
    const origin = state.airports[state.origin];
    const ring = el.gNodes.selectAll(".node-origin-ring").data(origin && pt(origin) ? [origin] : []);
    ring.join("circle")
      .attr("class", "node-origin-ring")
      .attr("cx", (d) => pt(d)[0]).attr("cy", (d) => pt(d)[1])
      .attr("r", 9 / currentK).attr("stroke-width", 2 / currentK)
      .style("pointer-events", "none");
  }

  function drawLabels() {
    const items = [];
    const origin = state.airports[state.origin];
    if (origin && pt(origin)) items.push({ a: origin, cls: "origin" });
    const hoverA = state.hover && state.airports[state.hover];
    if (hoverA && pt(hoverA) && state.hover !== state.origin) items.push({ a: hoverA, cls: "" });

    const labels = el.gLabels.selectAll("text").data(items, (d) => d.a.code);
    labels.join("text")
      .attr("class", (d) => "node-label " + d.cls)
      .attr("x", (d) => pt(d.a)[0]).attr("y", (d) => pt(d.a)[1] - 10 / currentK)
      .attr("text-anchor", "middle")
      .attr("font-size", (10.5 / currentK) + "px")
      .attr("stroke-width", 3 / currentK)
      .text((d) => d.a.code);
  }

  /* ------------------------------------------------------------------ arcs */
  function arcWidth(d) {
    const base = 1.1 + Math.max(0, 12 - d.rank) * 0.18;
    return base;
  }
  function arcData(o, dest) {
    return { type: "LineString", coordinates: [[o.lon, o.lat], [dest.lon, dest.lat]] };
  }

  function drawArcs(animate) {
    const origin = state.airports[state.origin];
    const arcs = el.gArcs.selectAll(".arc").data(state.deals, (d) => d.code);
    arcs.exit().remove();
    const enter = arcs.enter().append("path").attr("class", "arc");
    const merged = enter.merge(arcs)
      .attr("d", (d) => geoPath(arcData(origin, state.airports[d.code])))
      .attr("stroke", (d) => state.colorFor(state.metric, d.value))
      .attr("stroke-width", (d) => arcWidth(d) / currentK)
      .attr("stroke-opacity", (d) => 0.2 + Math.max(0, 0.5 - d.rank * 0.02));

    if (animate && !REDUCED_MOTION) {
      merged.each(function (d) {
        const len = this.getTotalLength();
        d3.select(this)
          .attr("stroke-dasharray", len + " " + len)
          .attr("stroke-dashoffset", len)
          .transition().duration(650).delay(Math.min(d.rank, 30) * 14)
          .ease(d3.easeCubicOut)
          .attr("stroke-dashoffset", 0)
          .on("end", function () { d3.select(this).attr("stroke-dasharray", null); });
      });
    } else {
      merged.attr("stroke-dasharray", null).attr("stroke-dashoffset", null);
    }
  }

  /* ---------------------------------------------------------------- scene  */
  function computeDeals() {
    const routes = state.routesByOrigin[state.origin] || [];
    const deals = [];
    routes.forEach((r) => {
      if (state.nonstopOnly && r.nonstop !== 1) return;
      const wb = windowBest(r);
      if (!wb) return;
      const dest = state.airports[r.d];
      if (!dest) return;
      deals.push({ code: r.d, route: r, value: wb.value, dateIdx: wb.dateIdx });
    });
    deals.sort((a, b) => a.value - b.value);
    deals.forEach((d, i) => { d.rank = i + 1; });

    // annotate airports for node styling
    Object.values(state.airports).forEach((a) => { a._deal = null; });
    const byCode = {};
    deals.forEach((d) => { byCode[d.code] = d; if (state.airports[d.code]) state.airports[d.code]._deal = d; });
    state.deals = deals;
    state.dealByCode = byCode;
  }

  function drawScene(animate) {
    computeDeals();
    drawArcs(animate);
    drawNodes();
    drawLabels();
    renderDealList();
    renderSummary();
  }

  /* --------------------------------------------------------------- summary */
  function renderSummary() {
    if (!state.deals.length) {
      el.dealSummary.hidden = true;
      return;
    }
    const origin = state.airports[state.origin];
    const cheapest = state.deals[0];
    const sc = state.scales[state.metric];
    el.dealSummary.hidden = false;
    el.dealSummary.innerHTML =
      `<strong>${state.deals.length}</strong> destinations from ${origin.city} · ` +
      `cheapest <span class="price-pop tnum">${sc.fmtRound(cheapest.value)}</span> to ` +
      `${state.airports[cheapest.code].city}`;
  }

  /* ------------------------------------------------------------- deal list */
  function renderDealList() {
    const list = el.dealList;
    list.innerHTML = "";
    if (!state.origin) {
      list.innerHTML =
        `<div class="deal-empty">` +
        `<div class="deal-empty-mark" aria-hidden="true">` +
        `<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>` +
        `</div>` +
        `<p class="deal-empty-title">Pick a home airport</p>` +
        `<p class="deal-empty-sub">We'll rank every destination by its cheapest fare and light up the map.</p></div>`;
      return;
    }
    if (!state.deals.length) {
      list.innerHTML = state.nonstopOnly
        ? `<div class="deal-empty"><p class="deal-empty-title">No nonstop routes here</p>` +
          `<p class="deal-empty-sub">Switch Stops back to All, widen the window, or try another home airport.</p></div>`
        : `<div class="deal-empty"><p class="deal-empty-title">No fares in this window</p>` +
          `<p class="deal-empty-sub">Widen the travel window or pick another home airport.</p></div>`;
      return;
    }
    const head = document.createElement("div");
    head.className = "deal-list-head";
    head.innerHTML = `<span></span><span>Destination</span><span>${state.metric === "cash" ? "From" : "Miles"}</span>`;
    list.appendChild(head);

    const sc = state.scales[state.metric];
    const frag = document.createDocumentFragment();
    state.deals.forEach((d) => {
      const dest = state.airports[d.code];
      const row = document.createElement("button");
      row.className = "deal-row";
      row.type = "button";
      row.dataset.code = d.code;
      row.setAttribute("aria-label",
        `${dest.city} (${d.code}), ${sc.fmtExact(d.value)}, best ${fmtDate(state.dates[d.dateIdx])}`);
      const color = state.colorFor(state.metric, d.value);
      row.innerHTML =
        `<span class="deal-rank tnum">${d.rank}</span>` +
        `<span class="deal-dest">` +
          `<span class="deal-dest-top"><span class="deal-city">${escapeHtml(dest.city)}</span>` +
          `<span class="deal-code">${escapeHtml(d.code)}</span></span>` +
          `<span class="deal-sub">${countryTag(dest.country)}` +
          `<span>best ${fmtDate(state.dates[d.dateIdx])}</span></span>` +
        `</span>` +
        `<span class="deal-price">` +
          `<span class="deal-price-main tnum"><span class="deal-dot" style="background:${color}"></span>` +
          `${sc.fmtRound(d.value)}</span>` +
          (state.metric === "cash"
            ? `<span class="deal-price-sub tnum">${milesTag(d.route)}</span>`
            : `<span class="deal-price-sub tnum">${cashTag(d.route)}</span>`) +
        `</span>`;
      row.addEventListener("mouseenter", () => setHover(d.code));
      row.addEventListener("mouseleave", () => setHover(null));
      row.addEventListener("click", () => setSelected(d.code));
      row.addEventListener("focus", () => setHover(d.code));
      frag.appendChild(row);
    });
    list.appendChild(frag);
  }

  function milesTag(route) {
    const wb = windowBestOf(route, "miles");
    return wb ? "or " + fmtMiles(wb.value) : "";
  }
  function cashTag(route) {
    const wb = windowBestOf(route, "cash");
    return wb ? "or " + fmtMoneyRound(wb.value) : "";
  }

  /* --------------------------------------------------------------- hover   */
  function setHover(code) {
    if (state.hover === code) return;
    state.hover = code;

    el.gArcs.selectAll(".arc")
      .classed("is-hover", (d) => d.code === code)
      .attr("stroke-opacity", (d) => d.code === code ? 1 : (0.2 + Math.max(0, 0.5 - d.rank * 0.02)) * (code ? 0.5 : 1))
      .attr("stroke-width", (d) => (arcWidth(d) * (d.code === code ? 2 : 1)) / currentK)
      .filter((d) => d.code === code).raise();

    el.gNodes.selectAll(".node")
      .attr("r", (d) => (nodeRadius(d) * (d.code === code ? 1.7 : 1)) / currentK);

    document.querySelectorAll(".deal-row").forEach((r) => {
      r.classList.toggle("is-hover", r.dataset.code === code);
    });

    drawLabels();

    if (code && state.dealByCode[code]) showTooltip(code); else hideTooltip();
  }

  function showTooltip(code) {
    const a = state.airports[code];
    const d = state.dealByCode[code];
    const p = pt(a);
    const t = d3.zoomTransform(el.svg.node());
    const [x, y] = t.apply(p);
    const sc = state.scales[state.metric];
    const alt = state.metric === "cash" ? milesTag(d.route) : cashTag(d.route);
    el.tooltip.hidden = false;
    el.tooltip.style.left = x + "px";
    el.tooltip.style.top = y + "px";
    el.tooltip.innerHTML =
      `<div class="tt-city">${a.city} <span class="tt-muted">${a.code}</span></div>` +
      `<div class="tt-row"><span class="tt-price">${sc.fmtExact(d.value)}</span>` +
      `<span class="tt-muted">${fmtDate(state.dates[d.dateIdx])}</span></div>` +
      (alt ? `<div class="tt-row tt-muted">${alt}</div>` : "");
  }
  function hideTooltip() { el.tooltip.hidden = true; }

  /* ------------------------------------------------------------- selection */
  function setSelected(code) {
    state.selected = code;
    document.querySelectorAll(".deal-row").forEach((r) => {
      r.classList.toggle("is-selected", r.dataset.code === code);
    });
    if (code) {
      renderRouteDetail(code);
      const row = document.querySelector(`.deal-row[data-code="${code}"]`);
      if (row) row.scrollIntoView({ block: "nearest", behavior: REDUCED_MOTION ? "auto" : "smooth" });
    } else {
      el.routeDetail.hidden = true;
    }
  }

  function renderRouteDetail(code) {
    const route = state.dealByCode[code].route;
    const o = state.airports[state.origin];
    const dest = state.airports[code];
    const cashWb = windowBestOf(route, "cash");
    const milesWb = windowBestOf(route, "miles");
    const primary = state.metric === "cash" ? cashWb : milesWb;
    const sc = state.scales[state.metric];

    // day-by-day bars over full date span, values from active metric
    const arr = state.metric === "cash" ? route.cashByDate : route.milesByDate;
    const vals = arr.map((v) => v);
    const present = vals.filter((v) => v != null);
    const maxV = d3.max(present) || 1;
    const minV = d3.min(present) || 0;
    const [lo, hi] = state.range;

    let bars = "";
    vals.forEach((v, i) => {
      const inWin = i >= lo && i <= hi;
      if (v == null) {
        bars += `<div class="rd-bar" style="height:3px;opacity:${inWin ? 0.5 : 0.25}" title="${fmtDate(state.dates[i])} · no fare"></div>`;
        return;
      }
      const h = 8 + ((maxV - v) / (maxV - minV || 1)) * 60; // taller = cheaper
      const isBest = v === minV;
      const color = state.colorFor(state.metric, v);
      const altV = state.metric === "cash" ? route.milesByDate[i] : route.cashByDate[i];
      const altStr = altV != null ? " · " + (state.metric === "cash" ? fmtMiles(altV) : fmtMoneyExact(altV)) : "";
      bars +=
        `<div class="rd-bar has-data${isBest ? " is-best" : ""}" ` +
        `style="height:${h}px;background:${color};opacity:${inWin ? 1 : 0.28}" ` +
        `title="${fmtDate(state.dates[i])} · ${sc.fmtExact(v)}${altStr}"></div>`;
    });

    const altLine =
      state.metric === "cash"
        ? (milesWb ? `or <strong>${fmtMiles(milesWb.value)}</strong>${route.fees ? " + " + fmtMoneyExact(route.fees) + " taxes" : ""}` : "cash only in this window")
        : (cashWb ? `or <strong>${fmtMoneyExact(cashWb.value)}</strong> cash` : "award only in this window");

    el.routeDetail.hidden = false;
    el.routeDetail.innerHTML =
      `<div class="rd-head">` +
        `<div class="rd-route">${escapeHtml(state.origin)}<span class="arrow">→</span>${escapeHtml(code)}</div>` +
        `<div class="rd-cities">${escapeHtml(o.city)} to ${escapeHtml(dest.city)}${countryTag(dest.country) ? " " + countryTag(dest.country) : ""}</div>` +
        `<button class="rd-close" aria-label="Close">` +
        `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="m4 4 8 8M12 4l-8 8"/></svg></button>` +
      `</div>` +
      `<div class="rd-body">` +
        `<div class="rd-hero"><span class="rd-hero-price tnum">${primary ? sc.fmtRound(primary.value) : "—"}</span>` +
        `<span class="rd-hero-label">cheapest ${state.metric === "cash" ? "fare" : "award"}${primary ? " · " + fmtDate(state.dates[primary.dateIdx]) : ""}</span></div>` +
        `<div class="rd-alt">${altLine}</div>` +
        `<div class="rd-chart-label"><span>Fare by day</span><span>taller = cheaper</span></div>` +
        `<div class="rd-bars">${bars}</div>` +
        `<div class="rd-axis"><span>${fmtDate(state.dates[0])}</span><span>${fmtDate(state.dates[state.dates.length - 1])}</span></div>` +
      `</div>`;
    el.routeDetail.querySelector(".rd-close").addEventListener("click", () => setSelected(null));
  }

  /* --------------------------------------------------------------- origin  */
  function setOrigin(code) {
    if (!state.routesByOrigin[code]) return;
    state.origin = code;
    state.selected = null;
    el.routeDetail.hidden = true;
    const a = state.airports[code];
    el.originInput.value = `${a.city} · ${a.code}`;
    el.originClear.hidden = false;
    el.body.dataset.hasOrigin = "true";
    hideTooltip();
    if (state.mode === "plan") {
      Object.values(state.airports).forEach((x) => { x._deal = null; });
      el.gArcs.selectAll(".arc").remove();
      drawNodes();
      runPlanSearch();
    } else {
      drawScene(true);
    }
  }

  // Fully reset origin selection: state, map arcs/nodes, and body flag — not
  // just the input text. Keeps the UI consistent after the clear button.
  function clearOrigin() {
    state.origin = null;
    state.selected = null;
    el.routeDetail.hidden = true;
    el.originInput.value = "";
    el.originClear.hidden = true;
    delete el.body.dataset.hasOrigin;
    hideTooltip();
    if (state.mode === "plan") {
      state.trips = [];
      state.selectedTrip = null;
      clearTripLayers();
      Object.values(state.airports).forEach((x) => { x._deal = null; });
      drawNodes();
      renderTripList();
      renderPlanSummary();
    } else {
      drawScene(false);
    }
  }

  /* -------------------------------------------------------------- controls */
  function setupControls() {
    setupCombobox();

    el.metricToggle.querySelectorAll(".segmented-opt").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (state.metric === btn.dataset.metric) return;
        state.metric = btn.dataset.metric;
        el.metricToggle.querySelectorAll(".segmented-opt").forEach((b) => {
          const on = b === btn;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-checked", on ? "true" : "false");
        });
        renderLegend();
        computeBudgetBounds();
        if (state.mode === "plan") {
          runPlanSearch();
        } else {
          if (state.origin) drawScene(false);
          if (state.selected) renderRouteDetail(state.selected);
        }
      });
    });

    el.stopsToggle.querySelectorAll(".segmented-opt").forEach((btn) => {
      btn.addEventListener("click", () => {
        const wantNonstop = btn.dataset.stops === "nonstop";
        if (state.nonstopOnly === wantNonstop) return;
        state.nonstopOnly = wantNonstop;
        el.stopsToggle.querySelectorAll(".segmented-opt").forEach((b) => {
          const on = b === btn;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-checked", on ? "true" : "false");
        });
        if (state.mode === "plan") { runPlanSearch(); return; }
        if (state.origin) drawScene(false);
        // A selected route may no longer be in the filtered set.
        if (state.selected && !state.dealByCode[state.selected]) setSelected(null);
        else if (state.selected) renderRouteDetail(state.selected);
      });
    });

    // range slider
    el.rangeMin.max = el.rangeMax.max = String(state.dates.length - 1);
    el.rangeMin.value = "0";
    el.rangeMax.value = String(state.dates.length - 1);
    const onRange = () => {
      let a = +el.rangeMin.value, b = +el.rangeMax.value;
      if (a > b) { if (document.activeElement === el.rangeMin) b = a; else a = b; }
      el.rangeMin.value = a; el.rangeMax.value = b;
      state.range = [a, b];
      updateRangeUI();
      if (state.mode === "plan") { schedulePlanSearch(); return; }
      if (state.origin) drawScene(false);
      if (state.selected) renderRouteDetail(state.selected);
    };
    el.rangeMin.addEventListener("input", onRange);
    el.rangeMax.addEventListener("input", onRange);
    updateRangeUI();

    setupPlanControls();

    // mobile sheet
    el.sheetHandle.hidden = false;
    el.sheetHandle.addEventListener("click", () => {
      el.body.dataset.sheet = el.body.dataset.sheet === "open" ? "closed" : "open";
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { if (state.selected) setSelected(null); }
    });
  }

  function updateRangeUI() {
    const n = state.dates.length - 1;
    const [a, b] = state.range;
    el.rangeFill.style.left = (a / n) * 100 + "%";
    el.rangeFill.style.right = (1 - b / n) * 100 + "%";
    el.windowReadout.textContent = `${fmtDate(state.dates[a])} – ${fmtDate(state.dates[b])}`;
  }

  /* ============================================================================
     Plan a trip — mode switch, controls, search, cards, and map path
     ========================================================================== */
  let planTimer = null;

  function setSegActive(container, btn) {
    container.querySelectorAll(".segmented-opt").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function setupPlanControls() {
    el.modeToggle.querySelectorAll(".segmented-opt").forEach((btn) => {
      btn.addEventListener("click", () => setMode(btn.dataset.mode));
    });

    el.shapeToggle.querySelectorAll(".segmented-opt").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = +btn.dataset.maxstops;
        if (state.plan.maxStops === v) return;
        state.plan.maxStops = v;
        setSegActive(el.shapeToggle, btn);
        schedulePlanSearch();
      });
    });

    el.nightsToggle.querySelectorAll(".segmented-opt").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = +btn.dataset.nights;
        if (state.plan.minStay === v) return;
        state.plan.minStay = v;
        setSegActive(el.nightsToggle, btn);
        schedulePlanSearch();
      });
    });

    const onLength = () => {
      let a = +el.lengthMin.value, b = +el.lengthMax.value;
      if (a > b) { if (document.activeElement === el.lengthMin) b = a; else a = b; }
      el.lengthMin.value = a; el.lengthMax.value = b;
      state.plan.tripMin = a; state.plan.tripMax = b;
      updateLengthUI();
      schedulePlanSearch();
    };
    el.lengthMin.addEventListener("input", onLength);
    el.lengthMax.addEventListener("input", onLength);
    el.budgetInput.addEventListener("input", onBudget);

    computeBudgetBounds();
    updateLengthUI();
  }

  function updateLengthUI() {
    const LMIN = +el.lengthMin.min, LMAX = +el.lengthMin.max, span = (LMAX - LMIN) || 1;
    const a = state.plan.tripMin, b = state.plan.tripMax;
    el.lengthFill.style.left = ((a - LMIN) / span) * 100 + "%";
    el.lengthFill.style.right = (1 - (b - LMIN) / span) * 100 + "%";
    el.lengthReadout.textContent = a === b ? `${a} days` : `${a}–${b} days`;
  }

  // Budget slider is metric-aware: its range and step change with cash vs miles,
  // and flipping metric resets it to "Any" to avoid comparing dollars to miles.
  function computeBudgetBounds() {
    const isCash = state.metric === "cash";
    const maxSingle = (state.priceExtent && (isCash ? state.priceExtent.cash : state.priceExtent.miles)) || 0;
    const step = isCash ? 10 : 1000;
    let max = Math.ceil((maxSingle * 2) / step) * step;
    if (!isFinite(max) || max <= 0) max = isCash ? 1000 : 100000;
    state.budgetMax = max;
    el.budgetInput.min = "0";
    el.budgetInput.max = String(max);
    el.budgetInput.step = String(step);
    state.plan.budget = null;
    el.budgetInput.value = String(max);
    updateBudgetUI();
  }

  function updateBudgetUI() {
    const max = state.budgetMax || 1;
    const v = +el.budgetInput.value;
    el.budgetFill.style.right = (1 - v / max) * 100 + "%";
    el.budgetReadout.textContent = state.plan.budget == null
      ? "Any"
      : (state.metric === "cash" ? fmtMoneyRound(state.plan.budget) : fmtMiles(state.plan.budget));
  }

  function onBudget() {
    const max = state.budgetMax || 1;
    const v = +el.budgetInput.value;
    state.plan.budget = (v >= max) ? null : v; // top of the track means "no cap"
    updateBudgetUI();
    schedulePlanSearch();
  }

  function setMode(mode) {
    if (state.mode === mode || (mode !== "plan" && mode !== "explore")) return;
    state.mode = mode;
    el.body.dataset.mode = mode;
    setSegActive(el.modeToggle, [...el.modeToggle.querySelectorAll(".segmented-opt")]
      .find((b) => b.dataset.mode === mode));
    hideTooltip();

    if (mode === "plan") {
      if (state.selected) setSelected(null);
      el.gArcs.selectAll(".arc").remove();
      Object.values(state.airports).forEach((a) => { a._deal = null; });
      el.dealSummary.hidden = true;
      el.dealList.hidden = true;
      el.tripList.hidden = false;
      drawNodes();
      runPlanSearch();
    } else {
      state.selectedTrip = null;
      clearTripLayers();
      el.planSummary.hidden = true;
      el.tripList.hidden = true;
      el.dealList.hidden = false;
      if (state.origin) drawScene(false);
      else { drawNodes(); renderDealList(); renderSummary(); }
    }
  }

  function schedulePlanSearch() {
    if (state.mode !== "plan") return;
    clearTimeout(planTimer);
    planTimer = setTimeout(runPlanSearch, 130);
  }

  function runPlanSearch() {
    if (state.mode !== "plan") return;
    if (!state.origin) {
      state.trips = []; state.selectedTrip = null;
      renderTripList(); renderPlanSummary(); clearTripLayers();
      return;
    }
    if (!window.Trips) { console.error("trips.js not loaded"); return; }
    const params = {
      home: state.origin,
      metric: state.metric,
      maxStops: state.plan.maxStops,
      minStay: state.plan.minStay,
      minTrip: state.plan.tripMin,
      maxTrip: state.plan.tripMax,
      budget: state.plan.budget == null ? undefined : state.plan.budget,
      nonstopOnly: state.nonstopOnly,
      rangeStart: state.range[0],
      rangeEnd: state.range[1],
      limit: 60,
    };
    const res = window.Trips.findTrips(
      { routesByPair: state.routesByPair, dates: state.dates }, params);
    state.trips = res.trips;
    state.planTruncated = res.truncated;
    state.selectedTrip = state.trips.length ? 0 : null;
    renderTripList();
    renderPlanSummary();
    drawTripScene(state.trips.length ? state.trips[0] : null);
  }

  function shapeLabel() {
    return state.plan.maxStops <= 1 ? "round-trips"
      : state.plan.maxStops === 2 ? "up to +1 city" : "up to +2 cities";
  }

  function renderPlanSummary() {
    if (!state.origin || !state.trips.length) { el.planSummary.hidden = true; return; }
    const origin = state.airports[state.origin];
    const sc = state.scales[state.metric];
    const cheapest = state.trips[0];
    el.planSummary.hidden = false;
    el.planSummary.innerHTML =
      `<strong>${state.trips.length}${state.planTruncated ? "+" : ""}</strong> ` +
      `${state.trips.length === 1 ? "trip" : "trips"} from ` +
      `${escapeHtml(origin.city)} · cheapest ` +
      `<span class="price-pop tnum">${sc.fmtRound(cheapest.total)}</span> · ${shapeLabel()}`;
  }

  function tripEmptyHtml(title, sub) {
    return `<div class="deal-empty">` +
      `<div class="deal-empty-mark" aria-hidden="true">` +
      `<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-6-5.7-6-10a6 6 0 0 1 12 0c0 4.3-6 10-6 10Z"/><circle cx="12" cy="11" r="2"/></svg>` +
      `</div>` +
      `<p class="deal-empty-title">${title}</p>` +
      `<p class="deal-empty-sub">${sub}</p></div>`;
  }

  function renderTripList() {
    const list = el.tripList;
    list.innerHTML = "";
    if (!state.origin) {
      list.innerHTML = tripEmptyHtml("Pick a home airport",
        "We'll build cheap round-trips and multi-city vacations from home and rank them by total price.");
      return;
    }
    if (!state.trips.length) {
      list.innerHTML = tripEmptyHtml("No trips fit those filters",
        "Try a bigger budget, a wider travel window, more stops, or fewer nights per stop.");
      return;
    }

    const head = document.createElement("div");
    head.className = "deal-list-head";
    head.innerHTML = `<span></span><span>Trip</span><span>${state.metric === "cash" ? "Total" : "Miles"}</span>`;
    list.appendChild(head);

    const sc = state.scales[state.metric];
    const frag = document.createDocumentFragment();
    state.trips.forEach((trip, i) => {
      const codes = [trip.home].concat(trip.cities).concat([trip.home]);
      const routeHtml = codes
        .map((c, idx) => (idx ? `<span class="trip-hop">→</span>` : "") + `<span>${escapeHtml(c)}</span>`)
        .join("");
      const out = fmtDate(trip.legs[0].date);
      const back = fmtDate(trip.legs[trip.legs.length - 1].date);
      const cityWord = trip.cities.length === 1 ? "1 city" : `${trip.cities.length} cities`;
      const badge = trip.nonstop ? `<span class="trip-badge">NONSTOP</span>` : "";
      const btn = document.createElement("button");
      btn.className = "trip-card" + (i === state.selectedTrip ? " is-selected" : "");
      btn.type = "button";
      btn.dataset.idx = i;
      btn.setAttribute("aria-label",
        `Trip ${i + 1}: ${codes.join(" to ")}, ${sc.fmtExact(trip.total)} total, ` +
        `${trip.days} days, ${out} to ${back}`);
      btn.innerHTML =
        `<span class="trip-rank tnum">${i + 1}</span>` +
        `<span class="trip-route">${routeHtml}</span>` +
        `<span class="trip-price">` +
          `<span class="trip-total tnum">${sc.fmtRound(trip.total)}</span>` +
          `<span class="trip-perday tnum">${sc.fmtRound(trip.pricePerDay)}/day</span>` +
        `</span>` +
        `<span class="trip-meta">${trip.days} days · ${cityWord} · ${out}–${back}` +
          `${badge ? " · " + badge : ""}</span>`;
      btn.addEventListener("click", () => selectTrip(i));
      frag.appendChild(btn);
    });
    list.appendChild(frag);
  }

  function selectTrip(i) {
    if (!state.trips[i]) return;
    state.selectedTrip = i;
    document.querySelectorAll(".trip-card").forEach((c) => {
      c.classList.toggle("is-selected", +c.dataset.idx === i);
    });
    const card = document.querySelector(`.trip-card[data-idx="${i}"]`);
    if (card) card.scrollIntoView({ block: "nearest", behavior: REDUCED_MOTION ? "auto" : "smooth" });
    drawTripScene(state.trips[i]);
  }

  function clearTripLayers() {
    el.gArcs.selectAll(".trip-arc").remove();
    el.gNodes.selectAll(".trip-city").remove();
    el.gLabels.selectAll(".trip-seq, .trip-city-label").remove();
  }

  function drawTripScene(trip) {
    clearTripLayers();
    if (!trip) return;

    const legPaths = trip.legs
      .map((l) => ({ o: state.airports[l.o], d: state.airports[l.d] }))
      .filter((x) => x.o && x.d && pt(x.o) && pt(x.d));
    el.gArcs.selectAll(".trip-arc").data(legPaths).join("path")
      .attr("class", "trip-arc")
      .attr("d", (x) => geoPath(arcData(x.o, x.d)))
      .attr("stroke-width", 2 / currentK)
      .attr("stroke-opacity", 0.9);

    const cityCodes = [trip.home].concat(trip.cities);
    const cityPts = cityCodes.map((c) => state.airports[c]).filter((a) => a && pt(a));
    el.gNodes.selectAll(".trip-city").data(cityPts, (d) => d.code).join("circle")
      .attr("class", "trip-city")
      .attr("cx", (d) => pt(d)[0]).attr("cy", (d) => pt(d)[1])
      .attr("r", 4 / currentK);

    el.gLabels.selectAll(".trip-city-label").data(cityPts, (d) => d.code).join("text")
      .attr("class", "trip-city-label")
      .attr("x", (d) => pt(d)[0]).attr("y", (d) => pt(d)[1] - 9 / currentK)
      .attr("text-anchor", "middle")
      .attr("font-size", (11 / currentK) + "px").attr("stroke-width", 3 / currentK)
      .text((d) => d.code);

    // Order badges only help when there is more than one intermediate city.
    if (trip.cities.length >= 2) {
      const seqData = cityCodes
        .map((c, i) => ({ a: state.airports[c], n: i + 1 }))
        .filter((x) => x.a && pt(x.a));
      el.gLabels.selectAll(".trip-seq").data(seqData, (d) => d.a.code).join("text")
        .attr("class", "trip-seq")
        .attr("x", (d) => pt(d.a)[0]).attr("y", (d) => pt(d.a)[1] + 14 / currentK)
        .attr("text-anchor", "middle")
        .attr("font-size", (9 / currentK) + "px").attr("stroke-width", 2.5 / currentK)
        .text((d) => d.n);
    }
  }

  /* ------------------------------------------------------------- combobox  */
  function setupCombobox() {
    let activeIdx = -1;
    let filtered = state.origins;

    const close = () => {
      el.originList.hidden = true;
      el.originInput.setAttribute("aria-expanded", "false");
      activeIdx = -1;
    };
    const open = () => {
      el.originList.hidden = false;
      el.originInput.setAttribute("aria-expanded", "true");
    };

    function render(q) {
      const query = q.trim().toLowerCase();
      filtered = !query
        ? state.origins
        : state.origins.filter((a) =>
            a.city.toLowerCase().includes(query) || a.code.toLowerCase().includes(query) ||
            (a.name && a.name.toLowerCase().includes(query)));
      el.originList.innerHTML = "";
      if (!filtered.length) {
        el.originList.innerHTML = `<li class="combobox-noresult">No airports match “${escapeHtml(q)}”</li>`;
        open(); return;
      }
      const frag = document.createDocumentFragment();
      filtered.slice(0, 60).forEach((a, i) => {
        const li = document.createElement("li");
        li.className = "combobox-opt" + (i === activeIdx ? " is-active" : "");
        li.setAttribute("role", "option");
        li.dataset.code = a.code;
        li.innerHTML =
          `<span class="code">${a.code}</span>` +
          `<span class="city">${highlight(a.city, query)}</span>` +
          `<span class="meta">${countryTag(a.country)}${state.routesByOrigin[a.code].length} routes</span>`;
        li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(a.code); });
        frag.appendChild(li);
      });
      el.originList.appendChild(frag);
      open();
    }

    function choose(code) { setOrigin(code); close(); el.originInput.blur(); }

    el.originInput.addEventListener("focus", () => { el.originInput.select(); render(""); });
    el.originInput.addEventListener("input", () => { activeIdx = -1; render(el.originInput.value); });
    el.originInput.addEventListener("keydown", (e) => {
      const opts = el.originList.querySelectorAll(".combobox-opt");
      if (e.key === "ArrowDown") { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, opts.length - 1); updateActive(opts); }
      else if (e.key === "ArrowUp") { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); updateActive(opts); }
      else if (e.key === "Enter") { e.preventDefault(); const pick = filtered[activeIdx] || filtered[0]; if (pick) choose(pick.code); }
      else if (e.key === "Escape") { close(); el.originInput.blur(); }
    });
    function updateActive(opts) {
      opts.forEach((o, i) => o.classList.toggle("is-active", i === activeIdx));
      if (opts[activeIdx]) opts[activeIdx].scrollIntoView({ block: "nearest" });
    }
    el.originClear.addEventListener("click", () => {
      clearOrigin();
      el.originInput.focus();
    });
    document.addEventListener("click", (e) => {
      if (!el.combobox.contains(e.target)) close();
    });
  }

  function renderLegend() {
    if (!state.scales) return;
    const sc = state.scales[state.metric];
    const gradient = `linear-gradient(to right, ${FARE_COLORS.join(",")})`;
    el.legend.hidden = false;
    el.legend.innerHTML =
      `<div class="legend-title">${state.metric === "cash" ? "Cheapest cash fare" : "Award miles"}</div>` +
      `<div class="legend-ramp" style="background:${gradient}"></div>` +
      `<div class="legend-scale"><span class="tnum">${sc.fmtRound(sc.domain[0])}</span>` +
      `<span class="legend-mid">cheaper → pricier</span>` +
      `<span class="tnum">${sc.fmtRound(sc.domain[1])}+</span></div>`;
  }

  /* ------------------------------------------------------------------ misc */
  function debounce(fn, ms) {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }
  function escapeHtml(s) { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function highlight(text, q) {
    if (!q) return escapeHtml(text);
    const i = text.toLowerCase().indexOf(q);
    if (i < 0) return escapeHtml(text);
    return escapeHtml(text.slice(0, i)) + "<mark>" + escapeHtml(text.slice(i, i + q.length)) + "</mark>" + escapeHtml(text.slice(i + q.length));
  }
})();

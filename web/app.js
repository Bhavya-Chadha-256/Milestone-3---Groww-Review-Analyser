"use strict";
// Review Pulse dashboard (Task 7.4): binds the Stitch page to the local, read-only API.
// Every value is written with textContent or an attribute setter, never parsed as HTML, so a review
// containing "<script>" shows as text. The filter state lives in the URL, so a view can be shared and the
// back button works.

const API = {
  meta: "/api/meta",
  summary: "/api/summary",
  themes: "/api/themes",
  trend: "/api/trend",
  ratings: "/api/ratings",
  reviews: "/api/reviews",
  csv: "/api/reviews.csv",
  quality: "/api/quality",
  pulse: "/api/pulse",
  jobs: "/api/jobs",
  budget: "/api/llm-budget",
  ready: "/api/delivery-ready",
};
const LIST_KEYS = ["sources", "ratings", "themes", "sentiments", "languages", "app_versions"];
const SCALAR_KEYS = ["start", "end", "query", "hide_duplicates", "include_excluded"];
const SOURCE_NAMES = { play_store: "Play Store", app_store: "App Store" };
const LANGUAGE_NAMES = { en: "English", hinglish: "Hinglish", hi: "Hindi", other: "Other" };
const EXCLUDED_LANGUAGES = ["hi", "other"]; // stored but outside the theme basis: they need include_excluded
const RATING_WORDS = { 5: "Excellent", 4: "Good", 3: "Mixed", 2: "Poor", 1: "Critical" };
const THEME_ICONS = ["candlestick_chart", "speed", "currency_rupee", "savings", "support_agent"];
const ACTION_ICONS = ["build", "lock_reset", "account_balance_wallet"];
const CHART_COLOURS = ["#006c4f", "#00d09c", "#565e74", "#f59e0b", "#57cc9a", "#bacac1"];
const NO_DRAFT_TOOL = "Your MCP server has no Gmail draft tool";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const CLASSES = {
  navOn: "py-space-sm px-space-md transition-colors bg-surface-container-low text-primary font-semibold border-b-2 border-primary",
  navOff: "font-body-md text-body-md text-on-surface-variant hover:text-on-surface py-space-sm px-space-md transition-colors",
  rangeOn: "bg-surface-container-lowest text-primary font-semibold shadow-[0_1px_8px_rgba(0,0,0,0.04)]",
  rangeOff: "text-on-surface-variant hover:text-on-surface",
  storeOn: "bg-surface-container-high text-on-surface",
  storeOff: "bg-surface-container-low text-on-surface-variant hover:text-on-surface",
  ratingOn: "bg-primary-container text-on-primary-container",
  ratingOff: "bg-surface-container-low text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface",
  sentimentOn: {
    negative: "bg-error-container text-on-error-container",
    mixed: "bg-amber-100 text-amber-800",
    positive: "bg-primary-container/20 text-on-primary-container",
  },
  sentimentOff: "bg-surface-container-low text-on-surface-variant hover:text-on-surface",
  chipBad: "text-error bg-error-container/40",
  chipGood: "text-primary bg-primary-container/20",
  chipFlat: "text-on-surface-variant bg-surface-container-high",
};

let state = readUrl();
let meta = null;
let trendChart = null;
let trendMode = "vol";
let lastTrend = [];
let loadTimer = null;
let pulseView = null;
let readiness = null;

const byId = (id) => document.getElementById(id);

// ---------------------------------------------------------------- URL state

function readUrl() {
  const params = new URLSearchParams(window.location.search);
  const filter = {};
  for (const key of LIST_KEYS) filter[key] = params.getAll(key);
  for (const key of SCALAR_KEYS) filter[key] = params.get(key) || "";
  return {
    tab: params.get("tab") === "pulse" ? "pulse" : "analyse",
    week: params.get("week") || "",
    sort: params.get("sort") || "newest",
    page: Math.max(1, parseInt(params.get("page") || "1", 10) || 1),
    filter,
  };
}

function filterParams(extra = {}) {
  const params = new URLSearchParams();
  if (state.week) params.set("week", state.week);
  for (const key of LIST_KEYS) for (const value of state.filter[key]) params.append(key, value);
  for (const key of SCALAR_KEYS) if (state.filter[key]) params.set(key, state.filter[key]);
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  return params;
}

function writeUrl() {
  const params = filterParams({ tab: state.tab, sort: state.sort, page: String(state.page) });
  if (state.tab === "analyse") params.delete("tab");
  if (state.sort === "newest") params.delete("sort");
  if (state.page === 1) params.delete("page");
  const query = params.toString();
  window.history.pushState(null, "", query ? `?${query}` : window.location.pathname);
}

function filterChanged() {
  state.page = 1;
  writeUrl();
  renderFilterBar();
  clearTimeout(loadTimer);
  loadTimer = setTimeout(loadAnalyse, 300);
}

// ---------------------------------------------------------------- helpers

async function getJson(path, params) {
  const url = params && params.toString() ? `${path}?${params}` : path;
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  return { status: response.status, body };
}

function setText(id, text) {
  const node = byId(id);
  if (node) node.textContent = text;
}

function field(root, name) {
  return root.querySelector(`[data-field="${name}"]`);
}

function fill(root, name, text) {
  const node = field(root, name);
  if (node) node.textContent = text;
  return node;
}

function setWidth(node, share) {
  node.style.width = `${Math.max(0, Math.min(100, share * 100)).toFixed(1)}%`;
}

function clone(templateId) {
  return byId(templateId).content.firstElementChild.cloneNode(true);
}

function setClasses(node, base, extra) {
  node.className = `${base} ${extra}`.trim();
}

function count(value) {
  return Number(value || 0).toLocaleString("en-IN");
}

function percent(share, digits = 1) {
  return `${(share * 100).toFixed(digits)}%`;
}

function parseDay(text) {
  const [year, month, day] = text.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function isoDay(date) {
  return date.toISOString().slice(0, 10);
}

function addDays(text, days) {
  const date = parseDay(text);
  date.setUTCDate(date.getUTCDate() + days);
  return isoDay(date);
}

function shortDate(text, withYear = false) {
  const date = parseDay(text);
  const base = `${String(date.getUTCDate()).padStart(2, "0")} ${MONTHS[date.getUTCMonth()]}`;
  return withYear ? `${base} ${date.getUTCFullYear()}` : base;
}

function weekMonday(isoWeek) {
  const [year, week] = isoWeek.split("-W").map(Number);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (week - 1) * 7);
  return isoDay(monday);
}

function themeName(themeId) {
  if (themeId === "unthemed") return "Unthemed";
  const theme = meta && meta.themes.find((item) => item.id === themeId);
  return theme ? theme.name : themeId;
}

function showToast(message, ok = true) {
  const toast = byId("actionToast");
  setText("toastMessage", message);
  setText("toastIcon", ok ? "check_circle" : "error");
  toast.classList.remove("translate-y-24", "opacity-0");
  toast.classList.add("translate-y-0", "opacity-100");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.classList.remove("translate-y-0", "opacity-100");
    toast.classList.add("translate-y-24", "opacity-0");
  }, 4500);
}

function deltaChip(node, value, text, goodWhenUp) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    node.hidden = true;
    return;
  }
  node.hidden = false;
  const up = value > 0;
  const flat = Math.abs(value) < 1e-9;
  const good = flat || up === goodWhenUp;
  setClasses(
    node,
    "inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full font-label-sm text-label-sm font-semibold",
    good ? "bg-primary-container/20 text-on-primary-container" : "bg-error-container/40 text-error",
  );
  node.querySelector("[data-icon]").textContent = flat ? "horizontal_rule" : up ? "trending_up" : "trending_down";
  node.querySelector("[data-text]").textContent = text;
}

// ---------------------------------------------------------------- filter bar

function rangeOf(name) {
  if (!meta) return { start: "", end: "" };
  const end = meta.window.end;
  if (name === "week") return { start: addDays(end, -6), end };
  if (name === "4w") return { start: addDays(end, -27), end };
  return { start: "", end: "" };
}

function activeRange() {
  for (const name of ["week", "4w", "12w"]) {
    const range = rangeOf(name);
    if (range.start === state.filter.start && range.end === state.filter.end) return name;
  }
  return "custom";
}

function toggleValue(key, value) {
  const values = state.filter[key];
  const index = values.indexOf(value);
  if (index >= 0) values.splice(index, 1);
  else values.push(value);
}

function renderFilterBar() {
  const range = activeRange();
  for (const button of document.querySelectorAll("[data-range]")) {
    setClasses(button, "px-space-sm py-space-2xs rounded-lg font-label-md text-label-md", button.dataset.range === range ? CLASSES.rangeOn : CLASSES.rangeOff);
  }
  for (const button of document.querySelectorAll("[data-source]")) {
    const on = state.filter.sources.length === 0 || state.filter.sources.includes(button.dataset.source);
    setClasses(button, "px-space-sm py-space-2xs rounded-full font-label-sm text-label-sm font-semibold flex items-center gap-space-2xs", on ? CLASSES.storeOn : CLASSES.storeOff);
    button.querySelector("[data-check]").hidden = !on;
  }
  for (const button of document.querySelectorAll("[data-rating]")) {
    const on = state.filter.ratings.includes(button.dataset.rating);
    setClasses(button, "w-7 h-7 rounded-lg font-label-sm text-label-sm flex items-center justify-center font-medium", on ? CLASSES.ratingOn : CLASSES.ratingOff);
  }
  for (const button of document.querySelectorAll("[data-sentiment]")) {
    const value = button.dataset.sentiment;
    const on = state.filter.sentiments.includes(value);
    setClasses(button, "px-space-sm py-space-2xs rounded-full font-label-sm text-label-sm font-semibold", on ? CLASSES.sentimentOn[value] : CLASSES.sentimentOff);
  }
  const search = byId("searchInput");
  if (document.activeElement !== search) search.value = state.filter.query;
  byId("hideDuplicates").checked = state.filter.hide_duplicates === "true";
  byId("includeShort").checked = state.filter.include_excluded === "true";
  byId("rangeStart").value = state.filter.start || (meta ? meta.window.start : "");
  byId("rangeEnd").value = state.filter.end || (meta ? meta.window.end : "");
  renderMenus();
  renderChips();
}

function menuLabel(prefix, values, names, allText) {
  if (values.length === 0) return allText;
  if (values.length === 1) return `${prefix}${names(values[0])}`;
  return `${prefix}${values.length} selected`;
}

function renderMenu(menuId, key, options) {
  const menu = byId(menuId);
  menu.replaceChildren();
  for (const option of options) {
    const item = clone("menuItemTpl");
    const box = field(item, "box");
    box.checked = state.filter[key].includes(option.value);
    box.addEventListener("change", () => {
      toggleValue(key, option.value);
      if (key === "languages" && EXCLUDED_LANGUAGES.includes(option.value) && box.checked) {
        state.filter.include_excluded = "true"; // Hindi and Other reviews sit outside the theme basis
      }
      filterChanged();
    });
    fill(item, "label", option.label);
    menu.append(item);
  }
}

function renderMenus() {
  if (!meta) return;
  const themes = [...meta.themes.map((theme) => ({ value: theme.id, label: theme.name })), { value: "unthemed", label: "Unthemed" }];
  renderMenu("themeMenu", "themes", themes);
  renderMenu("langMenu", "languages", ["en", "hinglish", "hi", "other"].map((code) => ({ value: code, label: LANGUAGE_NAMES[code] })));
  renderMenu("versionMenu", "app_versions", meta.app_versions.map((version) => ({ value: version, label: `v${version}` })));
  setText("themeMenuLabel", menuLabel("Theme: ", state.filter.themes, themeName, "Theme: All"));
  setText("langMenuLabel", menuLabel("Lang: ", state.filter.languages, (code) => LANGUAGE_NAMES[code] || code, "Lang: All"));
  setText("versionMenuLabel", menuLabel("v", state.filter.app_versions, (version) => version, "All versions"));
}

function chip(label, onRemove) {
  const node = clone("chipTpl");
  fill(node, "label", label);
  field(node, "remove").addEventListener("click", () => {
    onRemove();
    filterChanged();
  });
  return node;
}

function renderChips() {
  const chips = [];
  const listChips = [
    ["sources", (value) => SOURCE_NAMES[value] || value],
    ["ratings", (value) => `${value}★`],
    ["sentiments", (value) => value.charAt(0).toUpperCase() + value.slice(1)],
    ["themes", themeName],
    ["languages", (value) => LANGUAGE_NAMES[value] || value],
    ["app_versions", (value) => `v${value}`],
  ];
  for (const [key, label] of listChips) {
    for (const value of state.filter[key]) chips.push(chip(label(value), () => toggleValue(key, value)));
  }
  if (state.filter.start || state.filter.end) {
    const start = state.filter.start || meta.window.start;
    const end = state.filter.end || meta.window.end;
    chips.push(chip(`${shortDate(start)} – ${shortDate(end)}`, () => { state.filter.start = ""; state.filter.end = ""; }));
  }
  if (state.filter.query) chips.push(chip(`Search: ${state.filter.query}`, () => { state.filter.query = ""; }));
  if (state.filter.hide_duplicates) chips.push(chip("No duplicates", () => { state.filter.hide_duplicates = ""; }));
  if (state.filter.include_excluded) chips.push(chip("Short & other-language included", () => { state.filter.include_excluded = ""; }));
  byId("activeChips").replaceChildren(...chips);
  byId("clearAll").hidden = chips.length === 0;
}

function clearFilters() {
  for (const key of LIST_KEYS) state.filter[key] = [];
  for (const key of SCALAR_KEYS) state.filter[key] = "";
  filterChanged();
}

// ---------------------------------------------------------------- Analyse tab

async function loadMeta() {
  const params = new URLSearchParams();
  if (state.week) params.set("week", state.week);
  const { status, body } = await getJson(API.meta, params);
  meta = status === 200 && body && body.ready ? body : null;
  byId("notReadyState").hidden = Boolean(meta);
  byId("tab-analyse").hidden = !meta || state.tab !== "analyse";
  byId("tab-pulse").hidden = !meta || state.tab !== "pulse";
  if (!meta) {
    setText("syncedText", "No data yet");
    return false;
  }
  setText("syncedText", `Reviews to ${shortDate(meta.window.end)}`);
  setText("showingTotal", count(meta.total));
  setText("showingCount", count(meta.eligible)); // the default view; loadAnalyse refines it per filter
  byId("exportBtn").hidden = !meta.allow_export;
  renderWeekPicker();
  return true;
}

async function loadAnalyse() {
  if (!meta) return;
  const params = filterParams();
  const [summary, themes, trend, ratings, quality] = await Promise.all(
    [API.summary, API.themes, API.trend, API.ratings, API.quality].map((path) => getJson(path, params)),
  );
  if ([summary, themes, trend, ratings, quality].some((result) => result.status !== 200)) {
    showToast("Some figures could not be loaded; check the filters.", false);
    return;
  }
  renderSummary(summary.body, ratings.body, trend.body);
  renderThemes(themes.body);
  lastTrend = trend.body;
  renderTrend();
  renderRatings(ratings.body);
  renderQuality(quality.body);
  byId("noResultsState").hidden = summary.body.total > 0;
  await loadReviews();
}

function renderSummary(summary, ratings, trend) {
  setText("showingCount", count(summary.total));
  setText("kpiTotal", count(summary.total));
  setText("kpiTotalOf", `/ ${count(meta.total)}`);
  const share = meta.total ? summary.total / meta.total : 0;
  setText("kpiTotalNote", `${percent(share)} of this window's reviews`);
  setWidth(byId("kpiTotalBar"), share);

  const change = summary.change;
  const mean = summary.mean_rating;
  setText("kpiRating", mean === null ? "–" : `${mean.toFixed(1)}★`);
  const ratingDelta = change ? change.mean_rating : null;
  deltaChip(byId("kpiRatingDelta"), ratingDelta, ratingDelta === null ? "" : `${ratingDelta >= 0 ? "+" : ""}${ratingDelta.toFixed(1)}★`, true);
  const days = periodDays();
  setText("kpiRatingNote", ratingDelta === null || mean === null ? "No reviews in the previous period" : `vs previous ${days}-day window (${(mean - ratingDelta).toFixed(1)}★)`);
  let happy = 0;
  let all = 0;
  for (const source of Object.values(ratings)) {
    for (const [stars, number] of Object.entries(source)) {
      all += number;
      if (Number(stars) >= 4) happy += number;
    }
  }
  setWidth(byId("kpiHappyBar"), all ? happy / all : 0);
  setText("kpiHappyText", all ? `${Math.round((happy / all) * 100)}% rated 4–5★` : "");

  setText("kpiNeg", percent(summary.negative_share));
  const negPts = change ? change.negative_share_pts : null;
  deltaChip(byId("kpiNegDelta"), negPts, negPts === null ? "" : `${negPts >= 0 ? "+" : ""}${negPts.toFixed(1)} pts`, false);
  setText("kpiNegNote", `${count(Math.round(summary.negative_share * summary.total))} reviews rated 1–2★`);
  setWidth(byId("kpiNegBar"), summary.negative_share);

  const velocity = change ? change.total_pct : null;
  setText("kpiVelocity", velocity === null ? "–" : `${velocity >= 0 ? "+" : ""}${(velocity * 100).toFixed(1)}%`);
  const tag = byId("kpiVelocityTag");
  tag.hidden = velocity === null;
  tag.textContent = velocity === null ? "" : velocity > 0.05 ? "Accelerating" : velocity < -0.05 ? "Slowing" : "Steady";
  setText("kpiVelocityNote", velocity === null ? "No reviews in the previous period" : `vs previous ${days}-day window`);
  setText("kpiRunRate", `${(summary.total / days).toFixed(1)} reviews/day run-rate`);
  const peak = trend.reduce((best, week) => (week.count > (best ? best.count : 0) ? week : best), null);
  setText("kpiPeak", peak ? `Peak: week of ${shortDate(peak.start)}` : "");
}

function periodDays() {
  const start = state.filter.start || meta.window.start;
  const end = state.filter.end || meta.window.end;
  return Math.round((parseDay(end) - parseDay(start)) / 86400000) + 1;
}

function trendChip(node, arrow, points) {
  if (!arrow) {
    node.hidden = true;
    return;
  }
  node.hidden = false;
  const base = "inline-flex items-center gap-0.5 font-label-sm text-label-sm font-semibold px-2 py-0.5 rounded-full tabular-nums";
  const icon = field(node, "trendIcon");
  if (arrow === "→") {
    setClasses(node, base, CLASSES.chipFlat);
    icon.textContent = "";
    fill(node, "trendText", "steady");
    return;
  }
  const up = arrow === "↑";
  setClasses(node, base, up ? CLASSES.chipBad : CLASSES.chipGood);
  icon.textContent = up ? "arrow_upward" : "arrow_downward";
  const size = Math.abs(points);
  fill(node, "trendText", `${up ? "+" : "−"}${size} ${size === 1 ? "pt" : "pts"}`);
}

function renderThemes(themes) {
  const definitions = Object.fromEntries(meta.themes.map((theme) => [theme.id, theme.definition]));
  const rows = themes.map((stats, index) => {
    const row = clone("themeRowTpl");
    const unthemed = stats.theme_id === "unthemed";
    fill(row, "icon", unthemed ? "more_horiz" : THEME_ICONS[index % THEME_ICONS.length]);
    fill(row, "name", stats.name);
    fill(row, "subtitle", unthemed ? "Reviews that fit no theme" : definitions[stats.theme_id] || "");
    fill(row, "share", percent(stats.share));
    fill(row, "count", `${count(stats.count)} reviews`);
    fill(row, "rating", stats.mean_rating === null ? "–" : `${stats.mean_rating.toFixed(1)}★`);
    const sentiment = stats.sentiment || {};
    const total = (sentiment.negative || 0) + (sentiment.mixed || 0) + (sentiment.positive || 0);
    const parts = { neg: sentiment.negative || 0, mix: sentiment.mixed || 0, pos: sentiment.positive || 0 };
    for (const [key, word] of [["neg", "Neg"], ["mix", "Mix"], ["pos", "Pos"]]) {
      const share = total ? parts[key] / total : 0;
      fill(row, `${key}Label`, `${Math.round(share * 100)}% ${word}`);
      setWidth(field(row, `${key}Bar`), share);
    }
    trendChip(field(row, "trend"), stats.arrow, stats.trend_pts);
    if (state.filter.themes.includes(stats.theme_id)) row.classList.add("bg-surface-container-high");
    row.addEventListener("click", () => {
      toggleValue("themes", stats.theme_id);
      filterChanged();
    });
    return row;
  });
  byId("themeRows").replaceChildren(...rows);
}

function renderTrend() {
  setText("trendWeeks", `${lastTrend.length} ${lastTrend.length === 1 ? "Week" : "Weeks"}`);
  const volume = trendMode === "vol";
  setClasses(byId("toggle-vol-btn"), "px-3 py-1 text-label-sm font-label-sm rounded-md", volume ? "font-semibold bg-surface-container-lowest text-primary shadow-sm" : "font-medium text-on-surface-variant hover:text-on-surface");
  setClasses(byId("toggle-theme-btn"), "px-3 py-1 text-label-sm font-label-sm rounded-md", volume ? "font-medium text-on-surface-variant hover:text-on-surface" : "font-semibold bg-surface-container-lowest text-primary shadow-sm");
  byId("trendLegend").hidden = !volume;
  if (typeof window.Chart === "undefined") return; // the chart script did not load (offline); the rest still works
  if (trendChart) trendChart.destroy();
  const labels = lastTrend.map((week) => shortDate(week.start));
  const grid = { color: "#E2E8F0", borderDash: [3, 3] };
  const ticks = { color: "#6B7B72", font: { family: "Inter", size: 10 } };
  const config = volume ? volumeChart(labels, grid, ticks) : themeChart(labels, grid, ticks);
  config.options.animation = false; // redraws on every filter change; no half-drawn frames
  trendChart = new window.Chart(byId("trendChart"), config);
}

function volumeChart(labels, grid, ticks) {
  return {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Reviews", data: lastTrend.map((week) => week.count), borderColor: "#006C4F", backgroundColor: "rgba(0, 208, 156, 0.15)", fill: true, tension: 0.25, borderWidth: 2.5, pointRadius: 2, yAxisID: "y" },
        { label: "Mean rating", data: lastTrend.map((week) => week.mean_rating), borderColor: "#F59E0B", borderDash: [4, 2], borderWidth: 2, pointRadius: 0, spanGaps: true, yAxisID: "y1" },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks },
        y: { position: "left", beginAtZero: true, grid, ticks },
        y1: { position: "right", min: 1, max: 5, grid: { display: false }, ticks: { ...ticks, callback: (value) => `${value}★` } },
      },
    },
  };
}

function themeChart(labels, grid, ticks) {
  const ids = [...meta.themes.map((theme) => theme.id), "unthemed"];
  return {
    type: "bar",
    data: {
      labels,
      datasets: ids.map((id, index) => ({
        label: themeName(id),
        data: lastTrend.map((week) => Math.round((week.themes[id] || 0) * 1000) / 10),
        backgroundColor: CHART_COLOURS[index % CHART_COLOURS.length],
        borderRadius: 2,
      })),
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { family: "Inter", size: 11 } } } },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks },
        y: { stacked: true, max: 100, grid, ticks: { ...ticks, callback: (value) => `${value}%` } },
      },
    },
  };
}

function renderRatings(ratings) {
  const play = ratings.play_store || {};
  const app = ratings.app_store || {};
  const sum = (source) => Object.values(source).reduce((total, number) => total + number, 0);
  const total = sum(play) + sum(app);
  const styles = {
    5: ["bg-primary-container", "bg-secondary"],
    4: ["bg-primary-container/80", "bg-secondary/80"],
    3: ["bg-primary-container/60", "bg-secondary/60"],
    2: ["bg-amber-500", "bg-secondary/40"],
    1: ["bg-error", "bg-error/60"],
  };
  const rows = [5, 4, 3, 2, 1].map((stars) => {
    const row = clone("ratingRowTpl");
    const number = (play[stars] || 0) + (app[stars] || 0);
    fill(row, "stars", `${stars}★`);
    fill(row, "word", RATING_WORDS[stars]);
    fill(row, "pctText", total ? percent(number / total) : "0%");
    fill(row, "count", `(${count(number)})`);
    const playBar = field(row, "playBar");
    const appBar = field(row, "appBar");
    setClasses(playBar, "h-full rounded-l-full", styles[stars][0]);
    setClasses(appBar, "h-full rounded-r-full", styles[stars][1]);
    setWidth(playBar, total ? (play[stars] || 0) / total : 0);
    setWidth(appBar, total ? (app[stars] || 0) / total : 0);
    if (stars === 1) {
      for (const name of ["label", "pct", "word", "count"]) field(row, name).classList.add("text-error");
    }
    return row;
  });
  byId("ratingRows").replaceChildren(...rows);
  setText("playShare", `Play (${total ? Math.round((sum(play) / total) * 100) : 0}%)`);
  setText("iosShare", `iOS (${total ? Math.round((sum(app) / total) * 100) : 0}%)`);
  const mean = (source) => {
    const reviews = sum(source);
    if (!reviews) return "–";
    const stars = Object.entries(source).reduce((total, [rating, number]) => total + Number(rating) * number, 0);
    return `${(stars / reviews).toFixed(2)}★`;
  };
  setText("playCsat", mean(play));
  setText("iosCsat", mean(app));
  setText("playCount", `${count(sum(play))} indexed entries`);
  setText("iosCount", `${count(sum(app))} indexed entries`);
}

function renderQuality(quality) {
  const excluded = quality.excluded || {};
  const parts = [
    `${count(excluded.unsupported_language)} excluded (unsupported language)`,
    `${count(excluded.too_short)} too short`,
    `${count(quality.duplicates)} duplicates`,
    `${count(quality.unclassified)} not yet classified`,
    "Strict PII filter active (all reviewer data anonymised)",
  ];
  setText("qualityLine", parts.join(" • "));
}

async function loadReviews() {
  const params = filterParams({ sort: state.sort, page: String(state.page) });
  const { status, body } = await getJson(API.reviews, params);
  if (status !== 200) return;
  const rows = body.items.map(reviewRow);
  byId("reviewRows").replaceChildren(...rows);
  setText("reviewsCount", `${count(body.total)} entries matching active criteria`);
  const size = meta.page_size;
  const pages = Math.max(1, Math.ceil(body.total / size));
  const from = body.total ? (body.page - 1) * size + 1 : 0;
  setText("pageRange", `${count(from)}–${count(Math.min(body.total, body.page * size))}`);
  setText("pageTotal", count(body.total));
  renderPager(body.page, pages);
  byId("sortSelect").value = state.sort;
  byId("exportBtn").setAttribute("href", `${API.csv}?${filterParams({ sort: state.sort })}`);
}

function ratingBadge(rating) {
  if (rating <= 2) return "bg-error-container text-on-error-container";
  if (rating === 3) return "bg-amber-100 text-amber-800";
  return "bg-primary-container/20 text-on-primary-container";
}

function reviewRow(item) {
  const row = clone("reviewRowTpl");
  const badge = fill(row, "rating", `${item.rating}★ ${RATING_WORDS[item.rating]}`);
  setClasses(badge, "inline-flex items-center px-2 py-0.5 rounded font-label-sm text-label-sm font-semibold tabular-nums", ratingBadge(item.rating));
  fill(row, "hash", `#${item.review_id.slice(0, 8).toUpperCase()}`);
  fill(row, "storeIcon", item.source === "app_store" ? "phone_iphone" : "android");
  fill(row, "store", SOURCE_NAMES[item.source] || item.source);
  const version = fill(row, "version", item.app_version ? `v${item.app_version}` : "");
  version.hidden = !item.app_version;
  const theme = fill(row, "theme", item.theme ? themeName(item.theme) : "");
  theme.hidden = !item.theme;
  fill(row, "date", shortDate(item.posted_on, true));
  const sentiment = fill(row, "sentiment", item.sentiment ? item.sentiment.charAt(0).toUpperCase() + item.sentiment.slice(1) : "");
  sentiment.hidden = !item.sentiment;
  if (item.sentiment) {
    setClasses(sentiment, "inline-flex items-center gap-0.5 font-label-sm text-label-sm px-2 py-0.5 rounded-full font-semibold", CLASSES.sentimentOn[item.sentiment]);
  }
  fill(row, "title", item.title ? `${item.title} — ` : "");
  fill(row, "text", item.text);
  return row;
}

function renderPager(page, pages) {
  byId("pagePrev").disabled = page <= 1;
  byId("pageNext").disabled = page >= pages;
  const numbers = [...new Set([1, 2, 3, page, pages].filter((number) => number >= 1 && number <= pages))].sort((a, b) => a - b);
  const nodes = [];
  numbers.forEach((number, index) => {
    if (index > 0 && number - numbers[index - 1] > 1) {
      const gap = document.createElement("span");
      gap.className = "px-1 text-on-surface-variant";
      gap.textContent = "...";
      nodes.push(gap);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = String(number);
    button.className = number === page
      ? "w-8 h-8 rounded-lg bg-primary text-on-primary font-label-md text-label-md font-semibold flex items-center justify-center"
      : "w-8 h-8 rounded-lg text-on-surface-variant hover:bg-surface-container-low font-label-md text-label-md flex items-center justify-center tabular-nums";
    button.addEventListener("click", () => goToPage(number));
    nodes.push(button);
  });
  byId("pageNumbers").replaceChildren(...nodes);
}

function goToPage(page) {
  state.page = page;
  writeUrl();
  loadReviews();
}

// ---------------------------------------------------------------- Weekly pulse tab

function pulseWeek() {
  return state.week || (meta ? meta.week : "");
}

function renderWeekPicker() {
  const week = pulseWeek();
  const monday = weekMonday(week);
  setText("weekPickerLabel", `Week of ${shortDate(monday, true)}`);
  setText("weekPickerRange", `(${shortDate(monday)} – ${shortDate(addDays(monday, 6))})`);
  const weeks = meta.weeks;
  const index = weeks.indexOf(week);
  byId("prevWeekBtn").disabled = index < 0 || index >= weeks.length - 1;
  byId("nextWeekBtn").disabled = index <= 0;
  const items = weeks.map((item) => {
    const button = document.createElement("button");
    button.type = "button";
    const start = weekMonday(item);
    const label = document.createElement("span");
    label.textContent = `${shortDate(start)} – ${shortDate(addDays(start, 6), true)}`;
    button.append(label);
    if (item === week) {
      button.className = "w-full text-left px-space-md py-space-sm rounded-lg bg-surface-container-high text-primary font-headline-sm text-headline-sm flex items-center justify-between";
      const check = document.createElement("span");
      check.className = "material-symbols-outlined text-[16px]";
      check.textContent = "check";
      button.append(check);
    } else {
      button.className = "w-full text-left px-space-md py-space-sm rounded-lg text-on-surface hover:bg-surface-container-low font-body-md text-body-md";
    }
    button.addEventListener("click", () => selectWeek(item));
    return button;
  });
  byId("weekDropdown").replaceChildren(...items);
}

async function selectWeek(week) {
  state.week = week === meta.week ? "" : week;
  byId("weekDropdown").classList.add("hidden");
  writeUrl();
  await loadMeta();
  loadPulse();
}

const STATUS_PILLS = {
  VALIDATED: ["Ready to publish", "bg-primary-fixed/20 text-primary", "bg-primary animate-pulse"],
  PUBLISHED_DOC_ONLY: ["Published (Doc only)", "bg-surface-container-high text-primary", "bg-primary"],
  DELIVERED: ["Published", "bg-surface-container-high text-primary", "bg-primary"],
  DELIVERY_UNVERIFIED: ["Published (unverified)", "bg-amber-100 text-amber-800", "bg-amber-500"],
};

function renderStatus(status) {
  const [text, colours, dot] = STATUS_PILLS[status] || (["VALIDATION_FAILED", "FAILED", "INSUFFICIENT_DATA"].includes(status)
    ? ["Needs attention", "bg-error-container text-on-error-container", "bg-error"]
    : [status ? `Draft (${status.toLowerCase()})` : "No pulse yet", "bg-surface-container-low text-on-surface-variant", "bg-outline"]);
  setClasses(byId("pulseStatus"), "inline-flex items-center gap-space-xs px-space-md py-space-2xs rounded-full font-label-md text-label-md", colours);
  setClasses(byId("pulseStatusDot"), "w-2 h-2 rounded-full", dot);
  setText("pulseStatusText", text);
}

async function loadPulse() {
  if (!meta) return;
  const week = pulseWeek();
  const params = new URLSearchParams({ week });
  const [pulse, budget, ready, themes] = await Promise.all([
    getJson(API.pulse, params),
    getJson(API.budget),
    getJson(API.ready),
    getJson(API.themes, params),
  ]);
  pulseView = pulse.status === 200 ? pulse.body : null;
  readiness = ready.status === 200 ? ready.body : { ready: false, problems: [{ name: "server", detail: "could not check delivery" }], draft_supported: false };
  renderBudget(budget.status === 200 ? budget.body : null);
  renderStatus(pulseView ? pulseView.status : null);
  const hasPulse = Boolean(pulseView && pulseView.has_pulse);
  byId("pulseArticle").hidden = !hasPulse;
  byId("pulseChecks").hidden = !hasPulse;
  byId("noPulseState").hidden = hasPulse;
  setText("noPulseGenerateLabel", `Generate pulse for ${week.replace("-W", " · Week ")}`);
  setText(
    "noPulseText",
    pulseView && !hasPulse
      ? `The latest run for this week stopped at ${pulseView.status || "an early stage"}; generate the pulse to finish it.`
      : "Pulse synthesis aggregates Play Store and App Store telemetry for this period, sanitizes PII, and extracts key squad drivers.",
  );
  if (hasPulse) renderPulse(pulseView, themes.status === 200 ? themes.body : []);
  renderDelivery();
}

function renderBudget(budget) {
  const banner = byId("budgetBanner");
  banner.hidden = !budget || !budget.exhausted;
  if (!budget) return;
  const model = budget.models[0];
  setText("budgetText", `Used ${count(model.tokens_used)} of ${count(model.token_limit)} tokens today. Generation resets after ${budget.resets_at_local}.`);
}

function renderPulse(view, themeStats) {
  setText("docId", `DOC-ID: #PULSE-${view.iso_week}`);
  setText("pulseTitle", view.title);
  const parts = view.meta_line.split(" · ");
  setText("metaReviews", `${parts[0] || ""} parsed`);
  setText("metaRange", `${parts[1] || ""} observation window`);
  setText("metaSources", parts[2] || "");
  const rating = (parts[3] || "").replace("avg", "").replace("★", "").trim();
  byId("metaRatingWrap").hidden = !rating;
  byId("metaRatingSep").hidden = !rating;
  setText("metaRating", rating ? `${rating} / 5.0` : "");

  const byName = Object.fromEntries(themeStats.map((stats) => [stats.name, stats]));
  const cards = view.themes.map((theme, index) => {
    const card = clone("themeCardTpl");
    fill(card, "driver", `Driver ${String(index + 1).padStart(2, "0")}`);
    fill(card, "name", theme.name);
    fill(card, "share", `Share: ${theme.share_pct}% of total`);
    fill(card, "summary", theme.summary);
    const chipNode = field(card, "trend");
    const match = theme.trend.match(/([↑↓])(\d+)/);
    const base = "inline-flex items-center gap-space-2xs font-label-sm text-label-sm font-semibold px-space-sm py-space-2xs rounded-full";
    if (match) {
      const up = match[1] === "↑";
      setClasses(chipNode, base, up ? "text-error bg-error-container/40" : "text-primary bg-primary-fixed/20");
      fill(chipNode, "trendIcon", up ? "trending_up" : "trending_down");
      fill(chipNode, "trendText", `${up ? "+" : "−"}${match[2]} pts`);
    } else if (theme.trend) {
      setClasses(chipNode, base, "text-secondary bg-surface-container-highest");
      fill(chipNode, "trendIcon", "horizontal_rule");
      fill(chipNode, "trendText", "Steady");
    } else {
      chipNode.hidden = true;
    }
    const stats = byName[theme.name];
    const sentiment = (stats && stats.sentiment) || {};
    const total = (sentiment.negative || 0) + (sentiment.mixed || 0) + (sentiment.positive || 0);
    const positive = total ? (sentiment.positive || 0) / total : 0;
    setWidth(field(card, "posBar"), positive);
    setWidth(field(card, "negBar"), total ? 1 - positive : 0);
    return card;
  });
  byId("themeCards").replaceChildren(...cards);

  const quotes = view.quotes.map((quote) => {
    const row = clone("quoteRowTpl");
    const badge = fill(row, "rating", `${quote.rating}★`);
    const colours = quote.rating <= 2 ? "bg-error-container text-on-error-container" : quote.rating >= 4 ? "bg-primary-fixed text-on-primary-fixed" : "bg-surface-container-high text-on-surface";
    setClasses(badge, "w-7 h-7 rounded-lg flex items-center justify-center font-mono font-semibold text-label-sm shrink-0 mt-0.5", colours);
    fill(row, "text", `“${quote.text}”`);
    fill(row, "store", SOURCE_NAMES[quote.source] || quote.source);
    fill(row, "month", quote.month);
    fill(row, "theme", themeName(quote.theme_id));
    return row;
  });
  byId("quoteRows").replaceChildren(...quotes);

  const actions = view.actions.map((action, index) => {
    const row = clone("actionRowTpl");
    fill(row, "icon", ACTION_ICONS[index % ACTION_ICONS.length]);
    fill(row, "themes", action.themes.join(" · "));
    fill(row, "text", action.text);
    return row;
  });
  byId("actionRows").replaceChildren(...actions);

  const share = view.word_count / view.max_words;
  setText("densityText", `${view.word_count} / ${view.max_words} words`);
  setText("densityPct", `(${Math.round(share * 100)}% capacity)`);
  setWidth(byId("densityBar"), share);
  const checks = view.checks;
  badge("badgeQuotes", checks.V3);
  badge("badgePii", checks.V6);
  badge("badgeFormat", checks.V1 && checks.V2 && checks.V5 && checks.V7);
}

function badge(id, ok) {
  const node = byId(id);
  setClasses(node, "inline-flex items-center gap-space-2xs font-label-sm text-label-sm px-space-sm py-space-2xs rounded-full font-semibold", ok ? "bg-surface-container-low text-primary" : "bg-error-container text-on-error-container");
  if (!ok) node.querySelector("[data-icon]").textContent = "error";
}

function renderDelivery() {
  const delivery = (pulseView && pulseView.delivery) || {};
  const docLink = byId("openDocLink");
  docLink.hidden = !delivery.doc_url;
  if (delivery.doc_url) docLink.setAttribute("href", delivery.doc_url);
  byId("openDraftLink").hidden = !delivery.draft_id;
  setText("deliveryText", delivery.doc_url ? `Published to Google Docs (${pulseView.status.replaceAll("_", " ").toLowerCase()})` : "Not published to Google Docs yet");

  const problems = readiness && readiness.problems.length ? readiness.problems.map((item) => `${item.name}: ${item.detail}`).join("; ") : "";
  const validated = Boolean(pulseView && pulseView.has_pulse && pulseView.validated);
  const publish = byId("publishDocBtn");
  publish.disabled = Boolean(problems) || !validated;
  publish.title = problems ? `Delivery isn't set up — ${problems}` : validated ? "" : "Generate a pulse that passes validation first";
  const draft = byId("gmailDraftBtn");
  const noTool = readiness && !readiness.draft_supported;
  draft.disabled = Boolean(problems) || noTool || !delivery.doc_url;
  draft.title = noTool ? NO_DRAFT_TOOL : problems ? `Delivery isn't set up — ${problems}` : delivery.doc_url ? "" : "Publish the Doc first";
}

// ---------------------------------------------------------------- jobs

async function startJob(action, question) {
  if (!meta) return;
  if (question && !window.confirm(question)) return;
  const response = await fetch(API.jobs, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Pulse-Client": "dashboard" },
    body: JSON.stringify({ action, week: pulseWeek() }),
  });
  let body = {};
  try {
    body = await response.json();
  } catch (error) {
    body = {};
  }
  if (response.status !== 202) {
    showToast(body.error || "The action could not start.", false);
    return;
  }
  setBusy(true);
  showToast(action === "generate" ? "Generating the pulse… this can take a minute." : "Working…");
  pollJob(body.id);
}

function setBusy(busy) {
  for (const id of ["generateBtn", "noPulseGenerateBtn"]) byId(id).disabled = busy;
  if (busy) {
    byId("publishDocBtn").disabled = true;
    byId("gmailDraftBtn").disabled = true;
  }
}

async function pollJob(jobId) {
  const { status, body } = await getJson(`${API.jobs}/${encodeURIComponent(jobId)}`);
  if (status !== 200) {
    setBusy(false);
    return;
  }
  if (body.state === "running") {
    setTimeout(() => pollJob(jobId), 3000);
    return;
  }
  setBusy(false);
  const warnings = body.warnings || [];
  if (body.state === "locked") {
    showToast("Another run for this week is in progress; try again when it finishes.", false);
  } else if (warnings.some((code) => code.startsWith("daily_token_budget"))) {
    showToast("Groq's daily token limit is used up; try again after the reset.", false);
  } else if (warnings.includes("doc_already_appended")) {
    showToast("This week's pulse is already in the Google Doc; nothing was appended.", false);
  } else if (body.state === "succeeded") {
    showToast(`Done: ${String(body.run_status || "").replaceAll("_", " ").toLowerCase()}`);
  } else {
    showToast(`The run ended as ${body.run_status || "failed"}; see runs/…/report.md.`, false);
  }
  await loadMeta();
  loadPulse();
}

// ---------------------------------------------------------------- tabs and events

function showTab(tab) {
  state.tab = tab;
  byId("navAnalyse").className = tab === "analyse" ? CLASSES.navOn : CLASSES.navOff;
  byId("navPulse").className = tab === "pulse" ? CLASSES.navOn : CLASSES.navOff;
  byId("navAnalyse").toggleAttribute("aria-current", tab === "analyse");
  byId("navPulse").toggleAttribute("aria-current", tab === "pulse");
  byId("tab-analyse").hidden = !meta || tab !== "analyse";
  byId("tab-pulse").hidden = !meta || tab !== "pulse";
  if (!meta) return;
  if (tab === "pulse") loadPulse();
  else loadAnalyse();
}

function toggleMenu(id) {
  for (const menu of ["themeMenu", "langMenu", "versionMenu", "customRangePanel", "weekDropdown"]) {
    if (menu !== id) byId(menu).classList.add("hidden");
  }
  byId(id).classList.toggle("hidden");
}

function bindEvents() {
  for (const link of document.querySelectorAll("[data-tab]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showTab(link.dataset.tab);
      writeUrl();
    });
  }
  for (const button of document.querySelectorAll("[data-range]")) {
    button.addEventListener("click", () => {
      const range = rangeOf(button.dataset.range);
      state.filter.start = range.start;
      state.filter.end = range.end;
      filterChanged();
    });
  }
  byId("customRangeBtn").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu("customRangePanel");
  });
  byId("rangeApply").addEventListener("click", () => {
    const start = byId("rangeStart").value;
    const end = byId("rangeEnd").value;
    if (start && end && start > end) {
      showToast("The start date must be on or before the end date.", false);
      return;
    }
    state.filter.start = start === meta.window.start && end === meta.window.end ? "" : start;
    state.filter.end = start === meta.window.start && end === meta.window.end ? "" : end;
    byId("customRangePanel").classList.add("hidden");
    filterChanged();
  });
  for (const button of document.querySelectorAll("[data-source]")) {
    button.addEventListener("click", () => {
      const all = ["play_store", "app_store"];
      const on = state.filter.sources.length ? state.filter.sources : all;
      const next = on.includes(button.dataset.source) ? on.filter((source) => source !== button.dataset.source) : [...on, button.dataset.source];
      state.filter.sources = next.length === all.length || next.length === 0 ? [] : next;
      filterChanged();
    });
  }
  for (const button of document.querySelectorAll("[data-rating]")) {
    button.addEventListener("click", () => { toggleValue("ratings", button.dataset.rating); filterChanged(); });
  }
  for (const button of document.querySelectorAll("[data-sentiment]")) {
    button.addEventListener("click", () => { toggleValue("sentiments", button.dataset.sentiment); filterChanged(); });
  }
  for (const button of document.querySelectorAll("[data-menu]")) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleMenu(button.dataset.menu);
    });
  }
  for (const id of ["themeMenu", "langMenu", "versionMenu", "customRangePanel", "weekDropdown"]) {
    byId(id).addEventListener("click", (event) => event.stopPropagation());
  }
  document.addEventListener("click", () => {
    for (const id of ["themeMenu", "langMenu", "versionMenu", "customRangePanel", "weekDropdown"]) byId(id).classList.add("hidden");
  });
  byId("searchInput").addEventListener("input", (event) => {
    state.filter.query = event.target.value.slice(0, 100);
    filterChanged();
  });
  byId("hideDuplicates").addEventListener("change", (event) => {
    state.filter.hide_duplicates = event.target.checked ? "true" : "";
    filterChanged();
  });
  byId("includeShort").addEventListener("change", (event) => {
    state.filter.include_excluded = event.target.checked ? "true" : "";
    filterChanged();
  });
  byId("clearAll").addEventListener("click", clearFilters);
  byId("noResultsClear").addEventListener("click", clearFilters);
  byId("closeBanner").addEventListener("click", () => { byId("piiBanner").hidden = true; });
  byId("sortSelect").addEventListener("change", (event) => {
    state.sort = event.target.value;
    state.page = 1;
    writeUrl();
    loadReviews();
  });
  byId("pagePrev").addEventListener("click", () => goToPage(Math.max(1, state.page - 1)));
  byId("pageNext").addEventListener("click", () => goToPage(state.page + 1));
  byId("toggle-vol-btn").addEventListener("click", () => { trendMode = "vol"; renderTrend(); });
  byId("toggle-theme-btn").addEventListener("click", () => { trendMode = "theme"; renderTrend(); });
  byId("weekPickerTrigger").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu("weekDropdown");
  });
  byId("prevWeekBtn").addEventListener("click", () => {
    const weeks = meta.weeks;
    selectWeek(weeks[Math.min(weeks.length - 1, weeks.indexOf(pulseWeek()) + 1)]);
  });
  byId("nextWeekBtn").addEventListener("click", () => {
    const weeks = meta.weeks;
    selectWeek(weeks[Math.max(0, weeks.indexOf(pulseWeek()) - 1)]);
  });
  const generate = () => startJob("generate", `Generate the pulse for ${pulseWeek()}? This runs the pipeline as a dry run and uses Groq tokens. Nothing is published.`);
  byId("generateBtn").addEventListener("click", () => {
    if (state.tab !== "pulse") {
      showTab("pulse");
      writeUrl();
    }
    generate();
  });
  byId("noPulseGenerateBtn").addEventListener("click", generate);
  byId("publishDocBtn").addEventListener("click", () =>
    startJob("publish", `Publish the ${pulseWeek()} pulse to Google Docs? It is appended once to the running Doc.`),
  );
  byId("gmailDraftBtn").addEventListener("click", () =>
    startJob("draft", `Create the Gmail draft for ${pulseWeek()}? Nothing is sent.`),
  );
  window.addEventListener("popstate", async () => {
    state = readUrl();
    await loadMeta();
    renderFilterBar();
    showTab(state.tab);
  });
}

async function init() {
  bindEvents();
  const ready = await loadMeta();
  renderFilterBar();
  showTab(state.tab);
  if (!ready) setText("showingCount", "0");
}

init();

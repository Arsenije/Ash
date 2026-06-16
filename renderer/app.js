"use strict";

// Filterable fields surfaced as type-ahead chips in the search bar.
const FIELDS = [
  { key: "location", label: "location", kind: "single", facet: "location", param: "location" },
  { key: "scene", label: "scene", kind: "single", facet: "scene", param: "scene" },
  { key: "object", label: "object", kind: "multi", facet: "objects", param: "objects" },
  { key: "tag", label: "tag", kind: "multi", facet: "tags", param: "tags" },
  { key: "after", label: "after", kind: "date", param: "date_from" },
  { key: "before", label: "before", kind: "date", param: "date_to" },
];

const state = {
  baseUrl: "",
  configured: false, // an engine has been chosen
  ready: false, // sidecar is up and able to ingest/search
  hardware: null,
  runtime: null,
  view: "gallery",
  chips: [], // {field, value}
  pendingField: null,
  facets: {},
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
};

async function getJSON(path) {
  const res = await fetch(state.baseUrl + path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function setStatus(msg) {
  $("#status").textContent = msg || "";
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let toastTimer;
function showToast(msg, opts = {}) {
  const t = $("#toast");
  t.querySelector(".toast-msg").textContent = msg;
  const bar = t.querySelector(".toast-bar");
  if (opts.progress == null) {
    bar.classList.add("hidden");
  } else {
    bar.classList.remove("hidden");
    bar.querySelector("i").style.width = Math.round(opts.progress * 100) + "%";
  }
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  if (opts.autohide) toastTimer = setTimeout(hideToast, opts.autohide);
}
function hideToast() {
  $("#toast").classList.add("hidden");
}

// ---------------------------------------------------------------------------
// Query construction
// ---------------------------------------------------------------------------
function currentParams() {
  const p = new URLSearchParams();
  const q = $("#q").value.trim();
  if (q && !state.pendingField) p.set("q", q);
  for (const chip of state.chips) {
    if (chip.field.kind === "multi") p.append(chip.field.param, chip.value);
    else p.set(chip.field.param, chip.value);
  }
  return p;
}

function hasAnyFilter() {
  const q = $("#q").value.trim();
  return (q && !state.pendingField) || state.chips.length > 0;
}

let refreshTimer;
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 320);
}

async function refresh() {
  if (state.view === "themes") return renderThemes();
  setStatus("Loading…");
  try {
    const data = hasAnyFilter()
      ? await getJSON("/search?" + currentParams().toString())
      : await getJSON("/gallery");
    renderGrid(data.photos, data.mode);
  } catch (err) {
    setStatus("Error: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// Chips
// ---------------------------------------------------------------------------
function addChip(field, value) {
  if (!value) return;
  if (field.kind === "single") {
    state.chips = state.chips.filter((c) => c.field.key !== field.key);
  } else if (state.chips.some((c) => c.field.key === field.key && c.value === value)) {
    return; // dedupe multi
  }
  state.chips.push({ field, value });
  renderChips();
}

function removeChip(idx) {
  state.chips.splice(idx, 1);
  renderChips();
  resetSearchField(); // back to the default search field (clears pending/placeholder/dropdown)
  refresh();
}

function renderChips() {
  const box = $("#chips");
  box.innerHTML = "";
  state.chips.forEach((chip, i) => {
    const c = el("span", "chip-token");
    c.append(`${chip.field.label}: ${chip.value}`);
    const x = el("button", "x");
    x.textContent = "×";
    x.onclick = (e) => {
      e.stopPropagation();
      removeChip(i);
    };
    c.appendChild(x);
    box.appendChild(c);
  });
}

// ---------------------------------------------------------------------------
// Suggestions popover
// ---------------------------------------------------------------------------
// Actionable suggestion rows for keyboard nav: [{el, action}], with a highlight cursor.
let suggestItems = [];
let activeIndex = -1;

function suggestOpen() {
  return !$("#suggest").classList.contains("hidden");
}

function hideSuggest() {
  $("#suggest").classList.add("hidden");
  suggestItems = [];
  activeIndex = -1;
}

function showSuggest() {
  $("#suggest").classList.remove("hidden");
}

function makeRow(box, action) {
  const row = el("div", "suggest-item");
  row.onmousedown = (e) => {
    e.preventDefault();
    action();
  };
  box.appendChild(row);
  suggestItems.push({ el: row, action });
  return row;
}

function setActive(i) {
  if (!suggestItems.length) return;
  activeIndex = (i + suggestItems.length) % suggestItems.length;
  suggestItems.forEach((it, idx) => it.el.classList.toggle("active", idx === activeIndex));
  suggestItems[activeIndex].el.scrollIntoView({ block: "nearest" });
}

function onInput() {
  if (state.pendingField) {
    renderValueSuggest(state.pendingField, $("#q").value);
    return; // value-stage typing filters the dropdown, not the query
  }
  renderFieldSuggest($("#q").value);
  refreshSoon(); // leftover words run semantic search
}

function renderFieldSuggest(text) {
  const token = text.trim().toLowerCase();
  const matches = token ? FIELDS.filter((f) => f.label.startsWith(token)) : FIELDS.slice();
  const box = $("#suggest");
  box.innerHTML = "";
  suggestItems = [];
  activeIndex = -1;
  if (matches.length) {
    const head = el("div", "suggest-head");
    head.textContent = "Filter by";
    box.appendChild(head);
    for (const f of matches) {
      const row = makeRow(box, () => pickField(f));
      row.innerHTML = `<b>${f.label}</b><span class="suggest-kind">${f.kind === "date" ? "date" : f.kind}</span>`;
    }
  }
  if (token && !FIELDS.some((f) => f.label === token)) {
    const hint = el("div", "suggest-hint");
    hint.textContent = `↵ Search “${text.trim()}”`;
    box.appendChild(hint);
  }
  if (box.children.length) showSuggest();
  else hideSuggest();
}

function pickField(field) {
  state.pendingField = field;
  const input = $("#q");
  input.value = "";
  input.placeholder = `${field.label}: ${field.kind === "date" ? "pick a date" : "type to filter…"}`;
  input.focus();
  renderValueSuggest(field, "");
}

function renderValueSuggest(field, filterText) {
  const box = $("#suggest");
  box.innerHTML = "";
  suggestItems = [];
  activeIndex = -1;
  const head = el("div", "suggest-head");
  head.textContent = field.label;
  box.appendChild(head);

  if (field.kind === "date") {
    const row = el("div", "suggest-item");
    const date = el("input");
    date.type = "date";
    date.className = "date-pick";
    date.onmousedown = (e) => e.stopPropagation();
    date.onchange = () => {
      if (date.value) {
        addChip(field, date.value);
        exitValueStage();
        refresh();
      }
    };
    row.appendChild(date);
    box.appendChild(row);
    showSuggest();
    setTimeout(() => date.focus(), 0);
    return;
  }

  const values = (state.facets[field.facet] || []).filter((v) =>
    v.toLowerCase().includes(filterText.trim().toLowerCase())
  );
  if (!values.length) {
    const empty = el("div", "suggest-hint");
    empty.textContent = "no values yet";
    box.appendChild(empty);
  }
  for (const v of values.slice(0, 60)) {
    const row = makeRow(box, () => selectValue(field, v));
    row.textContent = v;
  }
  showSuggest();
}

function selectValue(field, v) {
  addChip(field, v);
  if (field.kind === "multi") {
    $("#q").value = ""; // stay in value stage to add more
    renderValueSuggest(field, "");
    refresh();
  } else {
    exitValueStage();
    refresh();
  }
}

function resetSearchField() {
  state.pendingField = null;
  const input = $("#q");
  input.value = "";
  input.placeholder = "Search photos… or type a filter like “location”";
  hideSuggest();
}

function exitValueStage() {
  resetSearchField();
  $("#q").focus();
}

// ---------------------------------------------------------------------------
// Grid
// ---------------------------------------------------------------------------
function renderGrid(photos, mode) {
  const grid = $("#grid");
  grid.innerHTML = "";
  $("#themes").classList.add("hidden");
  grid.classList.remove("hidden");
  setStatus(`${photos.length} photo${photos.length === 1 ? "" : "s"}${mode ? " · " + mode : ""}`);

  // Empty library, no active filter — show the front door instead of a blank grid.
  if (!photos.length && !hasAnyFilter() && state.ready) {
    setStatus("");
    const empty = el("div", "empty");
    const msg = el("div", "empty-msg");
    msg.textContent = "No photos yet.";
    const hint = el("div", "hint");
    hint.textContent = "Drag photos or a folder here, or choose them.";
    const btn = el("button", "btn");
    btn.textContent = "Choose photos";
    btn.onclick = pickAndIngest;
    empty.append(msg, hint, btn);
    grid.appendChild(empty);
    return;
  }

  for (const ph of photos) {
    const card = el("div", "card");
    if (ph.src === null) card.classList.add("missing");
    const img = el("img");
    img.loading = "lazy";
    if (ph.thumb_url) img.src = state.baseUrl + ph.thumb_url;
    card.appendChild(img);
    if (ph.score != null) {
      const s = el("div", "score");
      s.textContent = ph.score.toFixed(2);
      card.appendChild(s);
    }
    const meta = el("div", "meta");
    const loc = el("div", "loc");
    loc.textContent = [ph.location, ph.scene].filter(Boolean).join(" · ") || ph.title || "";
    meta.appendChild(loc);
    card.appendChild(meta);
    card.onclick = () => openDetail(ph.id);
    grid.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// Facets (feed the value dropdowns)
// ---------------------------------------------------------------------------
async function loadFacets() {
  try {
    state.facets = await getJSON("/facets");
  } catch {
    state.facets = {};
  }
}

// ---------------------------------------------------------------------------
// Themes
// ---------------------------------------------------------------------------
async function renderThemes() {
  $("#grid").classList.add("hidden");
  const box = $("#themes");
  box.classList.remove("hidden");
  box.innerHTML = "";
  setStatus("Loading themes…");
  let data;
  try {
    data = await getJSON("/themes");
  } catch (err) {
    setStatus("Error: " + err.message);
    return;
  }
  if (!data.themes.length) {
    setStatus("No themes yet — add more photos and they'll group automatically.");
    return;
  }
  setStatus(`${data.themes.length} themes`);
  for (const t of data.themes) {
    const section = el("div", "theme-section");
    const head = el("div", "theme-head");
    head.innerHTML = `<b></b><span></span>`;
    head.querySelector("b").textContent = t.label;
    head.querySelector("span").textContent = t.count;
    section.appendChild(head);
    const strip = el("div", "theme-strip");
    for (const ph of t.photos) {
      const img = el("img");
      img.loading = "lazy";
      if (ph.thumb_url) img.src = state.baseUrl + ph.thumb_url;
      img.title = ph.title || "";
      img.onclick = () => openDetail(ph.id);
      strip.appendChild(img);
    }
    section.appendChild(strip);
    box.appendChild(section);
  }
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------
async function openDetail(id) {
  let ph;
  try {
    ph = await getJSON("/photo/" + id);
  } catch {
    return;
  }
  $("#d-img").src = state.baseUrl + "/image/" + id;
  $("#d-title").textContent = ph.title || "";
  $("#d-desc").textContent = ph.description || "";
  const attrs = $("#d-attrs");
  attrs.innerHTML = "";
  const add = (label) => {
    const c = el("div", "chip");
    c.textContent = label;
    attrs.appendChild(c);
  };
  if (ph.location) add("📍 " + ph.location);
  if (ph.scene) add("🎬 " + ph.scene);
  (ph.objects || []).forEach((o) => add(o));
  (ph.animals || []).forEach((a) => add("🐾 " + a));
  (ph.tags || []).forEach((t) => add("#" + t));
  if (ph.occurred_at) add("🕑 " + ph.occurred_at.slice(0, 10));
  $("#d-reveal").onclick = () => window.api.reveal(ph.src);

  const rel = $("#d-related");
  rel.innerHTML = "";
  try {
    const r = await getJSON("/related/" + id);
    for (const rp of r.photos) {
      const img = el("img");
      if (rp.thumb_url) img.src = state.baseUrl + rp.thumb_url;
      img.title = `${rp.shared_entities} shared`;
      img.onclick = () => openDetail(rp.id);
      rel.appendChild(img);
    }
  } catch {
    /* none */
  }
  $("#detail").classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Ingest (drag-drop only)
// ---------------------------------------------------------------------------
function goToGallery() {
  state.view = "gallery";
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === "gallery"));
}

const IMG_RE = /\.(jpe?g|png|webp|gif|bmp|tiff?)$/i;

function makeOptimisticCard(entry) {
  const card = el("div", "card scanning");
  card.dataset.path = entry.path;
  const img = el("img");
  img.src = URL.createObjectURL(entry.file);
  card.appendChild(img);
  const badge = el("div", "badge");
  badge.appendChild(el("div", "spinner"));
  card.appendChild(badge);
  const meta = el("div", "meta");
  const loc = el("div", "loc");
  loc.textContent = entry.file.name;
  meta.appendChild(loc);
  card.appendChild(meta);
  return card;
}

function finalizeCard(card, item) {
  card.classList.remove("scanning", "failed");
  const badge = card.querySelector(".badge");
  if (badge) badge.remove();
  if (item && item.doc_id) {
    card.dataset.docId = item.doc_id;
    card.onclick = () => openDetail(item.doc_id);
  }
}

function failCard(card, item) {
  card.classList.remove("scanning");
  card.classList.add("failed");
  const badge = card.querySelector(".badge");
  if (badge) {
    badge.innerHTML = "";
    const x = el("div", "x-mark");
    x.textContent = "✕";
    badge.appendChild(x);
  }
  if (item && item.error) card.title = item.error;
}

async function ingestDrop(entries) {
  if (!entries.length) {
    showToast("Couldn't read any photo paths from that drop.", { autohide: 5000 });
    return;
  }
  if (!state.ready) {
    openEngine(state.configured ? 2 : 1);
    return;
  }
  goToGallery();
  $("#themes").classList.add("hidden");
  const grid = $("#grid");
  grid.classList.remove("hidden");

  // Optimistic cards (the actual image via object URL) for dropped image files.
  // Dialog-picked entries have no File object, so they sync from the server instead.
  const cards = {};
  const imageEntries = entries.filter((e) => e.file && IMG_RE.test(e.path));
  for (const entry of imageEntries) {
    const card = makeOptimisticCard(entry);
    cards[entry.path] = card;
    grid.prepend(card);
  }

  showToast(`Adding ${entries.length} item(s)…`, { progress: 0 });
  let job;
  try {
    const res = await fetch(state.baseUrl + "/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: entries.map((e) => e.path) }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    job = await res.json();
  } catch (err) {
    showToast("Couldn't start ingest: " + err.message, {});
    Object.values(cards).forEach((c) => failCard(c, { error: err.message }));
    return;
  }
  const expandedBeyondOptimistic = (job.paths || []).length > imageEntries.length;

  while (true) {
    await sleep(700);
    let st;
    try {
      st = await getJSON("/ingest/status?job_id=" + job.job_id);
    } catch {
      break;
    }
    const items = st.items || {};
    for (const [path, card] of Object.entries(cards)) {
      const it = items[path];
      if (!it) continue;
      if (it.status === "done" || it.status === "skipped") finalizeCard(card, it);
      else if (it.status === "failed") failCard(card, it);
    }
    const handled = st.done + st.skipped + st.failed;
    const frac = st.total ? handled / st.total : 0;
    const bits = [`${st.done} added`];
    if (st.skipped) bits.push(`${st.skipped} skipped`);
    if (st.failed) bits.push(`${st.failed} failed`);
    showToast(`Scanning — ${handled}/${st.total} (${bits.join(", ")})`, { progress: frac });

    if (st.status === "done") {
      let msg = `Done — ${st.done} added`;
      if (st.skipped) msg += `, ${st.skipped} skipped`;
      if (st.failed) msg += `, ${st.failed} failed`;
      if (st.failed && st.errors && st.errors[0]) msg += ` · ${st.errors[0].error}`;
      // Keep failures on screen; auto-dismiss clean runs.
      showToast(msg, { progress: 1, autohide: st.failed ? 0 : 4000 });
      break;
    }
  }
  await loadFacets();
  // Folder drops expand into images we didn't show optimistically — sync from server.
  if (expandedBeyondOptimistic) await refresh();
}

// ---------------------------------------------------------------------------
// Pick photos (empty-state button + a path into ingest without drag-drop)
// ---------------------------------------------------------------------------
async function pickAndIngest() {
  if (!state.ready) return openEngine(state.configured ? 2 : 1);
  const paths = await window.api.pickPaths();
  if (!paths.length) return;
  // No File objects from the dialog, so no optimistic thumbnails — server syncs.
  ingestDrop(paths.map((p) => ({ path: p })));
}

// ---------------------------------------------------------------------------
// Onboarding / engine picker
// ---------------------------------------------------------------------------
function openEngine(step) {
  // Only allow closing once an engine already works.
  $(".onb-close").classList.toggle("hidden", !state.ready);
  gotoStep(step || 1);
  $("#onboarding").classList.remove("hidden");
}

function closeEngine() {
  $("#onboarding").classList.add("hidden");
}

function gotoStep(n) {
  document.querySelectorAll(".onb-step").forEach((s) => s.classList.toggle("hidden", Number(s.dataset.step) !== n));
  if (n === 2) {
    $("#onb-dl-status").textContent = "";
    renderEngineStep();
  }
}

// Reflect this machine's hardware + whether the local engine is present.
function renderEngineStep() {
  const hw = state.hardware || {};
  const warn = $("#onb-warn");
  if (hw.tier === "slow") {
    warn.textContent =
      "This machine has an Intel chip, so the models run on the CPU — describing photos will be slow, often a minute or more each. Apple Silicon is much faster.";
    warn.classList.remove("hidden");
  } else if (hw.tier === "lowram") {
    warn.textContent = `Ash works best with 8 GB of memory; this machine has ${hw.totalRamGB} GB. Ingest may be slow or stall.`;
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }
  const haveRuntime = state.runtime && state.runtime.available;
  $("#onb-engine-ready").classList.toggle("hidden", !haveRuntime);
  $("#onb-engine-missing").classList.toggle("hidden", haveRuntime);
}

function applyStatus(res) {
  state.configured = res.configured;
  state.ready = res.ready;
  state.hardware = res.hardware;
  state.runtime = res.runtime;
  if (res.baseUrl) state.baseUrl = res.baseUrl;
}

async function finishEngine(res, statusEl) {
  if (!res.ok || !res.ready) {
    if (statusEl) statusEl.textContent = "Couldn't start the engine. " + (res.error || "");
    return;
  }
  applyStatus(res);
  closeEngine();
  showToast("Model ready. Add your first photos.", { progress: 1, autohide: 4000 });
  await loadFacets();
  await refresh();
}

// Recommended path: download the local models, then start on them.
async function downloadLocal() {
  const dl = $("#onb-download");
  const status = $("#onb-dl-status");
  dl.disabled = true;
  status.textContent = "Starting download…";
  const off = window.api.onModelProgress((p) => {
    const pct = Math.round((p.fraction || 0) * 100);
    status.textContent = `Downloading ${p.model} — ${pct}%`;
    showToast(`Downloading ${p.model} — ${pct}%`, { progress: p.fraction || 0 });
  });
  let res;
  try {
    res = await window.api.downloadModels();
  } finally {
    off();
  }
  hideToast();
  dl.disabled = false;
  if (!res.ok) {
    status.textContent = /installed|running|available/.test(res.error || "")
      ? "The local engine isn't available. Reinstall Ash, or check the logs."
      : "Download stopped. " + res.error;
    return;
  }
  status.textContent = "Setting up…";
  await finishEngine(await window.api.engineSet(), status);
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
function wire() {
  const input = $("#q");
  input.addEventListener("input", onInput);
  input.addEventListener("focus", () => {
    if (state.pendingField) renderValueSuggest(state.pendingField, input.value);
    else renderFieldSuggest(input.value);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && suggestOpen() && suggestItems.length) {
      e.preventDefault();
      setActive(activeIndex < 0 ? 0 : activeIndex + 1);
    } else if (e.key === "ArrowUp" && suggestOpen() && suggestItems.length) {
      e.preventDefault();
      setActive(activeIndex < 0 ? suggestItems.length - 1 : activeIndex - 1);
    } else if (e.key === "Enter") {
      if (activeIndex >= 0 && suggestItems[activeIndex]) {
        e.preventDefault();
        suggestItems[activeIndex].action();
      } else if (!state.pendingField) {
        hideSuggest();
        refresh();
      }
    } else if (e.key === "Escape") {
      if (state.pendingField) exitValueStage();
      else hideSuggest();
    } else if (e.key === "Backspace" && !input.value && !state.pendingField && state.chips.length) {
      removeChip(state.chips.length - 1);
    }
  });
  $("#searchbar").addEventListener("click", () => input.focus());

  // Hide popover when clicking outside the search area.
  document.addEventListener("mousedown", (e) => {
    if (!e.target.closest(".searchwrap")) hideSuggest();
  });

  document.querySelectorAll(".tab").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.view = b.dataset.view;
      refresh();
    };
  });

  $("#settings-btn").onclick = () => openEngine(state.configured ? 2 : 1);
  document.querySelectorAll("[data-close]").forEach((b) => {
    b.onclick = () => b.closest(".modal").classList.add("hidden");
  });

  // Onboarding / engine setup.
  $("#onb-next").onclick = () => gotoStep(2);
  $("#onb-download").onclick = downloadLocal;
  $(".onb-close").onclick = closeEngine;
  document.querySelectorAll(".modal").forEach((m) => {
    m.onclick = (e) => {
      if (e.target === m) m.classList.add("hidden");
    };
  });

  // Drag & drop ingest (whole window).
  const hint = $("#drop-hint");
  window.addEventListener("dragover", (e) => {
    e.preventDefault();
    hint.classList.remove("hidden");
  });
  window.addEventListener("dragleave", (e) => {
    if (e.relatedTarget === null) hint.classList.add("hidden");
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    hint.classList.add("hidden");
    const files = [...e.dataTransfer.files];
    const entries = files
      .map((f) => ({ file: f, path: window.api.pathForFile(f) }))
      .filter((x) => x.path);
    if (files.length && !entries.length) {
      showToast(`Dropped ${files.length} file(s) but couldn't resolve their paths on disk.`, { autohide: 6000 });
      return;
    }
    ingestDrop(entries);
  });

  $("#toast").addEventListener("click", hideToast);
}

async function init() {
  wire();
  const cfg = await window.api.getConfig();
  applyStatus(cfg);
  if (!state.ready) {
    // First run (no engine) or engine configured but not up yet — onboard / repair.
    openEngine(state.configured ? 2 : 1);
    return;
  }
  await loadFacets();
  await refresh();
}

init();

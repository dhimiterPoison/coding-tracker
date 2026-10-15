"use strict";
/* Coding Diary dashboard. No frameworks, no network calls except to this local server. */

const $ = (s, r = document) => r.querySelector(s);
const SVGNS = "http://www.w3.org/2000/svg";

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
// Like el.append, but skips null/false (Element.append would print the word "null").
function put(el, ...kids) {
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

// ───────── state ─────────
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("cd." + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("cd." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const state = {
  view: store.get("view", "overview"),
  preset: store.get("preset", "7"),
  from: store.get("from", ""), to: store.get("to", ""),
  cat: store.get("cat", "all"),
  cfg: { work_types: [] },
  filters: { q: "", type: "", project: "" },
  report: store.get("report", "refinement"),
  day: "",            // selected day on the Day tab (YYYY-MM-DD); empty means today
  editingDay: false,  // reflection form open on an already-closed day
};

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function range() {
  const end = new Date();
  if (state.preset === "custom" && state.from && state.to) return { start: state.from, end: state.to };
  const n = Number(state.preset) || 7;
  const start = new Date(); start.setDate(end.getDate() - (n - 1));
  return { start: iso(start), end: iso(end) };
}
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null)).toString();

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", "X-Tracker": "1", ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

// ───────── formatting ─────────
function dur(s) {
  s = Math.round(s || 0);
  if (s < 60) return s ? `${s}s` : "0m";
  const m = Math.round(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
}
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const hhmm = (isoStr) => new Date(isoStr).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayLabel = (d) => new Date(d + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
const typeColor = (t) => `var(--t-${t in TYPE_SET ? t : "other"})`;
const TYPE_SET = Object.fromEntries(["feature", "bugfix", "refactor", "testing", "docs", "ui-design", "devops-config", "exploration", "review", "planning", "other"].map((t) => [t, 1]));
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2600); }
function typePill(t) { return h("span", { class: "pill" }, h("i", { style: { background: typeColor(t) } }), t); }
function focusPill(s) {
  const cls = { "deep flow": "deep", steady: "steady", scattered: "scattered", "too short": "short" }[s.focus_label];
  return h("span", { class: `pill focus-${cls}`, title: s.focus == null ? "Under 5 min of activity" : `Focus score ${s.focus}/100` }, s.focus_label);
}
function delta(cur, prev, { good = "up", fmt = (x) => x, unit = "" } = {}) {
  if (!prev) return h("div", { class: "d" }, " ");
  const diff = cur - prev;
  if (Math.abs(diff) < 1e-9 || /^0(\.0+)?$/.test(String(fmt(Math.abs(diff))))) return h("div", { class: "d" }, "same as previous");
  const up = diff > 0; const ok = (good === "up") === up;
  return h("div", { class: `d ${up ? "up" : "down"}-${ok ? "good" : "bad"}` }, `${up ? "▲" : "▼"} ${fmt(Math.abs(diff))}${unit} vs previous`);
}

// ───────── charts ─────────
function stackedDays(days, r) {
  // fill empty days so gaps are visible
  const map = Object.fromEntries(days.map((d) => [d.day, d]));
  const all = [];
  const a = new Date(r.start + "T12:00:00"), b = new Date(r.end + "T12:00:00");
  if ((b - a) / 864e5 <= 62) for (let d = new Date(a); d <= b; d.setDate(d.getDate() + 1)) all.push(map[iso(d)] || { day: iso(d), active_s: 0, by_type: {}, flow_s: 0 });
  else all.push(...days);
  const W = 640, H = 220, L = 34, B = 26, T = 8;
  const max = Math.max(3600, ...all.map((d) => d.active_s));
  const niceMax = Math.ceil(max / 3600) * 3600;
  const bw = (W - L) / all.length;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", "aria-label": "Active time per day, stacked by type of work" });
  for (let i = 0; i <= 4; i++) {
    const y = T + (H - B - T) * (1 - i / 4);
    root.append(svg("line", { class: "grid-line", x1: L, x2: W, y1: y, y2: y }), svg("text", { x: L - 6, y: y + 4, "text-anchor": "end" }, `${Math.round((niceMax * i) / 4 / 3600 * 10) / 10}h`));
  }
  all.forEach((d, i) => {
    let y = H - B; const x = L + i * bw + bw * 0.14, w = bw * 0.72;
    const g = svg("g", {}, svg("title", {}, `${dayLabel(d.day)}: ${dur(d.active_s)} of session time (${dur(d.clock_s || 0)} on the clock), ${dur(d.flow_s)} deep flow`));
    for (const [t, s] of Object.entries(d.by_type || {}).sort((p, q) => q[1] - p[1])) {
      const hh = (s / niceMax) * (H - B - T); y -= hh;
      g.append(svg("rect", { x, y, width: w, height: Math.max(hh, 0.5), fill: typeColor(t), rx: 2 }));
    }
    root.append(g);
    if (all.length <= 14 || i % Math.ceil(all.length / 12) === 0) root.append(svg("text", { x: x + w / 2, y: H - 8, "text-anchor": "middle" }, all.length <= 8 ? dayLabel(d.day).split(",")[0] + " " + d.day.slice(8) : d.day.slice(5)));
  });
  return root;
}
function hourChart(byHour) {
  const W = 640, H = 150, L = 8, B = 22, T = 6;
  const max = Math.max(60, ...byHour.active);
  const bw = (W - L) / 24;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", "aria-label": "Active and deep-flow minutes by hour of day" });
  byHour.active.forEach((s, i) => {
    const x = L + i * bw + 2, w = bw - 4, ha = (s / max) * (H - B - T), hf = (byHour.flow[i] / max) * (H - B - T);
    root.append(svg("g", {}, svg("title", {}, `${String(i).padStart(2, "0")}:00 · ${dur(s)} active · ${dur(byHour.flow[i])} deep flow`),
      svg("rect", { x, y: H - B - ha, width: w, height: ha, rx: 2, fill: "var(--surface-2)", stroke: "var(--line)" }),
      svg("rect", { x, y: H - B - hf, width: w, height: hf, rx: 2, fill: "var(--accent)" })));
    if (i % 3 === 0) root.append(svg("text", { x: x + w / 2, y: H - 6, "text-anchor": "middle" }, String(i).padStart(2, "0")));
  });
  return root;
}
function barList(items, max, color) {
  return h("div", {}, items.map((it) => h("div", { class: "hbar" },
    h("span", { class: "name", title: it.name }, it.name),
    h("span", { class: "track" }, h("span", { class: "fill", style: { width: `${Math.max(2, (it.value / max) * 100)}%`, background: it.color || color } })),
    h("span", { class: "val" }, it.label))));
}
function legend(types) {
  return h("div", { class: "legend" }, types.map((t) => h("span", {}, h("i", { class: "dot", style: { background: typeColor(t) } }), t)));
}

// ───────── views ─────────
async function viewOverview() {
  const r = range();
  const [d, morning] = await Promise.all([
    api(`/api/overview?${qs({ ...r, category: state.cat })}`),
    api("/api/morning").catch(() => null),
  ]);
  const T = d.summary.totals, P = d.previous;
  const root = h("div", {});
  root.append(h("h1", {}, "Overview"), h("p", { class: "sub" }, `${r.start} to ${r.end} · compared with the ${Math.round((new Date(r.end) - new Date(r.start)) / 864e5) + 1} days before`));
  const mc = morningCard(morning); if (mc) root.append(mc);
  if (!T.sessions) { root.append(h("div", { class: "card empty" }, "No sessions in this range. Pick a longer range, press Sync, or log a session manually.")); return root; }
  root.append(h("div", { class: "kpis" },
    kpi("Time on the clock", dur(T.clock_s), delta(T.clock_s, P.clock_s, { fmt: dur }), T.parallel_s > 600 ? `${dur(T.active_s)} of session time (${dur(T.parallel_s)} ran in parallel)` : ""),
    kpi("Deep flow", pct(T.flow_ratio), delta(T.flow_ratio, P.flow_ratio, { fmt: (x) => Math.round(x * 100), unit: " pts" })),
    kpi("Sessions", T.sessions, delta(T.sessions, P.sessions)),
    kpi("Project switches / day", T.switches_per_day, delta(T.switches_per_day, P.switches_per_day, { good: "down", fmt: (x) => x.toFixed(1) })),
    kpi("Interruptions / hour", T.interruptions_per_hour, delta(T.interruptions_per_hour, P.interruptions_per_hour, { good: "down", fmt: (x) => x.toFixed(1) })),
    kpi("Commits", T.commits, delta(T.commits, P.commits)),
  ));
  root.append(h("section", { class: "card", style: { marginBottom: "16px" } }, h("h2", {}, "Coach"),
    h("ul", { class: "coach" }, d.coach.map((c) => h("li", { class: c.kind }, h("span", { class: "badge" }, { win: "Going well", watch: "Watch", tip: "Try" }[c.kind]), h("span", {}, c.text))))));
  const types = Object.keys(d.summary.by_type);
  root.append(h("div", { class: "grid cols-2", style: { marginBottom: "16px" } },
    h("section", { class: "card" }, h("h2", {}, "Session time per day"), stackedDays(d.summary.by_day, r), legend(types)),
    h("section", { class: "card" }, h("h2", {}, "When you focus"), hourChart(d.summary.by_hour),
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "dot", style: { background: "var(--accent)" } }), "deep flow"), h("span", {}, h("i", { class: "dot", style: { background: "var(--surface-2)", border: "1px solid var(--line)" } }), "any activity")))));
  const maxT = Math.max(...Object.values(d.summary.by_type));
  const maxP = Math.max(...d.summary.by_project.map((p) => p.active_s));
  root.append(h("div", { class: "grid cols-2" },
    h("section", { class: "card" }, h("h2", {}, "Type of work"), barList(Object.entries(d.summary.by_type).map(([t, v]) => ({ name: t, value: v, label: dur(v), color: typeColor(t) })), maxT)),
    h("section", { class: "card" }, h("h2", {}, "Time by project"), barList(d.summary.by_project.slice(0, 10).map((p) => ({ name: p.project, value: p.active_s, label: dur(p.active_s) })), maxP, "var(--accent)"))));
  return root;
}
const kpi = (l, v, d, note) => h("div", { class: "kpi" }, h("div", { class: "l" }, l), h("div", { class: "v" }, String(v)), d, note ? h("div", { class: "d" }, note) : null);

async function viewSessions() {
  const r = range();
  const f = state.filters;
  const rows = await api(`/api/sessions?${qs({ ...r, category: state.cat, type: f.type, project: f.project, q: f.q })}`);
  const all = f.project || f.type || f.q ? null : rows;
  if (all) state.projectsSeen = [...new Set(all.map((x) => x.project))].sort();
  const root = h("div", {}, h("h1", {}, "Sessions"), h("p", { class: "sub" }, `${rows.length} in range. Click one for the story, then correct the type or add a note.`));
  const q = h("input", { type: "search", placeholder: "Search titles, prompts, notes", value: f.q, "aria-label": "Search sessions" });
  q.addEventListener("change", () => { f.q = q.value; render(); });
  const ty = h("select", { "aria-label": "Type" }, h("option", { value: "" }, "All types"), state.cfg.work_types.map((t) => h("option", { value: t, selected: t === f.type }, t)));
  ty.addEventListener("change", () => { f.type = ty.value; render(); });
  const pr = h("select", { "aria-label": "Project" }, h("option", { value: "" }, "All projects"), (state.projectsSeen || []).map((p) => h("option", { value: p, selected: p === f.project }, p)));
  pr.addEventListener("change", () => { f.project = pr.value; render(); });
  root.append(h("div", { class: "filters" }, q, ty, pr));
  if (!rows.length) { root.append(h("div", { class: "card empty" }, "Nothing matches.")); return root; }
  const body = h("tbody", {});
  let cur = "";
  for (const s of rows) {
    if (s.day !== cur) {
      cur = s.day;
      const dayTotal = rows.filter((x) => x.day === cur).reduce((a, x) => a + x.active_s, 0);
      body.append(h("tr", { class: "day-head" }, h("td", { colspan: 6 }, `${dayLabel(cur)} · ${dur(dayTotal)}`)));
    }
    body.append(h("tr", { tabindex: 0, onclick: () => openSession(s.id), onkeydown: (e) => e.key === "Enter" && openSession(s.id) },
      h("td", {}, h("span", { class: "title", title: s.title }, s.title), h("span", { class: "meta" }, `${s.project}${s.branch ? " · " + s.branch : ""} · ${hhmm(s.started_at)}${s.source === "manual" ? " · manual" : ""}`)),
      h("td", {}, typePill(s.type)),
      h("td", { class: "num" }, dur(s.active_s)),
      h("td", {}, focusPill(s)),
      h("td", { class: "meta" }, flags(s)),
      h("td", { class: "num" }, s.rating ? "★".repeat(s.rating) : "")));
  }
  root.append(h("div", { class: "card", style: { padding: "6px 10px" } }, h("table", {},
    h("thead", {}, h("tr", {}, ["Session", "Type", "Active", "Focus", "Signals", "Self"].map((c, i) => h("th", { class: i === 2 || i === 5 ? "num" : "" }, c)))), body)));
  return root;
}
function flags(s) {
  const out = [];
  if (s.prompts) out.push(`${s.prompts} prompts`);
  if (s.interruptions + s.rejections) out.push(`${s.interruptions + s.rejections} interrupts`);
  if (s.topic_shifts) out.push(`${s.topic_shifts} topic shifts`);
  if (s.commit_count) out.push(`${s.commit_count} commits`);
  return out.join(" · ");
}

async function openSession(id) {
  const s = await api(`/api/sessions/${encodeURIComponent(id)}`);
  const dr = $("#drawer"); dr.hidden = false; dr.replaceChildren();
  const close = () => { dr.hidden = true; };
  const save = async (patch, msg) => { await api(`/api/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: patch }); toast(msg || "Saved"); };
  const ratingBox = h("span", { class: "stars", role: "group", "aria-label": "Self-rated focus" });
  const paintStars = (n) => ratingBox.replaceChildren(...[1, 2, 3, 4, 5].map((i) => h("button", { class: i <= n ? "on" : "", "aria-label": `${i} of 5`, onclick: async () => { await save({ rating: i }); s.rating = i; paintStars(i); } }, "★")));
  paintStars(s.rating || 0);
  const typeSel = h("select", { "aria-label": "Type of work" }, state.cfg.work_types.map((t) => h("option", { value: t, selected: t === s.type }, t)));
  typeSel.addEventListener("change", async () => { await save({ type_override: typeSel.value }, "Type updated"); });
  const notes = h("textarea", { rows: 4, placeholder: "What mattered here? Decisions, blockers, what to tell the team…" }, s.notes || "");
  notes.addEventListener("blur", () => { if (notes.value !== (s.notes || "")) { s.notes = notes.value; save({ notes: notes.value }); } });
  const sc = Object.entries(s.work_scores || {}).slice(0, 3).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(", ");
  dr.append(
    h("button", { class: "ghost close", onclick: close, "aria-label": "Close" }, "✕"),
    h("h1", { style: { paddingRight: "40px" } }, s.title),
    h("div", { class: "meta sub" }, `${s.project}${s.branch ? " · " + s.branch : ""} · ${dayLabel(s.day)} ${hhmm(s.started_at)}–${hhmm(s.ended_at)}${s.models ? " · " + s.models : ""}`),
    h("div", { class: "row" }, focusPill(s), h("span", {}, "Type:"), typeSel, h("span", { class: "tag", title: "How the type was guessed" }, sc ? `guess: ${sc}` : "")),
    h("div", { class: "stats" },
      stat(dur(s.active_s), "active"), stat(dur(s.flow_s), "deep flow"), stat(dur(s.longest_block_s), "longest block"),
      stat(s.prompts, "prompts"), stat(s.interruptions + s.rejections, "interruptions"), stat(s.topic_shifts + s.area_switches, "context switches"),
      stat(s.edits, "edits"), stat(s.tests_run, "test runs"), stat(s.commit_count, "commits")),
    h("div", { class: "row" }, h("span", {}, "How did it feel?"), ratingBox),
    h("label", { class: "f" }, "Notes (private)", notes));
  if (s.commits.length) dr.append(h("h2", { style: { marginTop: "18px" } }, "Shipped"), h("ul", {}, s.commits.map((c) => h("li", {}, c.msg))));
  if (s.prompt_list.length) dr.append(h("h2", { style: { marginTop: "18px" } }, "What you asked for"),
    h("ul", { class: "tl" }, s.prompt_list.map((p) => h("li", {}, h("time", {}, hhmm(p.t)), p.text))));
  if (s.last_assistant) dr.append(h("h2", { style: { marginTop: "18px" } }, "Where it ended"), h("div", { class: "left-off" }, s.last_assistant));
  if (s.files.length) dr.append(h("h2", { style: { marginTop: "18px" } }, "Files touched"), h("div", { class: "meta" }, s.files.slice(0, 15).join(" · ")));
  const acts = h("div", { class: "row", style: { marginTop: "20px" } });
  if (s.source === "manual") acts.append(h("button", { class: "danger", onclick: async () => { if (confirm("Delete this manual entry?")) { await api(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }); close(); render(); } } }, "Delete entry"));
  else acts.append(h("button", { class: "danger", onclick: async () => { await save({ hidden: true }, "Hidden from stats"); close(); render(); } }, "Hide from stats"));
  dr.append(acts);
  dr.focus();
}
const stat = (v, l) => h("div", {}, h("b", {}, String(v)), h("span", {}, l));

async function viewProjects() {
  const ps = await api(`/api/projects?${qs({ category: state.cat })}`);
  const root = h("div", {}, h("h1", {}, "Projects"), h("p", { class: "sub" }, "Every repository you touched, with where you left off. Skim this before standups and refinement."));
  if (!ps.length) { root.append(h("div", { class: "card empty" }, "No projects yet.")); return root; }
  const grid = h("div", { class: "grid cols-2" });
  for (const p of ps) {
    const cat = h("select", { "aria-label": `Category of ${p.name}` }, ["work", "personal"].map((c) => h("option", { value: c, selected: c === p.category }, c)));
    cat.addEventListener("change", async () => { await api(`/api/projects/${encodeURIComponent(p.name)}`, { method: "PATCH", body: { category: cat.value } }); toast(`${p.name} is now ${cat.value}`); });
    grid.append(h("section", { class: "card proj" },
      h("div", { class: "top" }, h("h2", {}, p.name), cat, h("span", { class: p.days_ago > 14 ? "stale" : "meta" }, p.days_ago === 0 ? "active today" : `${p.days_ago}d ago`)),
      h("div", { class: "meta" }, `${p.sessions_30d} sessions / ${dur(p.active_s_30d)} in 30 days · ${p.total_sessions} overall${p.branch ? " · last branch " + p.branch : ""}`),
      h("div", { class: "left-off" }, h("small", {}, "Where you left off"), p.left_off.last_prompt || p.left_off.title,
        p.left_off.last_assistant ? h("div", { class: "meta", style: { marginTop: "6px" } }, "→ " + p.left_off.last_assistant) : null),
      h("ul", { class: "tl" }, p.recent_titles.map((t) => h("li", {}, h("time", {}, t.day.slice(5)), t.title)))));
  }
  root.append(grid);
  return root;
}

async function viewReports() {
  const kinds = { refinement: "Weekly refinement", retro: "Sprint retro" };
  const r = range();
  const useCustom = state.preset === "custom";
  const defaults = { refinement: state.cfg.refinement_length_days, retro: state.cfg.sprint_length_days };
  const e = new Date(), s = new Date(); s.setDate(e.getDate() - (defaults[state.report] - 1));
  const rr = useCustom ? r : { start: iso(s), end: iso(e) };
  const rep = await api(`/api/report/${state.report}?${qs({ ...rr, category: state.cat })}`);
  const root = h("div", {}, h("h1", {}, "Meeting prep"),
    h("p", { class: "sub" }, `Auto-drafted from your sessions (${rr.start} to ${rr.end}, ${state.cat} projects). Choose Work above to prep for the job; pick Custom for another window.`));
  const tabs = h("div", { class: "seg" }, Object.entries(kinds).map(([k, l]) => h("button", { "aria-pressed": String(k === state.report), onclick: () => { state.report = k; store.set("report", k); render(); } }, l)));
  const copy = h("button", { class: "primary", onclick: async () => { try { await navigator.clipboard.writeText(rep.markdown); toast("Markdown copied"); } catch { toast("Copy blocked by the browser"); } } }, "Copy as Markdown");
  root.append(h("div", { class: "row" }, tabs, h("span", { class: "spacer" }), copy));
  const body = h("div", { class: "card report" }, h("h2", {}, rep.title));
  for (const sec of rep.sections) body.append(h("h3", {}, sec.title), h("ul", {}, sec.items.map((i) => h("li", {}, i))));
  root.append(body);
  return root;
}

// ───────── goals, day, morning ─────────
const GSTAT = { met: ["✓", "Met"], partly: ["◐", "Partly"], missed: ["○", "Not today"], na: ["–", "n/a"] };
const addDays = (isoStr, n) => { const d = new Date(isoStr + "T12:00:00"); d.setDate(d.getDate() + n); return iso(d); };
const goalDots = (dots) => h("span", { class: "gdots", role: "img", "aria-label": dots.length ? dots.map((x) => x.status).join(", ") : "no judged days yet" },
  dots.length ? dots.map((x) => h("i", { class: `g g-${x.status}`, title: `${dayLabel(x.day)}: ${GSTAT[x.status][1]}` })) : h("span", { class: "meta" }, "no judged days yet"));
const gchip = (status) => h("span", { class: `gchip gc-${status}` }, `${GSTAT[status][0]} ${GSTAT[status][1]}`);

function morningCard(m) {
  if (!m || (!m.steps.length && !m.goals.length) || store.get("morning-" + iso(new Date()), false)) return null;
  const when = m.from_day ? (m.days_ago === 1 ? "yesterday" : m.days_ago === 0 ? "today" : dayLabel(m.from_day)) : "";
  const body = h("section", { class: "card morning" },
    h("div", { class: "row", style: { margin: "0 0 8px" } },
      h("h2", { style: { margin: 0 } }, "Pick up where you left off"),
      m.steps.length ? h("span", { class: "tag" }, m.source === "you" ? `your next steps · ${when}` : `last prompts · ${when}`) : null,
      h("span", { class: "spacer" }),
      m.from_day ? h("button", { class: "ghost", onclick: () => { state.day = m.from_day; state.view = "day"; store.set("view", "day"); render(); } }, "Open that day") : null,
      h("button", { class: "ghost", onclick: () => { store.set("morning-" + iso(new Date()), true); body.remove(); } }, "Hide for today")));
  if (m.steps.length) body.append(h("ul", { class: "steps" }, m.steps.map((s) => h("li", {},
    h("b", {}, s.project), " ", s.text, s.worked_since ? h("span", { class: "tag", style: { marginLeft: "8px" } }, "worked on since") : null))));
  if (m.goals.length) body.append(h("div", { class: "goalrow" }, m.goals.map((g) => h("span", { class: "goalmini", title: g.title },
    h("span", { class: "gname" }, g.title), goalDots(g.dots), h("span", { class: "meta" }, `${g.met}/${g.target}`)))));
  return body;
}

function projectColor(name, order) { return `var(--p${(order.indexOf(name) % 8) + 1})`; }
function dayTimeline(rev) {
  const mins = (s) => { const [a, b] = s.split(":").map(Number); return a * 60 + b; };
  const items = rev.stretches.map((s) => { const a = mins(s.start); let b = mins(s.end); if (b <= a) b += 1440; return { ...s, a, b }; });
  if (!items.length) return null;
  const projects = [...new Set(items.map((s) => s.project))];
  const lo = Math.max(0, Math.floor(Math.min(...items.map((s) => s.a)) / 60) * 60);
  const hi = Math.min(1440, Math.max(lo + 360, Math.ceil(Math.max(...items.map((s) => s.b)) / 60) * 60));
  const L = 120, W = 640, laneH = 24, gap = 8, T = 6;
  const H = T + projects.length * (laneH + gap) + 40;
  const x = (m) => L + ((m - lo) / (hi - lo)) * (W - L - 8);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", "aria-label": "Timeline of focus stretches per project" });
  const step = hi - lo > 720 ? 180 : 60;
  for (let m = lo; m <= hi; m += step) root.append(svg("line", { class: "grid-line", x1: x(m), x2: x(m), y1: T, y2: H - 30 }), svg("text", { x: x(m), y: H - 14, "text-anchor": "middle" }, `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:00`));
  projects.forEach((p, i) => {
    const y = T + i * (laneH + gap);
    root.append(svg("text", { x: L - 8, y: y + laneH / 2 + 4, "text-anchor": "end" }, p.length > 16 ? p.slice(0, 15) + "…" : p));
    root.append(svg("rect", { x: L, y, width: W - L - 8, height: laneH, rx: 4, fill: "var(--surface-2)", opacity: 0.55 }));
  });
  for (const s of items) {
    const y = T + projects.indexOf(s.project) * (laneH + gap);
    root.append(svg("g", {}, svg("title", {}, `${s.start}–${s.end} · ${s.project} · ${s.minutes}m · ${s.prompts} prompts${s.interruptions ? " · " + s.interruptions + " interruptions" : ""}\n${s.title}`),
      svg("rect", { x: x(s.a), y, width: Math.max(2, x(s.b) - x(s.a)), height: laneH, rx: 4, fill: projectColor(s.project, projects) })));
  }
  for (const b of rev.interruption_bursts) {
    const y = T + Math.max(0, projects.indexOf(b.project)) * (laneH + gap);
    root.append(svg("g", {}, svg("title", {}, `${b.count} interruptions ${b.start}–${b.end}`), svg("rect", { x: x(mins(b.start)), y: y - 3, width: Math.max(4, x(mins(b.end)) - x(mins(b.start))), height: laneH + 6, rx: 4, fill: "none", stroke: "var(--watch)", "stroke-width": 2 })));
  }
  const cy = T + projects.length * (laneH + gap) + 4;
  root.append(svg("text", { x: L - 8, y: cy + 4, "text-anchor": "end" }, "commits"));
  for (const c of rev.commits) root.append(svg("g", {}, svg("title", {}, `${c.time} [${c.project}] ${c.msg}`), svg("circle", { cx: x(mins(c.time)), cy, r: 4, fill: projectColor(c.project, projects) })));
  const wrap = h("div", {}, root, h("div", { class: "legend" }, projects.map((p) => h("span", {}, h("i", { class: "dot", style: { background: projectColor(p, projects) } }), p)),
    rev.interruption_bursts.length ? h("span", {}, h("i", { class: "dot", style: { background: "none", border: "2px solid var(--watch)" } }), "interruption burst") : null));
  return wrap;
}

function dayStrip(days, selected, today) {
  const max = Math.max(3600, ...days.map((d) => d.clock_s));
  const el = h("div", { class: "strip", role: "group", "aria-label": "Recent days" }, days.map((d) => h("button", {
    class: "chip" + (d.day === selected ? " sel" : "") + (d.closed ? " closed" : ""), "aria-pressed": String(d.day === selected),
    title: `${dayLabel(d.day)} · ${dur(d.clock_s)} on the clock${d.closed ? " · closed" : ""}${d.win ? " · " + d.win : ""}`,
    onclick: () => { state.day = d.day; state.editingDay = false; render(); } },
    h("span", { class: "dow" }, new Date(d.day + "T12:00:00").toLocaleDateString([], { weekday: "short" })),
    h("span", { class: "dnum" }, String(Number(d.day.slice(8)))),
    h("span", { class: "bar" }, h("i", { style: { height: `${Math.round((d.clock_s / max) * 100)}%` } })),
    h("span", { class: "mark" }, d.closed ? "✓" + (d.feeling ? " " + d.feeling : "") : d.day === today ? "•" : " "))));
  requestAnimationFrame(() => { const s = el.querySelector(".sel"); if (s) el.scrollLeft = s.offsetLeft - el.clientWidth / 2 + s.offsetWidth / 2; });
  return el;
}

async function viewDay() {
  const today = iso(new Date());
  const day = state.day || today;
  const start = addDays(today, -13) < addDays(day, -3) ? addDays(today, -13) : addDays(day, -3);
  const [rev, strip] = await Promise.all([
    api(`/api/day/${day}?${qs({ category: state.cat })}`),
    api(`/api/days?${qs({ start, end: today > day ? today : day, category: state.cat })}`),
  ]);
  const closed = rev.closed;
  const root = h("div", {});
  const nav = (n) => h("button", { class: "ghost", "aria-label": n < 0 ? "Previous day" : "Next day", onclick: () => { state.day = addDays(day, n); state.editingDay = false; render(); } }, n < 0 ? "‹" : "›");
  root.append(h("div", { class: "row", style: { margin: "0 0 4px" } }, h("h1", { style: { margin: 0 } }, new Date(day + "T12:00:00").toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })),
    nav(-1), day < today ? nav(1) : null,
    closed ? h("span", { class: "gchip gc-met" }, `✓ Closed ${hhmm(closed.closed_at)}`) : h("span", { class: "gchip gc-na" }, day === today ? "Open · still going" : "Not closed")));
  root.append(h("p", { class: "sub" }, "Your day in one page: what happened, where flow held and broke, how your goals did, and what to pick up tomorrow."), dayStrip(strip, day, today));
  if (rev.empty) { root.append(h("div", { class: "card empty", style: { marginTop: "16px" } }, "No sessions recorded for this day. Press Sync, or log work Claude did not see.")); return root; }
  const T = rev.totals, ds = rev.day_stats;
  root.append(h("div", { class: "kpis", style: { marginTop: "16px" } },
    kpi("On the clock", dur(T.clock_s), h("div", { class: "d" }, `${rev.span.start}–${rev.span.end}`)),
    kpi("Focus stretches", rev.focus_stretches, h("div", { class: "d" }, rev.longest_stretch ? `longest ${dur(rev.longest_stretch.minutes * 60)}` : "none yet")),
    kpi("Project switches", ds.switches, h("div", { class: "d" }, rev.baseline ? `usual ${Math.round(rev.baseline.switches)}` : " ")),
    kpi("Interruptions", ds.interruptions, h("div", { class: "d" }, " ")),
    kpi("Commits", T.commits, h("div", { class: "d" }, `${T.sessions} sessions`))));
  const tl = dayTimeline(rev);
  if (tl) root.append(h("section", { class: "card", style: { marginBottom: "16px" } }, h("h2", {}, "Timeline"), tl));
  root.append(h("div", { class: "grid cols-2", style: { marginBottom: "16px" } },
    h("section", { class: "card" }, h("h2", {}, "Coach"), rev.findings.length ? h("ul", { class: "coach" }, rev.findings.map((c) => h("li", { class: c.kind }, h("span", { class: "badge" }, { win: "Going well", watch: "Watch", tip: "Try" }[c.kind]), h("span", {}, c.text)))) : h("p", { class: "meta" }, "Nothing stood out today.")),
    h("section", { class: "card" }, h("h2", {}, "Goals"), goalsList(rev.goals))));
  root.append(h("div", { class: "grid cols-2", style: { marginBottom: "16px" } },
    h("section", { class: "card" }, h("h2", {}, "Shipped"), rev.commits.length ? h("ul", { class: "plain" }, rev.commits.map((c) => h("li", {}, h("span", { class: "meta" }, `${c.time} `), h("b", {}, `[${c.project}] `), c.msg))) : h("p", { class: "meta" }, "No commits recorded.")),
    h("section", { class: "card" }, h("h2", {}, "Where you left off"), h("ul", { class: "plain" }, Object.entries(rev.left_off).map(([p, lo]) => h("li", {}, h("b", {}, p), ": ", lo.last_prompt, lo.state ? h("div", { class: "meta" }, "→ " + lo.state) : null))))));
  root.append(reflectCard(rev, day));
  return root;
}

function goalsList(gs) {
  if (!gs.length) return h("p", { class: "meta" }, "No goals yet. Add a few on the Goals tab and they will be judged here every day.");
  return h("ul", { class: "plain goals" }, gs.map((g) => h("li", {},
    h("div", { class: "row", style: { margin: 0 } }, h("b", {}, g.title), gchip(g.status), h("span", { class: "spacer" }), goalDots(g.week.dots), h("span", { class: "meta" }, `${g.week.met}/${g.week.target} this week`)),
    g.detail ? h("div", { class: "meta" }, g.detail) : null)));
}

function reflectCard(rev, day) {
  const c = rev.closed;
  const card = h("section", { class: "card reflect" });
  const projects = rev.by_project.map((p) => p.project);
  if (c && !state.editingDay) {
    put(card, h("h2", {}, "Your reflection"),
      c.stale ? h("p", { class: "gchip gc-partly" }, "Sessions changed since you closed this day. Update the entry to refresh it.") : null,
      c.feeling ? h("div", { class: "stars" }, h("span", { class: "meta" }, "Felt "), "★".repeat(c.feeling) + "☆".repeat(5 - c.feeling)) : null,
      c.win ? h("p", {}, h("b", {}, "Win: "), c.win) : null,
      Object.keys(c.next_steps).length ? h("div", {}, h("b", {}, "Next steps"), h("ul", { class: "plain" }, Object.entries(c.next_steps).map(([p, t]) => h("li", {}, h("b", {}, p + ": "), t)))) : null,
      c.notes ? h("p", { class: "meta", style: { whiteSpace: "pre-wrap" } }, c.notes) : null,
      c.narrative ? h("div", { class: "left-off", style: { whiteSpace: "pre-wrap" } }, h("small", {}, "Written review"), c.narrative) : null,
      h("div", { class: "row" },
        h("button", { class: "ghost", onclick: () => { state.editingDay = true; render(); } }, "Edit"),
        h("button", { class: "ghost", onclick: async () => { if (!confirm("Reopen this day? Your reflection will be removed (the sessions stay).")) return; await api(`/api/day/${day}/close`, { method: "DELETE" }); toast("Day reopened"); render(); } }, "Reopen")));
    return card;
  }
  let feeling = c?.feeling || 0;
  const stars = h("span", { class: "stars", role: "group", "aria-label": "How did the day feel?" });
  const paint = () => stars.replaceChildren(...[1, 2, 3, 4, 5].map((i) => h("button", { type: "button", class: i <= feeling ? "on" : "", "aria-label": `${i} of 5`, onclick: () => { feeling = i; paint(); } }, "★")));
  paint();
  const win = h("input", { placeholder: "One thing that went well today", value: c?.win || "", maxlength: 1000 });
  const steps = projects.map((p) => {
    const lo = rev.left_off[p];
    return { p, el: h("input", { placeholder: lo?.last_prompt ? "e.g. continue: " + lo.last_prompt.slice(0, 70) : "First step tomorrow", value: c?.next_steps?.[p] || "", maxlength: 500 }) };
  });
  const answers = {};
  const manual = rev.goals.filter((g) => g.metric === "manual");
  const manualRows = manual.map((g) => {
    answers[g.id] = c?.goal_answers?.[g.id]?.status || (g.auto === false && g.status !== "na" ? g.status : "");
    const grp = h("span", { class: "seg", role: "group", "aria-label": g.title });
    const paintG = () => grp.replaceChildren(...["met", "partly", "missed"].map((s) => h("button", { type: "button", "aria-pressed": String(answers[g.id] === s), onclick: () => { answers[g.id] = answers[g.id] === s ? "" : s; paintG(); } }, GSTAT[s][1])));
    paintG();
    return h("div", { class: "row", style: { margin: "4px 0" } }, h("span", {}, g.title), h("span", { class: "spacer" }), grp);
  });
  const notes = h("textarea", { rows: 3, placeholder: "Anything else worth remembering (private)", maxlength: 5000 }, c?.notes || "");
  const save = h("button", { class: "primary", type: "button", onclick: async () => {
    const body = { feeling: feeling || null, win: win.value, notes: notes.value, next_steps: Object.fromEntries(steps.map((s) => [s.p, s.el.value])) };
    const ga = Object.fromEntries(Object.entries(answers).filter(([, v]) => v).map(([k, v]) => [k, { status: v }]));
    if (Object.keys(ga).length) body.goal_answers = ga;
    if (!body.feeling) delete body.feeling;
    try { await api(`/api/day/${day}/close?${qs({ category: state.cat })}`, { method: "POST", body }); state.editingDay = false; toast(c ? "Day updated" : "Day closed"); render(); } catch (e) { toast(e.message); }
  } }, c ? "Update the day" : "Close the day");
  put(card, h("h2", {}, c ? "Edit your reflection" : "Close the day"),
    h("p", { class: "meta", style: { marginTop: "-6px" } }, "Takes a minute. Saves a frozen copy of this review, feeds your retro, and shows your next steps tomorrow morning."),
    h("div", { class: "row" }, h("span", {}, "How did it feel?"), stars),
    h("label", { class: "f" }, "One win", win),
    h("div", { style: { margin: "12px 0 4px" } }, h("b", {}, "First step tomorrow, per project")),
    h("div", { class: "form", style: { gridTemplateColumns: "140px 1fr", alignItems: "center", gap: "8px" } }, steps.flatMap((s) => [h("span", { class: "meta" }, s.p), s.el])),
    manualRows.length ? h("div", { style: { margin: "12px 0 4px" } }, h("b", {}, "Your goals (self-reported)")) : null, ...manualRows,
    h("label", { class: "f", style: { marginTop: "12px" } }, "Notes", notes),
    h("div", { class: "row" }, save, c ? h("button", { class: "ghost", type: "button", onclick: () => { state.editingDay = false; render(); } }, "Cancel") : null));
  return card;
}

async function viewGoals() {
  const [cur, long] = await Promise.all([api("/api/goals?window=7"), api("/api/goals?window=28")]);
  const longBy = Object.fromEntries(long.goals.map((g) => [g.id, g]));
  const root = h("div", {}, h("h1", {}, "Goals"),
    h("p", { class: "sub" }, "Practices you are working on, not outcomes. Each is judged per day from your sessions (or by you), counted against a weekly target. Quiet days do not count against you. They also shape the coach and your day review."));
  const active = cur.goals.filter((g) => g.active), archived = cur.goals.filter((g) => !g.active);
  const refresh = () => render();
  const card = (g) => {
    const l = longBy[g.id];
    const target = h("select", { "aria-label": `Target per week for ${g.title}` }, [1, 2, 3, 4, 5, 6, 7].map((n) => h("option", { value: n, selected: n === g.target_per_week }, `${n}× / week`)));
    target.addEventListener("change", async () => { await api(`/api/goals/${g.id}`, { method: "PATCH", body: { target_per_week: Number(target.value) } }); toast("Target updated"); refresh(); });
    return h("section", { class: "card goalcard" },
      h("div", { class: "row", style: { margin: 0 } }, h("h2", { style: { margin: 0 } }, g.title), h("span", { class: "tag" }, cur.metrics[g.metric]), h("span", { class: "spacer" }), target),
      g.why ? h("p", { class: "meta", style: { margin: "6px 0" } }, g.why) : null,
      h("div", { class: "row", style: { margin: "6px 0 0" } }, goalDots(g.dots), h("span", {}, `${g.met} of ${g.target} this week`),
        l && l.judged ? h("span", { class: "meta" }, `· last 4 weeks: ${l.met} of ${l.judged} judged days`) : null, h("span", { class: "spacer" }),
        h("button", { class: "ghost", onclick: async () => { await api(`/api/goals/${g.id}`, { method: "PATCH", body: { active: !g.active } }); refresh(); } }, g.active ? "Archive" : "Restore"),
        !g.active ? h("button", { class: "danger", onclick: async () => { if (confirm(`Delete "${g.title}" for good?`)) { await api(`/api/goals/${g.id}`, { method: "DELETE" }); refresh(); } } }, "Delete") : null));
  };
  root.append(active.length ? h("div", { class: "grid" }, active.map(card)) : h("div", { class: "card empty" }, "No goals yet. Pick a few below. Two or three is plenty."));
  const have = new Set(cur.goals.map((g) => g.title));
  const tmpl = cur.templates.filter((t) => !have.has(t.title));
  if (tmpl.length) root.append(h("h2", { style: { margin: "24px 0 10px" } }, "Start from a template"),
    h("div", { class: "grid cols-2" }, tmpl.map((t) => h("section", { class: "card" },
      h("b", {}, t.title), h("p", { class: "meta", style: { margin: "4px 0 8px" } }, t.why),
      h("div", { class: "row", style: { margin: 0 } }, h("span", { class: "tag" }, cur.metrics[t.metric]), h("span", { class: "tag" }, `${t.target_per_week}× / week`), h("span", { class: "spacer" }),
        h("button", { class: "ghost", onclick: async () => { await api("/api/goals", { method: "POST", body: t }); toast("Goal added"); refresh(); } }, "Add"))))));
  const title = h("input", { placeholder: "e.g. Explain my design choices in the PR description", maxlength: 200 });
  const why = h("input", { placeholder: "Why it matters to you (optional)", maxlength: 1000 });
  const metric = h("select", {}, Object.entries(cur.metrics).map(([k, v]) => h("option", { value: k }, k === "manual" ? "Self-reported: " + v : "Auto: " + v)));
  const tgt = h("select", {}, [1, 2, 3, 4, 5, 6, 7].map((n) => h("option", { value: n, selected: n === 3 }, `${n}× / week`)));
  root.append(h("h2", { style: { margin: "24px 0 10px" } }, "Write your own"), h("section", { class: "card" }, h("div", { class: "form" },
    h("label", { class: "f wide" }, "Goal", title), h("label", { class: "f wide" }, "Why", why), h("label", { class: "f" }, "How to track it", metric), h("label", { class: "f" }, "Target", tgt),
    h("div", { class: "row wide" }, h("button", { class: "primary", onclick: async () => {
      try { await api("/api/goals", { method: "POST", body: { title: title.value, why: why.value, metric: metric.value, target_per_week: Number(tgt.value) } }); toast("Goal added"); refresh(); } catch (e) { toast(e.message); }
    } }, "Add goal")))));
  if (archived.length) root.append(h("h2", { style: { margin: "24px 0 10px" } }, "Archived"), h("div", { class: "grid" }, archived.map(card)));
  return root;
}

// ───────── log a session ─────────
function openLogDialog() {
  const dlg = $("#dlg"); dlg.replaceChildren();
  const today = iso(new Date());
  const projects = state.projectsSeen || [];
  const dl = h("datalist", { id: "projlist" }, projects.map((p) => h("option", { value: p })));
  const f = (label, el, wide) => h("label", { class: "f" + (wide ? " wide" : "") }, label, el);
  const inp = {
    project: h("input", { list: "projlist", required: true, placeholder: "repo or project name" }),
    day: h("input", { type: "date", value: today, required: true }),
    start: h("input", { type: "time", value: "09:00", required: true }),
    minutes: h("input", { type: "number", min: 1, max: 1440, value: 60, required: true }),
    title: h("input", { required: true, placeholder: "What did you work on?" }),
    type: h("select", {}, state.cfg.work_types.map((t) => h("option", { value: t }, t))),
    rating: h("select", {}, h("option", { value: "" }, "skip"), [1, 2, 3, 4, 5].map((n) => h("option", { value: n }, "★".repeat(n)))),
    interruptions: h("input", { type: "number", min: 0, value: 0 }),
    switches: h("input", { type: "number", min: 0, value: 0 }),
    notes: h("textarea", { rows: 3, placeholder: "Optional: decisions, blockers, next step" }),
  };
  const form = h("form", { method: "dialog", class: "form" },
    h("h1", { class: "wide" }, "Log a session"),
    f("Project", inp.project), f("Type of work", inp.type), f("Day", inp.day), f("Start time", inp.start),
    f("Minutes", inp.minutes), f("Felt focus", inp.rating), f("Interruptions", inp.interruptions), f("Context switches", inp.switches),
    f("What did you work on?", inp.title, true), f("Notes", inp.notes, true),
    h("div", { class: "row wide" }, h("button", { class: "primary", type: "submit" }, "Save"), h("button", { class: "ghost", type: "button", onclick: () => dlg.close() }, "Cancel")), dl);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("/api/sessions", { method: "POST", body: { project: inp.project.value, day: inp.day.value, start_time: inp.start.value, minutes: Number(inp.minutes.value), title: inp.title.value, type: inp.type.value, rating: inp.rating.value ? Number(inp.rating.value) : null, interruptions: Number(inp.interruptions.value), context_switches: Number(inp.switches.value), notes: inp.notes.value } });
      dlg.close(); toast("Session logged"); render();
    } catch (e) { toast(e.message); }
  });
  dlg.append(form); dlg.showModal(); inp.project.focus();
}

// ───────── chrome ─────────
const VIEWS = { overview: viewOverview, day: viewDay, sessions: viewSessions, projects: viewProjects, goals: viewGoals, reports: viewReports };
let renderToken = 0;
async function render() {
  const token = ++renderToken;
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
  const ranged = !["day", "goals"].includes(state.view);   // these two tabs have their own date handling
  $("#range").hidden = !ranged;
  $("#cat").hidden = state.view === "goals";
  $("#custom").hidden = !ranged || state.preset !== "custom";
  const view = $("#view");
  try {
    const node = await VIEWS[state.view]();
    if (token !== renderToken) return;
    view.replaceChildren(node);
  } catch (e) {
    view.replaceChildren(h("div", { class: "card empty" }, "Could not load: " + e.message));
  }
}
function paintBar() {
  const presets = [["1", "Today"], ["7", "7 days"], ["21", "21 days"], ["30", "30 days"], ["custom", "Custom"]];
  $("#range").replaceChildren(...presets.map(([v, l]) => h("button", { "aria-pressed": String(state.preset === v), onclick: () => { state.preset = v; store.set("preset", v); if (v === "custom" && !state.from) { state.to = iso(new Date()); const d = new Date(); d.setDate(d.getDate() - 20); state.from = iso(d); } paintBar(); $("#from").value = state.from; $("#to").value = state.to; render(); } }, l)));
  $("#cat").replaceChildren(...[["all", "All"], ["work", "Work"], ["personal", "Personal"]].map(([v, l]) => h("button", { "aria-pressed": String(state.cat === v), onclick: () => { state.cat = v; store.set("cat", v); paintBar(); render(); } }, l)));
}
async function init() {
  try { state.cfg = await api("/api/config"); } catch (e) { toast(e.message); }
  $("#datadir").textContent = state.cfg.data_dir || "";
  $("#from").value = state.from; $("#to").value = state.to;
  for (const id of ["from", "to"]) $("#" + id).addEventListener("change", () => { state.from = $("#from").value; state.to = $("#to").value; store.set("from", state.from); store.set("to", state.to); if (state.from && state.to) render(); });
  $("#nav").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.view = b.dataset.view; store.set("view", state.view); render(); });
  $("#add").addEventListener("click", openLogDialog);
  $("#sync").addEventListener("click", async () => { try { const s = await api("/api/sync", { method: "POST" }); toast(`Synced: ${s.imported} updated, ${s.unchanged} unchanged`); render(); } catch (e) { toast(e.message); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });
  paintBar();
  render();
}
init();

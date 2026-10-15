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
    const g = svg("g", {}, svg("title", {}, `${dayLabel(d.day)}: ${dur(d.active_s)} active, ${dur(d.flow_s)} deep flow`));
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
  const d = await api(`/api/overview?${qs({ ...r, category: state.cat })}`);
  const T = d.summary.totals, P = d.previous;
  const root = h("div", {});
  root.append(h("h1", {}, "Overview"), h("p", { class: "sub" }, `${r.start} to ${r.end} · compared with the ${Math.round((new Date(r.end) - new Date(r.start)) / 864e5) + 1} days before`));
  if (!T.sessions) { root.append(h("div", { class: "card empty" }, "No sessions in this range. Pick a longer range, press Sync, or log a session manually.")); return root; }
  root.append(h("div", { class: "kpis" },
    kpi("Active time", dur(T.active_s), delta(T.active_s, P.active_s, { fmt: dur })),
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
    h("section", { class: "card" }, h("h2", {}, "Active time per day"), stackedDays(d.summary.by_day, r), legend(types)),
    h("section", { class: "card" }, h("h2", {}, "When you focus"), hourChart(d.summary.by_hour),
      h("div", { class: "legend" }, h("span", {}, h("i", { class: "dot", style: { background: "var(--accent)" } }), "deep flow"), h("span", {}, h("i", { class: "dot", style: { background: "var(--surface-2)", border: "1px solid var(--line)" } }), "any activity")))));
  const maxT = Math.max(...Object.values(d.summary.by_type));
  const maxP = Math.max(...d.summary.by_project.map((p) => p.active_s));
  root.append(h("div", { class: "grid cols-2" },
    h("section", { class: "card" }, h("h2", {}, "Type of work"), barList(Object.entries(d.summary.by_type).map(([t, v]) => ({ name: t, value: v, label: dur(v), color: typeColor(t) })), maxT)),
    h("section", { class: "card" }, h("h2", {}, "Time by project"), barList(d.summary.by_project.slice(0, 10).map((p) => ({ name: p.project, value: p.active_s, label: dur(p.active_s) })), maxP, "var(--accent)"))));
  return root;
}
const kpi = (l, v, d) => h("div", { class: "kpi" }, h("div", { class: "l" }, l), h("div", { class: "v" }, String(v)), d);

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
const VIEWS = { overview: viewOverview, sessions: viewSessions, projects: viewProjects, reports: viewReports };
let renderToken = 0;
async function render() {
  const token = ++renderToken;
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
  $("#custom").hidden = state.preset !== "custom";
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

/* BalanceIQ ERP front end - plain JS, no build step.
   Pages are chosen by the signed-in user's role; the server enforces the same rules. */
"use strict";

// ----------------------------- helpers ------------------------------------
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const FEATURES = [
  { key: "work_hours",    label: "Work hours",             q: "Hours worked per week",        min: 20, max: 80, step: 1,    def: 45, unit: "hrs/week" },
  { key: "commute",       label: "Commute time",           q: "Daily commute (round trip)",   min: 0,  max: 4,  step: 0.25, def: 1,  unit: "hrs/day" },
  { key: "sleep",         label: "Sleep",                  q: "Average sleep per night",      min: 3,  max: 10, step: 0.5,  def: 7,  unit: "hrs/night" },
  { key: "stress",        label: "Stress level",           q: "Stress on most days (1-10)",   min: 1,  max: 10, step: 1,    def: 5,  unit: "/10" },
  { key: "family_time",   label: "Family / personal time", q: "Family or personal time",      min: 0,  max: 6,  step: 0.5,  def: 2,  unit: "hrs/day" },
  { key: "social_time",   label: "Social time",            q: "Social-life time",             min: 0,  max: 20, step: 1,    def: 5,  unit: "hrs/week" },
  { key: "satisfaction",  label: "Job satisfaction",       q: "Satisfaction with job (1-10)", min: 1,  max: 10, step: 1,    def: 6,  unit: "/10" },
  { key: "mental_health", label: "Mental health",          q: "Mental health lately (1-10)",  min: 1,  max: 10, step: 1,    def: 6,  unit: "/10" },
];
const FLABEL = Object.fromEntries(FEATURES.map((f) => [f.key, f.label]));
const RISKS = ["Low Risk", "Medium Risk", "High Risk"];
const riskCls = (l) => ({ "Low Risk": "low", "Medium Risk": "med", "High Risk": "high" }[l] || "none");
const riskBadge = (l) => l ? `<span class="badge ${riskCls(l)}">${esc(l)}</span>` : `<span class="badge none">Not assessed</span>`;
const pct = (x) => (x == null ? "-" : Math.round(x * 100));
const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : "-";
const fmtDT = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "-";

function errText(data, status) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail)) {
    return data.detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return `Request failed (${status})`;
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { location.replace("/login.html"); throw new Error("Signed out"); }
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(errText(data, res.status));
  return data;
}

let ME = null;
const isStaff = () => ME && (ME.role === "admin" || ME.role === "hr");
const view = () => $("#view");

// ----------------------------- dialog -------------------------------------
function openDialog(html, onReady) {
  const dlg = $("#dlg");
  dlg.innerHTML = html;
  const close = () => dlg.close();
  dlg.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  if (onReady) onReady(dlg, close);
  dlg.showModal();
}

// ----------------------------- navigation ---------------------------------
function navItems() {
  const items = [["dashboard", "Dashboard"], ["assess", "New assessment"], ["history", "My history"]];
  if (isStaff()) {
    items.push(["#", "HR & Admin"], ["overview", "Org overview"], ["people", "People"], ["all", "All assessments"]);
  }
  items.push(["#", "Account"], ["profile", "Profile & password"]);
  return items;
}

function renderNav(current) {
  $("#nav").innerHTML = navItems().map(([id, label]) =>
    id === "#" ? `<div class="navhead">${esc(label)}</div>`
      : `<a href="#${id}" class="${id === current ? "active" : ""}">${esc(label)}</a>`).join("");
}

const PAGES = {
  dashboard: pageDashboard, assess: pageAssess, history: pageHistory,
  overview: pageOverview, people: pagePeople, all: pageAll, profile: pageProfile,
};
const STAFF_PAGES = new Set(["overview", "people", "all"]);

async function route() {
  let id = (location.hash || "#dashboard").slice(1);
  if (!PAGES[id] || (STAFF_PAGES.has(id) && !isStaff())) id = "dashboard";
  renderNav(id);
  view().innerHTML = `<p class="muted">Loading…</p>`;
  try { await PAGES[id](); }
  catch (e) { if (e.message !== "Signed out") view().innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

function renderBanner() {
  const b = $("#banner");
  b.innerHTML = ME.must_change_password
    ? `<div class="banner"><b>Please set a new password.</b> You are using a temporary or default password. <a href="#profile">Change it now</a>.</div>`
    : "";
}

function renderWho() {
  $("#who").innerHTML = `<b>${esc(ME.full_name)}</b><span>${esc(ME.role.toUpperCase())} &middot; ${esc(ME.department)}</span>
    <button id="logoutBtn" type="button">Sign out</button>`;
  $("#logoutBtn").addEventListener("click", async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch (_) {}
    location.replace("/login.html");
  });
}

// ----------------------------- shared widgets -----------------------------
function resultCard(r, title = "Result") {
  const factors = Object.entries(r.contributions || {})
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 4);
  const maxAbs = Math.max(...factors.map(([, v]) => Math.abs(v)), 0.001);
  const probs = r.proba || {};
  return `
    <h2>${esc(title)}</h2>
    <div style="margin:10px 0 4px">${riskBadge(r.label).replace('class="badge', 'class="badge bigbadge')}</div>
    <div class="scorebig">${pct(r.score)}<span class="muted" style="font-size:16px"> / 100 balance score</span></div>
    <div class="small muted">Higher is better. Based on a model trained on the project's WLB parameters.</div>
    <h3 style="margin-top:18px">What is driving it</h3>
    ${factors.map(([k, v]) => `
      <div class="factor"><div class="flabel"><span>${esc(FLABEL[k] || k)}</span><span>${v >= 0 ? "toward" : "away from"} ${esc(r.label)}</span></div>
      <div class="fbar"><i style="width:${Math.round(Math.abs(v) / maxAbs * 100)}%;background:var(--${v >= 0 ? riskCls(r.label) : "muted"})"></i></div></div>`).join("")}
    <h3 style="margin-top:18px">Class probabilities</h3>
    ${RISKS.map((c) => `
      <div class="factor"><div class="flabel"><span>${c}</span><span>${pct(probs[c] || 0)}%</span></div>
      <div class="fbar"><i style="width:${pct(probs[c] || 0)}%;background:var(--${riskCls(c)})"></i></div></div>`).join("")}
    <h3 style="margin-top:18px">Suggestions</h3>
    <ul class="tips">${(r.tips || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
    <div class="note">Decision-support only - not a clinical or HR assessment. Results are saved to your record and visible to HR.</div>`;
}

function sparkline(rows) {          // rows: oldest -> newest, with risk_score
  if (rows.length < 2) return `<p class="placeholder">Take at least two assessments to see a trend.</p>`;
  const W = 300, H = 90, pad = 8;
  const pts = rows.map((r, i) => [pad + i * (W - 2 * pad) / (rows.length - 1), H - pad - r.risk_score * (H - 2 * pad)]);
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Balance score trend">
    <line x1="${pad}" y1="${H / 2}" x2="${W - pad}" y2="${H / 2}" stroke="var(--line)" stroke-dasharray="3 3"/>
    <polyline fill="none" stroke="var(--teal)" stroke-width="2.5" points="${pts.map((p) => p.join(",")).join(" ")}"/>
    ${pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="var(--teal)"/>`).join("")}</svg>`;
}

function stackBar(c) {
  const t = (c["Low Risk"] || 0) + (c["Medium Risk"] || 0) + (c["High Risk"] || 0);
  if (!t) return `<div class="stack"></div>`;
  return `<div class="stack">${["Low Risk", "Medium Risk", "High Risk"].map((k) =>
    `<i class="${riskCls(k)}" style="width:${(c[k] || 0) / t * 100}%" title="${k}: ${c[k] || 0}"></i>`).join("")}</div>`;
}
const legend = () => `<div class="legend"><span><i style="background:var(--low)"></i>Low</span><span><i style="background:var(--med)"></i>Medium</span><span><i style="background:var(--high)"></i>High</span></div>`;

// ----------------------------- employee pages -----------------------------
async function pageDashboard() {
  const [latest, hist] = await Promise.all([api("/my/latest"), api("/my/assessments?limit=30")]);
  const first = ME.full_name.split(" ")[0];
  let html = `<div class="pagehead"><div><h1>Hello, ${esc(first)}</h1>
    <p class="muted">${esc(ME.designation || ME.role)} &middot; ${esc(ME.department)}</p></div>
    <a href="#assess"><button class="btn-primary" type="button">New assessment</button></a></div>`;
  if (!latest) {
    html += `<div class="panel"><h2>No assessments yet</h2><p class="muted">Answer eight quick questions and the model will estimate your work-life balance risk.</p>
      <a href="#assess"><button class="btn-primary" type="button">Take your first assessment</button></a></div>`;
  } else {
    const avg = hist.reduce((s, r) => s + r.risk_score, 0) / hist.length;
    html += `<div class="stats">
      <div class="stat"><div class="n">${riskBadge(latest.label)}</div><div class="l">Latest result &middot; ${fmtDate(latest.created_at)}</div></div>
      <div class="stat"><div class="n">${pct(latest.score)}</div><div class="l">Latest balance score</div></div>
      <div class="stat"><div class="n">${hist.length}</div><div class="l">Assessments taken</div></div>
      <div class="stat"><div class="n">${pct(avg)}</div><div class="l">Average score</div></div></div>
      <div class="grid2"><div class="panel">${resultCard(latest, "Latest assessment")}</div>
      <div class="panel"><h2>Your trend</h2><p class="small muted">Balance score over time (higher is better)</p>
      ${sparkline([...hist].reverse())}</div></div>`;
  }
  view().innerHTML = html;
}

async function pageAssess() {
  view().innerHTML = `<div class="pagehead"><div><h1>New assessment</h1>
    <p class="muted">Drag each slider to match a typical week, then run the assessment.</p></div></div>
    <div class="grid-assess"><section class="panel"><form id="aform"></form>
      <button class="btn-primary wide" id="runBtn" type="button">Run assessment</button><div id="aerr"></div></section>
    <section class="panel" id="aresult"><h2>Result</h2><p class="placeholder">Your prediction will appear here and be saved to your history.</p></section></div>`;
  const form = $("#aform");
  form.innerHTML = FEATURES.map((f) => `
    <div class="qrow"><div class="qhead"><span>${esc(f.q)}</span><b id="v_${f.key}">${f.def} ${esc(f.unit)}</b></div>
    <input type="range" id="r_${f.key}" min="${f.min}" max="${f.max}" step="${f.step}" value="${f.def}" aria-label="${esc(f.q)}"></div>`).join("");
  FEATURES.forEach((f) => $("#r_" + f.key).addEventListener("input", (e) => { $("#v_" + f.key).textContent = `${e.target.value} ${f.unit}`; }));

  $("#runBtn").addEventListener("click", async () => {
    const btn = $("#runBtn"), err = $("#aerr");
    err.innerHTML = ""; btn.disabled = true; btn.textContent = "Running…";
    try {
      const body = {};
      FEATURES.forEach((f) => { body[f.key] = parseFloat($("#r_" + f.key).value); });
      const r = await api("/predict", { method: "POST", body });
      $("#aresult").innerHTML = resultCard(r);
    } catch (e) { err.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
    btn.disabled = false; btn.textContent = "Run assessment";
  });
}

async function pageHistory() {
  const rows = await api("/my/assessments?limit=200");
  view().innerHTML = `<div class="pagehead"><div><h1>My history</h1><p class="muted">Every assessment you have taken.</p></div></div>
    <div class="panel">${rows.length ? `<div class="tablewrap"><table><thead><tr><th>Date</th><th>Result</th><th>Score</th><th>Main driver</th>
      ${FEATURES.map((f) => `<th>${esc(f.label)}</th>`).join("")}</tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${fmtDT(r.created_at)}</td><td>${riskBadge(r.risk_label)}</td><td>${pct(r.risk_score)}</td>
        <td>${esc(FLABEL[r.top_factor] || r.top_factor)}</td>${FEATURES.map((f) => `<td>${r[f.key]}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>` : `<p class="placeholder">Nothing here yet. <a href="#assess">Take your first assessment</a>.</p>`}</div>`;
}

// ----------------------------- HR / admin pages ---------------------------
async function pageOverview() {
  const o = await api("/hr/overview");
  const d = o.distribution;
  const maxDriver = Math.max(...o.top_drivers.map((x) => x.count), 1);
  const maxWeek = Math.max(...o.weekly_trend.map((w) => w.avg_score), 0.01);
  view().innerHTML = `<div class="pagehead"><div><h1>Organisation overview</h1>
    <p class="muted">Based on each person's most recent assessment.</p></div></div>
    <div class="stats">
      <div class="stat"><div class="n">${o.total_people}</div><div class="l">Active people</div></div>
      <div class="stat"><div class="n">${o.coverage_pct}%</div><div class="l">Assessed (${o.assessed} of ${o.total_people})</div></div>
      <div class="stat"><div class="n" style="color:var(--high)">${d["High Risk"]}</div><div class="l">High risk</div></div>
      <div class="stat"><div class="n">${o.avg_balance_score == null ? "-" : pct(o.avg_balance_score)}</div><div class="l">Average balance score</div></div></div>
    <div class="grid2">
      <div class="panel"><h2>Risk distribution</h2>${o.assessed ? stackBar(d) + legend() +
        `<p class="small muted" style="margin-top:10px">Low ${d["Low Risk"]} &middot; Medium ${d["Medium Risk"]} &middot; High ${d["High Risk"]}</p>` : `<p class="placeholder">No assessments yet.</p>`}
        <h3 style="margin-top:20px">By department</h3>
        ${o.by_department.length ? o.by_department.map((x) => `<div class="deptrow"><span>${esc(x.department)}</span>${stackBar(x)}<span class="muted">${x.total}</span></div>`).join("") : `<p class="placeholder">-</p>`}</div>
      <div class="panel"><h2>Top drivers of risk</h2>
        ${o.top_drivers.length ? o.top_drivers.map((x) => `<div class="factor"><div class="flabel"><span>${esc(x.label)}</span><span>${x.count} people</span></div>
          <div class="fbar"><i style="width:${x.count / maxDriver * 100}%;background:var(--teal)"></i></div></div>`).join("") : `<p class="placeholder">-</p>`}
        <h3 style="margin-top:20px">Weekly average balance score</h3>
        ${o.weekly_trend.length ? `<div class="weekly">${o.weekly_trend.map((w) => `<div><span>${pct(w.avg_score)}</span><b style="height:${w.avg_score / maxWeek * 100}%"></b><span>${esc(w.week.slice(5))}</span></div>`).join("")}</div>` : `<p class="placeholder">No recent data.</p>`}</div></div>
    <div class="panel"><h2>People needing attention</h2><p class="small muted">Latest result is High Risk, lowest balance score first.</p>
      ${o.at_risk.length ? `<div class="tablewrap"><table><thead><tr><th>Name</th><th>Department</th><th>Score</th><th>Main driver</th><th>Assessed</th></tr></thead><tbody>
        ${o.at_risk.map((r) => `<tr><td>${esc(r.name)}<div class="small muted">${esc(r.designation)}</div></td><td>${esc(r.department)}</td><td>${pct(r.risk_score)}</td>
          <td>${esc(FLABEL[r.top_factor] || r.top_factor)}</td><td>${fmtDate(r.created_at)}</td></tr>`).join("")}</tbody></table></div>`
        : `<p class="placeholder">Nobody is currently flagged High Risk.</p>`}</div>`;
}

async function pageAll() {
  const depts = await api("/departments");
  view().innerHTML = `<div class="pagehead"><div><h1>All assessments</h1><p class="muted">Every submitted assessment across the organisation.</p></div>
    <button class="btn-ghost" id="csvBtn" type="button">Export CSV</button></div>
    <div class="toolbar"><input id="fq" type="search" placeholder="Search name or username">
      <select id="fdept"><option value="">All departments</option>${depts.map((d) => `<option>${esc(d)}</option>`).join("")}</select>
      <select id="frisk"><option value="">All risk levels</option>${RISKS.map((r) => `<option>${r}</option>`).join("")}</select></div>
    <div class="panel"><div id="tbl"></div></div>`;
  let rows = [];
  async function load() {
    const p = new URLSearchParams();
    if ($("#fq").value.trim()) p.set("q", $("#fq").value.trim());
    if ($("#fdept").value) p.set("department", $("#fdept").value);
    if ($("#frisk").value) p.set("risk", $("#frisk").value);
    p.set("limit", "500");
    rows = await api("/hr/assessments?" + p.toString());
    $("#tbl").innerHTML = rows.length ? `<div class="tablewrap"><table><thead><tr><th>Date</th><th>Person</th><th>Department</th><th>Result</th><th>Score</th><th>Main driver</th><th>Work hrs</th><th>Sleep</th><th>Stress</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${fmtDT(r.created_at)}</td><td>${esc(r.name)}<div class="small muted">${esc(r.username)}</div></td><td>${esc(r.department)}</td>
        <td>${riskBadge(r.risk_label)}</td><td>${pct(r.risk_score)}</td><td>${esc(FLABEL[r.top_factor] || r.top_factor)}</td><td>${r.work_hours}</td><td>${r.sleep}</td><td>${r.stress}</td></tr>`).join("")}</tbody></table></div>
      <p class="small muted" style="margin-top:8px">${rows.length} shown</p>` : `<p class="placeholder">No assessments match.</p>`;
  }
  let t; $("#fq").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => load().catch(showErr), 250); });
  $("#fdept").addEventListener("change", () => load().catch(showErr));
  $("#frisk").addEventListener("change", () => load().catch(showErr));
  $("#csvBtn").addEventListener("click", () => exportCsv(rows));
  await load();
}

function exportCsv(rows) {
  const cols = ["created_at", "name", "username", "department", "risk_label", "risk_score", "top_factor", ...FEATURES.map((f) => f.key)];
  // prefix risky leading characters so spreadsheets don't run cell content as a formula
  const cell = (v) => { let s = String(v ?? ""); if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; return `"${s.replace(/"/g, '""')}"`; };
  const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "wlb_assessments.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}
function showErr(e) { if (e.message !== "Signed out") view().insertAdjacentHTML("afterbegin", `<div class="error">${esc(e.message)}</div>`); }

async function pagePeople() {
  const depts = await api("/departments");
  view().innerHTML = `<div class="pagehead"><div><h1>People</h1><p class="muted">${ME.role === "admin" ? "Manage every account and role." : "Manage employee accounts."}</p></div>
    <button class="btn-primary" id="addBtn" type="button">Add person</button></div>
    <div class="toolbar"><input id="pq" type="search" placeholder="Search name or username">
      <select id="pdept"><option value="">All departments</option>${depts.map((d) => `<option>${esc(d)}</option>`).join("")}</select></div>
    <div class="panel"><div id="ptbl"></div></div>`;
  let people = [];
  async function load() {
    const p = new URLSearchParams();
    if ($("#pq").value.trim()) p.set("q", $("#pq").value.trim());
    if ($("#pdept").value) p.set("department", $("#pdept").value);
    people = await api("/employees?" + p.toString());
    $("#ptbl").innerHTML = people.length ? `<div class="tablewrap"><table><thead><tr><th>Name</th><th>Role</th><th>Department</th><th>Latest result</th><th>Last sign-in</th><th>Status</th><th></th></tr></thead><tbody>
      ${people.map((u) => `<tr><td>${esc(u.full_name)}<div class="small muted">${esc(u.username)}${u.designation ? " &middot; " + esc(u.designation) : ""}</div></td>
        <td><span class="badge role">${esc(u.role)}</span></td><td>${esc(u.department)}</td>
        <td>${riskBadge(u.latest_risk)}${u.latest_at ? `<div class="small muted">${fmtDate(u.latest_at)}</div>` : ""}</td>
        <td>${u.last_login ? fmtDate(u.last_login) : "Never"}</td>
        <td>${u.is_active ? "Active" : '<span class="badge off">Inactive</span>'}${u.must_change_password ? '<div class="small muted">temp password</div>' : ""}</td>
        <td class="actions">${u.manageable ? `<button class="btn-ghost btn-sm" data-edit="${u.id}" type="button">Edit</button>
          <button class="btn-ghost btn-sm" data-reset="${u.id}" type="button">Reset password</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="placeholder">No people match.</p>`;
    document.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => editDialog(people.find((u) => u.id === +b.dataset.edit), depts, load)));
    document.querySelectorAll("[data-reset]").forEach((b) => b.addEventListener("click", () => resetDialog(people.find((u) => u.id === +b.dataset.reset))));
  }
  let t; $("#pq").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => load().catch(showErr), 250); });
  $("#pdept").addEventListener("change", () => load().catch(showErr));
  $("#addBtn").addEventListener("click", () => addDialog(depts, load));
  await load();
}

const deptList = (depts) => `<datalist id="deptlist">${depts.map((d) => `<option value="${esc(d)}">`).join("")}</datalist>`;
const roleSelect = (cur) => ME.role === "admin"
  ? `<label for="f_role">Role</label><select id="f_role">${["employee", "hr", "admin"].map((r) => `<option value="${r}" ${r === cur ? "selected" : ""}>${r}</option>`).join("")}</select>` : "";

function addDialog(depts, reload) {
  openDialog(`<form id="dform" method="dialog"><h2>Add person</h2>
    <label for="f_name">Full name</label><input id="f_name" required minlength="2">
    <label for="f_user">Username</label><input id="f_user" required minlength="3" pattern="[A-Za-z0-9._\\-]{3,40}" title="Letters, digits, . _ -">
    <label for="f_email">Email (optional)</label><input id="f_email" type="email">
    <label for="f_dept">Department</label><input id="f_dept" list="deptlist" required value="General">${deptList(depts)}
    <label for="f_desig">Designation</label><input id="f_desig">
    ${roleSelect("employee")}
    <label for="f_pw">Password (leave blank to generate one)</label><input id="f_pw" type="text" autocomplete="off">
    <div id="derr"></div><div class="dlgactions"><button class="btn-ghost" type="button" data-close>Cancel</button><button class="btn-primary" type="submit">Create</button></div></form>`,
  (dlg, close) => {
    $("#dform", dlg).addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = { full_name: $("#f_name").value, username: $("#f_user").value, email: $("#f_email").value || null,
          department: $("#f_dept").value, designation: $("#f_desig").value, password: $("#f_pw").value || null };
        if ($("#f_role")) body.role = $("#f_role").value;
        const u = await api("/employees", { method: "POST", body });
        const pw = u.temp_password || $("#f_pw").value;
        dlg.innerHTML = `<h2>Account created</h2><p>Share these sign-in details with <b>${esc(u.full_name)}</b>. They will be asked to change the password at first sign-in.</p>
          <p class="small muted">Username</p><div class="temp">${esc(u.username)}</div><p class="small muted">${u.temp_password ? "Temporary password (shown once)" : "Password"}</p><div class="temp">${esc(pw)}</div>
          <div class="dlgactions"><button class="btn-primary" type="button" data-close>Done</button></div>`;
        dlg.querySelector("[data-close]").addEventListener("click", () => { close(); reload().catch(showErr); });
      } catch (err) { $("#derr", dlg).innerHTML = `<div class="error">${esc(err.message)}</div>`; }
    });
  });
}

function editDialog(u, depts, reload) {
  openDialog(`<form id="dform" method="dialog"><h2>Edit ${esc(u.full_name)}</h2>
    <label for="f_name">Full name</label><input id="f_name" required minlength="2" value="${esc(u.full_name)}">
    <label for="f_email">Email</label><input id="f_email" type="email" value="${esc(u.email || "")}">
    <label for="f_dept">Department</label><input id="f_dept" list="deptlist" required value="${esc(u.department)}">${deptList(depts)}
    <label for="f_desig">Designation</label><input id="f_desig" value="${esc(u.designation || "")}">
    ${roleSelect(u.role)}
    <label for="f_active">Status</label><select id="f_active"><option value="1" ${u.is_active ? "selected" : ""}>Active - can sign in</option><option value="0" ${u.is_active ? "" : "selected"}>Inactive - sign-in blocked</option></select>
    <div id="derr"></div><div class="dlgactions"><button class="btn-ghost" type="button" data-close>Cancel</button><button class="btn-primary" type="submit">Save</button></div></form>`,
  (dlg, close) => {
    $("#dform", dlg).addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = { full_name: $("#f_name").value, email: $("#f_email").value || null, department: $("#f_dept").value,
          designation: $("#f_desig").value, is_active: $("#f_active").value === "1" };
        if ($("#f_role")) body.role = $("#f_role").value;
        await api("/employees/" + u.id, { method: "PATCH", body });
        close(); await reload();
      } catch (err) { $("#derr", dlg).innerHTML = `<div class="error">${esc(err.message)}</div>`; }
    });
  });
}

function resetDialog(u) {
  openDialog(`<form id="dform" method="dialog"><h2>Reset password</h2>
    <p>Set a new password for <b>${esc(u.full_name)}</b> (${esc(u.username)}). They will be signed out everywhere and must change it at next sign-in.</p>
    <label for="f_pw">New password (leave blank to generate one)</label><input id="f_pw" type="text" autocomplete="off">
    <div id="derr"></div><div class="dlgactions"><button class="btn-ghost" type="button" data-close>Cancel</button><button class="btn-primary" type="submit">Reset</button></div></form>`,
  (dlg, close) => {
    $("#dform", dlg).addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await api(`/employees/${u.id}/reset-password`, { method: "POST", body: { new_password: $("#f_pw").value || null } });
        dlg.innerHTML = `<h2>Password reset</h2><p>New temporary password for <b>${esc(u.full_name)}</b> (shown once):</p><div class="temp">${esc(r.temp_password)}</div>
          <div class="dlgactions"><button class="btn-primary" type="button" data-close>Done</button></div>`;
        dlg.querySelector("[data-close]").addEventListener("click", close);
      } catch (err) { $("#derr", dlg).innerHTML = `<div class="error">${esc(err.message)}</div>`; }
    });
  });
}

// ----------------------------- profile ------------------------------------
async function pageProfile() {
  view().innerHTML = `<div class="pagehead"><div><h1>Profile &amp; password</h1></div></div>
    <div class="grid2"><div class="panel"><h2>Your details</h2>
      <p><b>${esc(ME.full_name)}</b><br><span class="muted">${esc(ME.username)}${ME.email ? " &middot; " + esc(ME.email) : ""}</span></p>
      <p>Role: <span class="badge role">${esc(ME.role)}</span><br>Department: ${esc(ME.department)}<br>Designation: ${esc(ME.designation || "-")}</p>
      <p class="small muted">Last sign-in: ${ME.last_login ? fmtDT(ME.last_login) : "-"}</p></div>
    <div class="panel"><h2>Change password</h2><form id="pwform">
      <label for="p_cur">Current password</label><input id="p_cur" type="password" autocomplete="current-password" required>
      <label for="p_new">New password (min 8 characters, letters and digits)</label><input id="p_new" type="password" autocomplete="new-password" required minlength="8">
      <label for="p_new2">Confirm new password</label><input id="p_new2" type="password" autocomplete="new-password" required>
      <div id="pmsg"></div><button class="btn-primary" style="margin-top:16px" type="submit">Update password</button></form></div></div>`;
  $("#pwform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#pmsg");
    if ($("#p_new").value !== $("#p_new2").value) { msg.innerHTML = `<div class="error">New passwords do not match.</div>`; return; }
    try {
      ME = await api("/auth/change-password", { method: "POST", body: { current_password: $("#p_cur").value, new_password: $("#p_new").value } });
      msg.innerHTML = `<div class="ok">Password updated.</div>`;
      $("#pwform").reset(); renderBanner();
    } catch (err) { msg.innerHTML = `<div class="error">${esc(err.message)}</div>`; }
  });
}

// ----------------------------- boot ---------------------------------------
(async function init() {
  try { ME = await api("/auth/me"); } catch (_) { return; }       // 401 -> redirected by api()
  renderWho(); renderBanner();
  window.addEventListener("hashchange", route);
  if (ME.must_change_password) location.hash = "#profile";
  await route();
})();

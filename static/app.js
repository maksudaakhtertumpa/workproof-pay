/* WorkProof Pay front end: plain JavaScript + AG Grid Community. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  config: null,
  source: "github",
  analysis: null,      // last AI analysis (summary, evidence index)
  form: null,          // form values used for the analysis
  chat: [],            // [{role, text}]
  itemsGrid: null,
  invoicesGrid: null,
};

// ------------------------------------------------------------ helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function money(value, currency) {
  const n = Number(value || 0);
  return `${currency} ${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

let toastTimer;
function toast(text, kind = "") {
  const el = $("#toast");
  el.textContent = text;
  el.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), kind === "error" ? 7000 : 3500);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    let msg = data?.detail ?? `Request failed (${res.status})`;
    if (Array.isArray(msg)) msg = msg.map(d => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ");
    throw new Error(msg);
  }
  return data;
}

async function withLoading(button, fn) {
  button.disabled = true;
  button.classList.add("loading");
  try { return await fn(); }
  finally { button.disabled = false; button.classList.remove("loading"); }
}

function showStep(n) {
  const stamp = $(".stamp");
  if (stamp) {
    stamp.classList.remove("stamped");
    if (n === 3) { void stamp.offsetWidth; stamp.classList.add("stamped"); }
  }
  $$(".step-panel").forEach(p => p.classList.toggle("hidden", p.dataset.panel !== String(n)));
  $$(".step").forEach(s => {
    const k = Number(s.dataset.step);
    s.classList.toggle("active", k === n);
    s.classList.toggle("done", k < n);
  });
}

// ------------------------------------------------------------ setup

async function loadConfig() {
  const cfg = await api("/api/config");
  state.config = cfg;
  const f = $("#analyzeForm");
  f.client_name.value = cfg.defaults.client_name;
  f.client_email.value = cfg.defaults.client_email;
  f.hourly_rate.value = cfg.defaults.hourly_rate;
  f.since.value = cfg.defaults.since;
  f.until.value = cfg.defaults.until;
  f.currency.innerHTML = cfg.currencies.map(c => `<option ${c === cfg.defaults.currency ? "selected" : ""}>${c}</option>`).join("");
  $("#statusBar").innerHTML =
    `<span class="env" id="envPill">${cfg.environment === "LIVE" ? "Live" : "Sandbox"}</span>` +
    [
      ["PayPal", cfg.paypal_ready],
      ["Gemini", cfg.gemini_ready],
    ].map(([name, ok]) => `<span class="dot ${ok ? "" : "off"}" title="${ok ? `${name} connected` : "Missing key in .env"}">${esc(name)}</span>`).join("");
  if (!cfg.paypal_ready || !cfg.gemini_ready) {
    toast("Some keys are missing. Copy .env.example to .env and add your keys, then restart.", "error");
  }
}

function setupTabs() {
  $$(".tab").forEach(tab => tab.addEventListener("click", () => {
    state.source = tab.dataset.source;
    $$(".tab").forEach(t => {
      t.classList.toggle("active", t === tab);
      t.setAttribute("aria-selected", t === tab);
    });
    $$("[data-source-panel]").forEach(p => p.classList.toggle("hidden", p.dataset.sourcePanel !== state.source));
  }));
}

// ------------------------------------------------------------ step 1: analyze

async function onAnalyze(e) {
  e.preventDefault();
  const f = e.target;
  const body = {
    source: state.source,
    client_name: f.client_name.value.trim(),
    hourly_rate: Number(f.hourly_rate.value),
    currency: f.currency.value,
    context: f.context.value.trim(),
  };
  if (state.source === "github") {
    Object.assign(body, { repo: f.repo.value.trim(), since: f.since.value || null, until: f.until.value || null, author: f.author.value.trim() });
  } else {
    body.text = f.text.value;
  }
  state.form = { ...body, client_email: f.client_email.value.trim() };

  await withLoading($("#analyzeBtn"), async () => {
    toast("Reading your work. This can take up to a minute.");
    try {
      const result = await api("/api/analyze", { method: "POST", body });
      state.analysis = result;
      renderReview(result);
      showStep(2);
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ------------------------------------------------------------ step 2: review

function evidenceRenderer(params) {
  const ids = params.value || [];
  const index = state.analysis?.evidence || {};
  return '<span class="ev-list">' + ids.map(id => {
    const ev = index[id] || {};
    const title = esc(ev.label || id);
    return ev.url
      ? `<a class="ev" href="${esc(ev.url)}" target="_blank" rel="noopener" title="${title}">${esc(id)}</a>`
      : `<span class="ev" title="${title}">${esc(id)}</span>`;
  }).join("") + "</span>";
}

function createItemsGrid() {
  state.itemsGrid = agGrid.createGrid($("#itemsGrid"), {
    columnDefs: [
      { field: "name", headerName: "Item", editable: true, flex: 2, minWidth: 118, wrapText: true, autoHeight: true },
      { field: "description", headerName: "Description", editable: true, flex: 3, minWidth: 150, wrapText: true, autoHeight: true,
        cellEditor: "agLargeTextCellEditor", cellEditorPopup: true },
      { field: "hours", headerName: "Hours", editable: true, width: 78, type: "numericColumn",
        valueParser: p => Math.max(0.25, Math.round(Number(p.newValue) * 4) / 4 || p.oldValue) },
      { headerName: "Amount", width: 98, type: "numericColumn",
        valueGetter: p => p.data.hours * (state.form?.hourly_rate || 0),
        valueFormatter: p => Number(p.value).toFixed(2), cellStyle: { fontWeight: 600 } },
      { field: "evidence", headerName: "Proof", flex: 1.2, minWidth: 84, cellRenderer: evidenceRenderer, sortable: false, autoHeight: true, cellClass: "proof-cell" },
      { headerName: "", width: 46, sortable: false, cellStyle: { paddingLeft: "6px", paddingRight: "6px", textOverflow: "clip" }, cellRenderer: () => `<button class="del-row" title="Remove line">✕</button>`,
        onCellClicked: p => { p.api.applyTransaction({ remove: [p.data] }); updateTotal(); } },
    ],
    rowData: [],
    domLayout: "autoHeight",
    singleClickEdit: true,
    stopEditingWhenCellsLoseFocus: true,
    defaultColDef: { resizable: true, sortable: false },
    onCellValueChanged: updateTotal,
    tooltipShowDelay: 300,
  });
}

function currentItems() {
  const rows = [];
  state.itemsGrid?.forEachNode(n => rows.push(n.data));
  return rows;
}

function updateTotal() {
  const rate = state.form?.hourly_rate || 0;
  const total = currentItems().reduce((sum, r) => sum + Number(r.hours || 0) * rate, 0);
  $("#totalAmount").textContent = money(total, state.form?.currency || "USD");
  state.itemsGrid?.refreshCells({ force: true });
  validateSend();
}

function validateSend() {
  $("#sendBtn").disabled = !($("#confirmBox").checked && currentItems().length > 0);
}

function renderReview(result) {
  $("#summary").value = result.summary;
  const s = result.stats;
  $("#evidenceStats").textContent =
    `AI grouped ${s.evidence_used} of ${s.evidence_count} evidence entries into ${result.line_items.length} line items at ${money(state.form.hourly_rate, state.form.currency)}/hour.`;
  const flags = $("#flags");
  if (result.flags.length) {
    flags.innerHTML = `<b>Please double-check:</b><ul>${result.flags.map(f => `<li>${esc(f)}</li>`).join("")}</ul>`;
    flags.classList.remove("hidden");
  } else {
    flags.classList.add("hidden");
  }
  state.itemsGrid.setGridOption("rowData", result.line_items.map(i => ({ ...i })));
  $("#confirmEmail").textContent = state.form.client_email || "(no email)";
  $("#confirmBox").checked = false;
  updateTotal();
}

async function onSend() {
  const items = currentItems().filter(i => String(i.name || "").trim());
  if (!items.length) return toast("Add at least one line item.", "error");
  const body = {
    client_name: state.form.client_name,
    client_email: state.form.client_email,
    currency: state.form.currency,
    hourly_rate: state.form.hourly_rate,
    due_in_days: Number($("#dueDays").value),
    summary: $("#summary").value,
    line_items: items.map(i => ({ name: i.name, description: i.description || "", hours: Number(i.hours), evidence: i.evidence || [] })),
    evidence: state.analysis?.evidence || {},
  };
  await withLoading($("#sendBtn"), async () => {
    try {
      const res = await api("/api/invoices", { method: "POST", body });
      $("#sentNumber").textContent = `#${res.invoice.number}`;
      $("#sentEmail").textContent = res.invoice.client_email;
      const link = $("#payerLink");
      link.classList.toggle("hidden", !res.invoice.payer_url);
      if (res.invoice.payer_url) link.href = res.invoice.payer_url;
      showStep(3);
      toast(`Invoice sent through PayPal: ${money(res.total, body.currency)}`, "success");
      loadInvoices();
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ------------------------------------------------------------ dashboard

function statusBadge(p) {
  const r = p.data;
  if (!r) return "";
  const label = (s) => esc(String(s || "").toLowerCase().replace(/_/g, " ").replace(/^./, c => c.toUpperCase()));
  if (r.is_paid) return `<span class="badge paid">Paid</span>`;
  if (r.is_open && r.days_overdue > 0) return `<span class="badge overdue">${r.days_overdue} days late</span>`;
  if (r.is_open) return `<span class="badge open">${label(r.status)}</span>`;
  return `<span class="badge other">${label(r.status)}</span>`;
}

function invoiceActions(p) {
  const r = p.data;
  const wrap = document.createElement("span");
  if (r.payer_url) {
    const a = document.createElement("a");
    a.className = "link-btn"; a.href = r.payer_url; a.target = "_blank"; a.rel = "noopener"; a.textContent = "View";
    wrap.appendChild(a);
  }
  if (r.is_open) {
    const b = document.createElement("button");
    b.className = "link-btn"; b.type = "button"; b.textContent = "Remind";
    b.addEventListener("click", () => askAgent(`Draft a payment reminder for invoice ${r.number} (id ${r.id}).`));
    wrap.appendChild(b);
  }
  return wrap;
}

function createInvoicesGrid() {
  state.invoicesGrid = agGrid.createGrid($("#invoicesGrid"), {
    columnDefs: [
      { field: "number", headerName: "No.", width: 74, cellStyle: { fontFamily: "var(--mono)", fontSize: "13px" } },
      { field: "client_name", headerName: "Client", flex: 1.4, minWidth: 110, tooltipField: "client_name" },
      { headerName: "Amount", width: 112, type: "numericColumn",
        valueGetter: p => Number(p.data.amount || 0), valueFormatter: p => money(p.value, p.data.currency) },
      { field: "status", headerName: "Status", width: 128, cellRenderer: statusBadge,
        tooltipValueGetter: p => p.data.due_date ? `Due ${p.data.due_date}` : "" },
      { headerName: "", width: 128, cellRenderer: invoiceActions, sortable: false },
    ],
    rowData: [],
    defaultColDef: { resizable: true, sortable: true },
    domLayout: "autoHeight",
    overlayNoRowsTemplate: `<span style="color:var(--muted)">No invoices yet. Draft your first one from your work.</span>`,
    getRowId: p => p.data.id,
    tooltipShowDelay: 300,
  });
}

async function loadInvoices(quiet = false) {
  if (!state.config?.paypal_ready) return;
  try {
    const { invoices, stats } = await api("/api/invoices");
    state.invoicesGrid.setGridOption("rowData", invoices);
    Object.entries(stats).forEach(([k, v]) => { const el = $(`[data-stat="${k}"]`); if (el) el.textContent = v; });
  } catch (err) {
    if (!quiet) toast(err.message, "error");
  }
}

// ------------------------------------------------------------ agent

function addMsg(role, text, extraClass = "") {
  const div = document.createElement("div");
  div.className = `msg ${role === "user" ? "user" : "bot"} ${extraClass}`;
  div.textContent = text;
  $("#chat").appendChild(div);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return div;
}

function addProposal(p) {
  const card = document.createElement("div");
  card.className = "proposal";
  card.innerHTML = `
    <div class="proposal-head"><span>Reminder draft for ${esc(p.client_name)}, invoice ${esc(p.invoice_number)}</span><span>${esc(p.amount)}</span></div>
    <input class="p-subject" maxlength="200">
    <textarea class="p-message" rows="5" maxlength="4000"></textarea>
    <button class="btn btn-pay btn-sm p-send" type="button">Send via PayPal</button>`;
  $(".p-subject", card).value = p.subject;
  $(".p-message", card).value = p.message;
  $(".p-send", card).addEventListener("click", async (e) => {
    await withLoading(e.currentTarget, async () => {
      try {
        await api(`/api/invoices/${encodeURIComponent(p.invoice_id)}/remind`, {
          method: "POST",
          body: { subject: $(".p-subject", card).value, message: $(".p-message", card).value },
        });
        card.innerHTML = `<span class="sent-ok">✓ Reminder for #${esc(p.invoice_number)} sent by PayPal to ${esc(p.client_name)}.</span>`;
        toast("Reminder sent through PayPal.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
  $("#chat").appendChild(card);
  $("#chat").scrollTop = $("#chat").scrollHeight;
}

let agentBusy = false;
async function askAgent(text) {
  text = text.trim();
  if (!text || agentBusy) return;
  agentBusy = true;
  state.chat.push({ role: "user", text });
  addMsg("user", text);
  const thinking = addMsg("bot", "Checking your PayPal invoices", "thinking");
  try {
    const res = await api("/api/agent", { method: "POST", body: { messages: state.chat } });
    thinking.remove();
    const msg = addMsg("bot", res.reply);
    if (res.tools_used?.length) {
      const t = document.createElement("div");
      t.className = "tools";
      t.textContent = "Looked up: " + res.tools_used.join(", ");
      msg.appendChild(t);
    }
    state.chat.push({ role: "assistant", text: res.reply });
    res.proposals.forEach(addProposal);
  } catch (err) {
    thinking.remove();
    addMsg("bot", `Sorry, something went wrong: ${err.message}`);
    state.chat.pop();
  } finally {
    agentBusy = false;
  }
}

// ------------------------------------------------------------ wire up

document.addEventListener("DOMContentLoaded", async () => {
  setupTabs();
  createItemsGrid();
  createInvoicesGrid();
  $("#analyzeForm").addEventListener("submit", onAnalyze);
  $("#backBtn").addEventListener("click", () => showStep(1));
  $("#newInvoiceBtn").addEventListener("click", () => showStep(1));
  $("#addRowBtn").addEventListener("click", () => {
    state.itemsGrid.applyTransaction({ add: [{ name: "New item", description: "", hours: 1, evidence: [] }] });
    updateTotal();
  });
  $("#confirmBox").addEventListener("change", validateSend);
  $("#sendBtn").addEventListener("click", onSend);
  $("#refreshBtn").addEventListener("click", () => loadInvoices());
  $("#chatForm").addEventListener("submit", e => { e.preventDefault(); const i = $("#chatInput"); askAgent(i.value); i.value = ""; });
  $$(".chip").forEach(c => c.addEventListener("click", () => askAgent(c.textContent)));

  try { await loadConfig(); } catch (err) { toast(err.message, "error"); }
  loadInvoices(true);
  setInterval(() => { if (!document.hidden) loadInvoices(true); }, 20000);
});

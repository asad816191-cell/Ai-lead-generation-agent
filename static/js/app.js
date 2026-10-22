(() => {
  "use strict";
  const $ = (sel) => document.querySelector(sel);
  const COLUMNS = JSON.parse($("#lead-columns").textContent);
  const PAGE_SIZE = 25;
  const NOT_FOUND = "Not Found";

  let pollTimer = null;
  let currentId = null;
  let allLeads = [];
  let filtered = [];
  let page = 1;

  // ------------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function safeHref(url) {
    try {
      const u = new URL(url);
      return (u.protocol === "http:" || u.protocol === "https:") ? u.href : null;
    } catch (_) { return null; }
  }

  function notify(kind, text) {
    const box = $("#alerts");
    const el = document.createElement("div");
    el.className = `alert alert-${kind} alert-dismissible fade show`;
    el.setAttribute("role", "alert");
    el.innerHTML = `${esc(text)}<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>`;
    box.prepend(el);
    if (kind === "success") setTimeout(() => el.remove(), 8000);
  }

  async function api(url, options) {
    const res = await fetch(url, options);
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
    if (!res.ok) throw new Error(data.error || `Request failed (HTTP ${res.status})`);
    return data;
  }

  // ------------------------------------------------------------- provider badges
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      const badge = (on, label) =>
        `<span class="badge rounded-pill ${on ? "provider-on" : "provider-off"}"><i class="bi ${on ? "bi-check-circle" : "bi-dash-circle"}"></i> ${label}</span>`;
      $("#provider-badges").innerHTML =
        badge(s.google_places, "Google Places") + badge(s.tavily, "Tavily") + badge(s.openai, "OpenAI");
      if (!s.google_places && !s.tavily) {
        notify("warning", "No search API key is configured yet. Add GOOGLE_PLACES_API_KEY and/or TAVILY_API_KEY to your .env file, then restart the app.");
      }
      $("#count").max = s.max_leads;
    } catch (e) { /* non-fatal */ }
  }

  // ---------------------------------------------------------------- recent list
  async function loadRecent() {
    try {
      const { searches } = await api("/api/searches?limit=8");
      const box = $("#recent");
      if (!searches.length) {
        box.innerHTML = '<div class="text-secondary small py-2">No searches yet.</div>';
        return;
      }
      box.innerHTML = searches.map((s) => `
        <div class="list-group-item">
          <a href="/?search=${s.id}" data-id="${s.id}" class="js-open d-block">
            <div class="recent-title text-truncate">${esc(s.industry)}</div>
            <div class="small text-secondary text-truncate">${esc(s.location)}</div>
            <div class="small text-secondary">${s.status === "completed" ? esc(s.result_count) + " leads" : esc(s.status)} · ${esc((s.created_at || "").slice(0, 16))}</div>
          </a>
        </div>`).join("");
      box.querySelectorAll(".js-open").forEach((a) => a.addEventListener("click", (ev) => {
        ev.preventDefault();
        openSearch(Number(a.dataset.id));
      }));
    } catch (e) { /* non-fatal */ }
  }

  // ------------------------------------------------------------------- progress
  function showProgress(search) {
    $("#progress-card").classList.remove("d-none");
    $("#progress-title").textContent =
      `Searching: ${search.industry || ""} in ${search.location || ""}`;
    const pct = Math.max(0, Math.min(100, search.progress || 0));
    $("#progress-bar").style.width = pct + "%";
    $("#progress-pct").textContent = pct + "%";
    $("#progress-msg").textContent = search.message || "";
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  let polling = false;

  async function poll(id) {
    if (polling) return;  // never let two requests overlap
    polling = true;
    try {
      const { search, leads } = await api(`/api/search/${id}`);
      if (id !== currentId || !pollTimer) return;  // stale response (finished or switched search)
      if (search.status === "completed") {
        stopPolling();
        $("#progress-card").classList.add("d-none");
        setBusy(false);
        renderResults(search, leads);
        (search.warnings || []).forEach((w) => notify("warning", w));
        if (leads.length) notify("success", `Search finished: ${leads.length} leads.`);
        else notify("info", search.message || "No leads found.");
        loadRecent();
      } else if (search.status === "failed") {
        stopPolling();
        $("#progress-card").classList.add("d-none");
        setBusy(false);
        notify("danger", search.message || "The search failed.");
        (search.warnings || []).forEach((w) => notify("warning", w));
        loadRecent();
      } else {
        showProgress(search);
      }
    } catch (e) {
      stopPolling();
      setBusy(false);
      notify("danger", e.message);
    } finally {
      polling = false;
    }
  }

  function setBusy(busy) {
    const btn = $("#search-btn");
    btn.disabled = busy;
    btn.innerHTML = busy
      ? '<span class="spinner-border spinner-border-sm"></span> Searching'
      : '<i class="bi bi-search"></i> Search';
  }

  function track(id) {
    currentId = id;
    stopPolling();
    pollTimer = setInterval(() => poll(id), 1500);
    poll(id);
  }

  // -------------------------------------------------------------------- results
  function cell(col, lead) {
    const value = lead[col.key];
    if (!value || value === NOT_FOUND) return `<td><span class="nf">${NOT_FOUND}</span></td>`;
    if (col.key === "website" || col.key === "maps_url") {
      const href = safeHref(value);
      return href ? `<td><a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(value.replace(/^https?:\/\/(www\.)?/, ""))}</a></td>`
                  : `<td>${esc(value)}</td>`;
    }
    if (col.key === "source_url") {
      const urls = value.split(" | ");
      const href = safeHref(urls[0]);
      const more = urls.length > 1 ? ` <span class="badge badge-soft">+${urls.length - 1}</span>` : "";
      return `<td title="${esc(value)}">${href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">source</a>` : esc(urls[0])}${more}</td>`;
    }
    if (col.key === "verification_status" || col.key === "email_status") {
      return `<td class="wide" title="${esc(value)}">${esc(value)}</td>`;
    }
    return `<td title="${esc(value)}">${esc(value)}</td>`;
  }

  function renderTable() {
    const total = filtered.length;
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    page = Math.min(page, pages);
    const slice = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    $("#results-body").innerHTML = slice.map((lead) => `<tr>${COLUMNS.map((c) => cell(c, lead)).join("")}</tr>`).join("")
      || `<tr><td colspan="${COLUMNS.length}" class="text-center text-secondary py-4">No matching rows.</td></tr>`;
    $("#page-info").textContent = total
      ? `Rows ${(page - 1) * PAGE_SIZE + 1}-${Math.min(page * PAGE_SIZE, total)} of ${total}`
      : "0 rows";
    $("#prev-page").disabled = page <= 1;
    $("#next-page").disabled = page >= pages;
  }

  function renderResults(search, leads) {
    allLeads = leads;
    filtered = leads.slice();
    page = 1;
    $("#table-filter").value = "";
    $("#results-card").classList.remove("d-none");
    $("#results-title").textContent = `${leads.length} leads: ${search.industry} in ${search.location}`;
    const withEmail = leads.filter((l) => l.email && l.email !== NOT_FOUND).length;
    const withPhone = leads.filter((l) => l.phone && l.phone !== NOT_FOUND).length;
    $("#results-sub").textContent =
      `Requested ${search.requested_count} · ${withPhone} with phone · ${withEmail} with email · search #${search.id}`;
    $("#results-head").innerHTML = `<tr>${COLUMNS.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr>`;
    $("#dl-xlsx").href = `/api/search/${search.id}/export/xlsx`;
    $("#dl-csv").href = `/api/search/${search.id}/export/csv`;
    const disabled = !leads.length;
    $("#dl-xlsx").classList.toggle("disabled", disabled);
    $("#dl-csv").classList.toggle("disabled", disabled);
    renderTable();
  }

  async function openSearch(id) {
    try {
      const { search, leads } = await api(`/api/search/${id}`);
      history.replaceState(null, "", `/?search=${id}`);
      currentId = id;
      stopPolling();
      $("#progress-card").classList.add("d-none");
      if (search.status === "completed") {
        renderResults(search, leads);
      } else if (search.status === "failed") {
        $("#results-card").classList.add("d-none");
        notify("danger", search.message || "That search failed.");
      } else {
        setBusy(true);
        track(id);
      }
    } catch (e) { notify("danger", e.message); }
  }

  // ----------------------------------------------------------------------- events
  $("#search-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const prompt = $("#prompt").value.trim();
    const industry = $("#industry").value.trim();
    const location = $("#location").value.trim();
    const countRaw = $("#count").value.trim();

    if (!prompt && !(industry && location)) {
      notify("warning", "Enter a prompt, or fill in both Industry and Location.");
      return;
    }
    if (countRaw && !/^\d+$/.test(countRaw)) {
      notify("warning", "Lead quantity must be a whole number.");
      return;
    }
    setBusy(true);
    $("#results-card").classList.add("d-none");
    try {
      const started = await api("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt, industry, location,
          count: countRaw ? Number(countRaw) : null,
          enrich: $("#enrich").checked,
        }),
      });
      notify("info", `Understood: ${started.count} × "${started.industry}" in "${started.location}"` +
        (started.filters.length ? ` (${started.filters.join("; ")})` : "") +
        (started.parser === "llm" ? " — parsed with the LLM." : "."));
      showProgress({ industry: started.industry, location: started.location, progress: 1, message: "Starting" });
      history.replaceState(null, "", `/?search=${started.id}`);
      track(started.id);
    } catch (e) {
      setBusy(false);
      notify("danger", e.message);
    }
  });

  $("#table-filter").addEventListener("input", (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    filtered = q
      ? allLeads.filter((l) => Object.values(l).some((v) => String(v).toLowerCase().includes(q)))
      : allLeads.slice();
    page = 1;
    renderTable();
  });
  $("#prev-page").addEventListener("click", () => { page -= 1; renderTable(); });
  $("#next-page").addEventListener("click", () => { page += 1; renderTable(); });

  // ------------------------------------------------------------------------ boot
  loadStatus();
  loadRecent();
  const initial = new URLSearchParams(location.search).get("search");
  if (initial && /^\d+$/.test(initial)) openSearch(Number(initial));
})();

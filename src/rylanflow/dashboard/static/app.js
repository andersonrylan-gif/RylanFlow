// Vanilla JS, no build step, no CDN — this page has to work fully offline.
(() => {
  "use strict";

  const TOKEN = new URLSearchParams(location.search).get("t") || "";
  const POLL_MS = 3000;

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: { "X-RylanFlow-Token": TOKEN, ...(options.headers || {}) },
    });
    if (!res.ok) throw new Error(`${options.method || "GET"} ${path} -> ${res.status}`);
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }

  // --- navigation ---

  const views = { home: document.getElementById("view-home"), settings: document.getElementById("view-settings") };
  for (const item of document.querySelectorAll(".nav-item")) {
    item.addEventListener("click", () => {
      const target = item.dataset.view;
      if (!(target in views)) return;
      document.querySelector(".nav-item.active")?.classList.remove("active");
      item.classList.add("active");
      for (const [name, el] of Object.entries(views)) el.hidden = name !== target;
      if (target === "settings") loadSettings();
    });
  }

  // --- stats ---

  function formatNumber(n) {
    return Math.round(n).toLocaleString();
  }

  async function refreshStats() {
    const stats = await api("/api/stats");
    document.getElementById("stat-today").textContent = formatNumber(stats.words_today);
    document.getElementById("stat-week").textContent = formatNumber(stats.words_week);
    document.getElementById("stat-total").textContent = formatNumber(stats.dictations_total);
    document.getElementById("stat-saved").textContent = formatNumber(stats.minutes_saved);
  }

  // --- dictation list ---

  const list = document.getElementById("list");
  const searchInput = document.getElementById("search");
  let currentQuery = "";
  const expanded = new Set(); // ids whose text the user clicked to expand
  const confirming = new Set(); // ids currently showing the "delete this?" state
  const copiedUntil = new Map(); // id -> timestamp the "Copied ✓" label should revert

  function dayLabel(date) {
    const now = new Date();
    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const diffDays = Math.round((startOfDay(now) - startOfDay(date)) / 86400000);
    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Yesterday";
    return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  }

  function timeLabel(date) {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function renderList(rows) {
    if (rows.length === 0) {
      list.innerHTML = `<div class="empty">${
        currentQuery ? "No dictations match your search." : "No dictations yet — hold your hotkey and start talking."
      }</div>`;
      return;
    }

    let html = "";
    let lastDay = null;
    for (const row of rows) {
      const created = new Date(row.created_at);
      const day = dayLabel(created);
      if (day !== lastDay) {
        html += `<div class="day-heading">${day}</div>`;
        lastDay = day;
      }
      const isCopied = (copiedUntil.get(row.id) || 0) > Date.now();
      const isConfirming = confirming.has(row.id);
      const isExpanded = expanded.has(row.id);
      html += `
        <div class="card" data-id="${row.id}">
          <div class="card-top">
            <span>${timeLabel(created)}</span>
            ${row.app_name ? `<span class="app-name">${escapeHtml(row.app_name)}</span>` : ""}
          </div>
          <div class="card-text${isExpanded ? " expanded" : ""}" data-action="toggle">${escapeHtml(row.text)}</div>
          <div class="card-actions">
            ${
              isConfirming
                ? `<span>Delete this?</span>
                   <button class="btn confirm" data-action="confirm-delete">Delete</button>
                   <button class="btn" data-action="cancel-delete">Cancel</button>`
                : `<button class="btn${isCopied ? " copied" : ""}" data-action="copy">${isCopied ? "Copied ✓" : "Copy"}</button>
                   <button class="btn danger" data-action="delete">Delete</button>`
            }
          </div>
        </div>`;
    }
    list.innerHTML = html;
  }

  list.addEventListener("click", async (e) => {
    const card = e.target.closest(".card");
    if (!card) return;
    const id = Number(card.dataset.id);
    const action = e.target.dataset.action;

    if (action === "toggle") {
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      refreshDictations();
    } else if (action === "copy") {
      const text = card.querySelector(".card-text").textContent;
      await api("/api/copy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      copiedUntil.set(id, Date.now() + 1500);
      refreshDictations();
      setTimeout(refreshDictations, 1550);
    } else if (action === "delete") {
      confirming.add(id);
      refreshDictations();
    } else if (action === "cancel-delete") {
      confirming.delete(id);
      refreshDictations();
    } else if (action === "confirm-delete") {
      confirming.delete(id);
      await api(`/api/dictations/${id}`, { method: "DELETE" });
      refreshDictations();
      refreshStats();
    }
  });

  async function refreshDictations() {
    const q = currentQuery ? `&q=${encodeURIComponent(currentQuery)}` : "";
    const rows = await api(`/api/dictations?limit=200${q}`);
    renderList(rows);
  }

  let searchDebounce;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      currentQuery = searchInput.value.trim();
      refreshDictations();
    }, 200);
  });

  // --- settings ---

  function fillSelect(select, choices, current) {
    select.innerHTML = "";
    for (const [label, value] of Object.entries(choices)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === current;
      select.appendChild(option);
    }
  }

  async function putSettings(changes) {
    const settings = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
    renderSettings(settings);
  }

  function renderSettings(settings) {
    fillSelect(document.getElementById("setting-hotkey"), settings.available_hotkeys, settings.hotkey);
    fillSelect(document.getElementById("setting-model"), settings.available_models, settings.model);
    fillSelect(document.getElementById("setting-overlay"), settings.available_overlay_positions, settings.overlay);
    for (const key of ["sounds", "remove_fillers", "start_at_login"]) {
      document.getElementById(`setting-${key}`).classList.toggle("on", !!settings[key]);
    }
  }

  async function loadSettings() {
    renderSettings(await api("/api/settings"));
  }

  document.getElementById("setting-hotkey").addEventListener("change", (e) => putSettings({ hotkey: e.target.value }));
  document.getElementById("setting-model").addEventListener("change", (e) => putSettings({ model: e.target.value }));
  document.getElementById("setting-overlay").addEventListener("change", (e) => putSettings({ overlay: e.target.value }));
  for (const key of ["sounds", "remove_fillers", "start_at_login"]) {
    document.getElementById(`setting-${key}`).addEventListener("click", (e) => {
      const nowOn = !e.currentTarget.classList.contains("on");
      e.currentTarget.classList.toggle("on", nowOn); // optimistic; renderSettings confirms it
      putSettings({ [key]: nowOn });
    });
  }

  // --- polling: keep the list fresh while the page is open, skip while mid-interaction ---

  async function poll() {
    if (document.visibilityState === "visible" && confirming.size === 0) {
      await Promise.all([refreshDictations(), refreshStats()]).catch(() => {});
    }
  }

  refreshDictations();
  refreshStats();
  setInterval(poll, POLL_MS);
})();

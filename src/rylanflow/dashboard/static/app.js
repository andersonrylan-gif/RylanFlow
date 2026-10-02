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

  const views = {
    home: document.getElementById("view-home"),
    meetings: document.getElementById("view-meetings"),
    settings: document.getElementById("view-settings"),
  };
  for (const item of document.querySelectorAll(".nav-item")) {
    item.addEventListener("click", () => {
      const target = item.dataset.view;
      if (!(target in views)) return;
      document.querySelector(".nav-item.active")?.classList.remove("active");
      item.classList.add("active");
      for (const [name, el] of Object.entries(views)) el.hidden = name !== target;
      if (target === "settings") loadSettings();
      if (target === "meetings") {
        showMeetingsList();
        refreshMeetingsList();
      }
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
    const action = e.target.closest("[data-action]")?.dataset.action;

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
    for (const key of ["sounds", "remove_fillers", "start_at_login", "auto_record_meetings"]) {
      document.getElementById(`setting-${key}`).classList.toggle("on", !!settings[key]);
    }
  }

  async function loadSettings() {
    renderSettings(await api("/api/settings"));
  }

  document.getElementById("setting-hotkey").addEventListener("change", (e) => putSettings({ hotkey: e.target.value }));
  document.getElementById("setting-model").addEventListener("change", (e) => putSettings({ model: e.target.value }));
  document.getElementById("setting-overlay").addEventListener("change", (e) => putSettings({ overlay: e.target.value }));
  for (const key of ["sounds", "remove_fillers", "start_at_login", "auto_record_meetings"]) {
    document.getElementById(`setting-${key}`).addEventListener("click", (e) => {
      const nowOn = !e.currentTarget.classList.contains("on");
      e.currentTarget.classList.toggle("on", nowOn); // optimistic; renderSettings confirms it
      putSettings({ [key]: nowOn });
    });
  }

  // --- meetings ---

  const SPEAKER_COLORS = ["#007aff", "#ff9500", "#34c759", "#af52de", "#ff3b30", "#5ac8fa"];
  const STATUS_LABELS = { recording: "Recording", processing: "Processing speakers…", done: "Done", failed: "Failed" };

  const meetingsListView = document.getElementById("meetings-list-view");
  const meetingDetailView = document.getElementById("meeting-detail-view");
  const meetingsList = document.getElementById("meetings-list");
  const meetingToggle = document.getElementById("meeting-toggle");
  const meetingsSearchInput = document.getElementById("meetings-search");
  let meetingsCache = [];
  let currentMeetingId = null; // set while the detail view is open
  let currentMeetingsQuery = "";
  const meetingConfirming = new Set();

  function showMeetingsList() {
    currentMeetingId = null;
    meetingsListView.hidden = false;
    meetingDetailView.hidden = true;
  }

  function mmss(seconds) {
    const s = Math.max(0, Math.round(seconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function meetingDuration(meeting) {
    if (meeting.status === "recording") return "Recording…";
    if (!meeting.ended_at) return "—";
    const seconds = (new Date(meeting.ended_at) - new Date(meeting.started_at)) / 1000;
    return mmss(seconds);
  }

  function renderMeetingsList(rows) {
    meetingsCache = rows;
    const anyActive = rows.some((m) => m.status === "recording");
    meetingToggle.textContent = anyActive ? "Stop meeting recording" : "Start meeting recording";
    meetingToggle.classList.toggle("danger-solid", anyActive);

    if (rows.length === 0) {
      meetingsList.innerHTML = `<div class="empty">${
        currentMeetingsQuery
          ? "No meetings match your search."
          : "No meetings yet — start one from here or the menu bar."
      }</div>`;
      return;
    }
    let html = "";
    for (const m of rows) {
      const isConfirming = meetingConfirming.has(m.id);
      html += `
        <div class="card meeting-card" data-id="${m.id}">
          <div class="meeting-card-main" data-action="open">
            <div class="card-top">
              <span class="chip ${m.status}">${STATUS_LABELS[m.status] || m.status}</span>
              <span>${new Date(m.started_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</span>
              <span>${meetingDuration(m)}</span>
            </div>
            <div class="card-text" style="-webkit-line-clamp: 1;">${escapeHtml(m.title || `Meeting with ${m.source_app || "unknown app"}`)}</div>
          </div>
          <div class="card-actions">
            ${
              isConfirming
                ? `<span>Delete this?</span>
                   <button class="btn confirm" data-action="confirm-delete-meeting">Delete</button>
                   <button class="btn" data-action="cancel-delete-meeting">Cancel</button>`
                : `<button class="btn danger" data-action="delete-meeting">Delete</button>`
            }
          </div>
        </div>`;
    }
    meetingsList.innerHTML = html;
  }

  async function refreshMeetingsList() {
    const q = currentMeetingsQuery ? `?q=${encodeURIComponent(currentMeetingsQuery)}` : "";
    renderMeetingsList(await api(`/api/meetings${q}`));
  }

  let meetingsSearchDebounce;
  meetingsSearchInput.addEventListener("input", () => {
    clearTimeout(meetingsSearchDebounce);
    meetingsSearchDebounce = setTimeout(() => {
      currentMeetingsQuery = meetingsSearchInput.value.trim();
      refreshMeetingsList();
    }, 200);
  });

  meetingsList.addEventListener("click", async (e) => {
    const card = e.target.closest(".meeting-card");
    if (!card) return;
    const id = Number(card.dataset.id);
    const action = e.target.closest("[data-action]")?.dataset.action;

    if (action === "open") {
      showMeetingDetail(id);
    } else if (action === "delete-meeting") {
      meetingConfirming.add(id);
      renderMeetingsList(meetingsCache);
    } else if (action === "cancel-delete-meeting") {
      meetingConfirming.delete(id);
      renderMeetingsList(meetingsCache);
    } else if (action === "confirm-delete-meeting") {
      meetingConfirming.delete(id);
      await api(`/api/meetings/${id}`, { method: "DELETE" });
      refreshMeetingsList();
    }
  });

  meetingToggle.addEventListener("click", async () => {
    const anyActive = meetingsCache.some((m) => m.status === "recording");
    await api(`/api/meetings/${anyActive ? "stop" : "start"}`, { method: "POST" });
    refreshMeetingsList();
  });

  function speakerColor(speakerId, speakers) {
    const index = speakers.filter((s) => !s.is_me).findIndex((s) => s.id === speakerId);
    const speaker = speakers.find((s) => s.id === speakerId);
    if (speaker && speaker.is_me) return SPEAKER_COLORS[0];
    return SPEAKER_COLORS[1 + (index % (SPEAKER_COLORS.length - 1))];
  }

  function renderMeetingDetail(meeting) {
    document.getElementById("meeting-detail-title").textContent =
      meeting.title || `Meeting with ${meeting.source_app || "unknown app"}`;
    document.getElementById("meeting-detail-meta").textContent =
      `${new Date(meeting.started_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })} · ${STATUS_LABELS[meeting.status] || meeting.status}`;

    const transcript = document.getElementById("meeting-transcript");
    if (meeting.segments.length === 0) {
      transcript.innerHTML = `<div class="empty">${meeting.status === "recording" ? "Listening…" : "No speech was captured."}</div>`;
      return;
    }
    const bySpeaker = Object.fromEntries(meeting.speakers.map((s) => [s.id, s]));
    const wasAtBottom = transcript.scrollTop + transcript.clientHeight >= transcript.scrollHeight - 40;
    transcript.innerHTML = meeting.segments
      .map((seg) => {
        const speaker = bySpeaker[seg.speaker_id];
        const label = speaker ? speaker.display_name || speaker.label : "Unknown";
        const color = seg.speaker_id ? speakerColor(seg.speaker_id, meeting.speakers) : "#8e8e93";
        return `
          <div class="transcript-line">
            <span class="transcript-speaker" style="color:${color}">${escapeHtml(label)}</span>
            <span class="transcript-time">${mmss(seg.start_s)}</span>
            <div class="transcript-text">${escapeHtml(seg.text)}</div>
          </div>`;
      })
      .join("");
    if (meeting.status === "recording" && wasAtBottom) transcript.scrollTop = transcript.scrollHeight;
  }

  async function showMeetingDetail(id) {
    currentMeetingId = id;
    meetingsListView.hidden = true;
    meetingDetailView.hidden = false;
    renderMeetingDetail(await api(`/api/meetings/${id}`));
  }

  document.getElementById("meeting-back").addEventListener("click", showMeetingsList);

  document.getElementById("meeting-copy-transcript").addEventListener("click", async () => {
    const meeting = await api(`/api/meetings/${currentMeetingId}`);
    const bySpeaker = Object.fromEntries(meeting.speakers.map((s) => [s.id, s]));
    const text = meeting.segments
      .map((seg) => {
        const speaker = bySpeaker[seg.speaker_id];
        const label = speaker ? speaker.display_name || speaker.label : "Unknown";
        return `[${mmss(seg.start_s)}] ${label}: ${seg.text}`;
      })
      .join("\n");
    await api("/api/copy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  });

  document.getElementById("meeting-download").addEventListener("click", async () => {
    const res = await fetch(`/api/meetings/${currentMeetingId}/export.md`, {
      headers: { "X-RylanFlow-Token": TOKEN },
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `meeting-${currentMeetingId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  });

  // --- polling: keep the list fresh while the page is open, skip while mid-interaction ---

  async function poll() {
    if (document.visibilityState !== "visible") return;
    const tasks = [];
    if (confirming.size === 0) tasks.push(refreshDictations(), refreshStats());
    if (!views.meetings.hidden) {
      tasks.push(
        currentMeetingId === null
          ? refreshMeetingsList()
          : showMeetingDetail(currentMeetingId)
      );
    }
    await Promise.all(tasks).catch(() => {});
  }

  refreshDictations();
  refreshStats();
  setInterval(poll, POLL_MS);
})();

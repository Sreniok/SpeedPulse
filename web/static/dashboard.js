let speedChart;
let latencyChart;
let thresholdChart;
let slaBreakdownChart;
let hoverGuidePluginRegistered = false;
let latestRows = [];
let currentPayload = null;
let currentPage = 1;
let currentSort = { key: "timestamp_iso", direction: "desc" };
let manualRunInFlight = false;
let weeklyReportInFlight = false;
let runModalTimerId = null;
let runModalStartedAt = 0;
let runModalAutoCloseId = null;
let runStatusPollId = null;
let runStatusRequestInFlight = false;
let lastHandledRunCompletion = "";
let completionWatchId = null;
let completionWatchRequestInFlight = false;
let lastSeenCompletionSequence = 0;
let initialMetricsLoaded = false;
let applicationClockTimerId = null;
let applicationClockState = null;
let currentServerLabel = "Auto (nearest server)";
let serverSettingsLoading = false;
let serverSettingsSaving = false;
let serverOptions = [];
const heroMetricLastValues = new Map();
const uiCore = window.SpeedPulseUiCore || null;
const csrfToken =
  document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
  "";

function byId(id) {
  if (uiCore && typeof uiCore.byId === "function") {
    return uiCore.byId(id);
  }
  return document.getElementById(id);
}

function isDialogElement(element) {
  if (uiCore && typeof uiCore.isDialogElement === "function") {
    return uiCore.isDialogElement(element);
  }
  return (
    typeof HTMLDialogElement !== "undefined" &&
    element instanceof HTMLDialogElement
  );
}

function modalIsOpen(id) {
  if (uiCore && typeof uiCore.modalIsOpen === "function") {
    return uiCore.modalIsOpen(id);
  }

  const modal = byId(id);
  if (!modal) return false;
  if (isDialogElement(modal)) return modal.open;
  return !modal.classList.contains("hidden");
}

function syncBodyModalState() {
  if (uiCore && typeof uiCore.syncBodyModalState === "function") {
    uiCore.syncBodyModalState(["server-modal", "run-modal"]);
    return;
  }

  document.body.classList.toggle("modal-open", modalIsOpen("server-modal") || modalIsOpen("run-modal"));
}

function updateServerSelectDisabled() {
  const defaultSelect = byId("server-select");
  if (defaultSelect) {
    defaultSelect.disabled =
      manualRunInFlight || serverSettingsLoading || serverSettingsSaving;
  }

  const modalSelect = byId("server-modal-select");
  if (modalSelect) {
    modalSelect.disabled = manualRunInFlight || serverSettingsLoading;
  }

  const startButton = byId("server-modal-start");
  if (startButton) {
    startButton.disabled = manualRunInFlight || serverSettingsLoading;
  }
}

function formatElapsed(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (!minutes) return `Elapsed ${remainder}s`;
  return `Elapsed ${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

function updateRunModalTimer() {
  const timer = byId("run-modal-timer");
  if (!timer) return;
  const elapsed = Math.max(
    0,
    Math.floor((Date.now() - runModalStartedAt) / 1000),
  );
  timer.textContent = formatElapsed(elapsed);
}

function setRunButtonState(isRunning) {
  const button = byId("run-test");
  if (!button) return;
  button.disabled = isRunning;
  button.textContent = isRunning ? "Running..." : "Manual speed test";
  updateServerSelectDisabled();
}

function populateSelectOptions(select, options, selectedId) {
  if (!select) return;

  select.textContent = "";
  for (const option of options) {
    const element = document.createElement("option");
    element.value = String(option.id || "");
    element.textContent = option.label;
    if (element.value === selectedId) {
      element.selected = true;
    }
    select.appendChild(element);
  }
}

function findServerLabel(serverId) {
  const selectedId = String(serverId || "");
  if (!selectedId) return "Auto (nearest server)";

  const option = serverOptions.find(
    (entry) => String(entry.id || "") === selectedId,
  );
  return option?.label || `Pinned server #${selectedId}`;
}

function currentManualServerId() {
  const modalSelect = byId("server-modal-select");
  if (modalSelect?.value !== undefined) {
    return String(modalSelect.value || "");
  }

  return String(
    byId("server-select")?.value || currentPayload?.server_selection_id || "",
  );
}

function syncServerModalSelection(selectedId = null) {
  const modalSelect = byId("server-modal-select");
  if (!modalSelect) return;

  const defaultSelect = byId("server-select");
  const nextValue =
    selectedId === null
      ? String(
          (defaultSelect ? defaultSelect.value : "") ||
            currentPayload?.server_selection_id ||
            "",
        )
      : String(selectedId || "");
  modalSelect.value = nextValue;
}

function openServerModal() {
  const modal = byId("server-modal");
  if (!modal || manualRunInFlight) return;

  syncServerModalSelection();

  if (isDialogElement(modal) && typeof modal.showModal === "function") {
    if (!modal.open) {
      modal.showModal();
    }
  } else {
    modal.classList.remove("hidden");
  }

  syncBodyModalState();
  byId("server-modal-select")?.focus();
}

function closeServerModal() {
  const modal = byId("server-modal");
  if (!modal) return;

  if (isDialogElement(modal) && modal.open) {
    modal.close();
  } else {
    modal.classList.add("hidden");
  }

  syncBodyModalState();
}

function runStageProgress(status) {
  const stage = String(status.stage || "").toLowerCase();
  if (status.status === "completed") return 100;
  if (status.status === "failed") return 100;
  if (stage.includes("upload")) return 78;
  if (stage.includes("download")) return 58;
  if (stage.includes("latency")) return 42;
  if (
    stage.includes("rendering") ||
    stage.includes("checking") ||
    stage.includes("saving")
  )
    return 90;
  if (stage.includes("reading")) return 76;
  if (stage.includes("measuring")) return 58;
  if (stage.includes("connecting")) return 36;
  if (stage.includes("selecting")) return 22;
  if (stage.includes("preparing") || stage.includes("launching")) return 12;
  return 8;
}

function parseRunMetrics(status) {
  const logs = status.logs || [];
  const patterns = {
    download: [/Download:\s+([\d.]+)\s*Mbps/i],
    upload: [/Upload:\s+([\d.]+)\s*Mbps/i],
    ping: [/Idle\s+Latency:\s+([\d.]+)\s*ms/i, /Ping:\s+([\d.]+)\s*ms/i],
  };

  const metrics = { download: null, upload: null, ping: null };
  for (const [key, keyPatterns] of Object.entries(patterns)) {
    for (const pattern of keyPatterns) {
      for (let index = logs.length - 1; index >= 0; index -= 1) {
        const match = String(logs[index] || "").match(pattern);
        if (match) {
          metrics[key] = Number(match[1]);
          break;
        }
      }
      if (metrics[key] !== null) {
        break;
      }
    }
  }

  return metrics;
}

function renderLiveMetric(id, value, unit, note) {
  byId(`run-live-${id}`).textContent =
    typeof value === "number" && !Number.isNaN(value)
      ? `${safeFixed(value)} ${unit}`
      : `-- ${unit}`;
  byId(`run-live-${id}-note`).textContent = note;
}

function renderRunLiveMetrics(status) {
  const parsed = parseRunMetrics(status);
  const stage = String(status.stage || "").toLowerCase();

  let download = parsed.download;
  let upload = parsed.upload;
  let ping = parsed.ping;
  let downloadNote =
    parsed.download !== null ? "Measured result" : "Waiting for measurement";
  let uploadNote =
    parsed.upload !== null ? "Measured result" : "Waiting for measurement";
  let pingNote =
    parsed.ping !== null ? "Measured result" : "Waiting for connection";

  if (status.status === "running") {
    if (stage.includes("latency")) {
      pingNote = parsed.ping !== null ? "Live measurement" : "Measuring latency";
    } else if (
      parsed.ping === null &&
      (stage.includes("connect") ||
        stage.includes("select") ||
        stage.includes("prepar"))
    ) {
      pingNote = "Connecting to server";
    }

    if (stage.includes("download")) {
      downloadNote =
        parsed.download !== null ? "Live measurement" : "Measuring download";
    }

    if (stage.includes("upload")) {
      uploadNote =
        parsed.upload !== null ? "Live measurement" : "Measuring upload";
    }

    if (
      stage.includes("reading") ||
      stage.includes("saving") ||
      stage.includes("checking") ||
      stage.includes("rendering")
    ) {
      downloadNote =
        parsed.download !== null ? "Measured result" : "Finalizing result";
      uploadNote =
        parsed.upload !== null ? "Measured result" : "Finalizing result";
      pingNote = parsed.ping !== null ? "Measured result" : "Finalizing result";
    }
  }

  renderLiveMetric("download", download, "Mbps", downloadNote);
  renderLiveMetric("upload", upload, "Mbps", uploadNote);
  renderLiveMetric("ping", ping, "ms", pingNote);
}

function renderRunModal(status) {
  byId("run-modal-title").textContent =
    status.status === "failed"
      ? "Speed test failed"
      : status.status === "completed"
        ? "Speed test completed"
        : "Running speed test";
  byId("run-modal-copy").textContent =
    status.message ||
    "Testing your line now. This window closes automatically when the result is ready.";
  byId("run-modal-stage").textContent = status.stage || "Preparing test";
  byId("run-modal-server-pill").textContent =
    status.selected_server_label || findServerLabel(status.selected_server_id);
  byId("run-modal-progress-bar").style.width = `${runStageProgress(status)}%`;
  byId("run-modal-log").textContent =
    (status.logs || []).join("\n") || "Waiting for speed test output...";
  renderRunLiveMetrics(status);
}

function populateServerOptions(payload) {
  const selectedId = String(payload.selected_id || "");
  currentServerLabel = payload.selected_label || "Auto (nearest server)";
  serverOptions = payload.options || [
    { id: "", label: "Auto (nearest server)" },
  ];
  populateSelectOptions(byId("server-select"), serverOptions, selectedId);
  populateSelectOptions(byId("server-modal-select"), serverOptions, selectedId);

  if (currentPayload) {
    renderScheduleNote(currentPayload);
  }

  const select = byId("server-select");
  if (select) {
    select.dataset.selectedValue = selectedId;
  }
}

async function loadServerSettings() {
  serverSettingsLoading = true;
  updateServerSelectDisabled();

  try {
    const response = await fetch("/api/settings/server");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) {
      throw new Error("Failed to load server settings");
    }

    const payload = await response.json();
    populateServerOptions(payload);
  } catch (error) {
    serverOptions = [{ id: "", label: "Auto (nearest server)" }];
    populateSelectOptions(byId("server-select"), serverOptions, "");
    populateSelectOptions(byId("server-modal-select"), serverOptions, "");
    const select = byId("server-select");
    if (select) {
      select.dataset.selectedValue = "";
    }
    currentServerLabel = "Auto (nearest server)";
  } finally {
    serverSettingsLoading = false;
    updateServerSelectDisabled();
  }
}

async function updateServerSettings() {
  const select = byId("server-select");
  if (!select || serverSettingsSaving || manualRunInFlight) {
    return;
  }

  const previousValue = select.dataset.selectedValue ?? "";
  serverSettingsSaving = true;
  updateServerSelectDisabled();

  try {
    const response = await fetch("/api/settings/server", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ server_id: select.value }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail ||
          payload.message ||
          "Failed to update server selection",
      );
    }

    populateServerOptions(payload);
    showMessage(payload.message || "Server selection updated.", "success");
    if (currentPayload) {
      currentPayload.server_selection_id = payload.selected_id || "";
      currentPayload.server_selection_label =
        payload.selected_label || "Auto (nearest server)";
      renderScheduleNote(currentPayload);
    }
  } catch (error) {
    select.value = String(previousValue || "");
    showMessage("Failed to update server selection.", "error");
  } finally {
    serverSettingsSaving = false;
    updateServerSelectDisabled();
  }
}

function openRunModal(startedAt = null) {
  const modal = byId("run-modal");
  if (!modal) return;

  if (runModalAutoCloseId) {
    window.clearTimeout(runModalAutoCloseId);
    runModalAutoCloseId = null;
  }

  const parsedStart = startedAt ? Date.parse(startedAt) : Number.NaN;
  runModalStartedAt = Number.isNaN(parsedStart) ? Date.now() : parsedStart;
  updateRunModalTimer();
  if (runModalTimerId) {
    window.clearInterval(runModalTimerId);
  }
  runModalTimerId = window.setInterval(updateRunModalTimer, 1000);

  modal.classList.remove("hidden");
  syncBodyModalState();
}

function closeRunModal() {
  const modal = byId("run-modal");
  if (!modal) return;

  modal.classList.add("hidden");
  syncBodyModalState();

  if (runModalAutoCloseId) {
    window.clearTimeout(runModalAutoCloseId);
    runModalAutoCloseId = null;
  }

  if (runModalTimerId) {
    window.clearInterval(runModalTimerId);
    runModalTimerId = null;
  }
}

function stopRunStatusPolling() {
  if (runStatusPollId) {
    window.clearInterval(runStatusPollId);
    runStatusPollId = null;
  }
}

async function syncRunStatus(announceFinal = false) {
  if (runStatusRequestInFlight) {
    return;
  }

  runStatusRequestInFlight = true;
  try {
    const response = await fetch("/api/run/speedtest/status");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) {
      return;
    }

    const payload = await response.json();

    if (payload.status === "running") {
      manualRunInFlight = true;
      setRunButtonState(true);
      closeServerModal();
      renderRunModal(payload);
      openRunModal(payload.started_at);
      setStatus(payload.stage || "Running speedtest...");
      return;
    }

    if (payload.status === "completed" || payload.status === "failed") {
      const completionKey =
        payload.completed_at || `${payload.status}:${payload.updated_at || ""}`;

      stopRunStatusPolling();
      setRunButtonState(false);
      manualRunInFlight = false;

      const shouldPresentFinal =
        announceFinal &&
        completionKey &&
        completionKey !== lastHandledRunCompletion;

      if (!shouldPresentFinal) {
        closeRunModal();
        return;
      }

      lastHandledRunCompletion = completionKey;
      if (payload.status === "completed") {
        showMessage(
          payload.message || "Speed test completed successfully.",
          "success",
        );
        setStatus("Speed test completed");
        await loadMetrics();
        void syncCompletionWatcher(true);
      } else {
        showMessage(payload.message || "Speed test failed.", "error");
        setStatus(
          payload.stage === "Timed out"
            ? "Speed test timed out"
            : "Speed test failed",
        );
        await loadMetrics();
        void syncCompletionWatcher(true);
      }

      renderRunModal(payload);
      openRunModal(payload.started_at);
      runModalAutoCloseId = window.setTimeout(closeRunModal, 1600);
      return;
    }

    if (!manualRunInFlight) {
      setRunButtonState(false);
    }
  } finally {
    runStatusRequestInFlight = false;
  }
}

function startRunStatusPolling() {
  stopRunStatusPolling();
  void syncRunStatus(true);
  runStatusPollId = window.setInterval(() => {
    void syncRunStatus(true);
  }, 1200);
}

async function syncCompletionWatcher(rebaselineOnly = false) {
  if (completionWatchRequestInFlight) {
    return;
  }

  completionWatchRequestInFlight = true;
  try {
    const response = await fetch("/api/run/speedtest/completion");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) {
      return;
    }

    const payload = await response.json();
    const sequence = Number(payload.sequence || 0);
    if (!Number.isFinite(sequence) || sequence < 0) {
      return;
    }

    if (rebaselineOnly || lastSeenCompletionSequence === 0) {
      lastSeenCompletionSequence = sequence;
      return;
    }

    if (sequence > lastSeenCompletionSequence) {
      lastSeenCompletionSequence = sequence;
      await loadMetrics();
    }
  } catch (error) {
    // Ignore transient watcher errors.
  } finally {
    completionWatchRequestInFlight = false;
  }
}

function startCompletionWatcher() {
  if (completionWatchId) {
    window.clearInterval(completionWatchId);
  }

  void syncCompletionWatcher(true);
  completionWatchId = window.setInterval(() => {
    void syncCompletionWatcher(false);
  }, 5000);
}

function setStatus(text) {
  byId("status").textContent = text;
}

function stopApplicationClockTicker() {
  if (!applicationClockTimerId) return;
  window.clearInterval(applicationClockTimerId);
  applicationClockTimerId = null;
}

function buildApplicationClockZoneText(timezone, utcOffset) {
  if (timezone && utcOffset) {
    return `${timezone} • UTC${utcOffset}`;
  }
  if (timezone) {
    return timezone;
  }
  if (utcOffset) {
    return `UTC${utcOffset}`;
  }
  return "Timezone";
}

function formatClockTime(date, timezone) {
  try {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone || "UTC",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).format(date);
  } catch {
    return "";
  }
}

function paintApplicationClock() {
  const clockTimeNode = byId("application-clock-time");
  const clockZoneNode = byId("application-clock-zone");
  if (!clockTimeNode || !clockZoneNode) {
    stopApplicationClockTicker();
    return;
  }

  if (!applicationClockState) {
    clockTimeNode.textContent = "--:--:--";
    clockZoneNode.textContent = "Timezone";
    return;
  }

  const elapsedMs = Math.max(0, Date.now() - applicationClockState.anchorSystemMs);
  const currentDate = new Date(applicationClockState.anchorTimestampMs + elapsedMs);
  const nowTime =
    formatClockTime(currentDate, applicationClockState.timezone) ||
    formatClockTime(currentDate, "UTC") ||
    "--:--:--";
  clockTimeNode.textContent = nowTime;
  clockZoneNode.textContent = buildApplicationClockZoneText(
    applicationClockState.timezone,
    applicationClockState.utcOffset,
  );
}

function renderApplicationClock(data) {
  const clockTimeNode = byId("application-clock-time");
  const clockZoneNode = byId("application-clock-zone");
  if (!clockTimeNode || !clockZoneNode) {
    stopApplicationClockTicker();
    applicationClockState = null;
    return;
  }

  const applicationTime = data?.application_time || {};
  const nowDisplay = String(applicationTime.now_display || "").trim();
  const nowIso = String(applicationTime.now_iso || "").trim();
  const timezone = String(applicationTime.timezone || "").trim();
  const utcOffset = String(applicationTime.utc_offset || "").trim();
  const parsedNowMs = Date.parse(nowIso);

  if (Number.isFinite(parsedNowMs)) {
    applicationClockState = {
      anchorTimestampMs: parsedNowMs,
      anchorSystemMs: Date.now(),
      timezone,
      utcOffset,
      fallbackDisplay: nowDisplay,
    };
    paintApplicationClock();
    stopApplicationClockTicker();
    applicationClockTimerId = window.setInterval(paintApplicationClock, 1000);
    return;
  }

  stopApplicationClockTicker();
  applicationClockState = null;
  clockTimeNode.textContent =
    (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(nowDisplay)
      ? nowDisplay.slice(-8)
      : "") || "--:--:--";
  clockZoneNode.textContent = buildApplicationClockZoneText(timezone, utcOffset);
}

let messageTimeoutId = 0;

function showMessage(text, kind = "info") {
  const element = byId("message");
  if (!element) return;

  if (messageTimeoutId) {
    window.clearTimeout(messageTimeoutId);
    messageTimeoutId = 0;
  }

  element.textContent = text;
  element.classList.remove("hidden", "info", "success", "warning", "error");
  element.classList.add(kind);
  element.setAttribute("aria-hidden", "false");

  const timeoutMs =
    kind === "error" ? 8000 : kind === "warning" ? 6000 : 4500;
  messageTimeoutId = window.setTimeout(() => {
    clearMessage();
  }, timeoutMs);
}

function clearMessage() {
  const element = byId("message");
  if (!element) return;

  if (messageTimeoutId) {
    window.clearTimeout(messageTimeoutId);
    messageTimeoutId = 0;
  }

  element.textContent = "";
  element.classList.remove("info", "success", "warning", "error");
  element.classList.add("hidden");
  element.setAttribute("aria-hidden", "true");
}

function safeFixed(value, digits = 2) {
  if (typeof value !== "number" || Number.isNaN(value)) return "0.00";
  return value.toFixed(digits);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

function withAlpha(color, alpha) {
  const normalized = String(color || "").trim();
  if (!normalized) return color;

  if (normalized.startsWith("#")) {
    let hex = normalized.slice(1);
    if (hex.length === 3) {
      hex = hex
        .split("")
        .map((char) => char + char)
        .join("");
    }
    if (hex.length >= 6) {
      const r = Number.parseInt(hex.slice(0, 2), 16);
      const g = Number.parseInt(hex.slice(2, 4), 16);
      const b = Number.parseInt(hex.slice(4, 6), 16);
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
  }

  const rgbMatch = normalized.match(/^rgba?\(([^)]+)\)$/i);
  if (rgbMatch) {
    const [r = "0", g = "0", b = "0"] = rgbMatch[1]
      .split(",")
      .map((part) => part.trim());
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  return normalized;
}

function cssAlpha(name, opacityPercent) {
  return withAlpha(cssVar(name), opacityPercent / 100);
}

function buildSurfaceGradient(chart, colorVar, topAlpha = 24, bottomAlpha = 4) {
  const area = chart?.chartArea;
  const ctx = chart?.ctx;
  if (!ctx || !area) {
    return cssAlpha(colorVar, Math.max(bottomAlpha, 8));
  }
  const gradient = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  gradient.addColorStop(0, cssAlpha(colorVar, topAlpha));
  gradient.addColorStop(1, cssAlpha(colorVar, bottomAlpha));
  return gradient;
}

function createEmptyStateCard(title, copy, options = {}) {
  const card = document.createElement("article");
  card.className = "empty-state-card";
  if (options.compact) {
    card.classList.add("empty-state-mini");
  }

  const icon = document.createElement("span");
  icon.className = "empty-state-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = options.icon || "◇";

  const heading = document.createElement("p");
  heading.className = "empty-state-title";
  heading.textContent = title;

  const paragraph = document.createElement("p");
  paragraph.className = "empty-state-copy";
  paragraph.textContent = copy;

  const hintText = String(options.hint || "").trim();
  let hint = null;
  if (hintText) {
    hint = document.createElement("p");
    hint.className = "empty-state-hint";
    hint.textContent = hintText;
  }

  card.appendChild(icon);
  card.appendChild(heading);
  card.appendChild(paragraph);
  if (hint) {
    card.appendChild(hint);
  }
  return card;
}

function initializeTheme() {
  const themeApi = window.SpeedPulseTheme;
  if (!themeApi) return;
  const { activeTheme } = themeApi.currentPreferences();
  document.documentElement.dataset.theme = activeTheme;
  if (document.body) {
    document.body.dataset.theme = activeTheme;
  }
}

function modeLabel(mode) {
  if (mode === "light") return "Light";
  if (mode === "dark") return "Dark";
  return "System";
}

function syncThemeModeToggle(preferences = null) {
  const toggle = byId("theme-mode-toggle");
  if (!toggle) return;
  const themeApi = window.SpeedPulseTheme;
  const prefs =
    preferences ||
    (themeApi && typeof themeApi.currentPreferences === "function"
      ? themeApi.currentPreferences()
      : null);
  const mode = String(prefs?.mode || "system");
  const label = modeLabel(mode);
  toggle.dataset.mode = mode;
  toggle.setAttribute("aria-label", `Theme mode: ${label}. Click to switch`);
  toggle.setAttribute("title", `Theme mode: ${label}. Click to cycle`);
}

function cycleThemeMode() {
  const themeApi = window.SpeedPulseTheme;
  if (!themeApi || typeof themeApi.setMode !== "function") return;
  const sequence = ["system", "light", "dark"];
  const current = String(themeApi.currentPreferences?.().mode || "system");
  const index = Math.max(0, sequence.indexOf(current));
  const nextMode = sequence[(index + 1) % sequence.length];
  const updated = themeApi.setMode(nextMode);
  syncThemeModeToggle(updated);
}

function healthyLabel(row) {
  return row.healthy ? "Yes" : "Check";
}

function defaultDirectionForKey(key) {
  return key === "timestamp_iso" ? "desc" : "asc";
}

function trendSummary(
  current,
  previous,
  higherIsBetter = true,
  comparisonLabel = "previous scan",
) {
  if (
    typeof current !== "number" ||
    typeof previous !== "number" ||
    previous <= 0
  ) {
    const fallbackNote =
      comparisonLabel === "previous period"
        ? "Need previous period data"
        : "Need one more scan to compare";
    return {
      label: "No baseline yet",
      tone: "tone-muted",
      note: fallbackNote,
      chip: null,
    };
  }

  const pct = ((current - previous) / previous) * 100;
  const absPct = Math.abs(pct).toFixed(1);
  const comparedWith = `vs ${comparisonLabel}`;

  if (Math.abs(pct) < 0.1) {
    return {
      label: "Stable",
      tone: "tone-muted",
      note: `Stable ${comparedWith}`,
      chip: { label: "• 0.0%", tone: "tone-muted" },
    };
  }

  if (higherIsBetter) {
    return pct > 0
      ? {
          label: "Faster",
          tone: "tone-good",
          note: `Faster ${comparedWith}`,
          chip: { label: `▲ ${absPct}%`, tone: "tone-good" },
        }
      : {
          label: "Slower",
          tone: "tone-bad",
          note: `Slower ${comparedWith}`,
          chip: { label: `▼ ${absPct}%`, tone: "tone-bad" },
        };
  }

  return pct < 0
    ? {
        label: "Lower latency",
        tone: "tone-good",
        note: `Lower latency ${comparedWith}`,
        chip: { label: `▼ ${absPct}%`, tone: "tone-good" },
      }
    : {
        label: "Higher latency",
        tone: "tone-bad",
        note: `Higher latency ${comparedWith}`,
        chip: { label: `▲ ${absPct}%`, tone: "tone-bad" },
      };
}

function scanSummary(todayCount, scheduledCount) {
  if (!scheduledCount) {
    return { label: `${todayCount} scheduled scans logged today`, tone: "tone-muted" };
  }

  if (todayCount >= scheduledCount) {
    return {
      label: `${todayCount}/${scheduledCount} scheduled scans completed`,
      tone: "tone-good",
    };
  }

  return {
    label: `${todayCount}/${scheduledCount} scheduled scans completed`,
    tone: "tone-muted",
  };
}

function manualScanSummary(manualCount) {
  if (!manualCount) {
    return { label: "No manual scans today", tone: "tone-muted" };
  }

  return {
    label: `${manualCount} manual scan${manualCount === 1 ? "" : "s"} today`,
    tone: "tone-good",
  };
}

function recentMetricSeries(timeseries, key, limit = 14) {
  return (timeseries || [])
    .map((item) => Number(item?.[key]))
    .filter((value) => Number.isFinite(value))
    .slice(-limit);
}

function metricContextText(currentValue, values, higherIsBetter = true) {
  if (!Number.isFinite(currentValue) || !Array.isArray(values) || values.length < 3) {
    return "";
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min;
  if (spread <= 0.0001) return "Stable in range";

  const best = higherIsBetter ? max : min;
  const worst = higherIsBetter ? min : max;
  const closenessToBest = Math.abs(currentValue - best) / spread;
  const closenessToWorst = Math.abs(currentValue - worst) / spread;

  if (closenessToBest <= 0.08) return "Best in range";
  if (closenessToWorst <= 0.08) return "Weakest in range";
  return "Within normal range";
}

function createMetricSparkline(values, colorToken) {
  if (!Array.isArray(values) || values.length < 2) return null;

  const width = 124;
  const height = 28;
  const pad = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;

  const points = values
    .map((value, index) => {
      const x = pad + (index * (width - pad * 2)) / (values.length - 1);
      const normalized = (value - min) / spread;
      const y = pad + (1 - normalized) * (height - pad * 2);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  const wrapper = document.createElement("div");
  wrapper.className = "metric-sparkline";
  wrapper.innerHTML =
    `<svg viewBox="0 0 ${width} ${height}" role="presentation" aria-hidden="true">` +
    '<polyline class="metric-sparkline-area" points="' +
    `${points} ${width - pad},${height - pad} ${pad},${height - pad}` +
    '"></polyline>' +
    `<polyline class="metric-sparkline-line" points="${points}"></polyline>` +
    "</svg>";

  wrapper.style.setProperty("--spark-color", cssVar(colorToken));
  return wrapper;
}

function formatMetricValue(value, unit = "", digits = 2) {
  const safeUnit = String(unit || "").trim();
  const formatted = safeFixed(Number(value || 0), digits);
  return safeUnit ? `${formatted} ${safeUnit}` : formatted;
}

function animateMetricValue(element, config = {}) {
  if (!(element instanceof HTMLElement)) return;

  const to = Number(config.to);
  const from = Number(config.from);
  const unit = String(config.unit || "");
  const digits = Number.isFinite(config.digits) ? Number(config.digits) : 2;
  const duration = Number.isFinite(config.duration)
    ? Math.max(220, Number(config.duration))
    : 560;

  if (!Number.isFinite(to)) {
    element.textContent = String(config.fallbackText || "");
    return;
  }

  if (!Number.isFinite(from) || prefersReducedMotion()) {
    element.textContent = formatMetricValue(to, unit, digits);
    return;
  }

  const startTime = performance.now();
  const delta = to - from;
  const easeOut = (progress) => 1 - (1 - progress) ** 3;

  const tick = (now) => {
    const progress = Math.min(1, (now - startTime) / duration);
    const eased = easeOut(progress);
    const current = from + delta * eased;
    element.textContent = formatMetricValue(current, unit, digits);

    if (progress < 1) {
      window.requestAnimationFrame(tick);
    }
  };

  window.requestAnimationFrame(tick);
}

function metricCard(
  title,
  valueText,
  noteText,
  toneClass = "tone-muted",
  options = {},
) {
  const deltaChip =
    options.deltaChip && options.deltaChip.label
      ? options.deltaChip
      : null;
  const compact = Boolean(options.compact);
  const reserveDeltaSpace = Boolean(options.reserveDeltaSpace);
  const contextText = String(options.contextText || "").trim();
  const sparklineValues = Array.isArray(options.sparklineValues)
    ? options.sparklineValues
    : null;
  const sparklineColorToken = String(options.sparklineColorToken || "--accent");
  const valueAnimation = options.valueAnimation || null;
  const card = document.createElement("article");
  card.className = "metric-card";
  if (compact) {
    card.classList.add("metric-card-compact");
  }

  const infoMap = {
    "Latest download":
      "Download speed is how fast data is received from the internet (Mbps). Higher is better.",
    "Latest upload":
      "Upload speed is how fast data is sent to the internet (Mbps). Higher is better.",
    "Latest ping":
      "Ping is network latency in milliseconds. Lower is better for responsiveness.",
    "Scheduled scans":
      "Scheduled scans are automated speed tests run at your configured times.",
    "Manual scans":
      "Manual scans are speed tests started by you from the dashboard.",
  };

  const titleRow = document.createElement("div");
  titleRow.className = "metric-title-row";

  const titleEl = document.createElement("h3");
  titleEl.textContent = title;
  titleRow.appendChild(titleEl);

  const infoText = infoMap[title];
  if (infoText) {
    const infoButton = document.createElement("button");
    infoButton.type = "button";
    infoButton.className = "metric-info";
    infoButton.setAttribute("aria-label", `About ${title.toLowerCase()}`);
    infoButton.setAttribute("data-tooltip", infoText);
    infoButton.textContent = "i";
    titleRow.appendChild(infoButton);
  }

  const valueEl = document.createElement("p");
  valueEl.className = "metric-value";
  if (valueAnimation) {
    animateMetricValue(valueEl, {
      ...valueAnimation,
      fallbackText: valueText,
    });
  } else {
    valueEl.textContent = valueText;
  }

  const noteEl = document.createElement("p");
  noteEl.className = `metric-note ${toneClass}`;
  noteEl.textContent = noteText;

  const sparklineEl =
    sparklineValues && sparklineValues.length >= 2
      ? createMetricSparkline(sparklineValues, sparklineColorToken)
      : null;

  card.appendChild(titleRow);
  card.appendChild(valueEl);
  if (sparklineEl) {
    card.appendChild(sparklineEl);
  }

  if (deltaChip) {
    const chipEl = document.createElement("span");
    chipEl.className = `metric-delta-chip ${deltaChip.tone || toneClass}`;
    chipEl.textContent = deltaChip.label;
    card.appendChild(chipEl);
  } else if (reserveDeltaSpace) {
    const spacer = document.createElement("span");
    spacer.className = "metric-delta-spacer";
    spacer.setAttribute("aria-hidden", "true");
    card.appendChild(spacer);
  }

  card.appendChild(noteEl);
  if (contextText) {
    const contextEl = document.createElement("p");
    contextEl.className = "metric-context";
    contextEl.textContent = contextText;
    card.appendChild(contextEl);
  }
  return card;
}

function reliabilityHighlightCard(
  title,
  valueText,
  noteText,
  toneClass = "tone-muted",
) {
  const card = document.createElement("article");
  card.className = "reliability-card";

  const titleEl = document.createElement("h3");
  titleEl.textContent = title;

  const valueEl = document.createElement("strong");
  valueEl.className = `reliability-value ${toneClass}`;
  valueEl.textContent = valueText;

  const noteEl = document.createElement("p");
  noteEl.className = `reliability-note ${toneClass}`;
  noteEl.textContent = noteText;

  card.appendChild(titleEl);
  card.appendChild(valueEl);
  card.appendChild(noteEl);
  return card;
}

function renderHeroMetrics(data) {
  const root = byId("hero-metrics");
  if (!root) return;
  root.textContent = "";
  root.removeAttribute("aria-busy");

  const rows = data.latest_tests || [];
  const latest = rows[0];
  const previous = rows[1];

  if (!latest) {
    heroMetricLastValues.clear();
    const empty = createEmptyStateCard(
      "No scan data yet",
      "Run a manual test or wait for the next scheduled scan to populate this range.",
      { icon: "↺", hint: "Tip: press R to run a test now." },
    );
    empty.classList.add("metric-card", "metric-card-empty");
    root.appendChild(empty);
    return;
  }

  const prevAvg = data.previous_averages || {};
  const curAvg = data.averages || {};
  const hasPeriodBaseline = prevAvg.total_tests > 0;
  const comparisonLabel = hasPeriodBaseline ? "previous period" : "previous scan";

  const downloadTrend =
    hasPeriodBaseline
      ? trendSummary(
          curAvg.download_mbps,
          prevAvg.download_mbps,
          true,
          comparisonLabel,
        )
      : trendSummary(
          latest.download_mbps,
          previous?.download_mbps,
          true,
          comparisonLabel,
        );
  const uploadTrend =
    hasPeriodBaseline
      ? trendSummary(
          curAvg.upload_mbps,
          prevAvg.upload_mbps,
          true,
          comparisonLabel,
        )
      : trendSummary(
          latest.upload_mbps,
          previous?.upload_mbps,
          true,
          comparisonLabel,
        );
  const pingTrend =
    hasPeriodBaseline
      ? trendSummary(curAvg.ping_ms, prevAvg.ping_ms, false, comparisonLabel)
      : trendSummary(
          latest.ping_ms,
          previous?.ping_ms,
          false,
          comparisonLabel,
        );
  const scheduledToday = data.today_scheduled_tests ?? data.today_tests ?? 0;
  const manualToday = data.today_manual_tests || 0;
  const todayTrend = scanSummary(
    scheduledToday,
    data.scheduled_tests_per_day || 0,
  );
  const manualTrend = manualScanSummary(manualToday);
  const downloadSeries = recentMetricSeries(data.timeseries, "download_mbps");
  const uploadSeries = recentMetricSeries(data.timeseries, "upload_mbps");
  const pingSeries = recentMetricSeries(data.timeseries, "ping_ms");
  const latestDownloadValue = Number(latest.download_mbps || 0);
  const latestUploadValue = Number(latest.upload_mbps || 0);
  const latestPingValue = Number(latest.ping_ms || 0);

  const cards = [
    metricCard(
      "Latest download",
      `${safeFixed(latestDownloadValue)} Mbps`,
      downloadTrend.note || downloadTrend.label,
      downloadTrend.tone,
      {
        deltaChip: downloadTrend.chip,
        sparklineValues: downloadSeries,
        sparklineColorToken: "--chart-download",
        contextText: metricContextText(latestDownloadValue, downloadSeries, true),
        valueAnimation: {
          from: heroMetricLastValues.get("latest_download"),
          to: latestDownloadValue,
          unit: "Mbps",
          digits: 2,
          duration: 560,
        },
      },
    ),
    metricCard(
      "Latest upload",
      `${safeFixed(latestUploadValue)} Mbps`,
      uploadTrend.note || uploadTrend.label,
      uploadTrend.tone,
      {
        deltaChip: uploadTrend.chip,
        sparklineValues: uploadSeries,
        sparklineColorToken: "--chart-upload",
        contextText: metricContextText(latestUploadValue, uploadSeries, true),
        valueAnimation: {
          from: heroMetricLastValues.get("latest_upload"),
          to: latestUploadValue,
          unit: "Mbps",
          digits: 2,
          duration: 560,
        },
      },
    ),
    metricCard(
      "Latest ping",
      `${safeFixed(latestPingValue)} ms`,
      pingTrend.note || pingTrend.label,
      pingTrend.tone,
      {
        deltaChip: pingTrend.chip,
        sparklineValues: pingSeries,
        sparklineColorToken: "--chart-ping",
        contextText: metricContextText(latestPingValue, pingSeries, false),
        valueAnimation: {
          from: heroMetricLastValues.get("latest_ping"),
          to: latestPingValue,
          unit: "ms",
          digits: 2,
          duration: 560,
        },
      },
    ),
    metricCard(
      "Scheduled scans",
      `${scheduledToday} / ${data.scheduled_tests_per_day || 0}`,
      todayTrend.label,
      todayTrend.tone,
      { reserveDeltaSpace: true },
    ),
    metricCard(
      "Manual scans",
      `${manualToday}`,
      manualTrend.label,
      manualTrend.tone,
      { reserveDeltaSpace: true },
    ),
  ];

  cards.forEach((card, index) => {
    card.classList.add("metric-card-enter");
    card.style.setProperty("--metric-enter-delay", `${index * 45}ms`);
    root.appendChild(card);
  });

  window.requestAnimationFrame(() => {
    root.querySelectorAll(".metric-card-enter").forEach((card) => {
      card.classList.add("is-visible");
    });
  });

  heroMetricLastValues.set("latest_download", latestDownloadValue);
  heroMetricLastValues.set("latest_upload", latestUploadValue);
  heroMetricLastValues.set("latest_ping", latestPingValue);
}

function renderHeroMetricsSkeleton(cardCount = 5) {
  const root = byId("hero-metrics");
  if (!root) return;

  root.textContent = "";
  root.setAttribute("aria-busy", "true");

  for (let index = 0; index < cardCount; index += 1) {
    const card = document.createElement("article");
    card.className = "metric-card metric-card-skeleton";
    card.setAttribute("aria-hidden", "true");

    const title = document.createElement("span");
    title.className = "skeleton-line skeleton-line-title";
    const value = document.createElement("span");
    value.className = "skeleton-line skeleton-line-value";
    const chip = document.createElement("span");
    chip.className = "skeleton-line skeleton-line-chip";
    const note = document.createElement("span");
    note.className = "skeleton-line skeleton-line-note";

    card.appendChild(title);
    card.appendChild(value);
    card.appendChild(chip);
    card.appendChild(note);
    root.appendChild(card);
  }
}

function renderSlaPanel(data) {
  const summary = byId("sla-summary");
  const cardsRoot = byId("sla-cards");
  const highlightsRoot = byId("sla-highlights");
  const breakdownSummary = byId("sla-breakdown-summary");
  if (!summary || !cardsRoot) return;

  const sla = data.sla || {};
  cardsRoot.textContent = "";
  if (highlightsRoot) {
    highlightsRoot.textContent = "";
  }

  const grade = sla.grade || "N/A";
  const compliancePct = Number(sla.compliance_pct || 0);
  const breachTests = Number(sla.breach_tests || 0);
  const incidentCount = Number(sla.incident_count || 0);
  const coveragePct = Number(sla.sample_coverage_pct || 0);
  const totalTests = Number(data.total_tests || 0);

  summary.textContent =
    `${sla.window_label || data.range_label || "Selected range"} | ${totalTests} logged test${totalTests === 1 ? "" : "s"} | ` +
    "Compliance is based on your current threshold settings.";

  cardsRoot.appendChild(
    metricCard(
      "SLA grade",
      grade,
      `${safeFixed(compliancePct, 1)}% threshold compliance`,
      grade === "A" || grade === "B"
        ? "tone-good"
        : grade === "N/A"
          ? "tone-muted"
          : "tone-bad",
    ),
  );
  cardsRoot.appendChild(
    metricCard(
      "Breached tests",
      String(breachTests),
      `${incidentCount} grouped incident${incidentCount === 1 ? "" : "s"}`,
      breachTests === 0 ? "tone-good" : "tone-bad",
    ),
  );
  cardsRoot.appendChild(
    metricCard(
      "Sample coverage",
      `${safeFixed(coveragePct, 1)}%`,
      `${sla.expected_tests || 0} scheduled samples expected`,
      coveragePct >= 100 ? "tone-good" : "tone-muted",
    ),
  );

  renderSlaBreakdown(data, breakdownSummary);
  renderReliabilityHighlights(data, highlightsRoot);
}

function compactDuration(totalMinutes) {
  const minutes = Number(totalMinutes || 0);
  if (!Number.isFinite(minutes) || minutes <= 0) return "0m";
  if (minutes >= 1440) {
    const days = minutes / 1440;
    return `${days >= 10 ? Math.round(days) : days.toFixed(1)}d`;
  }
  if (minutes >= 60) {
    const hours = minutes / 60;
    return `${hours >= 10 ? Math.round(hours) : hours.toFixed(1)}h`;
  }
  return `${Math.round(minutes)}m`;
}

function durationFromIso(value) {
  if (!value) return 0;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return 0;
  return Math.max(0, (Date.now() - parsed) / 60000);
}

function slaBreakdownChartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: "y",
    animation: {
      duration: 720,
      easing: "easeOutQuart",
    },
    plugins: {
      speedpulseHoverGuide: { enabled: false },
      speedpulseLegendFocus: { enabled: false },
      legend: { display: false },
      tooltip: {
        backgroundColor: cssAlpha("--bg", 88),
        borderColor: cssAlpha("--accent", 24),
        borderWidth: 1,
        titleColor: cssVar("--text"),
        bodyColor: cssVar("--text"),
      },
    },
    scales: {
      x: {
        beginAtZero: true,
        ticks: {
          color: cssVar("--chart-label-strong"),
          precision: 0,
        },
        grid: { color: cssVar("--chart-grid") },
      },
      y: {
        ticks: { color: cssVar("--chart-label-strong") },
        grid: { display: false },
      },
    },
  };
}

function renderSlaBreakdown(data, summaryElement) {
  const canvas = byId("slaBreakdownChart");
  if (!canvas) return;

  const breaches = data.violations || {};
  const values = [
    Number(breaches.download || 0),
    Number(breaches.upload || 0),
    Number(breaches.ping || 0),
    Number(breaches.packet_loss || 0),
  ];
  const total = values.reduce((sum, value) => sum + value, 0);

  if (summaryElement) {
    summaryElement.textContent =
      total > 0
        ? `${total} total breach event${total === 1 ? "" : "s"} across the selected range.`
        : "No threshold breaches recorded in this range.";
  }

  if (slaBreakdownChart) {
    slaBreakdownChart.destroy();
  }

  slaBreakdownChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: ["Download", "Upload", "Ping", "Packet loss"],
      datasets: [
        {
          data: values,
          backgroundColor: [
            cssAlpha("--chart-download", 36),
            cssAlpha("--chart-upload", 36),
            cssAlpha("--chart-ping", 36),
            cssAlpha("--chart-loss", 32),
          ],
          borderColor: [
            cssVar("--chart-download"),
            cssVar("--chart-upload"),
            cssVar("--chart-ping"),
            cssVar("--chart-loss"),
          ],
          borderWidth: 1.3,
          borderRadius: 12,
          categoryPercentage: 0.72,
          barPercentage: 0.72,
        },
      ],
    },
    options: slaBreakdownChartOptions(),
  });
}

function renderReliabilityHighlights(data, root) {
  if (!root) return;

  const incidents = data.incidents || [];
  const serverCounts = new Map();
  incidents.forEach((incident) => {
    const server = incident.primary_server || "Unknown";
    serverCounts.set(server, (serverCounts.get(server) || 0) + 1);
  });

  let worstServer = null;
  for (const [server, count] of serverCounts.entries()) {
    if (!worstServer || count > worstServer.count) {
      worstServer = { server, count };
    }
  }

  const longestIncident = incidents.reduce((longest, incident) => {
    if (
      !longest ||
      Number(incident.duration_minutes || 0) >
        Number(longest.duration_minutes || 0)
    ) {
      return incident;
    }
    return longest;
  }, null);

  const ongoing = incidents.find((incident) => incident.ongoing);
  let cleanStreak = null;
  if (ongoing) {
    cleanStreak = {
      value: "Ongoing",
      note: ongoing.headline || "Active incident in progress",
      tone: "tone-bad",
    };
  } else if (incidents.length > 0) {
    const resolvedAt = incidents
      .map((incident) => incident.resolved_at || incident.ended_at)
      .filter(Boolean)
      .sort((left, right) => Date.parse(right) - Date.parse(left))[0];
    const minutes = durationFromIso(resolvedAt);
    cleanStreak = {
      value: compactDuration(minutes),
      note: "Since the last incident cleared",
      tone: minutes >= 1440 ? "tone-good" : "tone-muted",
    };
  } else {
    cleanStreak = {
      value: "Clean",
      note: `No incidents in ${data.range_label || "this window"}`,
      tone: "tone-good",
    };
  }

  root.appendChild(
    reliabilityHighlightCard(
      "Worst server",
      worstServer ? worstServer.server : "None",
      worstServer
        ? `${worstServer.count} incident group${worstServer.count === 1 ? "" : "s"}`
        : "No affected servers in this range",
      worstServer ? "tone-bad" : "tone-good",
    ),
  );
  root.appendChild(
    reliabilityHighlightCard(
      "Longest incident",
      longestIncident
        ? compactDuration(longestIncident.duration_minutes)
        : "0m",
      longestIncident
        ? longestIncident.headline || "Threshold breach"
        : "No incident duration recorded",
      longestIncident ? "tone-muted" : "tone-good",
    ),
  );
  root.appendChild(
    reliabilityHighlightCard(
      "Clean streak",
      cleanStreak.value,
      cleanStreak.note,
      cleanStreak.tone,
    ),
  );
}

function incidentSeverityLabel(severity) {
  if (severity === "high") return "High";
  if (severity === "medium") return "Medium";
  return "Low";
}

function incidentSeverityClass(severity) {
  if (severity === "high") return "incident-pill-high";
  if (severity === "medium") return "incident-pill-medium";
  return "incident-pill-low";
}

function formatIncidentWindow(incident) {
  const started = formatTimestamp(incident.started_at);
  if (incident.ongoing) {
    return `${started} to now`;
  }
  if (incident.resolved_at) {
    return `${started} to ${formatTimestamp(incident.resolved_at)}`;
  }
  return started;
}

function renderIncidentHistory(data) {
  const root = byId("incident-list");
  const summary = byId("incident-summary");
  if (!root || !summary) return;

  const incidents = data.incidents || [];
  root.textContent = "";
  summary.textContent =
    incidents.length > 0
      ? `Showing ${incidents.length} most recent incident group${incidents.length === 1 ? "" : "s"} for ${data.range_label || "the selected range"}.`
      : `No grouped incidents were found for ${data.range_label || "the selected range"}.`;

  if (incidents.length === 0) {
    const empty = createEmptyStateCard(
      "No incidents detected",
      "Consecutive threshold breaches did not occur in this selected window.",
      { icon: "✓", hint: "Tip: try 30 or 90 days for deeper history." },
    );
    empty.classList.add("incident-item", "incident-item-empty");
    root.appendChild(empty);
    return;
  }

  for (const incident of incidents) {
    const card = document.createElement("article");
    card.className = "incident-item";

    const top = document.createElement("div");
    top.className = "incident-top";

    const titleWrap = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = incident.headline || "Threshold breach";
    const meta = document.createElement("p");
    meta.className = "incident-meta";
    meta.textContent = `${formatIncidentWindow(incident)} | ${incident.primary_server || "Unknown server"}`;
    titleWrap.appendChild(title);
    titleWrap.appendChild(meta);

    const pill = document.createElement("span");
    pill.className = `incident-pill ${incidentSeverityClass(incident.severity)}`;
    pill.textContent = incident.ongoing
      ? `${incidentSeverityLabel(incident.severity)} · Ongoing`
      : incidentSeverityLabel(incident.severity);

    top.appendChild(titleWrap);
    top.appendChild(pill);

    const summaryRow = document.createElement("p");
    summaryRow.className = "incident-copy";
    summaryRow.textContent = `${incident.summary || ""} | Duration ${safeFixed(Number(incident.duration_minutes || 0), 1)} min`;

    const breaches = document.createElement("p");
    breaches.className = "incident-breaches";
    breaches.textContent = (incident.breach_types || [])
      .map((value) => value.replace("_", " "))
      .join(" · ");

    card.appendChild(top);
    card.appendChild(summaryRow);
    card.appendChild(breaches);
    root.appendChild(card);
  }
}

function renderScheduleNote(data) {
  const testTimes = data.scheduling?.test_times || [];
  const weekly = data.scheduling?.weekly_report_time || "not set";
  const selectedServer =
    data.server_selection_label ||
    currentServerLabel ||
    "Auto (nearest server)";
  const timesHost = byId("schedule-times");
  const scanPlan = byId("scan-plan");
  const scheduleServer = byId("schedule-server");
  const scheduleWeekly = byId("schedule-weekly");

  const scheduledToday =
    data.today_scheduled_tests ?? data.today_tests ?? 0;
  if (scanPlan) {
    scanPlan.textContent =
      `${scheduledToday} / ${data.scheduled_tests_per_day || 0} scans today`;
  }
  if (scheduleServer) {
    scheduleServer.textContent = selectedServer;
  }
  if (scheduleWeekly) {
    scheduleWeekly.textContent = weekly;
  }

  if (timesHost) {
    timesHost.textContent = "";

    if (testTimes.length > 0) {
      for (const time of testTimes) {
        const chip = document.createElement("span");
        chip.className = "schedule-chip";
        chip.textContent = time;
        timesHost.appendChild(chip);
      }
    } else {
      const chip = document.createElement("span");
      chip.className = "schedule-chip";
      chip.textContent = "Not scheduled";
      timesHost.appendChild(chip);
    }
  }
}

function renderDetectedConnectionInfo(data) {
  const provider = String(data?.detected_provider || "").trim();
  const ipAddress = String(data?.detected_ip_address || "").trim();

  const providerNode = byId("sidebar-account-provider");
  if (providerNode) {
    providerNode.textContent = provider || "Provider not detected yet";
  }

  const ipNode = byId("sidebar-account-ip");
  if (ipNode) {
    ipNode.textContent = `IP: ${ipAddress || "Not detected yet"}`;
  }
}

function renderSidebarContract() {
  fetch("/api/contract/summary")
    .then((r) => {
      if (!r.ok) return null;
      return r.json();
    })
    .then((data) => {
      if (!data) return;
      const section = byId("sidebar-contract");
      if (!section) return;

      const current = data.current || {};
      if (!current.start_date && !current.end_date) {
        section.classList.add("hidden");
        return;
      }

      section.classList.remove("hidden");
      byId("sidebar-contract-period").textContent =
        `${current.start_date || "?"} — ${current.end_date || "?"}`;
      const speeds = byId("sidebar-contract-speeds");
      if (speeds) {
        const download = Number(current.download_mbps || 0);
        const upload = Number(current.upload_mbps || 0);
        speeds.textContent = "";
        speeds.append(`${download}`);
        const downIcon = document.createElement("span");
        downIcon.className = "contract-speed-icon";
        downIcon.setAttribute("aria-hidden", "true");
        downIcon.textContent = "↓";
        speeds.appendChild(downIcon);
        speeds.append(` / ${upload} Mbps`);
        const upIcon = document.createElement("span");
        upIcon.className = "contract-speed-icon";
        upIcon.setAttribute("aria-hidden", "true");
        upIcon.textContent = "↑";
        speeds.appendChild(upIcon);
      }

      const remaining = byId("sidebar-contract-remaining");
      if (current.end_date) {
        const now = new Date();
        const end = new Date(current.end_date + "T23:59:59");
        const diff = Math.ceil((end - now) / (1000 * 60 * 60 * 24));
        if (diff > 0) {
          remaining.textContent = `${diff} day${diff === 1 ? "" : "s"} remaining`;
        } else if (diff === 0) {
          remaining.textContent = "Ends today";
        } else {
          remaining.textContent = `Expired ${Math.abs(diff)} day${Math.abs(diff) === 1 ? "" : "s"} ago`;
          remaining.classList.add("tone-danger");
        }
      } else {
        remaining.textContent = "";
      }
    })
    .catch(() => {});
}

function ensureHoverGuidePlugin() {
  if (hoverGuidePluginRegistered || typeof Chart === "undefined") {
    return;
  }

  Chart.register(
    {
      id: "speedpulseHoverGuide",
      afterDatasetsDraw(chart, _args, options) {
        if (!options?.enabled) return;
        const activeElements = chart?.tooltip?.getActiveElements?.() || [];
        if (activeElements.length === 0) return;
        const x = activeElements[0]?.element?.x;
        if (!Number.isFinite(x)) return;
        const area = chart?.chartArea;
        if (!area) return;

        const ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(x, area.top + 2);
        ctx.lineTo(x, area.bottom - 2);
        ctx.lineWidth = Number(options.width || 1);
        ctx.setLineDash(Array.isArray(options.dash) ? options.dash : [4, 4]);
        ctx.strokeStyle = String(options.color || "rgba(255,255,255,0.15)");
        ctx.stroke();
        ctx.restore();
      },
    },
    {
      id: "speedpulseLegendFocus",
      afterDatasetsDraw(chart, _args, options) {
        if (!options?.enabled) return;
        const activeIndex = Number(chart.$legendActiveDatasetIndex);
        if (!Number.isInteger(activeIndex) || activeIndex < 0) return;
        const area = chart?.chartArea;
        if (!area) return;
        const activeMeta = chart.getDatasetMeta(activeIndex);
        if (!activeMeta || activeMeta.hidden) return;

        const ctx = chart.ctx;
        ctx.save();
        ctx.fillStyle = String(options.overlayColor || "rgba(0, 0, 0, 0.45)");
        ctx.fillRect(
          area.left,
          area.top,
          area.right - area.left,
          area.bottom - area.top,
        );
        ctx.restore();

        ctx.save();
        if (activeMeta.controller && typeof activeMeta.controller.draw === "function") {
          activeMeta.controller.draw();
        }
        ctx.restore();
      },
    },
  );

  hoverGuidePluginRegistered = true;
}

function chartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: "index",
      intersect: false,
    },
    animation: {
      duration: 760,
      easing: "easeOutQuart",
    },
    animations: {
      x: { duration: 560, easing: "easeOutCubic" },
      y: { duration: 680, easing: "easeOutCubic" },
    },
    elements: {
      line: { borderWidth: 2.1, tension: 0.28 },
      point: { radius: 0, hoverRadius: 3.4, hitRadius: 12 },
      bar: { borderWidth: 1.2, borderRadius: 10 },
    },
    plugins: {
      speedpulseHoverGuide: {
        enabled: true,
        color: cssAlpha("--accent", 20),
        width: 1,
        dash: [4, 4],
      },
      speedpulseLegendFocus: {
        enabled: true,
        overlayColor: cssAlpha("--bg", 54),
      },
      legend: {
        position: "bottom",
        align: "start",
        maxHeight: 84,
        onHover: (_event, item, legend) => {
          if (!legend?.chart || !item || typeof item.datasetIndex !== "number")
            return;
          const chart = legend.chart;
          chart.$legendActiveDatasetIndex = item.datasetIndex;
          chart.draw();
        },
        onLeave: (_event, _item, legend) => {
          if (!legend?.chart) return;
          const chart = legend.chart;
          chart.$legendActiveDatasetIndex = null;
          chart.draw();
        },
        labels: {
          color: cssVar("--chart-label-strong"),
          boxWidth: 12,
          boxHeight: 3,
          borderRadius: 999,
          useBorderRadius: true,
          usePointStyle: true,
          pointStyle: "line",
          padding: 10,
          font: { weight: 700, size: 11 },
          filter: (item, data) => {
            const dataset = data?.datasets?.[item.datasetIndex];
            return !dataset?.hideFromLegend;
          },
        },
      },
      tooltip: {
        backgroundColor: cssAlpha("--bg", 88),
        borderColor: cssAlpha("--accent", 24),
        borderWidth: 1,
        titleColor: cssVar("--text"),
        bodyColor: cssVar("--text"),
        displayColors: true,
        padding: 10,
        cornerRadius: 12,
      },
    },
    layout: {
      padding: { top: 4, right: 2, bottom: 8, left: 2 },
    },
    scales: {
      x: {
        ticks: { color: cssVar("--chart-label-strong") },
        grid: {
          color: cssVar("--chart-grid"),
          lineWidth: 0.8,
          drawTicks: false,
        },
      },
      y: {
        ticks: { color: cssVar("--chart-label-strong") },
        grid: {
          color: cssVar("--chart-grid"),
          lineWidth: 0.8,
          drawTicks: false,
        },
      },
    },
  };
}

function rollingAverage(values, windowSize = 7) {
  if (!Array.isArray(values) || values.length === 0) return [];

  const safeWindow = Math.max(1, Number(windowSize) || 1);
  return values.map((_, index) => {
    const start = Math.max(0, index - safeWindow + 1);
    const sample = values
      .slice(start, index + 1)
      .filter((value) => typeof value === "number" && !Number.isNaN(value));
    if (sample.length === 0) {
      return null;
    }
    const total = sample.reduce((sum, value) => sum + value, 0);
    return Number((total / sample.length).toFixed(2));
  });
}

function latencyChartOptions() {
  const options = chartOptions();
  options.scales = {
    x: {
      ticks: { color: cssVar("--chart-label-strong") },
      grid: {
        color: cssVar("--chart-grid"),
        lineWidth: 0.8,
        drawTicks: false,
      },
    },
    y: {
      beginAtZero: true,
      ticks: { color: cssVar("--chart-label-strong") },
      grid: {
        color: cssVar("--chart-grid"),
        lineWidth: 0.8,
        drawTicks: false,
      },
      title: {
        display: true,
        text: "ms",
        color: cssVar("--chart-label-strong"),
      },
    },
    yLoss: {
      beginAtZero: true,
      position: "right",
      suggestedMax: 5,
      ticks: { color: cssVar("--chart-label-strong") },
      grid: { drawOnChartArea: false },
      title: {
        display: true,
        text: "Loss %",
        color: cssVar("--chart-label-strong"),
      },
    },
  };
  return options;
}

function thresholdChartOptions() {
  const options = chartOptions();
  options.plugins.speedpulseHoverGuide.enabled = false;
  options.plugins.speedpulseLegendFocus.enabled = false;
  options.plugins.legend.display = false;
  options.scales.y = {
    beginAtZero: true,
    ticks: {
      color: cssVar("--chart-label-strong"),
      precision: 0,
    },
    grid: {
      color: cssVar("--chart-grid"),
      lineWidth: 0.8,
      drawTicks: false,
    },
    title: {
      display: true,
      text: "Breaches (count)",
      color: cssVar("--chart-label-strong"),
    },
  };
  return options;
}

function renderThresholdSummary(data) {
  const summary = byId("breach-summary");
  if (!summary) return;

  const thresholds = data.thresholds || {};
  const downloadThreshold = Number(thresholds.download_mbps || 0);
  const uploadThreshold = Number(thresholds.upload_mbps || 0);
  const pingThreshold = Number(thresholds.ping_ms || 0);
  const lossThreshold = Number(thresholds.packet_loss_percent || 0);

  summary.textContent =
    `Window: ${data.range_label || "Selected range"} | Min download ${safeFixed(downloadThreshold)} Mbps | ` +
    `Min upload ${safeFixed(uploadThreshold)} Mbps | Max ping ${safeFixed(pingThreshold)} ms | ` +
    `Max loss ${safeFixed(lossThreshold)}%`;
}

function renderCharts(data) {
  currentPayload = data;
  ensureHoverGuidePlugin();

  if (speedChart) speedChart.destroy();
  if (latencyChart) latencyChart.destroy();
  if (thresholdChart) thresholdChart.destroy();
  speedChart = undefined;
  latencyChart = undefined;
  thresholdChart = undefined;

  const speedCanvas = byId("speedChart");
  const latencyCanvas = byId("latencyChart");
  const thresholdCanvas = byId("thresholdChart");
  if (!speedCanvas || !latencyCanvas || !thresholdCanvas) {
    return;
  }

  const labels = data.timeseries.map((item) =>
    item.timestamp.slice(5, 16).replace("T", " "),
  );
  const download = data.timeseries.map((item) => item.download_mbps);
  const upload = data.timeseries.map((item) => item.upload_mbps);
  const ping = data.timeseries.map((item) => item.ping_ms);
  const jitter = data.timeseries.map((item) => item.jitter_ms);
  const loss = data.timeseries.map((item) => item.packet_loss_percent);
  const downloadTrend = rollingAverage(download, 7);
  const uploadTrend = rollingAverage(upload, 7);
  const hasTrendWindow = labels.length >= 3;

  const thresholds = data.thresholds || {};
  const dlThreshold = Number(thresholds.download_mbps || 0);
  const ulThreshold = Number(thresholds.upload_mbps || 0);
  const pingThreshold = Number(thresholds.ping_ms || 0);

  const thresholdDatasets = [];
  if (dlThreshold > 0) {
      thresholdDatasets.push({
        label: `Min DL ${dlThreshold} Mbps`,
        data: labels.map(() => dlThreshold),
        borderColor: "rgba(255,107,107,0.6)",
        borderDash: [4, 4],
        borderWidth: 1.5,
        fill: false,
        pointRadius: 0,
        pointHitRadius: 0,
      });
  }
  if (ulThreshold > 0) {
      thresholdDatasets.push({
        label: `Min UL ${ulThreshold} Mbps`,
        data: labels.map(() => ulThreshold),
        borderColor: "rgba(255,170,80,0.5)",
        borderDash: [4, 4],
        borderWidth: 1.5,
        fill: false,
        pointRadius: 0,
        pointHitRadius: 0,
      });
  }

  const speedOptions = chartOptions();

  speedChart = new Chart(speedCanvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Download",
          data: download,
          borderColor: cssVar("--chart-download"),
          backgroundColor: (context) =>
            buildSurfaceGradient(context.chart, "--chart-download", 28, 4),
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 3,
        },
        {
          label: "Upload",
          data: upload,
          borderColor: cssVar("--chart-upload"),
          backgroundColor: (context) =>
            buildSurfaceGradient(context.chart, "--chart-upload", 24, 4),
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 3,
        },
        {
          label: "Download trend",
          data: downloadTrend,
          borderColor: cssVar("--chart-dl-avg"),
          borderDash: [8, 5],
          fill: false,
          tension: 0.25,
          pointRadius: 0,
          hideFromLegend: !hasTrendWindow,
        },
        {
          label: "Upload trend",
          data: uploadTrend,
          borderColor: cssVar("--chart-ul-avg"),
          borderDash: [8, 5],
          fill: false,
          tension: 0.25,
          pointRadius: 0,
          hideFromLegend: !hasTrendWindow,
        },
        ...thresholdDatasets,
      ],
    },
    options: speedOptions,
  });

  const latencyOptions = latencyChartOptions();

  latencyChart = new Chart(latencyCanvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Ping",
          data: ping,
          borderColor: cssVar("--chart-ping"),
          backgroundColor: (context) =>
            buildSurfaceGradient(context.chart, "--chart-ping", 20, 5),
          fill: false,
          pointRadius: 0,
          pointHoverRadius: 3,
          type: "line",
          yAxisID: "y",
        },
        {
          label: "Jitter",
          data: jitter,
          borderColor: cssVar("--chart-download"),
          backgroundColor: (context) =>
            buildSurfaceGradient(context.chart, "--chart-download", 16, 4),
          fill: false,
          pointRadius: 0,
          pointHoverRadius: 3,
          type: "line",
          yAxisID: "y",
        },
        {
          label: "Packet loss",
          data: loss,
          borderColor: "transparent",
          backgroundColor: (context) =>
            buildSurfaceGradient(context.chart, "--chart-loss", 28, 8),
          type: "bar",
          yAxisID: "yLoss",
          borderRadius: 10,
          maxBarThickness: 22,
        },
        ...(pingThreshold > 0
          ? [
              {
                label: `Max ping ${pingThreshold} ms`,
                data: labels.map(() => pingThreshold),
                borderColor: "rgba(255,107,107,0.5)",
                borderDash: [4, 4],
                borderWidth: 1.5,
                fill: false,
                pointRadius: 0,
                pointHitRadius: 0,
                type: "line",
                yAxisID: "y",
              },
            ]
          : []),
      ],
    },
    options: latencyOptions,
  });

  const breaches = data.violations || {};
  thresholdChart = new Chart(thresholdCanvas, {
    type: "bar",
    data: {
      labels: [
        "Download below min",
        "Upload below min",
        "Ping above max",
        "Loss above max",
      ],
      datasets: [
        {
          data: [
            Number(breaches.download || 0),
            Number(breaches.upload || 0),
            Number(breaches.ping || 0),
            Number(breaches.packet_loss || 0),
          ],
          backgroundColor: [
            cssAlpha("--chart-download", 42),
            cssAlpha("--chart-upload", 42),
            cssAlpha("--chart-ping", 42),
            cssAlpha("--chart-loss", 42),
          ],
          borderColor: [
            cssVar("--chart-download"),
            cssVar("--chart-upload"),
            cssVar("--chart-ping"),
            cssVar("--chart-loss"),
          ],
          borderWidth: 1.4,
          borderRadius: 12,
          hoverBorderWidth: 1.8,
          barPercentage: 0.74,
          categoryPercentage: 0.74,
        },
      ],
    },
    options: thresholdChartOptions(),
  });

  renderThresholdSummary(data);
  renderHeatmap(data);
}

function renderHeatmap(data) {
  const grid = byId("heatmap-grid");
  if (!grid) return;
  grid.textContent = "";

  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const buckets = {};
  for (let d = 0; d < 7; d++) {
    for (let h = 0; h < 24; h++) {
      buckets[`${d}-${h}`] = [];
    }
  }

  for (const item of data.timeseries || []) {
    const ts = new Date(item.timestamp);
    const day = (ts.getDay() + 6) % 7;
    const hour = ts.getHours();
    if (typeof item.download_mbps === "number") {
      buckets[`${day}-${hour}`].push(item.download_mbps);
    }
  }

  let allAvg = [];
  const avgGrid = {};
  for (const [key, vals] of Object.entries(buckets)) {
    if (vals.length > 0) {
      const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
      avgGrid[key] = avg;
      allAvg.push(avg);
    }
  }

  if (allAvg.length === 0) {
    const empty = createEmptyStateCard(
      "Heatmap waiting for data",
      "Collect a few scans across different times to reveal usage patterns.",
      { icon: "◌", compact: true, hint: "Tip: keep scheduled scans enabled for 24h." },
    );
    empty.style.gridColumn = "1 / -1";
    grid.appendChild(empty);
    return;
  }

  const minVal = Math.min(...allAvg);
  const maxVal = Math.max(...allAvg);
  const range = maxVal - minVal || 1;

  // corner blank
  const corner = document.createElement("div");
  corner.className = "heatmap-corner";
  grid.appendChild(corner);

  // hour headers
  for (let h = 0; h < 24; h++) {
    const hdr = document.createElement("div");
    hdr.className = "heatmap-hour";
    hdr.textContent = String(h).padStart(2, "0");
    grid.appendChild(hdr);
  }

  // rows
  for (let d = 0; d < 7; d++) {
    const label = document.createElement("div");
    label.className = "heatmap-day";
    label.textContent = days[d];
    grid.appendChild(label);

    for (let h = 0; h < 24; h++) {
      const cell = document.createElement("div");
      cell.className = "heatmap-cell";
      const key = `${d}-${h}`;
      if (avgGrid[key] !== undefined) {
        const pct = (avgGrid[key] - minVal) / range;
        const hue = pct * 120;
        const light = 25 + pct * 30;
        cell.style.background = `hsl(${hue}, 70%, ${light}%)`;
        cell.title = `${days[d]} ${String(h).padStart(2, "0")}:00 — ${avgGrid[key].toFixed(1)} Mbps`;
      } else {
        cell.classList.add("heatmap-empty");
        cell.title = `${days[d]} ${String(h).padStart(2, "0")}:00 — No data`;
      }
      grid.appendChild(cell);
    }
  }
}

function compareValues(left, right, key) {
  if (key === "healthy") {
    return Number(left) - Number(right);
  }

  if (typeof left === "number" && typeof right === "number") {
    return left - right;
  }

  return String(left).localeCompare(String(right));
}

function sortRows(rows) {
  const sorted = [...rows];
  const { key, direction } = currentSort;

  sorted.sort((a, b) => {
    const result = compareValues(a[key], b[key], key);
    return direction === "asc" ? result : -result;
  });

  return sorted;
}

function filterRows(rows) {
  const query = String(byId("table-search")?.value || "").trim().toLowerCase();
  if (!query) return rows;

  return rows.filter((row) => {
    return [row.server, row.timestamp, row.status, healthyLabel(row)].some(
      (value) => String(value).toLowerCase().includes(query),
    );
  });
}

function pageSize() {
  return Number(byId("page-size")?.value || "10");
}

function totalPages(rows) {
  return Math.max(1, Math.ceil(rows.length / pageSize()));
}

function createBadge(text, badgeClass) {
  const badge = document.createElement("span");
  badge.className = `badge ${badgeClass}`;
  badge.textContent = text;
  return badge;
}

function addTextCell(rowElement, value, className = "") {
  const cell = document.createElement("td");
  if (className) {
    cell.className = className;
  }
  cell.textContent = value;
  rowElement.appendChild(cell);
}

function addBadgeCell(rowElement, text, badgeClass) {
  const cell = document.createElement("td");
  cell.appendChild(createBadge(text, badgeClass));
  rowElement.appendChild(cell);
}

function formatTimestamp(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function visibleRows() {
  return sortRows(filterRows(latestRows));
}

function renderResultsSkeleton(rowCount = 5) {
  const tbody = document.querySelector("#latest-table tbody");
  if (!tbody) return;

  tbody.textContent = "";

  for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
    const row = document.createElement("tr");
    row.className = "skeleton-row";
    row.setAttribute("aria-hidden", "true");

    for (let colIndex = 0; colIndex < 9; colIndex += 1) {
      const cell = document.createElement("td");
      const line = document.createElement("span");

      if (colIndex === 0) {
        line.className = "skeleton-line skeleton-line-id";
      } else if (colIndex === 1 || colIndex === 7) {
        line.className = "skeleton-line skeleton-line-chip";
      } else {
        line.className = "skeleton-line skeleton-line-cell";
      }

      cell.appendChild(line);
      row.appendChild(cell);
    }

    tbody.appendChild(row);
  }

  const resultsCount = byId("results-count");
  const pageIndicator = byId("page-indicator");
  const pagePrev = byId("page-prev");
  const pageNext = byId("page-next");
  if (resultsCount) resultsCount.textContent = "Loading results...";
  if (pageIndicator) pageIndicator.textContent = "Page --";
  if (pagePrev) pagePrev.disabled = true;
  if (pageNext) pageNext.disabled = true;
}

function setDashboardLoadingState(isLoading, isInitial = false) {
  const panelTargets = [
    byId("latest-results"),
    byId("heatmap-section"),
    byId("notification-history"),
    ...Array.from(document.querySelectorAll("#charts .panel")),
    ...Array.from(document.querySelectorAll("#reliability .panel")),
  ].filter(Boolean);

  if (isInitial) {
    if (isLoading) {
      renderHeroMetricsSkeleton();
      renderResultsSkeleton();
    }

    panelTargets.forEach((panel) => {
      panel.classList.toggle("panel-loading", isLoading);
      panel.classList.remove("panel-refreshing");
    });
  } else {
    panelTargets.forEach((panel) => {
      panel.classList.toggle("panel-refreshing", isLoading);
      panel.classList.remove("panel-loading");
    });
  }

  byId("hero-metrics")?.classList.toggle("hero-metrics-loading", isLoading);
  byId("hero-metrics")?.classList.toggle(
    "hero-metrics-refreshing",
    isLoading && !isInitial,
  );
}

function setChartsTransitioning(isTransitioning) {
  byId("charts")?.classList.toggle("charts-transitioning", isTransitioning);
}

function renderTable() {
  const tbody = document.querySelector("#latest-table tbody");
  if (!tbody) return;
  tbody.textContent = "";

  const rows = visibleRows();
  const pages = totalPages(rows);
  currentPage = Math.min(Math.max(currentPage, 1), pages);

  const start = (currentPage - 1) * pageSize();
  const pagedRows = rows.slice(start, start + pageSize());

  if (pagedRows.length === 0) {
    const emptyRow = document.createElement("tr");
    const emptyCell = document.createElement("td");
    emptyCell.colSpan = 9;
    emptyCell.className = "empty-state";
    const empty = createEmptyStateCard(
      "No matching results",
      "Adjust the search text or range filter to find historical scans.",
      { icon: "⌕", compact: true, hint: "Tip: clear search or widen the range." },
    );
    emptyCell.appendChild(empty);
    emptyRow.appendChild(emptyCell);
    tbody.appendChild(emptyRow);
  }

  for (const [index, row] of pagedRows.entries()) {
    const tableRow = document.createElement("tr");
    const displayId = start + index + 1;

    addTextCell(tableRow, String(displayId), "table-id");
    addBadgeCell(tableRow, row.status || "Completed", "badge-complete");
    addTextCell(tableRow, row.server);
    addTextCell(tableRow, `${safeFixed(row.download_mbps)} Mbps`);
    addTextCell(tableRow, `${safeFixed(row.upload_mbps)} Mbps`);
    addTextCell(tableRow, `${safeFixed(row.ping_ms)} ms`);
    addTextCell(tableRow, `${safeFixed(row.packet_loss_percent)} %`);
    addBadgeCell(
      tableRow,
      healthyLabel(row),
      row.healthy ? "badge-healthy" : "badge-warning",
    );
    addTextCell(tableRow, formatTimestamp(row.timestamp_iso));

    tbody.appendChild(tableRow);
  }

  const rangeLabel = currentPayload?.range_label || "Selected range";
  const scheduledPerDay = currentPayload?.scheduled_tests_per_day || 0;
  const resultsCount = byId("results-count");
  const pageIndicator = byId("page-indicator");
  const pagePrev = byId("page-prev");
  const pageNext = byId("page-next");

  if (resultsCount) {
    resultsCount.textContent =
      `Showing ${pagedRows.length} of ${rows.length} results | ${rangeLabel} | ${scheduledPerDay} scans scheduled daily`;
  }
  if (pageIndicator) pageIndicator.textContent = `Page ${currentPage} of ${pages}`;
  if (pagePrev) pagePrev.disabled = currentPage <= 1;
  if (pageNext) pageNext.disabled = currentPage >= pages;
}

function updateSortIndicators() {
  const headers = document.querySelectorAll("th.sortable");
  for (const header of headers) {
    header.classList.remove("sort-asc", "sort-desc");
    if (header.dataset.sortKey === currentSort.key) {
      header.classList.add(
        currentSort.direction === "asc" ? "sort-asc" : "sort-desc",
      );
      header.setAttribute(
        "aria-sort",
        currentSort.direction === "asc" ? "ascending" : "descending",
      );
    } else {
      header.setAttribute("aria-sort", "none");
    }
  }
}

function bindSorting() {
  const headers = document.querySelectorAll("th.sortable");
  for (const header of headers) {
    header.tabIndex = 0;
    header.setAttribute("role", "button");

    const triggerSort = () => {
      const nextKey = header.dataset.sortKey;
      if (!nextKey) return;

      if (currentSort.key === nextKey) {
        currentSort.direction =
          currentSort.direction === "asc" ? "desc" : "asc";
      } else {
        currentSort = {
          key: nextKey,
          direction: defaultDirectionForKey(nextKey),
        };
      }

      currentPage = 1;
      updateSortIndicators();
      renderTable();
    };

    header.addEventListener("click", triggerSort);
    header.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        triggerSort();
      }
    });
  }

  updateSortIndicators();
}

function currentRangeSelection() {
  const value = byId("range")?.value || "today";
  if (value === "today") {
    return { mode: "today", days: null };
  }
  return { mode: "days", days: Number(value || "30") };
}

function metricsUrl() {
  const selection = currentRangeSelection();
  if (selection.mode === "today") {
    return "/api/metrics?mode=today";
  }
  return `/api/metrics?mode=days&days=${encodeURIComponent(selection.days)}`;
}

function reportDownloadUrl() {
  const selection = currentRangeSelection();
  const themeApi = window.SpeedPulseTheme;
  const activeTheme =
    themeApi && typeof themeApi.currentPreferences === "function"
      ? String(themeApi.currentPreferences().activeTheme || "default-dark")
      : "default-dark";

  const params = new URLSearchParams({ mode: selection.mode, format: "html" });
  if (selection.mode === "days") {
    params.set("days", String(selection.days || 30));
  }
  params.set("theme_id", activeTheme);
  return `/api/reports/download?${params.toString()}`;
}

function attachmentFilenameFromResponse(response, fallbackName) {
  const disposition = String(response.headers.get("Content-Disposition") || "");
  const match = disposition.match(/filename=\"?([^\";]+)\"?/i);
  return match ? match[1] : fallbackName;
}

async function generateRangeReport() {
  const button = byId("generate-report");
  if (!button) return;
  button.disabled = true;

  try {
    const response = await fetch(reportDownloadUrl());
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || payload.message || "Unable to generate report.");
    }

    const blob = await response.blob();
    const fallbackName = `speedpulse-report-${new Date().toISOString().slice(0, 10)}.html`;
    const filename = attachmentFilenameFromResponse(response, fallbackName);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showMessage("Report generated and downloaded.", "success");
  } catch (error) {
    showMessage(error.message || "Unable to generate report.", "error");
  } finally {
    button.disabled = false;
  }
}

async function sendWeeklyReportNow() {
  const button = byId("send-weekly-report");
  if (!button || weeklyReportInFlight) return;

  weeklyReportInFlight = true;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");

  try {
    const response = await fetch("/api/reports/weekly/send-now", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: "{}",
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || payload.message || "Unable to send weekly report.");
    }

    showMessage(payload.message || "Weekly report email sent.", "success");
  } catch (error) {
    showMessage(error.message || "Unable to send weekly report.", "error");
  } finally {
    weeklyReportInFlight = false;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

async function loadMetrics() {
  setStatus("Loading metrics...");
  const isInitialLoad = !initialMetricsLoaded;
  setDashboardLoadingState(true, isInitialLoad);
  if (!isInitialLoad) {
    setChartsTransitioning(true);
  }

  try {
    const response = await fetch(metricsUrl());
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    if (!response.ok) {
      showMessage("Failed to load metrics. Click Refresh to retry.", "error");
      setStatus("Load error — click Refresh to retry");
      return;
    }

    clearMessage();
    const data = await response.json();

    currentPayload = data;
    latestRows = data.latest_tests || [];
    currentPage = 1;
    currentServerLabel = data.server_selection_label || currentServerLabel;

    renderDetectedConnectionInfo(data);
    renderApplicationClock(data);
    renderScheduleNote(data);
    renderHeroMetrics(data);
    initPremiumDepth();
    renderSlaPanel(data);
    renderIncidentHistory(data);
    renderCharts(data);
    renderTable();
    initialMetricsLoaded = true;
    setDashboardLoadingState(false, isInitialLoad);
    if (!isInitialLoad) {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          setChartsTransitioning(false);
        });
      });
    }

    setStatus(
      data.last_test_at
        ? `${data.range_label} | Last test: ${data.last_test_at.replace("T", " ").slice(0, 19)}`
        : `${data.range_label} | No tests in selected range`,
    );
  } catch (error) {
    showMessage(
      "Network error loading metrics. Click Refresh to retry.",
      "error",
    );
    setStatus("Connection error — click Refresh to retry");
  } finally {
    setDashboardLoadingState(false, isInitialLoad);
    if (!isInitialLoad) {
      setChartsTransitioning(false);
    }
  }
}

function saveChartAsPng(chartId) {
  const chartMap = {
    speedChart,
    latencyChart,
    thresholdChart,
    slaBreakdownChart,
  };
  const chart = chartMap[chartId];
  if (!chart) return;
  const filename = `${chartId}-${new Date().toISOString().slice(0, 10)}.png`;
  const canvas = chart.canvas;

  if (canvas instanceof HTMLCanvasElement && typeof canvas.toBlob === "function") {
    canvas.toBlob((blob) => {
      if (!blob) return;
      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    }, "image/png");
    return;
  }

  const link = document.createElement("a");
  link.href = chart.toBase64Image();
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function escapeCsv(value) {
  const text = String(value ?? "");
  if (text.includes(",") || text.includes('"') || text.includes("\n")) {
    return `"${text.replaceAll('"', '""')}"`;
  }
  return text;
}

function exportResults() {
  const rows = visibleRows();
  if (rows.length === 0) {
    showMessage("No results available to export.", "warning");
    return;
  }

  const headers = [
    "ID",
    "Status",
    "Server",
    "Download Mbps",
    "Upload Mbps",
    "Ping ms",
    "Packet loss %",
    "Healthy",
    "Created at",
  ];
  const lines = [headers.join(",")];

  rows.forEach((row, index) => {
    lines.push(
      [
        index + 1,
        row.status || "Completed",
        row.server,
        safeFixed(row.download_mbps),
        safeFixed(row.upload_mbps),
        safeFixed(row.ping_ms),
        safeFixed(row.packet_loss_percent),
        healthyLabel(row),
        formatTimestamp(row.timestamp_iso),
      ]
        .map(escapeCsv)
        .join(","),
    );
  });

  const blob = new Blob([`${lines.join("\n")}\n`], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `speed-results-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function runSpeedtestNow(serverId = "") {
  if (manualRunInFlight) {
    return;
  }

  clearMessage();
  setStatus("Starting speedtest...");

  try {
    const response = await fetch("/api/run/speedtest", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ server_id: serverId }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));

    if (
      response.ok &&
      (payload.status === "running" || response.status === 202)
    ) {
      manualRunInFlight = true;
      setRunButtonState(true);
      closeServerModal();
      renderRunModal(payload);
      openRunModal(payload.started_at);
      setStatus(payload.stage || "Running speedtest...");
      startRunStatusPolling();
      return;
    }

    if (response.status === 409 && payload.status === "running") {
      manualRunInFlight = true;
      setRunButtonState(true);
      closeServerModal();
      renderRunModal(payload);
      openRunModal(payload.started_at);
      setStatus(payload.stage || "Running speedtest...");
      startRunStatusPolling();
      return;
    }

    if (response.status === 429) {
      showMessage(
        payload.message || "Please wait before running another test.",
        "warning",
      );
      setStatus("Speed test not started");
      return;
    }

    showMessage(
      payload.detail || payload.message || "Speed test failed.",
      "error",
    );
    setStatus("Speed test failed");
  } catch (error) {
    showMessage("Unable to run speed test right now.", "error");
    setStatus("Speed test failed");
  }
}

function prefersReducedMotion() {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function collectMotionTargets(selectors) {
  const unique = new Set();
  const targets = [];

  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((element) => {
      if (!(element instanceof HTMLElement) || unique.has(element)) return;
      unique.add(element);
      targets.push(element);
    });
  });

  return targets;
}

function initMotionReveals() {
  const targets = collectMotionTargets([
    ".dashboard-page .topbar",
    ".dashboard-page .status-line",
    ".dashboard-page .section-jumpbar-wrap",
    ".dashboard-page #hero-metrics",
    ".dashboard-page #charts .panel",
    ".dashboard-page #latest-results",
    ".dashboard-page #heatmap-section",
    ".dashboard-page #reliability .panel",
    ".dashboard-page #notification-history",
    ".dashboard-page .sidebar .brand-lockup",
    ".dashboard-page .sidebar .sidebar-card",
    ".dashboard-page .sidebar .nav-block",
    ".dashboard-page .sidebar .sidebar-footer",
  ]);

  if (targets.length === 0) return;

  targets.forEach((element, index) => {
    element.classList.add("motion-reveal");
    element.style.setProperty(
      "--motion-reveal-delay",
      `${Math.min(index, 10) * 52}ms`,
    );
  });

  if (prefersReducedMotion() || typeof IntersectionObserver !== "function") {
    targets.forEach((element) => element.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        if (entry.target instanceof HTMLElement) {
          entry.target.classList.add("is-visible");
        }
        obs.unobserve(entry.target);
      });
    },
    { threshold: 0.08, rootMargin: "0px 0px -8% 0px" },
  );

  targets.forEach((element) => observer.observe(element));
}

function initPremiumDepth() {
  if (prefersReducedMotion()) return;
  if (
    typeof window.matchMedia === "function" &&
    !window.matchMedia("(hover: hover) and (pointer: fine)").matches
  ) {
    return;
  }

  document.querySelectorAll(".dashboard-page .topbar").forEach((topbar) => {
    topbar.classList.remove("premium-depth-target", "is-depth-active");
    topbar.removeAttribute("data-depth-bound");
    topbar.querySelectorAll(":scope > .depth-sheen").forEach((sheen) => {
      sheen.remove();
    });
  });

  const targets = collectMotionTargets([
    ".dashboard-page #hero-metrics .metric-card",
    ".dashboard-page #charts .panel",
  ]);

  targets.forEach((target) => {
    if (target.dataset.depthBound === "true") return;
    target.dataset.depthBound = "true";
    target.classList.add("premium-depth-target");
    if (!target.querySelector(":scope > .depth-sheen")) {
      const sheen = document.createElement("span");
      sheen.className = "depth-sheen";
      sheen.setAttribute("aria-hidden", "true");
      target.prepend(sheen);
    }

    target.addEventListener("pointermove", (event) => {
      const rect = target.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width) * 100;
      const y = ((event.clientY - rect.top) / rect.height) * 100;
      target.style.setProperty("--depth-x", `${Math.max(0, Math.min(100, x))}%`);
      target.style.setProperty("--depth-y", `${Math.max(0, Math.min(100, y))}%`);
      target.classList.add("is-depth-active");
    });

    target.addEventListener("pointerleave", () => {
      target.classList.remove("is-depth-active");
    });
  });
}

function initMagneticPrimaryCta() {
  if (prefersReducedMotion()) return;
  if (
    typeof window.matchMedia === "function" &&
    !window.matchMedia("(hover: hover) and (pointer: fine)").matches
  ) {
    return;
  }

  const button = byId("run-test");
  if (!(button instanceof HTMLElement)) return;
  if (button.dataset.magneticBound === "true") return;
  button.dataset.magneticBound = "true";
  button.classList.add("is-magnetic");

  button.addEventListener("pointermove", (event) => {
    const rect = button.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 100;
    const y = ((event.clientY - rect.top) / rect.height) * 100;
    button.style.setProperty("--magnetic-x", `${Math.max(0, Math.min(100, x))}`);
    button.style.setProperty("--magnetic-y", `${Math.max(0, Math.min(100, y))}`);
  });

  button.addEventListener("pointerleave", () => {
    button.style.removeProperty("--magnetic-x");
    button.style.removeProperty("--magnetic-y");
  });
}

function setPanelCollapsedState(toggle, body, collapsed, options = {}) {
  const instant = Boolean(options.instant) || prefersReducedMotion();
  const panel = toggle.closest(".panel-collapsible");
  const sectionName = toggle.dataset.collapseName || "section";
  const action = collapsed ? "Expand" : "Collapse";

  if (body.dataset.animating === "true") return;

  toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  toggle.setAttribute("aria-label", `${action} ${sectionName}`);
  toggle.setAttribute("title", `${action} ${sectionName}`);
  panel?.classList.toggle("is-collapsed", collapsed);

  if (instant) {
    body.dataset.animating = "false";
    body.hidden = collapsed;
    body.style.height = "";
    body.style.opacity = "";
    body.style.overflow = "";
    body.style.transition = "";
    return;
  }

  if (collapsed) {
    const startHeight = body.scrollHeight;
    if (startHeight <= 0) {
      body.hidden = true;
      return;
    }

    body.hidden = false;
    body.dataset.animating = "true";
    body.style.overflow = "hidden";
    body.style.height = `${startHeight}px`;
    body.style.opacity = "1";
    body.style.transition =
      "height 340ms cubic-bezier(0.22, 0.61, 0.36, 1), opacity 260ms cubic-bezier(0.22, 0.61, 0.36, 1)";

    window.requestAnimationFrame(() => {
      body.style.height = "0px";
      body.style.opacity = "0";
    });

    const onCollapseEnd = (event) => {
      if (event.propertyName !== "height") return;
      body.hidden = true;
      body.dataset.animating = "false";
      body.style.height = "";
      body.style.opacity = "";
      body.style.overflow = "";
      body.style.transition = "";
      body.removeEventListener("transitionend", onCollapseEnd);
    };

    body.addEventListener("transitionend", onCollapseEnd);
    return;
  }

  body.hidden = false;
  body.dataset.animating = "true";
  body.style.overflow = "hidden";
  body.style.height = "0px";
  body.style.opacity = "0";
  body.style.transition =
    "height 340ms cubic-bezier(0.22, 0.61, 0.36, 1), opacity 260ms cubic-bezier(0.22, 0.61, 0.36, 1)";

  const targetHeight = body.scrollHeight;
  if (targetHeight <= 0) {
    body.dataset.animating = "false";
    body.style.height = "";
    body.style.opacity = "";
    body.style.overflow = "";
    body.style.transition = "";
    return;
  }

  window.requestAnimationFrame(() => {
    body.style.height = `${targetHeight}px`;
    body.style.opacity = "1";
  });

  const onExpandEnd = (event) => {
    if (event.propertyName !== "height") return;
    body.dataset.animating = "false";
    body.style.height = "";
    body.style.opacity = "";
    body.style.overflow = "";
    body.style.transition = "";
    body.removeEventListener("transitionend", onExpandEnd);
  };

  body.addEventListener("transitionend", onExpandEnd);
}

function bindCollapsiblePanels() {
  document
    .querySelectorAll(".panel-collapse-toggle[data-collapse-target]")
    .forEach((toggle) => {
      const targetId = String(toggle.dataset.collapseTarget || "");
      const body = byId(targetId);
      if (!body) return;
      const panel = toggle.closest(".panel");
      const panelHead = panel ? panel.querySelector(".panel-head") : null;
      const triggerToggle = () => {
        if (body.dataset.animating === "true") return;
        const isExpanded = toggle.getAttribute("aria-expanded") === "true";
        setPanelCollapsedState(toggle, body, isExpanded);
      };

      const initiallyCollapsed =
        body.hasAttribute("hidden") ||
        toggle.getAttribute("aria-expanded") !== "true";
      setPanelCollapsedState(toggle, body, initiallyCollapsed, {
        instant: true,
      });

      toggle.addEventListener("click", triggerToggle);

      if (panelHead instanceof HTMLElement) {
        panelHead.classList.add("panel-head-clickable");
        panelHead.addEventListener("click", (event) => {
          if (!(event.target instanceof HTMLElement)) {
            triggerToggle();
            return;
          }
          if (event.target.closest(".panel-collapse-toggle")) return;
          if (event.target.closest("a, button, input, select, textarea, label")) return;
          triggerToggle();
        });
      }
    });
}

function bindEvents() {
  bindCollapsiblePanels();
  bindSectionNavHighlight();
  bindMobileNav();
  const themeModeToggle = byId("theme-mode-toggle");
  if (themeModeToggle) {
    syncThemeModeToggle();
    themeModeToggle.addEventListener("click", cycleThemeMode);
  }
  const reportButton = byId("generate-report");
  const runButton = byId("run-test");
  const weeklyReportButton = byId("send-weekly-report");
  if (reportButton) {
    reportButton.setAttribute("title", "Generate report (Shortcut: G)");
  }
  if (runButton) {
    runButton.setAttribute("title", "Manual speed test (Shortcut: R)");
  }
  if (weeklyReportButton) {
    weeklyReportButton.setAttribute("title", "Send weekly report email now (Shortcut: W)");
  }
  byId("range")?.addEventListener("change", loadMetrics);
  reportButton?.addEventListener("click", () => {
    void generateRangeReport();
  });
  weeklyReportButton?.addEventListener("click", () => {
    void sendWeeklyReportNow();
  });
  const defaultServerSelect = byId("server-select");
  if (defaultServerSelect) {
    defaultServerSelect.addEventListener("change", updateServerSettings);
  }
  runButton?.addEventListener("click", () => {
    if (serverOptions.length === 0 && !serverSettingsLoading) {
      void loadServerSettings();
    }
    openServerModal();
  });
  byId("server-modal-start")?.addEventListener("click", () => {
    void runSpeedtestNow(currentManualServerId());
  });
  byId("server-modal-cancel")?.addEventListener("click", closeServerModal);
  byId("server-modal-close")?.addEventListener("click", closeServerModal);
  byId("server-modal")?.addEventListener("close", syncBodyModalState);
  byId("server-modal")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeServerModal();
  });
  byId("server-modal")?.addEventListener("click", (event) => {
    if (!(event.target instanceof HTMLElement)) return;
    if (!event.target.closest(".dialog-card")) {
      closeServerModal();
    }
  });
  byId("table-search")?.addEventListener("input", () => {
    currentPage = 1;
    renderTable();
  });
  byId("page-size")?.addEventListener("change", () => {
    currentPage = 1;
    renderTable();
  });
  byId("page-prev")?.addEventListener("click", () => {
    currentPage -= 1;
    renderTable();
  });
  byId("page-next")?.addEventListener("click", () => {
    currentPage += 1;
    renderTable();
  });
  byId("export-results")?.addEventListener("click", exportResults);
  document.querySelectorAll(".chart-save").forEach((btn) => {
    btn.addEventListener("click", () => saveChartAsPng(btn.dataset.chart));
  });

  document.addEventListener("keydown", (event) => {
    const tag = (event.target?.tagName || "").toLowerCase();
    if (
      tag === "input" ||
      tag === "textarea" ||
      tag === "select" ||
      event.target?.isContentEditable
    )
      return;

    if (event.key === "Escape") {
      if (modalIsOpen("run-modal")) {
        closeRunModal();
        return;
      }
      if (modalIsOpen("server-modal")) {
        closeServerModal();
        return;
      }
    }
    if (event.key === "r" || event.key === "R") {
      if (!event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        if (serverOptions.length === 0 && !serverSettingsLoading)
          void loadServerSettings();
        openServerModal();
      }
    }
    if (event.key === "g" || event.key === "G") {
      if (!event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        void generateRangeReport();
      }
    }
    if (event.key === "w" || event.key === "W") {
      if (!event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        void sendWeeklyReportNow();
      }
    }
    const rangeKeys = { 1: "today", 2: "7", 3: "30", 4: "90", 5: "365" };
    if (
      rangeKeys[event.key] &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey
    ) {
      const rangeSelect = byId("range");
      if (rangeSelect) {
        rangeSelect.value = rangeKeys[event.key];
        void loadMetrics();
      }
    }
  });
}

function setActiveSectionNav(sectionId) {
  document.querySelectorAll("a.nav-link").forEach((link) => {
    const href = String(link.getAttribute("href") || "");
    const isHashLink = href.startsWith("#");
    const active = isHashLink && href === `#${sectionId}`;
    link.classList.toggle("active", active);
    if (active) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

function bindSectionNavHighlight() {
  const mainShell = document.querySelector(".main-shell");
  if (!(mainShell instanceof HTMLElement)) return;
  const jumpbar = byId("section-jumpbar");

  const sectionLinks = Array.from(
    document.querySelectorAll("a.nav-link[href^='#']"),
  )
    .map((link) => {
      const href = String(link.getAttribute("href") || "");
      const sectionId = href.slice(1);
      const section = byId(sectionId);
      if (!sectionId || !section) return null;
      return { link, sectionId, section };
    })
    .filter(Boolean);

  if (sectionLinks.length === 0) return;

  const scrollAnchorOffset = () =>
    (jumpbar instanceof HTMLElement ? jumpbar.offsetHeight + 12 : 0) + 18;
  const probeOffset = () => Math.min(mainShell.clientHeight * 0.31, 210);
  const switchHysteresis = () =>
    Math.max(72, Math.min(mainShell.clientHeight * 0.16, 140));
  const topSnapThreshold = () =>
    Math.max(12, Math.min(scrollAnchorOffset() + 6, 72));
  const sectionTop = (entry) =>
    Math.max(0, entry.section.offsetTop - scrollAnchorOffset());
  const sectionById = new Map(
    sectionLinks.map((entry) => [entry.sectionId, entry]),
  );

  let activeSectionId = sectionLinks[0].sectionId;
  let lastProbeY = 0;

  const candidateForProbe = (probeY) => {
    if (mainShell.scrollTop <= topSnapThreshold()) {
      return sectionLinks[0].sectionId;
    }

    if (mainShell.scrollTop + mainShell.clientHeight >= mainShell.scrollHeight - 6) {
      return sectionLinks[sectionLinks.length - 1].sectionId;
    }

    let candidate = sectionLinks[0].sectionId;
    for (const entry of sectionLinks) {
      if (probeY >= sectionTop(entry)) {
        candidate = entry.sectionId;
      } else {
        break;
      }
    }
    return candidate;
  };

  const recalc = (force = false) => {
    if (mainShell.scrollTop <= topSnapThreshold()) {
      const topSectionId = sectionLinks[0].sectionId;
      if (activeSectionId !== topSectionId || force) {
        activeSectionId = topSectionId;
        setActiveSectionNav(activeSectionId);
      }
      lastProbeY = mainShell.scrollTop + probeOffset();
      return;
    }

    const probeY = mainShell.scrollTop + probeOffset();
    let candidate = candidateForProbe(probeY);

    if (!force && candidate !== activeSectionId) {
      const movingDown = probeY >= lastProbeY;
      const current = sectionById.get(activeSectionId);
      const next = sectionById.get(candidate);
      const hysteresis = switchHysteresis();

      if (current && next) {
        const currentTop = sectionTop(current);
        const nextTop = sectionTop(next);
        if (movingDown && probeY < nextTop + hysteresis) {
          candidate = activeSectionId;
        }
        if (!movingDown && probeY > currentTop - hysteresis) {
          candidate = activeSectionId;
        }
      }
    }

    if (candidate !== activeSectionId || force) {
      activeSectionId = candidate;
      setActiveSectionNav(activeSectionId);
    }
    lastProbeY = probeY;
  };

  let ticking = false;
  const scheduleRecalc = () => {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(() => {
      ticking = false;
      recalc(false);
    });
  };

  for (const entry of sectionLinks) {
    entry.link.addEventListener("click", (event) => {
      event.preventDefault();
      activeSectionId = entry.sectionId;
      setActiveSectionNav(activeSectionId);
      const targetTop = Math.max(
        0,
        entry.section.offsetTop - scrollAnchorOffset(),
      );
      mainShell.scrollTo({ top: targetTop, behavior: "smooth" });
      if (window.history && typeof window.history.replaceState === "function") {
        window.history.replaceState(null, "", `#${entry.sectionId}`);
      }
      window.setTimeout(() => recalc(true), 320);
    });
  }

  mainShell.addEventListener("scroll", scheduleRecalc, { passive: true });
  window.addEventListener("resize", scheduleRecalc);

  const initialHash = window.location.hash.slice(1);
  const initialTarget = sectionLinks.find(
    (entry) => entry.sectionId === initialHash,
  );
  if (initialTarget) {
    mainShell.scrollTop = Math.max(
      0,
      initialTarget.section.offsetTop - scrollAnchorOffset(),
    );
    activeSectionId = initialTarget.sectionId;
    setActiveSectionNav(activeSectionId);
    recalc(true);
  } else {
    recalc(true);
  }
}

function bindMobileNav() {
  const toggle = byId("mobile-nav-toggle");
  const sidebar = byId("sidebar");
  const backdrop = byId("sidebar-backdrop");
  if (!toggle || !sidebar || !backdrop) return;

  function openSidebar() {
    sidebar.classList.add("open");
    backdrop.classList.remove("hidden");
    toggle.setAttribute("aria-expanded", "true");
    document.body.classList.add("nav-open");
  }

  function closeSidebar() {
    sidebar.classList.remove("open");
    backdrop.classList.add("hidden");
    toggle.setAttribute("aria-expanded", "false");
    document.body.classList.remove("nav-open");
  }

  toggle.addEventListener("click", () => {
    sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
  });

  backdrop.addEventListener("click", closeSidebar);

  sidebar.querySelectorAll("a.nav-link").forEach((link) => {
    link.addEventListener("click", () => {
      if (window.innerWidth <= 1080) closeSidebar();
    });
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 1080) closeSidebar();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSidebar();
  });
}

function notificationChannelIcon(channel) {
  if (channel === "email") return "📧";
  if (channel === "webhook") return "🔗";
  if (channel === "ntfy") return "🔔";
  return "📨";
}

function notificationEventLabel(eventType) {
  if (eventType === "alert") return "Speed Alert";
  if (eventType === "weekly_report") return "Weekly Report";
  if (eventType === "monthly_report") return "Monthly Report";
  if (eventType === "health_check") return "Health Check";
  return eventType;
}

async function loadNotificationLog() {
  const root = byId("notification-log");
  if (!root) return;

  try {
    const response = await fetch("/api/notifications/log");
    if (!response.ok) {
      root.textContent = "";
      root.appendChild(
        createEmptyStateCard(
          "Notification log unavailable",
          "The history feed could not be loaded right now.",
          { icon: "!", hint: "Tip: refresh in a few seconds." },
        ),
      );
      return;
    }
    const entries = await response.json();
    root.textContent = "";

    if (!entries.length) {
      root.appendChild(
        createEmptyStateCard(
          "No notifications yet",
          "Alerts and reports will appear here after the first delivery.",
          { icon: "✦", hint: "Tip: enable alerts in Settings > Push alerts." },
        ),
      );
      return;
    }

    for (const entry of entries) {
      const item = document.createElement("div");
      item.className = "notification-item";

      const icon = document.createElement("span");
      icon.className = "notification-icon";
      icon.textContent = notificationChannelIcon(entry.channel);

      const body = document.createElement("div");
      body.className = "notification-body";

      const top = document.createElement("div");
      top.className = "notification-top";
      const label = document.createElement("strong");
      label.textContent = notificationEventLabel(entry.event_type);
      const badge = document.createElement("span");
      badge.className = "notification-channel";
      badge.textContent = entry.channel;
      top.appendChild(label);
      top.appendChild(badge);

      const summary = document.createElement("p");
      summary.className = "notification-summary";
      summary.textContent = entry.summary || "";

      const time = document.createElement("time");
      time.className = "notification-time";
      time.textContent = formatTimestamp(
        new Date(entry.timestamp * 1000).toISOString(),
      );

      body.appendChild(top);
      if (entry.summary) body.appendChild(summary);
      body.appendChild(time);

      item.appendChild(icon);
      item.appendChild(body);
      root.appendChild(item);
    }
  } catch {
    root.textContent = "";
    root.appendChild(
      createEmptyStateCard(
        "Notification log unavailable",
        "The history feed could not be loaded right now.",
        { icon: "!", hint: "Tip: refresh in a few seconds." },
      ),
    );
  }
}

document.addEventListener("speedpulse:themechange", () => {
  syncThemeModeToggle();
  if (currentPayload) {
    renderHeroMetrics(currentPayload);
    initPremiumDepth();
    renderSlaPanel(currentPayload);
    renderCharts(currentPayload);
  }
});

initializeTheme();
bindSorting();
bindEvents();
initMagneticPrimaryCta();
initMotionReveals();
initPremiumDepth();
void loadServerSettings();
void syncRunStatus(false);
loadMetrics();
startCompletionWatcher();
renderSidebarContract();
loadNotificationLog();

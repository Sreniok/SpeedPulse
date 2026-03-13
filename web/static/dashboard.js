let speedChart;
let latencyChart;
let thresholdChart;
let latestRows = [];
let currentPayload = null;
let currentPage = 1;
let currentSort = { key: "timestamp_iso", direction: "desc" };
let manualRunInFlight = false;
let runModalTimerId = null;
let runModalStartedAt = 0;
let runModalAutoCloseId = null;
let runStatusPollId = null;
let runStatusRequestInFlight = false;
let lastHandledRunCompletion = "";
let currentServerLabel = "Auto (nearest server)";
let serverSettingsLoading = false;
let serverSettingsSaving = false;
let serverOptions = [];

const themeStorageKey = "speed-monitor-theme";
const csrfToken =
  document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
  "";

function byId(id) {
  return document.getElementById(id);
}

function isDialogElement(element) {
  return (
    typeof HTMLDialogElement !== "undefined" &&
    element instanceof HTMLDialogElement
  );
}

function modalIsOpen(id) {
  const modal = byId(id);
  if (!modal) return false;

  if (isDialogElement(modal)) {
    return modal.open;
  }

  return !modal.classList.contains("hidden");
}

function syncBodyModalState() {
  document.body.classList.toggle(
    "modal-open",
    modalIsOpen("server-modal") || modalIsOpen("run-modal"),
  );
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

function latestBaselineMetrics() {
  const latest = currentPayload?.latest_tests?.[0];
  const averages = currentPayload?.averages || {};

  return {
    download: latest?.download_mbps ?? averages.download_mbps ?? 0,
    upload: latest?.upload_mbps ?? averages.upload_mbps ?? 0,
    ping: latest?.ping_ms ?? averages.ping_ms ?? 0,
  };
}

function parseRunMetrics(status) {
  const logs = status.logs || [];
  const patterns = {
    download: /Download:\s+([\d.]+)\s*Mbps/i,
    upload: /Upload:\s+([\d.]+)\s*Mbps/i,
    ping: /Ping:\s+([\d.]+)\s*ms/i,
  };

  const metrics = { download: null, upload: null, ping: null };
  for (const [key, pattern] of Object.entries(patterns)) {
    for (let index = logs.length - 1; index >= 0; index -= 1) {
      const match = String(logs[index] || "").match(pattern);
      if (match) {
        metrics[key] = Number(match[1]);
        break;
      }
    }
  }

  return metrics;
}

function estimatedMetric(base, amplitude, elapsedMs, divisor, offset = 0) {
  const seed = (elapsedMs + offset) / divisor;
  return Math.max(
    0,
    base + Math.sin(seed) * amplitude + Math.cos(seed / 1.7) * amplitude * 0.35,
  );
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
  const baseline = latestBaselineMetrics();
  const elapsedMs = Math.max(0, Date.now() - runModalStartedAt);
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
    if (
      parsed.ping === null &&
      (stage.includes("connect") ||
        stage.includes("select") ||
        stage.includes("prepar"))
    ) {
      ping = estimatedMetric(
        baseline.ping || 12,
        Math.max(1.2, (baseline.ping || 12) * 0.12),
        elapsedMs,
        520,
        160,
      );
      pingNote = "Connecting to server";
    }

    if (parsed.download === null && stage.includes("measuring")) {
      download = estimatedMetric(
        baseline.download || 90,
        Math.max(10, (baseline.download || 90) * 0.18),
        elapsedMs,
        440,
      );
      downloadNote = "Live estimate from current stage";
    }

    if (parsed.upload === null && stage.includes("measuring")) {
      upload = estimatedMetric(
        (baseline.upload || 20) * 0.72,
        Math.max(3, (baseline.upload || 20) * 0.15),
        elapsedMs,
        620,
        260,
      );
      uploadNote = "Upload starts after download";
    }

    if (
      stage.includes("reading") ||
      stage.includes("saving") ||
      stage.includes("checking") ||
      stage.includes("rendering")
    ) {
      download = download ?? baseline.download ?? null;
      upload = upload ?? baseline.upload ?? null;
      ping = ping ?? baseline.ping ?? null;
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

      if (
        announceFinal &&
        completionKey &&
        completionKey !== lastHandledRunCompletion
      ) {
        lastHandledRunCompletion = completionKey;
        if (payload.status === "completed") {
          showMessage(
            payload.message || "Speed test completed successfully.",
            "success",
          );
          setStatus("Speed test completed");
          await loadMetrics();
        } else {
          showMessage(payload.message || "Speed test failed.", "error");
          setStatus(
            payload.stage === "Timed out"
              ? "Speed test timed out"
              : "Speed test failed",
          );
        }
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

function setStatus(text) {
  byId("status").textContent = text;
}

function showMessage(text, kind = "info") {
  const element = byId("message");
  element.textContent = text;
  element.classList.remove("hidden", "info", "success", "warning", "error");
  element.classList.add(kind);
}

function clearMessage() {
  const element = byId("message");
  element.textContent = "";
  element.classList.add("hidden");
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

function preferredTheme() {
  const stored = window.localStorage.getItem(themeStorageKey);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function applyTheme(theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  document.body.dataset.theme = theme;

  const button = byId("theme-toggle");
  if (button) {
    button.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    button.setAttribute("aria-pressed", String(theme === "light"));
  }

  if (persist) {
    window.localStorage.setItem(themeStorageKey, theme);
  }

  if (currentPayload) {
    renderCharts(currentPayload);
  }
}

function toggleTheme() {
  const nextTheme =
    document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(nextTheme);
}

function healthyLabel(row) {
  return row.healthy ? "Yes" : "Check";
}

function defaultDirectionForKey(key) {
  return key === "timestamp_iso" ? "desc" : "asc";
}

function trendSummary(current, previous, higherIsBetter = true) {
  if (
    typeof current !== "number" ||
    typeof previous !== "number" ||
    previous <= 0
  ) {
    return { label: "No baseline", tone: "tone-muted" };
  }

  const pct = ((current - previous) / previous) * 100;
  const absPct = Math.abs(pct).toFixed(1);

  if (Math.abs(pct) < 0.1) {
    return { label: "Stable", tone: "tone-muted" };
  }

  if (higherIsBetter) {
    return pct > 0
      ? { label: `${absPct}% faster`, tone: "tone-good" }
      : { label: `${absPct}% slower`, tone: "tone-bad" };
  }

  return pct < 0
    ? { label: `${absPct}% better`, tone: "tone-good" }
    : { label: `${absPct}% slower`, tone: "tone-bad" };
}

function scanSummary(todayCount, scheduledCount) {
  if (!scheduledCount) {
    return { label: `${todayCount} scans logged today`, tone: "tone-muted" };
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

function metricCard(title, valueText, noteText, toneClass = "tone-muted") {
  const card = document.createElement("article");
  card.className = "metric-card";

  const titleEl = document.createElement("h3");
  titleEl.textContent = title;

  const valueEl = document.createElement("p");
  valueEl.className = "metric-value";
  valueEl.textContent = valueText;

  const noteEl = document.createElement("p");
  noteEl.className = `metric-note ${toneClass}`;
  noteEl.textContent = noteText;

  card.appendChild(titleEl);
  card.appendChild(valueEl);
  card.appendChild(noteEl);
  return card;
}

function renderHeroMetrics(data) {
  const root = byId("hero-metrics");
  root.textContent = "";

  const rows = data.latest_tests || [];
  const latest = rows[0];
  const previous = rows[1];

  if (!latest) {
    const empty = document.createElement("article");
    empty.className = "metric-card metric-card-empty";
    empty.textContent = "No tests available in the selected range.";
    root.appendChild(empty);
    return;
  }

  const downloadTrend = trendSummary(
    latest.download_mbps,
    previous?.download_mbps,
    true,
  );
  const uploadTrend = trendSummary(
    latest.upload_mbps,
    previous?.upload_mbps,
    true,
  );
  const pingTrend = trendSummary(latest.ping_ms, previous?.ping_ms, false);
  const todayTrend = scanSummary(
    data.today_tests || 0,
    data.scheduled_tests_per_day || 0,
  );

  root.appendChild(
    metricCard(
      "Latest download",
      `${safeFixed(latest.download_mbps)} Mbps`,
      downloadTrend.label,
      downloadTrend.tone,
    ),
  );
  root.appendChild(
    metricCard(
      "Latest upload",
      `${safeFixed(latest.upload_mbps)} Mbps`,
      uploadTrend.label,
      uploadTrend.tone,
    ),
  );
  root.appendChild(
    metricCard(
      "Latest ping",
      `${safeFixed(latest.ping_ms)} ms`,
      pingTrend.label,
      pingTrend.tone,
    ),
  );
  root.appendChild(
    metricCard(
      "Today's scans",
      `${data.today_tests || 0} / ${data.scheduled_tests_per_day || 0}`,
      todayTrend.label,
      todayTrend.tone,
    ),
  );
}

function renderScheduleNote(data) {
  const testTimes = data.scheduling?.test_times || [];
  const weekly = data.scheduling?.weekly_report_time || "not set";
  const selectedServer =
    data.server_selection_label ||
    currentServerLabel ||
    "Auto (nearest server)";
  const timesHost = byId("schedule-times");

  byId("scan-plan").textContent =
    `${data.today_tests || 0} / ${data.scheduled_tests_per_day || 0} scans today`;
  byId("schedule-server").textContent = `Server: ${selectedServer}`;
  byId("schedule-weekly").textContent = `Weekly report: ${weekly}`;

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
      byId("sidebar-contract-speeds").textContent =
        `${current.download_mbps || 0} / ${current.upload_mbps || 0} Mbps (DL / UL)`;

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

function chartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: "bottom",
        labels: {
          color: cssVar("--chart-label"),
          boxWidth: 12,
          usePointStyle: true,
          pointStyle: "line",
        },
      },
    },
    scales: {
      x: {
        ticks: { color: cssVar("--chart-label") },
        grid: { color: cssVar("--chart-grid") },
      },
      y: {
        ticks: { color: cssVar("--chart-label") },
        grid: { color: cssVar("--chart-grid") },
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
      ticks: { color: cssVar("--chart-label") },
      grid: { color: cssVar("--chart-grid") },
    },
    y: {
      beginAtZero: true,
      ticks: { color: cssVar("--chart-label") },
      grid: { color: cssVar("--chart-grid") },
      title: {
        display: true,
        text: "ms",
        color: cssVar("--chart-label"),
      },
    },
    yLoss: {
      beginAtZero: true,
      position: "right",
      suggestedMax: 5,
      ticks: { color: cssVar("--chart-label") },
      grid: { drawOnChartArea: false },
      title: {
        display: true,
        text: "Loss %",
        color: cssVar("--chart-label"),
      },
    },
  };
  return options;
}

function thresholdChartOptions() {
  const options = chartOptions();
  options.plugins.legend.display = false;
  options.scales.y = {
    beginAtZero: true,
    ticks: {
      color: cssVar("--chart-label"),
      precision: 0,
    },
    grid: { color: cssVar("--chart-grid") },
    title: {
      display: true,
      text: "Breaches (count)",
      color: cssVar("--chart-label"),
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

  if (speedChart) speedChart.destroy();
  if (latencyChart) latencyChart.destroy();
  if (thresholdChart) thresholdChart.destroy();

  speedChart = new Chart(byId("speedChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Download",
          data: download,
          borderColor: cssVar("--chart-download"),
          backgroundColor: cssVar("--chart-download-fill"),
          fill: true,
          tension: 0.32,
          pointRadius: 2,
        },
        {
          label: "Upload",
          data: upload,
          borderColor: cssVar("--chart-upload"),
          backgroundColor: cssVar("--chart-upload-fill"),
          fill: true,
          tension: 0.32,
          pointRadius: 2,
        },
        {
          label: "Download (7-test avg)",
          data: downloadTrend,
          borderColor: cssVar("--chart-download"),
          borderDash: [8, 5],
          fill: false,
          tension: 0.25,
          pointRadius: 0,
        },
        {
          label: "Upload (7-test avg)",
          data: uploadTrend,
          borderColor: cssVar("--chart-upload"),
          borderDash: [8, 5],
          fill: false,
          tension: 0.25,
          pointRadius: 0,
        },
      ],
    },
    options: chartOptions(),
  });

  latencyChart = new Chart(byId("latencyChart"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Ping",
          data: ping,
          borderColor: cssVar("--chart-ping"),
          backgroundColor: cssVar("--chart-ping-fill"),
          fill: false,
          tension: 0.32,
          pointRadius: 2,
          type: "line",
          yAxisID: "y",
        },
        {
          label: "Jitter",
          data: jitter,
          borderColor: cssVar("--chart-download"),
          backgroundColor: cssVar("--chart-download-fill"),
          fill: false,
          tension: 0.32,
          pointRadius: 2,
          type: "line",
          yAxisID: "y",
        },
        {
          label: "Packet loss",
          data: loss,
          borderColor: "transparent",
          backgroundColor: cssVar("--chart-loss-fill"),
          type: "bar",
          yAxisID: "yLoss",
          borderRadius: 10,
          maxBarThickness: 22,
        },
      ],
    },
    options: latencyChartOptions(),
  });

  const breaches = data.violations || {};
  thresholdChart = new Chart(byId("thresholdChart"), {
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
            "rgba(24, 182, 255, 0.42)",
            "rgba(255, 178, 36, 0.42)",
            "rgba(255, 123, 140, 0.42)",
            "rgba(255, 212, 107, 0.42)",
          ],
          borderColor: [
            cssVar("--chart-download"),
            cssVar("--chart-upload"),
            cssVar("--chart-ping"),
            cssVar("--chart-loss"),
          ],
          borderWidth: 1.4,
          borderRadius: 12,
        },
      ],
    },
    options: thresholdChartOptions(),
  });

  renderThresholdSummary(data);
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
  const query = byId("table-search").value.trim().toLowerCase();
  if (!query) return rows;

  return rows.filter((row) => {
    return [row.server, row.timestamp, row.status, healthyLabel(row)].some(
      (value) => String(value).toLowerCase().includes(query),
    );
  });
}

function pageSize() {
  return Number(byId("page-size").value || "10");
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

function renderTable() {
  const tbody = document.querySelector("#latest-table tbody");
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
    emptyCell.textContent = "No results match the current filter.";
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
  byId("results-count").textContent =
    `Showing ${pagedRows.length} of ${rows.length} results | ${rangeLabel} | ${scheduledPerDay} scans scheduled daily`;

  byId("page-indicator").textContent = `Page ${currentPage} of ${pages}`;
  byId("page-prev").disabled = currentPage <= 1;
  byId("page-next").disabled = currentPage >= pages;
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
    header.addEventListener("click", () => {
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
    });
  }

  updateSortIndicators();
}

function metricsUrl() {
  const value = byId("range").value;
  if (value === "today") {
    return "/api/metrics?mode=today";
  }

  return `/api/metrics?mode=days&days=${encodeURIComponent(value)}`;
}

async function loadMetrics() {
  setStatus("Loading metrics...");

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

    renderScheduleNote(data);
    renderHeroMetrics(data);
    renderCharts(data);
    renderTable();

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
  }
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

function bindEvents() {
  byId("theme-toggle").addEventListener("click", toggleTheme);
  bindMobileNav();
  byId("range").addEventListener("change", loadMetrics);
  const defaultServerSelect = byId("server-select");
  if (defaultServerSelect) {
    defaultServerSelect.addEventListener("change", updateServerSettings);
  }
  byId("run-test").addEventListener("click", () => {
    if (serverOptions.length === 0 && !serverSettingsLoading) {
      void loadServerSettings();
    }
    openServerModal();
  });
  byId("server-modal-start").addEventListener("click", () => {
    void runSpeedtestNow(currentManualServerId());
  });
  byId("server-modal-cancel").addEventListener("click", closeServerModal);
  byId("server-modal-close").addEventListener("click", closeServerModal);
  byId("server-modal").addEventListener("close", syncBodyModalState);
  byId("server-modal").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeServerModal();
  });
  byId("server-modal").addEventListener("click", (event) => {
    if (!(event.target instanceof HTMLElement)) return;
    if (!event.target.closest(".dialog-card")) {
      closeServerModal();
    }
  });
  byId("table-search").addEventListener("input", () => {
    currentPage = 1;
    renderTable();
  });
  byId("page-size").addEventListener("change", () => {
    currentPage = 1;
    renderTable();
  });
  byId("page-prev").addEventListener("click", () => {
    currentPage -= 1;
    renderTable();
  });
  byId("page-next").addEventListener("click", () => {
    currentPage += 1;
    renderTable();
  });
  byId("export-results").addEventListener("click", exportResults);
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

applyTheme(preferredTheme(), false);
bindSorting();
bindEvents();
void loadServerSettings();
void syncRunStatus(false);
loadMetrics();
renderSidebarContract();

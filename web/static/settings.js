const themeStorageKey = "speed-monitor-theme";
const csrfToken =
  document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
  "";
const broadbandProviders = new Set([
  "BT",
  "Virgin Media",
  "Sky Broadband",
  "TalkTalk",
  "Vodafone",
  "Plusnet",
  "EE",
  "Community Fibre",
  "Hyperoptic",
]);
let settingsServerSelectionId = "";

function byId(id) {
  return document.getElementById(id);
}

function settingsSaveButtons() {
  return Array.from(document.querySelectorAll("[data-save-settings]"));
}

function populateSelectOptions(select, options, selectedId) {
  if (!select) return;

  select.textContent = "";
  for (const option of options) {
    const element = document.createElement("option");
    element.value = String(option.id || "");
    element.textContent = option.label;
    if (element.value === String(selectedId || "")) {
      element.selected = true;
    }
    select.appendChild(element);
  }
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
}

function toggleTheme() {
  const nextTheme =
    document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(nextTheme);
}

function toggleNotificationFieldState() {
  const webhookEnabled = byId("settings-webhook-enabled")?.checked;
  const ntfyEnabled = byId("settings-ntfy-enabled")?.checked;
  const weeklyEnabled = byId("settings-weekly-enabled")?.checked;

  byId("settings-webhook-url").disabled = !webhookEnabled;
  byId("settings-ntfy-server").disabled = !ntfyEnabled;
  byId("settings-ntfy-topic").disabled = !ntfyEnabled;
  byId("settings-weekly-day").disabled = !weeklyEnabled;
  byId("settings-weekly-time").disabled = !weeklyEnabled;
}

function syncProviderFieldState() {
  const providerValue = byId("settings-provider")?.value || "";
  const customGroup = byId("settings-provider-custom-group");
  const customInput = byId("settings-provider-custom");
  const isCustom = providerValue === "custom";

  customGroup.classList.toggle("hidden", !isCustom);
  customInput.disabled = !isCustom;
  if (!isCustom) {
    customInput.value = "";
  }
}

function populateProviderFields(provider) {
  const normalizedProvider = String(provider || "").trim();
  if (normalizedProvider && broadbandProviders.has(normalizedProvider)) {
    byId("settings-provider").value = normalizedProvider;
    byId("settings-provider-custom").value = "";
  } else if (normalizedProvider) {
    byId("settings-provider").value = "custom";
    byId("settings-provider-custom").value = normalizedProvider;
  } else {
    byId("settings-provider").value = "";
    byId("settings-provider-custom").value = "";
  }

  syncProviderFieldState();
}

function selectedBroadbandProvider() {
  const providerValue = byId("settings-provider").value;
  if (providerValue === "custom") {
    return byId("settings-provider-custom").value.trim();
  }
  return providerValue.trim();
}

function renderAccountSummary(account) {
  const name = String(account?.name || "").trim() || "N/A";
  const provider = String(account?.provider || "").trim() || "Provider not set";
  const number = String(account?.number || "").trim() || "N/A";

  byId("settings-sidebar-account-name").textContent = name;
  byId("settings-sidebar-account-provider").textContent = provider;
  byId("settings-sidebar-account-number").textContent = `Account No: ${number}`;
}

function syncDailyScanRowState() {
  const rows = Array.from(document.querySelectorAll("[data-scan-time-row]"));
  const countBadge = byId("settings-scan-count");

  rows.forEach((row, index) => {
    const label = row.querySelector("[data-scan-time-label]");
    const removeButton = row.querySelector("[data-remove-scan-time]");

    if (label) {
      label.textContent = `Scan ${index + 1}`;
    }

    if (removeButton) {
      removeButton.disabled = rows.length <= 1;
    }
  });

  if (countBadge) {
    countBadge.textContent = `${rows.length} scan${rows.length === 1 ? "" : "s"}`;
  }
}

function addDailyScanTimeRow(value = "08:00") {
  const container = byId("settings-scan-times");
  const row = document.createElement("div");
  row.className = "settings-time-row";
  row.dataset.scanTimeRow = "true";

  const label = document.createElement("span");
  label.className = "settings-time-label";
  label.dataset.scanTimeLabel = "true";

  const input = document.createElement("input");
  input.className = "settings-time-input";
  input.type = "time";
  input.step = "60";
  input.value = value;
  input.dataset.scanTime = "true";

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "btn-muted btn-small";
  removeButton.textContent = "Remove";
  removeButton.dataset.removeScanTime = "true";
  removeButton.addEventListener("click", () => {
    row.remove();
    syncDailyScanRowState();
  });

  row.append(label, input, removeButton);
  container.appendChild(row);
  syncDailyScanRowState();
}

function renderDailyScanTimes(values) {
  const container = byId("settings-scan-times");
  container.textContent = "";

  const times = Array.isArray(values) && values.length > 0 ? values : ["08:00"];
  times.forEach((value) => {
    addDailyScanTimeRow(String(value || "").trim() || "08:00");
  });
}

function collectDailyScanTimes() {
  return Array.from(document.querySelectorAll("[data-scan-time]")).map(
    (input) => input.value.trim(),
  );
}

function parseWeeklySchedule(value) {
  const rawValue = String(value || "").trim();
  const match = rawValue.match(/^([A-Za-z]+)\s+(\d{2}:\d{2})$/);
  if (!match) {
    return { day: "Monday", time: "08:00" };
  }

  return {
    day: match[1],
    time: match[2],
  };
}

function buildWeeklySchedule() {
  const day = byId("settings-weekly-day").value || "Monday";
  const time = byId("settings-weekly-time").value || "08:00";
  return `${day} ${time}`;
}

function renderSettingsHero(payload) {
  const email = payload.email || {};
  const notifications = payload.notifications || {};

  byId("settings-alert-status").textContent = email.send_realtime_alerts
    ? "Enabled"
    : "Paused";
  byId("settings-weekly-status").textContent =
    notifications.weekly_report_enabled ? "Scheduled" : "Disabled";
}

function syncScheduledServerSelect() {
  const select = byId("settings-schedule-server");
  if (!select) return;
  select.value = String(settingsServerSelectionId || "");
}

function populateSettingsForm(payload) {
  const account = payload.account || {};
  const email = payload.email || {};
  const notifications = payload.notifications || {};
  const contract = payload.contract || {};
  const currentContract = contract.current || {};
  settingsServerSelectionId = String(payload.server_selection_id || "");

  byId("settings-account-name").value = account.name || "";
  byId("settings-account-number").value = account.number || "";
  populateProviderFields(account.provider || "");

  byId("settings-contract-start").value = currentContract.start_date || "";
  byId("settings-contract-end").value = currentContract.end_date || "";
  byId("settings-contract-download").value =
    currentContract.download_mbps || "";
  byId("settings-contract-upload").value = currentContract.upload_mbps || "";
  byId("settings-contract-reminder").checked = Boolean(
    currentContract.reminder_enabled,
  );
  byId("settings-contract-reminder-days").value =
    currentContract.reminder_days || 31;
  renderContractDaysRemaining(currentContract.end_date);
  renderContractHistory(contract.history || []);

  byId("settings-account-name").value = account.name || "";
  byId("settings-account-number").value = account.number || "";
  populateProviderFields(account.provider || "");

  byId("settings-smtp-server").value = email.smtp_server || "";
  byId("settings-smtp-port").value = String(email.smtp_port || 465);
  byId("settings-smtp-username").value = email.smtp_username || "";
  byId("settings-email-from").value = email.from || "";
  byId("settings-email-to").value = email.to || "";
  byId("settings-realtime-alerts").checked = Boolean(
    email.send_realtime_alerts,
  );

  byId("settings-weekly-enabled").checked = Boolean(
    notifications.weekly_report_enabled,
  );
  const weeklySchedule = parseWeeklySchedule(
    notifications.weekly_report_time || "Monday 08:00",
  );
  byId("settings-weekly-day").value = weeklySchedule.day;
  byId("settings-weekly-time").value = weeklySchedule.time;
  renderDailyScanTimes(notifications.test_times || []);
  byId("settings-webhook-enabled").checked = Boolean(
    notifications.webhook_enabled,
  );
  byId("settings-webhook-url").value = notifications.webhook_url || "";
  byId("settings-ntfy-enabled").checked = Boolean(notifications.ntfy_enabled);
  byId("settings-ntfy-server").value =
    notifications.ntfy_server || "https://ntfy.sh";
  byId("settings-ntfy-topic").value = notifications.ntfy_topic || "";

  renderSettingsHero(payload);
  renderAccountSummary(account);
  toggleNotificationFieldState();
  syncScheduledServerSelect();
}

function collectSettingsPayload() {
  return {
    account_name: byId("settings-account-name").value.trim(),
    broadband_provider: selectedBroadbandProvider(),
    broadband_account_number: byId("settings-account-number").value.trim(),
    smtp_server: byId("settings-smtp-server").value.trim(),
    smtp_port: Number(byId("settings-smtp-port").value || "465"),
    smtp_username: byId("settings-smtp-username").value.trim(),
    smtp_password: byId("settings-smtp-password").value,
    email_from: byId("settings-email-from").value.trim(),
    email_to: byId("settings-email-to").value.trim(),
    send_realtime_alerts: byId("settings-realtime-alerts").checked,
    weekly_report_enabled: byId("settings-weekly-enabled").checked,
    weekly_report_time: buildWeeklySchedule(),
    test_times: collectDailyScanTimes(),
    server_id: byId("settings-schedule-server").value,
    webhook_enabled: byId("settings-webhook-enabled").checked,
    webhook_url: byId("settings-webhook-url").value.trim(),
    ntfy_enabled: byId("settings-ntfy-enabled").checked,
    ntfy_server: byId("settings-ntfy-server").value.trim(),
    ntfy_topic: byId("settings-ntfy-topic").value.trim(),
    contract: {
      current: {
        start_date: byId("settings-contract-start").value,
        end_date: byId("settings-contract-end").value,
        download_mbps: Number(byId("settings-contract-download").value) || 0,
        upload_mbps: Number(byId("settings-contract-upload").value) || 0,
        reminder_enabled: byId("settings-contract-reminder").checked,
        reminder_days:
          Number(byId("settings-contract-reminder-days").value) || 31,
      },
    },
  };
}

async function loadNotificationSettings() {
  try {
    const response = await fetch("/api/settings/notifications");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) {
      throw new Error("Failed to load settings");
    }
    const payload = await response.json();
    populateSettingsForm(payload);
    clearMessage();
  } catch (error) {
    showMessage("Unable to load settings.", "warning");
  }
}

async function loadScheduledServerOptions() {
  const select = byId("settings-schedule-server");

  try {
    const response = await fetch("/api/settings/server");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) {
      throw new Error("Failed to load server options");
    }

    const payload = await response.json();
    populateSelectOptions(
      select,
      payload.options || [],
      settingsServerSelectionId || payload.selected_id || "",
    );
  } catch (error) {
    populateSelectOptions(
      select,
      [{ id: "", label: "Auto (nearest server)" }],
      settingsServerSelectionId,
    );
  }
}

async function saveNotificationSettings() {
  const saveButtons = settingsSaveButtons();
  saveButtons.forEach((button) => {
    button.disabled = true;
  });

  try {
    if (
      byId("settings-provider").value === "custom" &&
      !byId("settings-provider-custom").value.trim()
    ) {
      throw new Error("Enter a custom broadband provider name.");
    }

    const response = await fetch("/api/settings/notifications", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(collectSettingsPayload()),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail || payload.message || "Failed to save settings",
      );
    }

    byId("settings-smtp-password").value = "";
    populateSettingsForm(payload);
    showMessage(payload.message || "Settings saved.", "success");
  } catch (error) {
    showMessage(error.message || "Failed to save settings.", "error");
  } finally {
    saveButtons.forEach((button) => {
      button.disabled = false;
    });
  }
}

async function updateDashboardPassword() {
  const saveButton = byId("settings-password-save");
  saveButton.disabled = true;

  try {
    const response = await fetch("/api/settings/password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        current_password: byId("settings-current-password").value,
        new_password: byId("settings-new-password").value,
        confirm_password: byId("settings-confirm-password").value,
      }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail || payload.message || "Failed to update password",
      );
    }

    byId("settings-current-password").value = "";
    byId("settings-new-password").value = "";
    byId("settings-confirm-password").value = "";
    showMessage(payload.message || "Password updated.", "success");
  } catch (error) {
    showMessage(error.message || "Failed to update password.", "error");
  } finally {
    saveButton.disabled = false;
  }
}

async function sendSettingsTestNotification(channel, buttonId) {
  const sendButton = byId(buttonId);
  if (!sendButton) return;

  sendButton.disabled = true;

  try {
    const response = await fetch("/api/settings/notifications/test", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ channel }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail || payload.message || "Failed to send test notification",
      );
    }

    showMessage(
      payload.message || `${channel} test notification sent.`,
      "success",
    );
  } catch (error) {
    showMessage(error.message || "Failed to send test notification.", "error");
  } finally {
    sendButton.disabled = false;
  }
}

function renderContractDaysRemaining(endDate) {
  const el = byId("contract-days-remaining");
  if (!el) return;
  if (!endDate) {
    el.textContent = "";
    return;
  }
  const now = new Date();
  const end = new Date(endDate + "T23:59:59");
  const diff = Math.ceil((end - now) / (1000 * 60 * 60 * 24));
  if (diff > 0) {
    el.textContent = `${diff} day${diff === 1 ? "" : "s"} remaining on current contract.`;
  } else if (diff === 0) {
    el.textContent = "Contract ends today.";
  } else {
    el.textContent = `Contract expired ${Math.abs(diff)} day${Math.abs(diff) === 1 ? "" : "s"} ago.`;
  }
}

function renderContractHistory(history) {
  const section = byId("contract-history-section");
  const list = byId("contract-history-list");
  if (!section || !list) return;

  list.textContent = "";

  if (!history || history.length === 0) {
    section.classList.add("hidden");
    return;
  }

  section.classList.remove("hidden");

  history
    .slice()
    .reverse()
    .forEach((entry, index) => {
      const card = document.createElement("div");
      card.className = "contract-history-card";

      const period = `${entry.start_date || "?"} to ${entry.end_date || "?"}`;
      const speeds = `Contracted: ${entry.download_mbps || 0} / ${entry.upload_mbps || 0} Mbps`;
      const summary = entry.summary || {};

      let summaryHtml = "";
      if (summary.total_tests > 0) {
        const dl = summary.download || {};
        const ul = summary.upload || {};
        const ping = summary.ping || {};
        summaryHtml = `
        <div class="contract-history-summary">
          <strong>${summary.total_tests} tests</strong> &middot;
          DL avg ${dl.avg || 0} Mbps (min ${dl.min || 0} / max ${dl.max || 0}) &middot;
          UL avg ${ul.avg || 0} Mbps &middot;
          Ping avg ${ping.avg || 0} ms
        </div>`;
      } else if (summary.message) {
        summaryHtml = `<div class="contract-history-message">${summary.message}</div>`;
      }

      card.innerHTML = `<div><strong>${period}</strong></div><div class="contract-history-speeds">${speeds}</div>${summaryHtml}`;
      list.appendChild(card);
    });
}

async function endCurrentContract() {
  const endButton = byId("settings-end-contract");
  if (endButton) endButton.disabled = true;

  if (
    !confirm("End the current contract and archive it? This cannot be undone.")
  ) {
    if (endButton) endButton.disabled = false;
    return;
  }

  try {
    const response = await fetch("/api/contract/end", {
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
      throw new Error(
        payload.detail || payload.message || "Failed to end contract",
      );
    }

    showMessage(payload.message || "Contract ended and archived.", "success");
    void loadNotificationSettings();
  } catch (error) {
    showMessage(error.message || "Failed to end contract.", "error");
  } finally {
    if (endButton) endButton.disabled = false;
  }
}

function bindEvents() {
  byId("theme-toggle").addEventListener("click", toggleTheme);
  settingsSaveButtons().forEach((button) => {
    button.addEventListener("click", () => {
      void saveNotificationSettings();
    });
  });
  byId("settings-password-save").addEventListener("click", () => {
    void updateDashboardPassword();
  });
  byId("settings-test-email").addEventListener("click", () => {
    void sendSettingsTestNotification("email", "settings-test-email");
  });
  byId("settings-test-webhook").addEventListener("click", () => {
    void sendSettingsTestNotification("webhook", "settings-test-webhook");
  });
  byId("settings-test-ntfy").addEventListener("click", () => {
    void sendSettingsTestNotification("ntfy", "settings-test-ntfy");
  });
  byId("settings-webhook-enabled").addEventListener(
    "change",
    toggleNotificationFieldState,
  );
  byId("settings-ntfy-enabled").addEventListener(
    "change",
    toggleNotificationFieldState,
  );
  byId("settings-weekly-enabled").addEventListener(
    "change",
    toggleNotificationFieldState,
  );
  byId("settings-provider").addEventListener("change", syncProviderFieldState);
  byId("settings-add-scan-time").addEventListener("click", () => {
    addDailyScanTimeRow();
  });
  byId("settings-end-contract").addEventListener("click", () => {
    void endCurrentContract();
  });
  byId("settings-contract-end").addEventListener("change", () => {
    renderContractDaysRemaining(byId("settings-contract-end").value);
  });
}

applyTheme(preferredTheme(), false);
bindEvents();
void loadNotificationSettings();
void loadScheduledServerOptions();

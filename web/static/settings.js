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
let savedBackupPasswordAvailable = false;
let messageTimeoutId = 0;

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

function themeDisplayName(themeApi, themeId) {
  return themeApi?.themeMap?.[themeId]?.name || "Default";
}

function renderThemeSummary(preferences) {
  const summary = byId("settings-theme-summary");
  const themeApi = window.SpeedPulseTheme;
  if (!summary || !themeApi) return;

  const activeThemeName = themeDisplayName(themeApi, preferences.activeTheme);
  const lightThemeName = themeDisplayName(themeApi, preferences.lightTheme);
  const darkThemeName = themeDisplayName(themeApi, preferences.darkTheme);

  if (preferences.mode === "system") {
    summary.textContent =
      `System mode is active and currently using ${activeThemeName}. ` +
      `Saved light palette: ${lightThemeName}. Saved dark palette: ${darkThemeName}.`;
    return;
  }

  if (preferences.mode === "light") {
    summary.textContent =
      `Light mode is active with ${lightThemeName}. ` +
      `If you switch back to System or Dark later, ${darkThemeName} is ready for dark mode.`;
    return;
  }

  summary.textContent =
    `Dark mode is active with ${darkThemeName}. ` +
    `If you switch back to System or Light later, ${lightThemeName} is ready for light mode.`;
}

function initializeTheme() {
  const themeApi = window.SpeedPulseTheme;
  const modeSelect = byId("settings-theme-mode");
  const lightSelect = byId("settings-theme-light");
  const darkSelect = byId("settings-theme-dark");
  if (!themeApi || !modeSelect || !lightSelect || !darkSelect) return;

  populateSelectOptions(
    lightSelect,
    themeApi.lightThemes.map((theme) => ({
      id: theme.id,
      label: theme.name,
    })),
    themeApi.currentPreferences().lightTheme,
  );
  populateSelectOptions(
    darkSelect,
    themeApi.darkThemes.map((theme) => ({
      id: theme.id,
      label: theme.name,
    })),
    themeApi.currentPreferences().darkTheme,
  );

  const syncControls = (preferences = themeApi.currentPreferences()) => {
    modeSelect.value = preferences.mode;
    lightSelect.value = preferences.lightTheme;
    darkSelect.value = preferences.darkTheme;
    renderThemeSummary(preferences);
  };

  syncControls(themeApi.currentPreferences());

  modeSelect.addEventListener("change", () => {
    syncControls(themeApi.setMode(modeSelect.value));
  });
  lightSelect.addEventListener("change", () => {
    syncControls(themeApi.setTheme("light", lightSelect.value));
  });
  darkSelect.addEventListener("change", () => {
    syncControls(themeApi.setTheme("dark", darkSelect.value));
  });

  document.addEventListener("speedpulse:themechange", (event) => {
    syncControls(event.detail || themeApi.currentPreferences());
  });
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

function renderSettingsHero(payload) {}

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
  const loginEmail = payload.login_email || payload.username || "";
  const notificationEmail =
    payload.notification_email || payload.user_email || "";

  // User account fields
  byId("settings-login-email").value = loginEmail;
  byId("settings-notification-email").value = notificationEmail;
  const sidebarLoginEmail = byId("settings-sidebar-login-email");
  if (sidebarLoginEmail) {
    sidebarLoginEmail.textContent = loginEmail;
  }

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

  // Backup schedule
  const backup = payload.backup || {};
  savedBackupPasswordAvailable = Boolean(backup.backup_password_set);
  byId("settings-scheduled-backup-enabled").checked = Boolean(
    backup.scheduled_backup_enabled,
  );
  byId("settings-scheduled-backup-frequency").value =
    backup.scheduled_backup_frequency || "daily";
  byId("settings-scheduled-backup-time").value =
    backup.scheduled_backup_time || "03:00";
  byId("settings-scheduled-backup-include-logs").checked =
    backup.scheduled_backup_include_logs !== false;
  const bpField = byId("settings-scheduled-backup-password");
  if (bpField) {
    bpField.value = "";
    bpField.placeholder = backup.backup_password_set
      ? "Leave blank to keep current"
      : "Set a password for scheduled backups";
  }
  const manualBackupField = byId("settings-backup-password");
  if (manualBackupField) {
    manualBackupField.value = "";
    manualBackupField.placeholder = backup.backup_password_set
      ? "Leave blank to use the scheduled backup password"
      : "Password to encrypt the backup";
  }
  const manualBackupHint = byId("settings-backup-password-hint");
  if (manualBackupHint) {
    manualBackupHint.textContent = backup.backup_password_set
      ? "Leave this blank to reuse the saved scheduled backup password, or enter a different one for this backup only."
      : "Enter a password for this backup, or save one under Scheduled backups to reuse it by default.";
  }
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
    backup: {
      scheduled_backup_enabled: byId("settings-scheduled-backup-enabled")
        .checked,
      scheduled_backup_frequency: byId("settings-scheduled-backup-frequency")
        .value,
      scheduled_backup_time:
        byId("settings-scheduled-backup-time").value || "03:00",
      scheduled_backup_include_logs: byId(
        "settings-scheduled-backup-include-logs",
      ).checked,
    },
    backup_password: byId("settings-scheduled-backup-password").value,
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
    setTimeout(() => {
      window.location.href = "/login";
    }, 1500);
  } catch (error) {
    showMessage(error.message || "Failed to update password.", "error");
  } finally {
    saveButton.disabled = false;
  }
}

async function updateDashboardLoginEmail() {
  const saveButton = byId("settings-save-login-email");
  saveButton.disabled = true;

  try {
    const response = await fetch("/api/settings/login-email", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        new_login_email: byId("settings-login-email").value.trim(),
        current_password: byId("settings-login-email-password").value,
      }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail || payload.message || "Failed to update login email",
      );
    }

    byId("settings-login-email-password").value = "";
    showMessage(payload.message || "Login email updated.", "success");
    setTimeout(() => {
      window.location.href = "/login";
    }, 1500);
  } catch (error) {
    showMessage(error.message || "Failed to update login email.", "error");
  } finally {
    saveButton.disabled = false;
  }
}

async function saveNotificationEmail() {
  const saveButton = byId("settings-save-notification-email");
  saveButton.disabled = true;

  try {
    const response = await fetch("/api/settings/notification-email", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        email: byId("settings-notification-email").value.trim(),
      }),
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
          "Failed to save notification email",
      );
    }

    showMessage(payload.message || "Notification email saved.", "success");
  } catch (error) {
    showMessage(error.message || "Failed to save notification email.", "error");
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
  bindMobileNav();
  settingsSaveButtons().forEach((button) => {
    button.addEventListener("click", () => {
      void saveNotificationSettings();
    });
  });
  byId("settings-password-save").addEventListener("click", () => {
    void updateDashboardPassword();
  });
  byId("settings-save-login-email").addEventListener("click", () => {
    void updateDashboardLoginEmail();
  });
  byId("settings-save-notification-email").addEventListener("click", () => {
    void saveNotificationEmail();
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
  byId("settings-backup-create-save").addEventListener("click", () => {
    void createBackup({ download: false });
  });
  byId("settings-backup-create-download").addEventListener("click", () => {
    void createBackup({ download: true });
  });
  byId("settings-backup-refresh").addEventListener("click", () => {
    void loadBackupList();
  });
  byId("settings-restore-preview").addEventListener("click", () => {
    void previewBackup();
  });
  byId("settings-restore-apply").addEventListener("click", () => {
    void restoreBackup();
  });
}

// ── Backup & Restore ────────────────────────────────────────────

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

async function loadBackupList() {
  const body = byId("backup-list-body");
  const table = byId("backup-list-table");
  const empty = byId("backup-list-empty");
  if (!body) return;

  try {
    const response = await fetch("/api/backup/list");
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error("Failed to load backups");
    const data = await response.json();
    const backups = data.backups || [];

    body.textContent = "";

    if (backups.length === 0) {
      table.classList.add("hidden");
      empty.classList.remove("hidden");
      return;
    }

    table.classList.remove("hidden");
    empty.classList.add("hidden");

    for (const backup of backups) {
      const row = document.createElement("tr");
      const created = new Date(backup.created_at);
      const dateStr =
        created.toLocaleDateString() +
        " " +
        created.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

      row.innerHTML =
        `<td class="backup-filename">${backup.filename}</td>` +
        `<td>${formatBytes(backup.size_bytes)}</td>` +
        `<td>${dateStr}</td>` +
        `<td class="backup-actions-cell"></td>`;

      const cell = row.querySelector(".backup-actions-cell");

      const dlBtn = document.createElement("button");
      dlBtn.type = "button";
      dlBtn.className = "btn-muted btn-small";
      dlBtn.textContent = "Download";
      dlBtn.addEventListener("click", () => {
        window.location.href = `/api/backup/download/${encodeURIComponent(backup.filename)}`;
      });
      cell.appendChild(dlBtn);

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn-ghost btn-small";
      delBtn.textContent = "Delete";
      delBtn.addEventListener("click", () => {
        void deleteBackupFile(backup.filename);
      });
      cell.appendChild(delBtn);

      body.appendChild(row);
    }
  } catch (error) {
    if (empty) {
      empty.textContent = "Unable to load backup list.";
      empty.classList.remove("hidden");
    }
    if (table) table.classList.add("hidden");
  }
}

function triggerBackupDownload(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

async function createBackup({ download = false } = {}) {
  const saveBtn = byId("settings-backup-create-save");
  const downloadBtn = byId("settings-backup-create-download");
  if (saveBtn) saveBtn.disabled = true;
  if (downloadBtn) downloadBtn.disabled = true;

  try {
    const password = byId("settings-backup-password").value;
    const includeLogs = byId("settings-backup-include-logs").checked;

    if (password && password.length < 6) {
      throw new Error("Backup password must be at least 6 characters.");
    }
    if (!password && !savedBackupPasswordAvailable) {
      throw new Error(
        "Enter a backup password, or save one first in Scheduled backups.",
      );
    }

    const response = await fetch("/api/backup/create", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ password, include_logs: includeLogs, download }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to create backup.");
    }

    if (download) {
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename=\"?([^\"]+)\"?/);
      const filename = match ? match[1] : "speedpulse-backup.speedpulse-backup";
      triggerBackupDownload(blob, filename);
      showMessage("Backup saved and downloaded.", "success");
    } else {
      const payload = await response.json();
      showMessage(
        payload.message || "Backup saved to the configured backup directory.",
        "success",
      );
    }

    byId("settings-backup-password").value = "";
    void loadBackupList();
  } catch (error) {
    showMessage(error.message || "Failed to create backup.", "error");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
    if (downloadBtn) downloadBtn.disabled = false;
  }
}

async function deleteBackupFile(filename) {
  if (!confirm(`Delete backup "${filename}"? This cannot be undone.`)) return;

  try {
    const response = await fetch(
      `/api/backup/${encodeURIComponent(filename)}`,
      {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
      },
    );

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Failed to delete backup.");
    }

    showMessage(data.message || "Backup deleted.", "success");
    void loadBackupList();
  } catch (error) {
    showMessage(error.message || "Failed to delete backup.", "error");
  }
}

async function previewBackup() {
  const btn = byId("settings-restore-preview");
  if (btn) btn.disabled = true;

  const resultEl = byId("restore-preview-result");

  try {
    const fileInput = byId("settings-restore-file");
    const password = byId("settings-restore-password").value.trim();

    if (!fileInput.files || !fileInput.files.length) {
      throw new Error("Select a backup file first.");
    }
    if (!password) {
      throw new Error("Enter the backup password.");
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("password", password);

    const response = await fetch("/api/backup/preview", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: formData,
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Failed to preview backup.");
    }

    const m = data.manifest || {};
    const files = (m.files || []).filter((f) => f !== "manifest.json");
    const logsIncluded = m.include_logs ? "Yes" : "No";
    resultEl.innerHTML =
      `<strong>Backup preview</strong><br>` +
      `Version: ${m.version || "unknown"}<br>` +
      `Created: ${m.created_at || "unknown"}<br>` +
      `Logs included: ${logsIncluded}<br>` +
      `Files: ${files.length} items`;
    resultEl.classList.remove("hidden");
    showMessage(
      "Backup is valid. Review the preview and confirm restore below.",
      "success",
    );
  } catch (error) {
    resultEl.textContent = "";
    resultEl.classList.add("hidden");
    showMessage(error.message || "Failed to preview backup.", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function restoreBackup() {
  if (
    !confirm("This will overwrite all current settings and data. Are you sure?")
  )
    return;

  const btn = byId("settings-restore-apply");
  if (btn) btn.disabled = true;

  try {
    const fileInput = byId("settings-restore-file");
    const password = byId("settings-restore-password").value.trim();
    const currentPassword = byId("settings-restore-confirm-password").value;

    if (!fileInput.files || !fileInput.files.length) {
      throw new Error("Select a backup file first.");
    }
    if (!password) {
      throw new Error("Enter the backup password.");
    }
    if (!currentPassword) {
      throw new Error("Enter your current dashboard password to confirm.");
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("password", password);
    formData.append("current_password", currentPassword);

    const response = await fetch("/api/backup/restore", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: formData,
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Failed to restore backup.");
    }

    const restored = (data.restored || []).join(", ") || "nothing";
    const warnings = data.warnings || [];
    let msg = data.message || "Backup restored successfully.";
    msg += ` Restored: ${restored}.`;
    if (warnings.length) {
      msg += ` Warnings: ${warnings.join("; ")}`;
    }

    byId("settings-restore-password").value = "";
    byId("settings-restore-confirm-password").value = "";
    byId("restore-preview-result").classList.add("hidden");

    showMessage(msg, "success");
  } catch (error) {
    showMessage(error.message || "Failed to restore backup.", "error");
  } finally {
    if (btn) btn.disabled = false;
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

  sidebar.querySelectorAll("a.nav-link, a.nav-link-section").forEach((link) => {
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

initializeTheme();
bindEvents();
void loadNotificationSettings();
void loadScheduledServerOptions();
void loadBackupList();

const csrfToken =
  document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
  "";
let settingsServerSelectionId = "";
let savedBackupPasswordAvailable = false;
let messageTimeoutId = 0;
let confirmDialogResolver = null;
let contractHistoryEntries = [];
let originalUserAccount = {
  loginEmail: "",
  notificationEmail: "",
};
let settingsClockTimerId = null;
let settingsClockState = null;
const animatedSelectSyncMap = new WeakMap();
const animatedSelectRenderMap = new WeakMap();
const MAX_SCAN_CUSTOM_DAY = 31;
let selectedScanCustomDays = [1];
const uiCore = window.SpeedPulseUiCore || null;
const CONTRACT_PROVIDER_DIRECTORY = {
  IE: [
    "Sky Ireland",
    "Vodafone Ireland",
    "Virgin Media Ireland",
    "eir",
    "SIRO",
    "Digiweb",
    "Pure Telecom",
    "Imagine",
    "Blacknight",
    "Regional Broadband",
  ],
  GB: [
    "Sky",
    "BT",
    "Virgin Media",
    "TalkTalk",
    "Vodafone UK",
    "Plusnet",
    "EE",
    "Zen Internet",
    "Hyperoptic",
    "Community Fibre",
    "Gigaclear",
    "KCOM",
  ],
};

function byId(id) {
  if (uiCore && typeof uiCore.byId === "function") {
    return uiCore.byId(id);
  }
  return document.getElementById(id);
}

function normalizeEmailAddress(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
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
  const modalIds = [
    "backup-restore-modal",
    "settings-confirm-modal",
    "contract-summary-modal",
    "contract-history-modal",
  ];
  if (uiCore && typeof uiCore.syncBodyModalState === "function") {
    uiCore.syncBodyModalState(modalIds);
    return;
  }

  document.body.classList.toggle(
    "modal-open",
    modalIds.some((id) => modalIsOpen(id)),
  );
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

  refreshAnimatedSelectOptions(select);
}

function closeAllAnimatedSelects(exceptWrapper = null) {
  document.querySelectorAll(".animated-select.is-open").forEach((wrapper) => {
    if (exceptWrapper && wrapper === exceptWrapper) return;
    wrapper.classList.remove("is-open");
    const panel = wrapper.closest(".panel-collapsible");
    if (panel instanceof HTMLElement) {
      panel.classList.remove("has-open-dropdown");
    }
    const trigger = wrapper.querySelector(".animated-select-trigger");
    if (trigger instanceof HTMLElement) {
      trigger.setAttribute("aria-expanded", "false");
      trigger.removeAttribute("aria-activedescendant");
    }
  });
}

function enhanceAnimatedSelect(select) {
  if (!(select instanceof HTMLSelectElement)) return;
  if (select.dataset.animatedSelectBound === "true") return;

  const wrapper = document.createElement("div");
  wrapper.className = "animated-select";
  select.parentNode.insertBefore(wrapper, select);
  wrapper.appendChild(select);

  select.classList.add("animated-select-native");
  select.tabIndex = -1;

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "animated-select-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute(
    "aria-label",
    select.getAttribute("aria-label") || select.name || "Select option",
  );

  const triggerLabel = document.createElement("span");
  triggerLabel.className = "animated-select-trigger-label";
  trigger.appendChild(triggerLabel);

  const triggerChevron = document.createElement("span");
  triggerChevron.className = "animated-select-trigger-chevron";
  triggerChevron.setAttribute("aria-hidden", "true");
  trigger.appendChild(triggerChevron);

  const menu = document.createElement("div");
  menu.className = "animated-select-menu";
  menu.setAttribute("role", "listbox");
  const menuId = `${select.id || `animated-select-${Date.now()}`}-menu`;
  menu.id = menuId;
  trigger.setAttribute("aria-controls", menuId);

  wrapper.appendChild(trigger);
  wrapper.appendChild(menu);

  let activeIndex = -1;

  const optionButtons = () =>
    Array.from(menu.querySelectorAll(".animated-select-option")).filter(
      (node) => !node.disabled,
    );

  const activeOptionAt = (index) => {
    const options = optionButtons();
    if (options.length === 0) return null;
    const normalized = Math.max(0, Math.min(index, options.length - 1));
    return options[normalized] || null;
  };

  const applyActiveIndex = (nextIndex) => {
    const options = optionButtons();
    if (options.length === 0) {
      activeIndex = -1;
      trigger.removeAttribute("aria-activedescendant");
      return;
    }

    activeIndex = Math.max(0, Math.min(nextIndex, options.length - 1));
    options.forEach((option, index) => {
      option.tabIndex = index === activeIndex ? 0 : -1;
    });

    const active = options[activeIndex];
    if (active?.id) {
      trigger.setAttribute("aria-activedescendant", active.id);
    } else {
      trigger.removeAttribute("aria-activedescendant");
    }
  };

  const focusSelectedOption = () => {
    const options = optionButtons();
    if (options.length === 0) return;
    const selectedIndex = options.findIndex(
      (option) => option.dataset.value === String(select.value),
    );
    applyActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
    options[activeIndex]?.focus();
  };

  const commitOptionValue = (optionButton) => {
    if (!(optionButton instanceof HTMLButtonElement) || optionButton.disabled) {
      return;
    }
    const nextValue = String(optionButton.dataset.value || "");
    if (String(select.value) === nextValue) return;
    select.value = nextValue;
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    syncFromSelect();
  };

  const closeMenu = (restoreFocus = false) => {
    closeAllAnimatedSelects();
    wrapper.classList.remove("is-menu-right");
    trigger.removeAttribute("aria-activedescendant");
    if (restoreFocus) {
      trigger.focus();
    }
  };

  const syncMenuLayout = () => {
    const viewportWidth = Math.max(
      window.innerWidth || document.documentElement.clientWidth || 0,
      320,
    );
    const triggerWidth = Math.ceil(trigger.getBoundingClientRect().width);
    const wrapperRect = wrapper.getBoundingClientRect();

    const maxToRight = Math.max(
      triggerWidth,
      Math.floor(viewportWidth - wrapperRect.left - 12),
    );

    menu.style.minWidth = `${triggerWidth}px`;
    menu.style.width = "max-content";
    menu.style.maxWidth = `${maxToRight}px`;

    const naturalWidth = Math.ceil(menu.scrollWidth + 2);
    const finalWidth = Math.min(
      Math.max(triggerWidth, naturalWidth),
      maxToRight,
    );
    menu.style.width = `${finalWidth}px`;

    wrapper.classList.remove("is-menu-right");
    if (wrapperRect.left + finalWidth > viewportWidth - 12) {
      wrapper.classList.add("is-menu-right");
    }
  };

  const openMenu = (focusMode = "selected") => {
    closeAllAnimatedSelects(wrapper);
    wrapper.classList.add("is-open");
    syncMenuLayout();
    const panel = wrapper.closest(".panel-collapsible");
    if (panel instanceof HTMLElement) {
      panel.classList.add("has-open-dropdown");
    }
    trigger.setAttribute("aria-expanded", "true");

    const options = optionButtons();
    if (options.length === 0) return;

    if (focusMode === "first") {
      applyActiveIndex(0);
      options[0]?.focus();
      return;
    }
    if (focusMode === "last") {
      applyActiveIndex(options.length - 1);
      options[options.length - 1]?.focus();
      return;
    }
    focusSelectedOption();
  };

  const syncFromSelect = () => {
    const selectedOption = select.options[select.selectedIndex];
    triggerLabel.textContent = selectedOption
      ? selectedOption.textContent || ""
      : "";
    trigger.disabled = select.disabled;

    menu.querySelectorAll(".animated-select-option").forEach((optionNode) => {
      const node = optionNode;
      const selected = node.dataset.value === String(select.value);
      node.classList.toggle("is-selected", selected);
      node.setAttribute("aria-selected", selected ? "true" : "false");
    });

    const selectedIndex = optionButtons().findIndex(
      (option) => option.dataset.value === String(select.value),
    );
    if (selectedIndex >= 0) {
      applyActiveIndex(selectedIndex);
    }
  };

  const renderOptions = () => {
    menu.textContent = "";

    Array.from(select.options).forEach((option, index) => {
      const optionButton = document.createElement("button");
      optionButton.type = "button";
      optionButton.className = "animated-select-option";
      optionButton.style.setProperty("--option-index", String(index));
      optionButton.dataset.value = option.value;
      optionButton.setAttribute("role", "option");
      optionButton.id = `${menuId}-option-${index}`;
      optionButton.setAttribute(
        "aria-selected",
        option.value === select.value ? "true" : "false",
      );
      optionButton.textContent = option.textContent || "";
      optionButton.disabled = option.disabled;
      optionButton.tabIndex = -1;

      if (option.value === select.value) {
        optionButton.classList.add("is-selected");
      }

      optionButton.addEventListener("click", () => {
        if (option.disabled) return;
        select.value = option.value;
        select.dispatchEvent(new Event("input", { bubbles: true }));
        select.dispatchEvent(new Event("change", { bubbles: true }));
        closeMenu(true);
        syncFromSelect();
      });

      optionButton.addEventListener("keydown", (event) => {
        const options = optionButtons();
        const current = options.indexOf(optionButton);
        if (event.key === "ArrowDown") {
          event.preventDefault();
          const next = current + 1 >= options.length ? 0 : current + 1;
          applyActiveIndex(next);
          const target = options[next];
          target?.focus();
          commitOptionValue(target);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          const next = current - 1 < 0 ? options.length - 1 : current - 1;
          applyActiveIndex(next);
          const target = options[next];
          target?.focus();
          commitOptionValue(target);
          return;
        }
        if (event.key === "Home") {
          event.preventDefault();
          applyActiveIndex(0);
          const target = options[0];
          target?.focus();
          commitOptionValue(target);
          return;
        }
        if (event.key === "End") {
          event.preventDefault();
          applyActiveIndex(options.length - 1);
          const target = options[options.length - 1];
          target?.focus();
          commitOptionValue(target);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          closeMenu(true);
          return;
        }
        if (event.key === "Tab") {
          closeMenu(false);
        }
      });

      menu.appendChild(optionButton);
    });

    syncFromSelect();
    syncMenuLayout();
  };

  const toggleMenu = () => {
    const isOpen = wrapper.classList.contains("is-open");
    if (isOpen) {
      closeMenu(true);
      return;
    }
    openMenu("selected");
  };

  trigger.addEventListener("click", toggleMenu);
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleMenu();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openMenu("first");
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      openMenu("last");
      return;
    }
    if (event.key === "Escape") {
      closeMenu(true);
    }
  });

  select.addEventListener("change", () => {
    syncFromSelect();
  });

  window.addEventListener(
    "resize",
    () => {
      if (wrapper.classList.contains("is-open")) {
        syncMenuLayout();
      }
    },
    { passive: true },
  );

  if (select.id) {
    document.querySelectorAll(`label[for="${select.id}"]`).forEach((label) => {
      label.addEventListener("click", (event) => {
        event.preventDefault();
        trigger.focus();
        toggleMenu();
      });
    });
  }

  select.dataset.animatedSelectBound = "true";
  animatedSelectSyncMap.set(select, syncFromSelect);
  animatedSelectRenderMap.set(select, renderOptions);
  renderOptions();
  syncFromSelect();
}

function syncAnimatedSelect(select) {
  if (!(select instanceof HTMLSelectElement)) return;
  const sync = animatedSelectSyncMap.get(select);
  if (typeof sync === "function") {
    sync();
  }
}

function refreshAnimatedSelectOptions(select) {
  if (!(select instanceof HTMLSelectElement)) return;
  const render = animatedSelectRenderMap.get(select);
  if (typeof render === "function") {
    render();
    syncAnimatedSelect(select);
  }
}

function syncAllAnimatedSelects() {
  document.querySelectorAll(".settings-page select").forEach((node) => {
    if (node instanceof HTMLSelectElement) {
      syncAnimatedSelect(node);
    }
  });
}

function initializeAnimatedSelects() {
  document.querySelectorAll(".settings-page select").forEach((node) => {
    if (node instanceof HTMLSelectElement) {
      enhanceAnimatedSelect(node);
    }
  });

  if (document.body.dataset.animatedSelectGlobalBound === "true") return;
  document.body.dataset.animatedSelectGlobalBound = "true";

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof HTMLElement)) {
      closeAllAnimatedSelects();
      return;
    }
    if (event.target.closest(".animated-select")) return;
    closeAllAnimatedSelects();
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllAnimatedSelects();
    }
  });

  document.addEventListener("settings:layoutchanged", () => {
    closeAllAnimatedSelects();
  });
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

function themeModeLabel(mode) {
  if (mode === "light") return "Light";
  if (mode === "dark") return "Dark";
  return "System";
}

function syncSettingsThemeModeToggle(preferences = null) {
  const toggle = byId("settings-theme-mode-toggle");
  if (!toggle) return;

  const themeApi = window.SpeedPulseTheme;
  const prefs =
    preferences ||
    (themeApi && typeof themeApi.currentPreferences === "function"
      ? themeApi.currentPreferences()
      : null);
  const mode = String(prefs?.mode || "system");
  const label = themeModeLabel(mode);

  toggle.dataset.mode = mode;
  toggle.setAttribute("aria-label", `Theme mode: ${label}. Click to switch`);
  toggle.setAttribute("title", `Theme mode: ${label}. Click to cycle`);
}

function cycleSettingsThemeMode() {
  const themeApi = window.SpeedPulseTheme;
  if (!themeApi || typeof themeApi.setMode !== "function") return;

  const sequence = ["system", "light", "dark"];
  const current = String(themeApi.currentPreferences?.().mode || "system");
  const index = Math.max(0, sequence.indexOf(current));
  const nextMode = sequence[(index + 1) % sequence.length];
  const updated = themeApi.setMode(nextMode);

  syncSettingsThemeModeToggle(updated);
}

function renderThemeSummary(preferences) {
  const summary = byId("settings-theme-summary");
  const themeApi = window.SpeedPulseTheme;
  if (!summary || !themeApi) return;

  const activeThemeName = themeDisplayName(themeApi, preferences.activeTheme);
  const lightThemeName = themeDisplayName(themeApi, preferences.lightTheme);
  const darkThemeName = themeDisplayName(themeApi, preferences.darkTheme);
  const resolvedMode = String(preferences.resolvedMode || preferences.mode || "system");

  if (preferences.mode === "system") {
    summary.textContent =
      `System mode is active and currently using the ${resolvedMode} palette: ${activeThemeName}. ` +
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

function buildThemeOptions(themeApi, mode, selectedThemeId) {
  const themes =
    mode === "light"
      ? themeApi.allLightThemes || themeApi.lightThemes
      : themeApi.allDarkThemes || themeApi.darkThemes;
  const options = themes.map((theme) => ({
    id: theme.id,
    label: theme.name,
  }));

  const selectedId = String(selectedThemeId || "");
  if (selectedId && !options.some((option) => option.id === selectedId)) {
    options.push({
      id: selectedId,
      label: `${themeDisplayName(themeApi, selectedId)} (Legacy)`,
    });
  }

  return options;
}

function initializeTheme() {
  const themeApi = window.SpeedPulseTheme;
  const modeSelect = byId("settings-theme-mode");
  const lightSelect = byId("settings-theme-light");
  const darkSelect = byId("settings-theme-dark");
  const quickToggle = byId("settings-theme-mode-toggle");
  if (!themeApi || !modeSelect || !lightSelect || !darkSelect) return;

  populateSelectOptions(
    lightSelect,
    buildThemeOptions(themeApi, "light", themeApi.currentPreferences().lightTheme),
    themeApi.currentPreferences().lightTheme,
  );
  populateSelectOptions(
    darkSelect,
    buildThemeOptions(themeApi, "dark", themeApi.currentPreferences().darkTheme),
    themeApi.currentPreferences().darkTheme,
  );
  initializeAnimatedSelects();

  const syncControls = (preferences = themeApi.currentPreferences()) => {
    modeSelect.value = preferences.mode;
    lightSelect.value = preferences.lightTheme;
    darkSelect.value = preferences.darkTheme;
    syncAnimatedSelect(modeSelect);
    syncAnimatedSelect(lightSelect);
    syncAnimatedSelect(darkSelect);
    renderThemeSummary(preferences);
    syncSettingsThemeModeToggle(preferences);
    syncAllAnimatedSelects();
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
  if (quickToggle) {
    quickToggle.addEventListener("click", () => {
      cycleSettingsThemeMode();
    });
  }

  document.addEventListener("speedpulse:themechange", (event) => {
    syncControls(event.detail || themeApi.currentPreferences());
  });
}

function currentThemeId() {
  const themeApi = window.SpeedPulseTheme;
  if (!themeApi || typeof themeApi.currentPreferences !== "function") {
    return "github-dark";
  }
  const prefs = themeApi.currentPreferences();
  return String(prefs.activeTheme || "github-dark");
}

function currentUiThemePreferences() {
  const fallback = {
    mode: "system",
    light: "github-light",
    dark: "github-dark",
  };
  const themeApi = window.SpeedPulseTheme;
  if (!themeApi || typeof themeApi.currentPreferences !== "function") {
    return fallback;
  }
  const prefs = themeApi.currentPreferences();
  return {
    mode: String(prefs.mode || fallback.mode),
    light: String(prefs.lightTheme || fallback.light),
    dark: String(prefs.darkTheme || fallback.dark),
  };
}

function applyThemePreferencesFromPayload(payload) {
  const themeApi = window.SpeedPulseTheme;
  const uiTheme = payload?.ui_theme || {};
  if (!themeApi || typeof themeApi.applyPreferences !== "function") {
    return;
  }

  themeApi.applyPreferences({
    mode: String(uiTheme.mode || "system"),
    lightTheme: String(uiTheme.light || "github-light"),
    darkTheme: String(uiTheme.dark || "github-dark"),
  });
}

function toggleNotificationFieldState() {
  const webhookEnabled = byId("settings-webhook-enabled")?.checked;
  const ntfyEnabled = byId("settings-ntfy-enabled")?.checked;
  const weeklyEnabled = byId("settings-weekly-enabled")?.checked;
  const monthlyEnabled = byId("settings-monthly-enabled")?.checked;

  byId("settings-webhook-url").disabled = !webhookEnabled;
  byId("settings-ntfy-server").disabled = !ntfyEnabled;
  byId("settings-ntfy-topic").disabled = !ntfyEnabled;
  byId("settings-weekly-day").disabled = !weeklyEnabled;
  byId("settings-weekly-time").disabled = !weeklyEnabled;
  byId("settings-monthly-time").disabled = !monthlyEnabled;
}

function renderAccountSummary(account) {
  const name = String(account?.name || "").trim() || "N/A";
  const provider =
    String(account?.provider || "").trim() || "Provider not detected yet";
  const ipAddress = String(account?.ip_address || "").trim() || "Not detected yet";
  const number = String(account?.number || "").trim() || "N/A";

  const accountNameNode = byId("settings-sidebar-account-name");
  if (accountNameNode) {
    accountNameNode.textContent = name;
  }
  const providerNode = byId("settings-sidebar-account-provider");
  if (providerNode) {
    providerNode.textContent = provider;
  }
  const ipNode = byId("settings-sidebar-account-ip");
  if (ipNode) {
    ipNode.textContent = `IP: ${ipAddress}`;
  }
  const numberNode = byId("settings-sidebar-account-number");
  if (numberNode) {
    const hasNumber = number !== "N/A";
    numberNode.textContent = `Account No: ${number}`;
    numberNode.classList.toggle("hidden", !hasNumber);
  }
}

function syncDailyScanRowState() {
  const rows = Array.from(document.querySelectorAll("[data-scan-time-row]"));
  const countBadge = byId("settings-scan-count");
  const scanEnabled = byId("settings-scan-enabled")?.checked !== false;

  rows.forEach((row, index) => {
    const label = row.querySelector("[data-scan-time-label]");
    const removeButton = row.querySelector("[data-remove-scan-time]");

    if (label) {
      label.textContent = `Scan ${index + 1}`;
    }

    if (removeButton) {
      removeButton.disabled = !scanEnabled || rows.length <= 1;
    }
  });

  if (countBadge) {
    countBadge.textContent = `${rows.length} scan${rows.length === 1 ? "" : "s"}`;
  }
}

function addDailyScanTimeRow(value = "08:00") {
  const container = byId("settings-scan-times");
  if (!container) return;
  const scanEnabled = byId("settings-scan-enabled")?.checked !== false;
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
  input.disabled = !scanEnabled;
  input.dataset.scanTime = "true";

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "btn-muted btn-small";
  removeButton.textContent = "Remove";
  removeButton.disabled = !scanEnabled;
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
  if (!container) return;
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

function normalizeScanCustomDays(values) {
  if (!Array.isArray(values)) {
    return [1];
  }

  const uniqueDays = new Set();
  values.forEach((value) => {
    const day = Number(value);
    if (Number.isInteger(day) && day >= 1 && day <= MAX_SCAN_CUSTOM_DAY) {
      uniqueDays.add(day);
    }
  });

  const normalized = Array.from(uniqueDays).sort((a, b) => a - b);
  return normalized.length > 0 ? normalized : [1];
}

function renderScanCustomDayPicker() {
  const container = byId("settings-scan-custom-days");
  if (!container) return;

  container.textContent = "";

  for (let day = 1; day <= MAX_SCAN_CUSTOM_DAY; day += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "settings-custom-day-chip";
    if (selectedScanCustomDays.includes(day)) {
      button.classList.add("is-selected");
      button.setAttribute("aria-pressed", "true");
    } else {
      button.setAttribute("aria-pressed", "false");
    }
    button.dataset.customScanDay = String(day);
    button.textContent = String(day);
    button.addEventListener("click", () => {
      const hasDay = selectedScanCustomDays.includes(day);
      if (hasDay && selectedScanCustomDays.length === 1) {
        return;
      }
      if (hasDay) {
        selectedScanCustomDays = selectedScanCustomDays.filter(
          (value) => value !== day,
        );
      } else {
        selectedScanCustomDays = normalizeScanCustomDays([
          ...selectedScanCustomDays,
          day,
        ]);
      }
      renderScanCustomDayPicker();
      syncScanScheduleState();
    });
    container.appendChild(button);
  }
}

function syncScanFrequencySummary() {
  const frequency = String(byId("settings-scan-frequency")?.value || "daily")
    .trim()
    .toLowerCase();
  const title = byId("settings-scan-frequency-title");
  const note = byId("settings-scan-frequency-note");
  const weeklyOptions = byId("settings-scan-weekly-options");
  const monthlyOptions = byId("settings-scan-monthly-options");
  const customOptions = byId("settings-scan-custom-options");

  const copy = {
    daily: {
      title: "Daily scans",
      note: "These run every day at the times below.",
    },
    weekly: {
      title: "Weekly scans",
      note: "These run once per selected weekday at the times below.",
    },
    monthly: {
      title: "Monthly scans",
      note: "These run on the selected day of month at the times below.",
    },
    custom: {
      title: "Custom monthly scans",
      note: "These run on each selected day-of-month at the times below.",
    },
  };
  const selected = copy[frequency] || copy.daily;

  if (title) title.textContent = selected.title;
  if (note) note.textContent = selected.note;
  if (weeklyOptions) weeklyOptions.classList.toggle("hidden", frequency !== "weekly");
  if (monthlyOptions) monthlyOptions.classList.toggle("hidden", frequency !== "monthly");
  if (customOptions) customOptions.classList.toggle("hidden", frequency !== "custom");
}

function syncScanScheduleState() {
  const scanEnabled = byId("settings-scan-enabled")?.checked !== false;
  const frequencySelect = byId("settings-scan-frequency");
  const weeklyDay = byId("settings-scan-weekly-day");
  const monthlyDay = byId("settings-scan-monthly-day");
  const customDayButtons = Array.from(
    document.querySelectorAll("[data-custom-scan-day]"),
  );
  const addButton = byId("settings-add-scan-time");
  const times = Array.from(document.querySelectorAll("[data-scan-time]"));
  const removeButtons = Array.from(document.querySelectorAll("[data-remove-scan-time]"));
  const frequency = String(frequencySelect?.value || "daily")
    .trim()
    .toLowerCase();

  if (frequencySelect) frequencySelect.disabled = !scanEnabled;
  if (weeklyDay) weeklyDay.disabled = !scanEnabled;
  if (monthlyDay) monthlyDay.disabled = !scanEnabled;
  customDayButtons.forEach((button) => {
    button.disabled = !scanEnabled || frequency !== "custom";
  });
  if (addButton) addButton.disabled = !scanEnabled;
  times.forEach((input) => {
    input.disabled = !scanEnabled;
  });
  removeButtons.forEach((button) => {
    button.disabled = !scanEnabled || removeButtons.length <= 1;
  });
  syncDailyScanRowState();
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

function stopSettingsClockTicker() {
  if (!settingsClockTimerId) return;
  window.clearInterval(settingsClockTimerId);
  settingsClockTimerId = null;
}

function formatSettingsClockDateTime(date, timezone) {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone || "UTC",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const getPart = (type) =>
      parts.find((entry) => entry.type === type)?.value || "";
    const year = getPart("year");
    const month = getPart("month");
    const day = getPart("day");
    const hour = getPart("hour");
    const minute = getPart("minute");
    const second = getPart("second");
    if (!year || !month || !day || !hour || !minute || !second) {
      return "";
    }
    return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
  } catch {
    return "";
  }
}

function formatSettingsClockTime(date, timezone) {
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

function buildSettingsAppTimeText(nowDisplay, timezone, utcOffset) {
  if (nowDisplay && timezone && utcOffset) {
    return `App time: ${nowDisplay} (${timezone}, UTC${utcOffset})`;
  }
  if (nowDisplay && timezone) {
    return `App time: ${nowDisplay} (${timezone})`;
  }
  if (nowDisplay) {
    return `App time: ${nowDisplay}`;
  }
  return "App time: --";
}

function buildSettingsClockZoneText(timezone, utcOffset) {
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

function paintSettingsClockPreview() {
  const previewNode = byId("settings-app-time-preview");
  const sidebarTimeNode = byId("settings-sidebar-clock-time");
  const sidebarZoneNode = byId("settings-sidebar-clock-zone");
  if (!previewNode && (!sidebarTimeNode || !sidebarZoneNode)) {
    stopSettingsClockTicker();
    return;
  }

  if (!settingsClockState) {
    if (previewNode) {
      previewNode.textContent = "App time: --";
    }
    if (sidebarTimeNode) {
      sidebarTimeNode.textContent = "--:--:--";
    }
    if (sidebarZoneNode) {
      sidebarZoneNode.textContent = "Timezone";
    }
    return;
  }

  const elapsedMs = Math.max(0, Date.now() - settingsClockState.anchorSystemMs);
  const currentDate = new Date(settingsClockState.anchorTimestampMs + elapsedMs);
  const nowDisplay =
    formatSettingsClockDateTime(currentDate, settingsClockState.timezone) ||
    formatSettingsClockDateTime(currentDate, "UTC") ||
    settingsClockState.fallbackDisplay ||
    "";
  const nowTime =
    formatSettingsClockTime(currentDate, settingsClockState.timezone) ||
    formatSettingsClockTime(currentDate, "UTC") ||
    "--:--:--";
  const zoneText = buildSettingsClockZoneText(
    settingsClockState.timezone,
    settingsClockState.utcOffset,
  );
  if (previewNode) {
    previewNode.textContent = buildSettingsAppTimeText(
      nowDisplay,
      settingsClockState.timezone,
      settingsClockState.utcOffset,
    );
  }
  if (sidebarTimeNode) {
    sidebarTimeNode.textContent = nowTime;
  }
  if (sidebarZoneNode) {
    sidebarZoneNode.textContent = zoneText;
  }
}

function timezonePathValue(timezoneName) {
  const normalized = String(timezoneName || "UTC").trim();
  const locationPart = normalized.split("/").filter(Boolean).pop() || "UTC";
  return locationPart
    .replace(/\s+/g, "_")
    .split("_")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("_");
}

function syncTimezoneCheckLink() {
  const linkNode = byId("settings-timezone-check-link");
  const timezoneInput = byId("settings-app-timezone");
  if (!linkNode || !timezoneInput) return;

  const timezone = String(timezoneInput.value || "").trim() || "UTC";
  linkNode.href = `https://time.is/${timezonePathValue(timezone)}`;
}

function renderTimezoneInfo(payload) {
  const app = payload?.app || {};
  const applicationTime = payload?.application_time || {};
  const timezoneInput = byId("settings-app-timezone");
  const previewNode = byId("settings-app-time-preview");
  const sidebarTimeNode = byId("settings-sidebar-clock-time");
  const sidebarZoneNode = byId("settings-sidebar-clock-zone");
  const sourceNode = byId("settings-timezone-source");

  const timezone = String(
    app.timezone || applicationTime.timezone || timezoneInput?.value || "UTC",
  ).trim() || "UTC";
  const nowIso = String(applicationTime.now_iso || "").trim();
  const utcOffset = String(applicationTime.utc_offset || "").trim();
  const fallbackDisplay = String(applicationTime.now_display || "").trim();
  const sourceRaw = String(app.timezone_source || "default").trim();
  const sourceLabel =
    sourceRaw === "settings"
      ? "Settings"
      : sourceRaw === "env"
        ? "Environment"
        : "Default (UTC)";

  if (sourceNode) {
    sourceNode.textContent = `Timezone source: ${sourceLabel}`;
  }

  syncTimezoneCheckLink();
  const parsedNowMs = Date.parse(nowIso);
  if (!previewNode && (!sidebarTimeNode || !sidebarZoneNode)) return;

  if (Number.isFinite(parsedNowMs)) {
    settingsClockState = {
      anchorTimestampMs: parsedNowMs,
      anchorSystemMs: Date.now(),
      timezone,
      utcOffset,
      fallbackDisplay,
    };
    paintSettingsClockPreview();
    stopSettingsClockTicker();
    settingsClockTimerId = window.setInterval(paintSettingsClockPreview, 1000);
    return;
  }

  stopSettingsClockTicker();
  settingsClockState = null;
  if (previewNode) {
    previewNode.textContent = buildSettingsAppTimeText(
      fallbackDisplay,
      timezone,
      utcOffset,
    );
  }
  if (sidebarTimeNode) {
    sidebarTimeNode.textContent =
      (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(fallbackDisplay)
        ? fallbackDisplay.slice(-8)
        : "") || "--:--:--";
  }
  if (sidebarZoneNode) {
    sidebarZoneNode.textContent = buildSettingsClockZoneText(
      timezone,
      utcOffset,
    );
  }
}

function syncScheduledServerSelect() {
  const select = byId("settings-schedule-server");
  if (!select) return;
  select.value = String(settingsServerSelectionId || "");
}

function populateSettingsForm(payload) {
  applyThemePreferencesFromPayload(payload);

  const account = payload.account || {};
  const app = payload.app || {};
  const email = payload.email || {};
  const notifications = payload.notifications || {};
  const thresholds = payload.thresholds || {};
  const contract = payload.contract || {};
  const currentContract = contract.current || {};
  settingsServerSelectionId = String(payload.server_selection_id || "");
  const loginEmail = payload.login_email || payload.username || "";
  const notificationEmail =
    payload.notification_email || payload.user_email || "";

  // User account fields
  byId("settings-login-email").value = loginEmail;
  byId("settings-notification-email").value = notificationEmail;
  originalUserAccount = {
    loginEmail: normalizeEmailAddress(loginEmail),
    notificationEmail: normalizeEmailAddress(notificationEmail),
  };
  const sidebarLoginEmail = byId("settings-sidebar-login-email");
  if (sidebarLoginEmail) {
    sidebarLoginEmail.textContent = loginEmail;
  }

  byId("settings-contract-start").value = currentContract.start_date || "";
  byId("settings-contract-end").value = currentContract.end_date || "";
  const detectedProvider = account.provider || "";
  const providerCountry = normalizeProviderCountry(
    currentContract.provider_country || "auto",
  );
  byId("settings-contract-provider-country").value = providerCountry;
  byId("settings-contract-provider-select").dataset.currentProvider =
    currentContract.provider || detectedProvider || "";
  populateContractProviderOptions({
    countryCode:
      providerCountry === "auto"
        ? inferProviderCountry({
            timezone: app.timezone || "UTC",
            provider: detectedProvider,
          })
        : providerCountry,
    detectedProvider,
    selectedProvider: currentContract.provider || detectedProvider || "",
  });
  byId("settings-contract-download").value =
    currentContract.download_mbps || "";
  byId("settings-contract-upload").value = currentContract.upload_mbps || "";
  byId("settings-threshold-download").value =
    thresholds.download_mbps ?? "";
  byId("settings-threshold-upload").value =
    thresholds.upload_mbps ?? "";
  byId("settings-contract-reminder").checked = Boolean(
    currentContract.reminder_enabled,
  );
  byId("settings-contract-reminder-days").value =
    currentContract.reminder_days || 31;
  renderContractDaysRemaining(currentContract.end_date);
  renderContractHistory(contract.history || []);

  byId("settings-account-name").value = account.name || "";
  byId("settings-account-number").value = account.number || "";
  byId("settings-provider-detected").value = account.provider || "";
  byId("settings-ip-detected").value = account.ip_address || "";

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
  byId("settings-monthly-enabled").checked = Boolean(
    notifications.monthly_report_enabled,
  );
  byId("settings-monthly-time").value =
    notifications.monthly_report_time || "08:00";
  byId("settings-app-timezone").value = app.timezone || "UTC";
  renderTimezoneInfo(payload);
  renderDailyScanTimes(notifications.test_times || []);
  byId("settings-scan-enabled").checked =
    notifications.scan_enabled !== false;
  byId("settings-scan-frequency").value =
    String(notifications.scan_frequency || "daily");
  byId("settings-scan-weekly-day").value =
    String(notifications.scan_weekly_day || "Monday");
  byId("settings-scan-monthly-day").value =
    String(Number(notifications.scan_monthly_day || 1));
  selectedScanCustomDays = normalizeScanCustomDays(
    notifications.scan_custom_days || [],
  );
  renderScanCustomDayPicker();
  syncScanFrequencySummary();
  syncScanScheduleState();
  byId("settings-webhook-enabled").checked = Boolean(
    notifications.webhook_enabled,
  );
  byId("settings-webhook-url").value = notifications.webhook_url || "";
  byId("settings-ntfy-enabled").checked = Boolean(notifications.ntfy_enabled);
  byId("settings-ntfy-server").value =
    notifications.ntfy_server || "https://ntfy.sh";
  byId("settings-ntfy-topic").value = notifications.ntfy_topic || "";
  const pushEvents = notifications.push_events || {};
  byId("settings-push-event-alert").checked = pushEvents.alert !== false;
  byId("settings-push-event-weekly").checked =
    pushEvents.weekly_report !== false;
  byId("settings-push-event-monthly").checked =
    pushEvents.monthly_report !== false;
  byId("settings-push-event-health").checked =
    pushEvents.health_check !== false;

  renderSettingsHero(payload);
  renderAccountSummary(account);
  toggleNotificationFieldState();
  syncScheduledServerSelect();
  syncAllAnimatedSelects();

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
  byId("settings-scheduled-backup-max").value = String(
    backup.max_backups || 10,
  );
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

function collectAppearancePayload() {
  const uiTheme = currentUiThemePreferences();
  return {
    ui_theme: uiTheme,
    ui_theme_mode: uiTheme.mode,
    ui_theme_light: uiTheme.light,
    ui_theme_dark: uiTheme.dark,
    report_theme_id: currentThemeId(),
  };
}

function collectSettingsPayload() {
  const uiTheme = currentUiThemePreferences();
  return {
    account_name: byId("settings-account-name").value.trim(),
    broadband_provider: byId("settings-provider-detected").value.trim(),
    broadband_account_number: byId("settings-account-number").value.trim(),
    smtp_server: byId("settings-smtp-server").value.trim(),
    smtp_port: Number(byId("settings-smtp-port").value || "465"),
    smtp_username: byId("settings-smtp-username").value.trim(),
    smtp_password: byId("settings-smtp-password").value,
    email_from: byId("settings-email-from").value.trim(),
    app_timezone: byId("settings-app-timezone").value.trim(),
    send_realtime_alerts: byId("settings-realtime-alerts").checked,
    weekly_report_enabled: byId("settings-weekly-enabled").checked,
    weekly_report_time: buildWeeklySchedule(),
    monthly_report_enabled: byId("settings-monthly-enabled").checked,
    monthly_report_time: byId("settings-monthly-time").value || "08:00",
    scan_enabled: byId("settings-scan-enabled").checked,
    scan_frequency:
      String(byId("settings-scan-frequency").value || "daily").trim().toLowerCase(),
    scan_weekly_day: String(byId("settings-scan-weekly-day").value || "Monday"),
    scan_monthly_day: Number(byId("settings-scan-monthly-day").value || "1"),
    scan_custom_days: normalizeScanCustomDays(selectedScanCustomDays),
    test_times: collectDailyScanTimes(),
    server_id: byId("settings-schedule-server").value,
    push_events: {
      alert: byId("settings-push-event-alert").checked,
      weekly_report: byId("settings-push-event-weekly").checked,
      monthly_report: byId("settings-push-event-monthly").checked,
      health_check: byId("settings-push-event-health").checked,
    },
    ui_theme: uiTheme,
    ui_theme_mode: uiTheme.mode,
    ui_theme_light: uiTheme.light,
    ui_theme_dark: uiTheme.dark,
    report_theme_id: currentThemeId(),
    webhook_enabled: byId("settings-webhook-enabled").checked,
    webhook_url: byId("settings-webhook-url").value.trim(),
    ntfy_enabled: byId("settings-ntfy-enabled").checked,
    ntfy_server: byId("settings-ntfy-server").value.trim(),
    ntfy_topic: byId("settings-ntfy-topic").value.trim(),
    thresholds: {
      download_mbps: Number(byId("settings-threshold-download").value) || 0,
      upload_mbps: Number(byId("settings-threshold-upload").value) || 0,
    },
    contract: {
      current: {
        start_date: byId("settings-contract-start").value,
        end_date: byId("settings-contract-end").value,
        provider: selectedContractProviderValue(),
        provider_country: normalizeProviderCountry(
          byId("settings-contract-provider-country").value,
        ),
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
      max_backups: Number(byId("settings-scheduled-backup-max").value) || 10,
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

async function saveAppearanceSettings() {
  const saveButton = byId("settings-save-appearance");
  if (!saveButton) return;
  saveButton.disabled = true;

  try {
    const response = await fetch("/api/settings/appearance", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(collectAppearancePayload()),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail || payload.message || "Failed to save appearance",
      );
    }

    populateSettingsForm(payload);
    showMessage(payload.message || "Appearance saved.", "success");
  } catch (error) {
    showMessage(error.message || "Failed to save appearance.", "error");
  } finally {
    saveButton.disabled = false;
  }
}

function clearUserAccountPasswordFields() {
  byId("settings-current-password").value = "";
  byId("settings-new-password").value = "";
  byId("settings-confirm-password").value = "";
}

async function saveUserAccountSettings() {
  const saveButton = byId("settings-save-user-account");
  if (!saveButton) return;

  const loginEmail = byId("settings-login-email").value.trim();
  const notificationEmail = byId("settings-notification-email").value.trim();
  const currentPassword = byId("settings-current-password").value;
  const newPassword = byId("settings-new-password").value;
  const confirmPassword = byId("settings-confirm-password").value;

  const loginChanged =
    normalizeEmailAddress(loginEmail) !== originalUserAccount.loginEmail;
  const notificationChanged =
    normalizeEmailAddress(notificationEmail) !==
    originalUserAccount.notificationEmail;
  const passwordRequested = Boolean(
    currentPassword || newPassword || confirmPassword,
  );

  if (!loginChanged && !notificationChanged && !passwordRequested) {
    showMessage("No account changes to save.", "info");
    return;
  }

  saveButton.disabled = true;

  try {
    const response = await fetch("/api/settings/user-account", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        login_email: loginEmail,
        notification_email: notificationEmail,
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      }),
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail || payload.message || "Failed to save account settings",
      );
    }

    const savedLoginEmail = String(payload.login_email || loginEmail).trim();
    const savedNotificationEmail = String(
      payload.notification_email || notificationEmail,
    ).trim();

    byId("settings-login-email").value = savedLoginEmail;
    byId("settings-notification-email").value = savedNotificationEmail;
    originalUserAccount = {
      loginEmail: normalizeEmailAddress(savedLoginEmail),
      notificationEmail: normalizeEmailAddress(savedNotificationEmail),
    };

    const sidebarLoginEmail = byId("settings-sidebar-login-email");
    if (sidebarLoginEmail) {
      sidebarLoginEmail.textContent = savedLoginEmail;
    }

    clearUserAccountPasswordFields();

    const successMessage = payload.message || "Account settings saved.";
    showMessage(successMessage, "success");

    if (payload.reauth_required) {
      window.setTimeout(() => {
        window.location.href = "/login";
      }, 1500);
    }
  } catch (error) {
    showMessage(error.message || "Failed to save account settings.", "error");
  } finally {
    saveButton.disabled = false;
  }
}

async function updateDashboardPassword() {
  const saveButton = byId("settings-password-save");
  if (!saveButton) return;
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
  if (!saveButton) return;
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
  if (!saveButton) return;
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

function normalizeProviderCountry(value) {
  const normalized = String(value || "auto").trim().toUpperCase();
  if (normalized === "IE" || normalized === "GB") return normalized;
  return "auto";
}

function inferProviderCountry({ timezone = "", provider = "" } = {}) {
  const tz = String(timezone || "").trim();
  const isp = String(provider || "").trim().toLowerCase();
  if (tz === "Europe/Dublin" || /\b(ireland|eir|siro|digiweb|pure telecom|imagine)\b/.test(isp)) {
    return "IE";
  }
  if (
    tz === "Europe/London" ||
    /\b(uk|britain|england|scotland|wales|bt|talktalk|plusnet|hyperoptic|gigaclear|kcom)\b/.test(isp)
  ) {
    return "GB";
  }
  return "IE";
}

function providerOptionsForCountry(countryCode) {
  return CONTRACT_PROVIDER_DIRECTORY[countryCode] || CONTRACT_PROVIDER_DIRECTORY.IE;
}

function populateContractProviderOptions({ countryCode, detectedProvider = "", selectedProvider = "" }) {
  const select = byId("settings-contract-provider-select");
  const customInput = byId("settings-contract-provider-custom");
  const customGroup = byId("settings-contract-provider-custom-group");
  if (!select || !customInput || !customGroup) return;

  const options = providerOptionsForCountry(countryCode);
  const desired = String(selectedProvider || detectedProvider || "").trim();
  const matchedOption = options.find(
    (option) => option.toLowerCase() === desired.toLowerCase(),
  );

  select.textContent = "";
  const autoOption = document.createElement("option");
  autoOption.value = "";
  autoOption.textContent = detectedProvider
    ? `Auto: ${detectedProvider}`
    : "Auto: detected provider";
  select.appendChild(autoOption);

  options.forEach((option) => {
    const element = document.createElement("option");
    element.value = option;
    element.textContent = option;
    select.appendChild(element);
  });

  const customOption = document.createElement("option");
  customOption.value = "__custom__";
  customOption.textContent = "Custom provider";
  select.appendChild(customOption);

  if (!desired) {
    select.value = "";
    customInput.value = "";
    customGroup.classList.add("hidden");
  } else if (matchedOption) {
    select.value = matchedOption;
    customInput.value = "";
    customGroup.classList.add("hidden");
  } else if (desired.toLowerCase() === String(detectedProvider || "").trim().toLowerCase()) {
    select.value = "";
    customInput.value = "";
    customGroup.classList.add("hidden");
  } else {
    select.value = "__custom__";
    customInput.value = desired;
    customGroup.classList.remove("hidden");
  }
}

function selectedContractProviderValue() {
  const select = byId("settings-contract-provider-select");
  const customInput = byId("settings-contract-provider-custom");
  const detectedInput = byId("settings-provider-detected");
  if (!select || !customInput || !detectedInput) return "";

  if (select.value === "__custom__") {
    return customInput.value.trim();
  }
  if (!select.value) {
    return detectedInput.value.trim();
  }
  return select.value.trim();
}

function syncContractProviderPicker() {
  const countrySelect = byId("settings-contract-provider-country");
  const detectedInput = byId("settings-provider-detected");
  const contractProviderSelect = byId("settings-contract-provider-select");
  const customGroup = byId("settings-contract-provider-custom-group");
  if (!countrySelect || !detectedInput || !contractProviderSelect || !customGroup) return;

  const resolvedCountry =
    normalizeProviderCountry(countrySelect.value) === "auto"
      ? inferProviderCountry({
          timezone: byId("settings-app-timezone")?.value || "",
          provider: detectedInput.value,
        })
      : normalizeProviderCountry(countrySelect.value);

  populateContractProviderOptions({
    countryCode: resolvedCountry,
    detectedProvider: detectedInput.value.trim(),
    selectedProvider:
      selectedContractProviderValue() ||
      contractProviderSelect.dataset.currentProvider ||
      detectedInput.value.trim(),
  });

  customGroup.classList.toggle(
    "hidden",
    contractProviderSelect.value !== "__custom__",
  );
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatMetricNumber(value, digits = 2) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : (0).toFixed(digits);
}

function contractPeriodLabel(entry) {
  return `${entry?.start_date || "?"} to ${entry?.end_date || "?"}`;
}

function contractContractedLabel(entry) {
  return `${entry?.download_mbps || 0} / ${entry?.upload_mbps || 0} Mbps`;
}

function contractSummaryHeadline(entry) {
  const summary = entry?.summary || {};
  if ((summary.total_tests || 0) <= 0) {
    return summary.message || "No speed test data found for this contract period.";
  }

  const download = summary.download || {};
  const upload = summary.upload || {};
  const ping = summary.ping || {};

  return `${summary.total_tests} tests · DL avg ${formatMetricNumber(download.avg)} Mbps (min ${formatMetricNumber(download.min)} / max ${formatMetricNumber(download.max)}) · UL avg ${formatMetricNumber(upload.avg)} Mbps · Ping avg ${formatMetricNumber(ping.avg)} ms`;
}

function buildContractHistoryCardHtml(entry, { compact = false } = {}) {
  const provider = escapeHtml(entry?.provider || "Unknown provider");
  const accountName = escapeHtml(entry?.account_name || "");
  const period = escapeHtml(contractPeriodLabel(entry));
  const contracted = escapeHtml(contractContractedLabel(entry));
  const summary = entry?.summary || {};
  const sources = summary.sources || {};
  const breaches = summary.breaches || {};
  const tests = Number(summary.total_tests || 0);
  const summaryHeadline = escapeHtml(contractSummaryHeadline(entry));
  const latestTestAt = escapeHtml(summary.latest_test_at || "");
  const metaLine = tests
    ? `${Number(sources.scheduled || 0)} scheduled · ${Number(sources.manual || 0)} manual · ${Number(breaches.total || 0)} breaches`
    : "Archived contract";
  const compactClass = compact ? " contract-mini-card-compact" : "";

  return `
    <article class="contract-mini-card${compactClass}">
      <div class="contract-mini-card-head">
        <div>
          <p class="eyebrow">Archived contract</p>
          <h4>${provider}</h4>
        </div>
        <span class="contract-mini-card-chip">${period}</span>
      </div>
      <p class="contract-mini-card-account">${accountName || "&nbsp;"}</p>
      <p class="contract-mini-card-contract">Contracted ${contracted}</p>
      <p class="contract-mini-card-summary">${summaryHeadline}</p>
      <div class="contract-mini-card-foot">
        <span>${escapeHtml(metaLine)}</span>
        <span>${latestTestAt ? `Last test ${latestTestAt}` : "Summary ready"}</span>
      </div>
      <div class="contract-mini-card-actions">
        <button class="btn-muted btn-small contract-history-view" type="button">
          View summary
        </button>
      </div>
    </article>
  `;
}

function setContractHistoryButtonsState(count) {
  const buttons = [
    byId("settings-contract-history"),
    byId("settings-contract-history-inline"),
  ].filter(Boolean);

  buttons.forEach((button) => {
    button.disabled = count === 0;
  });

  const primary = byId("settings-contract-history");
  if (primary) {
    primary.textContent = count > 0 ? `History (${count})` : "History";
  }
}

function renderContractHistory(history) {
  const section = byId("contract-history-section");
  const list = byId("contract-history-list");
  if (!section || !list) return;

  list.textContent = "";
  contractHistoryEntries = Array.isArray(history) ? history.slice() : [];
  setContractHistoryButtonsState(contractHistoryEntries.length);

  if (!contractHistoryEntries.length) {
    section.classList.add("hidden");
    return;
  }

  section.classList.remove("hidden");

  contractHistoryEntries
    .slice()
    .reverse()
    .slice(0, 4)
    .forEach((entry) => {
      const card = document.createElement("div");
      card.className = "contract-history-card";
      card.innerHTML = buildContractHistoryCardHtml(entry, { compact: true });
      card
        .querySelector(".contract-history-view")
        ?.addEventListener("click", () => {
          openContractSummaryModal(entry);
        });
      list.appendChild(card);
    });
}

function openSettingsDialog(id) {
  const modal = byId(id);
  if (!modal) return;

  if (isDialogElement(modal) && typeof modal.showModal === "function") {
    if (!modal.open) {
      modal.showModal();
    }
  } else {
    modal.classList.remove("hidden");
  }

  syncBodyModalState();
}

function closeSettingsDialog(id) {
  const modal = byId(id);
  if (!modal) return;

  if (isDialogElement(modal) && modal.open) {
    modal.close();
  } else {
    modal.classList.add("hidden");
  }

  syncBodyModalState();
}

function renderContractSummaryModal(entry, { email = null } = {}) {
  const summary = entry?.summary || {};
  const body = byId("contract-summary-modal-body");
  const eyebrow = byId("contract-summary-modal-eyebrow");
  const title = byId("contract-summary-modal-title");
  const copy = byId("contract-summary-modal-copy");
  const emailStatus = byId("contract-summary-email-status");
  if (!body || !eyebrow || !title || !copy || !emailStatus) return;

  const provider = entry?.provider || "Unknown provider";
  const period = contractPeriodLabel(entry);
  const contracted = contractContractedLabel(entry);
  const accountName = entry?.account_name || "Unknown account";
  const accountNumber = entry?.account_number || "N/A";
  const ipAddress = entry?.ip_address || "Not detected";
  const tests = Number(summary.total_tests || 0);
  const sources = summary.sources || {};
  const breaches = summary.breaches || {};
  const download = summary.download || {};
  const upload = summary.upload || {};
  const ping = summary.ping || {};
  const jitter = summary.jitter || {};
  const packetLoss = summary.packet_loss || {};

  eyebrow.textContent = "Contract summary";
  title.textContent = provider;
  copy.textContent = `${period} · Contracted ${contracted}`;

  if (email?.message) {
    emailStatus.textContent = email.message;
    emailStatus.classList.remove("hidden", "success-banner", "error-banner");
    emailStatus.classList.add(email.sent ? "success-banner" : "error-banner");
  } else {
    emailStatus.textContent = "";
    emailStatus.classList.add("hidden");
    emailStatus.classList.remove("success-banner", "error-banner");
  }

  const emptyState =
    tests === 0
      ? `<div class="contract-summary-empty">${escapeHtml(summary.message || "No speed test data found for this contract period.")}</div>`
      : "";

  body.innerHTML = `
    <section class="contract-summary-shell">
      <div class="contract-summary-meta">
        <div class="contract-summary-meta-item">
          <span>Account</span>
          <strong>${escapeHtml(accountName)}</strong>
        </div>
        <div class="contract-summary-meta-item">
          <span>Account no.</span>
          <strong>${escapeHtml(accountNumber)}</strong>
        </div>
        <div class="contract-summary-meta-item">
          <span>IP</span>
          <strong>${escapeHtml(ipAddress)}</strong>
        </div>
        <div class="contract-summary-meta-item">
          <span>Tests</span>
          <strong>${tests}</strong>
        </div>
      </div>
      <div class="contract-summary-metrics">
        <article class="contract-summary-metric">
          <p class="eyebrow">Latency</p>
          <div class="contract-summary-metric-value">${formatMetricNumber(ping.avg)} ms</div>
          <p class="contract-summary-metric-note">Min ${formatMetricNumber(ping.min)} · Max ${formatMetricNumber(ping.max)}</p>
        </article>
        <article class="contract-summary-metric">
          <p class="eyebrow">Download</p>
          <div class="contract-summary-metric-value">${formatMetricNumber(download.avg)} Mbps</div>
          <p class="contract-summary-metric-note">Min ${formatMetricNumber(download.min)} · Max ${formatMetricNumber(download.max)}</p>
        </article>
        <article class="contract-summary-metric">
          <p class="eyebrow">Upload</p>
          <div class="contract-summary-metric-value">${formatMetricNumber(upload.avg)} Mbps</div>
          <p class="contract-summary-metric-note">Min ${formatMetricNumber(upload.min)} · Max ${formatMetricNumber(upload.max)}</p>
        </article>
      </div>
      ${emptyState}
      <div class="contract-summary-details-grid">
        <section class="contract-summary-panel">
          <p class="eyebrow">Breakdown</p>
          <div class="contract-summary-detail-row"><span>Packet loss average</span><strong>${formatMetricNumber(packetLoss.avg)}%</strong></div>
          <div class="contract-summary-detail-row"><span>Jitter average</span><strong>${formatMetricNumber(jitter.avg)} ms</strong></div>
          <div class="contract-summary-detail-row"><span>Scheduled scans</span><strong>${Number(sources.scheduled || 0)}</strong></div>
          <div class="contract-summary-detail-row"><span>Manual scans</span><strong>${Number(sources.manual || 0)}</strong></div>
        </section>
        <section class="contract-summary-panel">
          <p class="eyebrow">Reliability</p>
          <div class="contract-summary-detail-row"><span>Total breaches</span><strong>${Number(breaches.total || 0)}</strong></div>
          <div class="contract-summary-detail-row"><span>Download / Upload</span><strong>${Number(breaches.download || 0)} / ${Number(breaches.upload || 0)}</strong></div>
          <div class="contract-summary-detail-row"><span>Ping / Loss</span><strong>${Number(breaches.ping || 0)} / ${Number(breaches.loss || 0)}</strong></div>
          <div class="contract-summary-detail-row"><span>Latest test</span><strong>${escapeHtml(summary.latest_test_at || "N/A")}</strong></div>
        </section>
      </div>
    </section>
  `;
}

function openContractSummaryModal(entry, options = {}) {
  renderContractSummaryModal(entry, options);
  openSettingsDialog("contract-summary-modal");
  byId("contract-summary-modal-ok")?.focus();
}

function renderContractHistoryModal() {
  const list = byId("contract-history-modal-list");
  if (!list) return;

  if (!contractHistoryEntries.length) {
    list.innerHTML = '<div class="contract-history-modal-empty">No archived contracts yet.</div>';
    return;
  }

  list.textContent = "";
  contractHistoryEntries
    .slice()
    .reverse()
    .forEach((entry) => {
      const wrapper = document.createElement("div");
      wrapper.className = "contract-history-modal-item";
      wrapper.innerHTML = buildContractHistoryCardHtml(entry, { compact: false });
      wrapper
        .querySelector(".contract-history-view")
        ?.addEventListener("click", () => {
          closeSettingsDialog("contract-history-modal");
          openContractSummaryModal(entry);
        });
      list.appendChild(wrapper);
    });
}

function openContractHistoryModal() {
  renderContractHistoryModal();
  openSettingsDialog("contract-history-modal");
  byId("contract-history-modal-dismiss")?.focus();
}

async function endCurrentContract() {
  const endButton = byId("settings-end-contract");
  if (endButton) endButton.disabled = true;

  const confirmed = await openConfirmDialog({
    eyebrow: "Contract",
    title: "End current contract?",
    copy: "This will archive the current contract and cannot be undone.",
    confirmLabel: "End contract",
  });
  if (!confirmed) {
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

    if (payload.email?.sent === false) {
      showMessage(
        `${payload.message || "Contract ended and archived."} Email report could not be sent.`,
        "warning",
      );
    } else {
      showMessage(payload.message || "Contract ended and archived.", "success");
    }

    await loadNotificationSettings();
    if (payload.archived) {
      openContractSummaryModal(payload.archived, { email: payload.email || null });
    }
  } catch (error) {
    showMessage(error.message || "Failed to end contract.", "error");
  } finally {
    if (endButton) endButton.disabled = false;
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
    ".settings-page .topbar",
    ".settings-page .section-jumpbar-wrap",
    ".settings-page .settings-grid > .panel",
    ".settings-page .sidebar .brand-lockup",
    ".settings-page .sidebar .sidebar-card",
    ".settings-page .sidebar .nav-block",
    ".settings-page .sidebar .nav-block-sections",
    ".settings-page .sidebar .sidebar-footer",
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

function notifySettingsLayoutChanged() {
  window.dispatchEvent(new Event("settings:layoutchanged"));
}

function setPanelCollapsedState(toggle, body, collapsed, options = {}) {
  const instant = Boolean(options.instant) || prefersReducedMotion();
  const silent = Boolean(options.silent);
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
    if (!silent) notifySettingsLayoutChanged();
    return;
  }

  if (collapsed) {
    const startHeight = body.scrollHeight;
    if (startHeight <= 0) {
      body.hidden = true;
      if (!silent) notifySettingsLayoutChanged();
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
      if (!silent) notifySettingsLayoutChanged();
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
    if (!silent) notifySettingsLayoutChanged();
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
    if (!silent) notifySettingsLayoutChanged();
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
        silent: true,
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

function setActiveSettingsSectionNav(sectionId) {
  document
    .querySelectorAll("[data-settings-section-link][href^='#']")
    .forEach((link) => {
      const href = String(link.getAttribute("href") || "");
      const active = href === `#${sectionId}`;
      link.classList.toggle("active", active);
      if (active) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    });
}

function bindSettingsSectionNav() {
  const mainShell = document.querySelector(".main-shell");
  if (!(mainShell instanceof HTMLElement)) return;
  const jumpbar = byId("settings-jumpbar");

  const rawLinks = Array.from(
    document.querySelectorAll("[data-settings-section-link][href^='#']"),
  );
  if (rawLinks.length === 0) return;

  const expandSectionIfCollapsed = (section) => {
    const toggle = section.querySelector(
      ".panel-collapse-toggle[data-collapse-target]",
    );
    if (!(toggle instanceof HTMLElement)) return;
    const targetId = String(toggle.dataset.collapseTarget || "");
    const body = byId(targetId);
    if (!(body instanceof HTMLElement)) return;
    const isExpanded = toggle.getAttribute("aria-expanded") === "true";
    if (isExpanded) return;
    setPanelCollapsedState(toggle, body, false, { instant: true });
  };

  const sections = new Map();
  rawLinks.forEach((link) => {
    const href = String(link.getAttribute("href") || "");
    const sectionId = href.slice(1);
    const section = byId(sectionId);
    if (!sectionId || !section) return;

    if (!sections.has(sectionId)) {
      sections.set(sectionId, { sectionId, section });
    }

    link.addEventListener("click", (event) => {
      event.preventDefault();
      expandSectionIfCollapsed(section);
      setActiveSettingsSectionNav(sectionId);
      const targetTop = Math.max(0, section.offsetTop - scrollAnchorOffset());
      mainShell.scrollTo({ top: targetTop, behavior: "smooth" });
      if (window.history && typeof window.history.replaceState === "function") {
        window.history.replaceState(null, "", `#${sectionId}`);
      }
      window.setTimeout(() => recalc(true), 300);
    });
  });

  const sectionEntries = Array.from(sections.values());
  if (sectionEntries.length === 0) return;

  const scrollAnchorOffset = () =>
    (jumpbar instanceof HTMLElement ? jumpbar.offsetHeight + 12 : 0) + 18;
  const probeOffset = () => Math.min(mainShell.clientHeight * 0.32, 220);
  const switchHysteresis = () =>
    Math.max(66, Math.min(mainShell.clientHeight * 0.14, 124));
  const topSnapThreshold = () =>
    Math.max(12, Math.min(scrollAnchorOffset() + 6, 72));
  const sectionTop = (entry) =>
    Math.max(0, entry.section.offsetTop - scrollAnchorOffset());
  const sectionById = new Map(
    sectionEntries.map((entry) => [entry.sectionId, entry]),
  );

  let activeSectionId = sectionEntries[0].sectionId;
  let lastProbeY = 0;

  const candidateForProbe = (probeY) => {
    if (mainShell.scrollTop <= topSnapThreshold()) {
      return sectionEntries[0].sectionId;
    }

    if (mainShell.scrollTop + mainShell.clientHeight >= mainShell.scrollHeight - 6) {
      return sectionEntries[sectionEntries.length - 1].sectionId;
    }

    let candidate = sectionEntries[0].sectionId;
    for (const entry of sectionEntries) {
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
      const topSectionId = sectionEntries[0].sectionId;
      if (activeSectionId !== topSectionId || force) {
        activeSectionId = topSectionId;
        setActiveSettingsSectionNav(activeSectionId);
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
      setActiveSettingsSectionNav(activeSectionId);
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

  mainShell.addEventListener("scroll", scheduleRecalc, { passive: true });
  window.addEventListener("resize", scheduleRecalc);
  window.addEventListener("settings:layoutchanged", scheduleRecalc);

  const initialHash = window.location.hash.slice(1);
  const initialTarget = sectionEntries.find(
    (entry) => entry.sectionId === initialHash,
  );
  if (initialTarget) {
    expandSectionIfCollapsed(initialTarget.section);
    mainShell.scrollTop = Math.max(
      0,
      initialTarget.section.offsetTop - scrollAnchorOffset(),
    );
    activeSectionId = initialTarget.sectionId;
  }

  recalc(true);
}

function bindEvents() {
  bindCollapsiblePanels();
  bindSettingsSectionNav();
  bindMobileNav();
  const appearanceSaveButton = byId("settings-save-appearance");
  if (appearanceSaveButton) {
    appearanceSaveButton.addEventListener("click", () => {
      void saveAppearanceSettings();
    });
  }
  settingsSaveButtons().forEach((button) => {
    button.addEventListener("click", () => {
      void saveNotificationSettings();
    });
  });
  byId("settings-save-user-account").addEventListener("click", () => {
    void saveUserAccountSettings();
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
  byId("settings-monthly-enabled").addEventListener(
    "change",
    toggleNotificationFieldState,
  );
  byId("settings-scan-enabled").addEventListener("change", () => {
    syncScanScheduleState();
  });
  byId("settings-scan-frequency").addEventListener("change", () => {
    syncScanFrequencySummary();
    syncScanScheduleState();
  });
  byId("settings-app-timezone").addEventListener("input", () => {
    syncTimezoneCheckLink();
    if (normalizeProviderCountry(byId("settings-contract-provider-country")?.value) === "auto") {
      syncContractProviderPicker();
    }
  });
  byId("settings-contract-provider-country").addEventListener("change", () => {
    syncContractProviderPicker();
  });
  byId("settings-contract-provider-select").addEventListener("change", () => {
    const select = byId("settings-contract-provider-select");
    const customGroup = byId("settings-contract-provider-custom-group");
    customGroup?.classList.toggle("hidden", select?.value !== "__custom__");
  });
  byId("settings-add-scan-time").addEventListener("click", () => {
    addDailyScanTimeRow();
  });
  byId("settings-end-contract").addEventListener("click", () => {
    void endCurrentContract();
  });
  byId("settings-contract-history").addEventListener("click", () => {
    openContractHistoryModal();
  });
  byId("settings-contract-history-inline").addEventListener("click", () => {
    openContractHistoryModal();
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
  byId("settings-restore-file").addEventListener("change", () => {
    updateRestoreSelectedFileLabel();
  });
  byId("settings-restore-apply").addEventListener("click", () => {
    openRestoreBackupModal();
  });
  byId("backup-restore-modal-confirm").addEventListener("click", () => {
    void restoreBackup();
  });
  byId("backup-restore-modal-reload").addEventListener("click", () => {
    window.location.reload();
  });
  byId("backup-restore-modal-cancel").addEventListener(
    "click",
    closeRestoreBackupModal,
  );
  byId("backup-restore-modal-close").addEventListener(
    "click",
    closeRestoreBackupModal,
  );
  byId("backup-restore-modal").addEventListener("close", syncBodyModalState);
  byId("backup-restore-modal").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeRestoreBackupModal();
  });
  byId("backup-restore-modal").addEventListener("click", (event) => {
    if (event.target === byId("backup-restore-modal")) {
      closeRestoreBackupModal();
    }
  });
  byId("settings-confirm-modal-confirm").addEventListener("click", () => {
    closeConfirmDialog(true);
  });
  byId("settings-confirm-modal-cancel").addEventListener("click", () => {
    closeConfirmDialog(false);
  });
  byId("settings-confirm-modal-close").addEventListener("click", () => {
    closeConfirmDialog(false);
  });
  byId("settings-confirm-modal").addEventListener("close", syncBodyModalState);
  byId("settings-confirm-modal").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeConfirmDialog(false);
  });
  byId("settings-confirm-modal").addEventListener("click", (event) => {
    if (event.target === byId("settings-confirm-modal")) {
      closeConfirmDialog(false);
    }
  });
  byId("contract-summary-modal-ok").addEventListener("click", () => {
    closeSettingsDialog("contract-summary-modal");
  });
  byId("contract-summary-modal-close").addEventListener("click", () => {
    closeSettingsDialog("contract-summary-modal");
  });
  byId("contract-summary-modal").addEventListener("close", syncBodyModalState);
  byId("contract-summary-modal").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeSettingsDialog("contract-summary-modal");
  });
  byId("contract-summary-modal").addEventListener("click", (event) => {
    if (event.target === byId("contract-summary-modal")) {
      closeSettingsDialog("contract-summary-modal");
    }
  });
  byId("contract-history-modal-dismiss").addEventListener("click", () => {
    closeSettingsDialog("contract-history-modal");
  });
  byId("contract-history-modal-close").addEventListener("click", () => {
    closeSettingsDialog("contract-history-modal");
  });
  byId("contract-history-modal").addEventListener("close", syncBodyModalState);
  byId("contract-history-modal").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeSettingsDialog("contract-history-modal");
  });
  byId("contract-history-modal").addEventListener("click", (event) => {
    if (event.target === byId("contract-history-modal")) {
      closeSettingsDialog("contract-history-modal");
    }
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
  const confirmed = await openConfirmDialog({
    eyebrow: "Backup",
    title: "Delete backup?",
    copy: `Delete "${filename}"? This cannot be undone.`,
    confirmLabel: "Delete backup",
  });
  if (!confirmed) return;

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

function openConfirmDialog({
  eyebrow = "Confirm",
  title = "Please confirm",
  copy = "",
  confirmLabel = "Continue",
} = {}) {
  const modal = byId("settings-confirm-modal");
  if (!modal) return Promise.resolve(false);

  byId("settings-confirm-modal-eyebrow").textContent = eyebrow;
  byId("settings-confirm-modal-title").textContent = title;
  byId("settings-confirm-modal-copy").textContent = copy;
  byId("settings-confirm-modal-confirm").textContent = confirmLabel;

  return new Promise((resolve) => {
    confirmDialogResolver = resolve;

    if (isDialogElement(modal) && typeof modal.showModal === "function") {
      if (!modal.open) {
        modal.showModal();
      }
    } else {
      modal.classList.remove("hidden");
    }

    syncBodyModalState();
    byId("settings-confirm-modal-confirm")?.focus();
  });
}

function closeConfirmDialog(confirmed) {
  const modal = byId("settings-confirm-modal");
  const resolve = confirmDialogResolver;
  confirmDialogResolver = null;

  if (isDialogElement(modal) && modal?.open) {
    modal.close();
  } else {
    modal?.classList.add("hidden");
  }

  syncBodyModalState();
  if (resolve) {
    resolve(Boolean(confirmed));
  }
}

function selectedRestoreBackupFile() {
  const fileInput = byId("settings-restore-file");
  if (!fileInput?.files?.length) return null;
  return fileInput.files[0];
}

function updateRestoreSelectedFileLabel() {
  const selected = selectedRestoreBackupFile();
  const text = selected
    ? `${selected.name} (${formatBytes(selected.size)})`
    : "No backup selected.";
  const inlineNote = byId("settings-restore-file-note");
  const modalFile = byId("backup-restore-modal-file");
  if (inlineNote) {
    inlineNote.textContent = text;
  }
  if (modalFile) {
    modalFile.textContent = text;
  }
}

function setRestoreModalStatus(text, kind = "error") {
  const status = byId("backup-restore-modal-status");
  if (!status) return;
  status.textContent = text;
  status.classList.remove("hidden", "success-banner", "error-banner");
  status.classList.add(kind === "success" ? "success-banner" : "error-banner");
}

function clearRestoreModalStatus() {
  const status = byId("backup-restore-modal-status");
  if (!status) return;
  status.textContent = "";
  status.classList.remove("success-banner", "error-banner");
  status.classList.add("hidden");
}

function setRestoreModalFollowup(text) {
  const followup = byId("backup-restore-modal-followup");
  if (!followup) return;
  if (!text) {
    followup.textContent = "";
    followup.classList.add("hidden");
    return;
  }
  followup.textContent = text;
  followup.classList.remove("hidden");
}

function resetRestoreBackupModal() {
  clearRestoreModalStatus();
  setRestoreModalFollowup("");

  const passwordField = byId("backup-restore-modal-password");
  const confirmButton = byId("backup-restore-modal-confirm");
  const reloadButton = byId("backup-restore-modal-reload");
  const cancelButton = byId("backup-restore-modal-cancel");
  const closeButton = byId("backup-restore-modal-close");

  if (passwordField) {
    passwordField.value = "";
    passwordField.disabled = false;
  }
  if (confirmButton) {
    confirmButton.disabled = false;
    confirmButton.classList.remove("hidden");
    confirmButton.textContent = "Restore now";
  }
  if (reloadButton) {
    reloadButton.classList.add("hidden");
  }
  if (cancelButton) {
    cancelButton.textContent = "Cancel";
  }
  if (closeButton) {
    closeButton.disabled = false;
  }
}

function openRestoreBackupModal() {
  const selected = selectedRestoreBackupFile();
  if (!selected) {
    showMessage("Select a backup file first.", "warning");
    byId("settings-restore-file")?.focus();
    return;
  }

  const modal = byId("backup-restore-modal");
  if (!modal) return;

  updateRestoreSelectedFileLabel();
  resetRestoreBackupModal();

  if (isDialogElement(modal) && typeof modal.showModal === "function") {
    if (!modal.open) {
      modal.showModal();
    }
  } else {
    modal.classList.remove("hidden");
  }

  syncBodyModalState();
  byId("backup-restore-modal-password")?.focus();
}

function closeRestoreBackupModal() {
  const modal = byId("backup-restore-modal");
  if (!modal) return;

  if (isDialogElement(modal) && modal.open) {
    modal.close();
  } else {
    modal.classList.add("hidden");
  }

  syncBodyModalState();
}

async function restoreBackup() {
  const confirmButton = byId("backup-restore-modal-confirm");
  const cancelButton = byId("backup-restore-modal-cancel");
  const reloadButton = byId("backup-restore-modal-reload");
  const closeButton = byId("backup-restore-modal-close");
  const passwordField = byId("backup-restore-modal-password");

  try {
    const selected = selectedRestoreBackupFile();
    const password = passwordField?.value.trim() || "";

    if (!selected) {
      throw new Error("Select a backup file first.");
    }
    if (!password) {
      throw new Error("Enter the backup password.");
    }

    if (confirmButton) {
      confirmButton.disabled = true;
      confirmButton.textContent = "Restoring...";
    }
    if (cancelButton) cancelButton.disabled = true;
    if (reloadButton) reloadButton.disabled = true;
    if (closeButton) closeButton.disabled = true;
    if (passwordField) passwordField.disabled = true;
    clearRestoreModalStatus();
    setRestoreModalFollowup("");

    const formData = new FormData();
    formData.append("file", selected);
    formData.append("password", password);

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
    let successText = data.message || "Backup restored successfully.";
    successText += ` Restored: ${restored}.`;
    setRestoreModalStatus(successText, "success");

    let followup =
      "Normal settings apply automatically within about 10 seconds. Restored backups replace app files and environment values, so restart the dashboard and scheduler containers, then refresh this page.";
    if (warnings.length) {
      followup = `Warnings: ${warnings.join("; ")}\n${followup}`;
    }
    setRestoreModalFollowup(followup);

    byId("settings-restore-file").value = "";
    const inlineNote = byId("settings-restore-file-note");
    if (inlineNote) {
      inlineNote.textContent = "No backup selected.";
    }

    if (confirmButton) confirmButton.classList.add("hidden");
    if (reloadButton) {
      reloadButton.disabled = false;
      reloadButton.classList.remove("hidden");
    }
    if (cancelButton) {
      cancelButton.disabled = false;
      cancelButton.textContent = "Close";
    }
    if (closeButton) {
      closeButton.disabled = false;
    }

    showMessage(successText, "success");
  } catch (error) {
    setRestoreModalStatus(
      error.message || "Failed to restore backup.",
      "error",
    );
    setRestoreModalFollowup("");
    showMessage(error.message || "Failed to restore backup.", "error");
  } finally {
    if (confirmButton && !confirmButton.classList.contains("hidden")) {
      confirmButton.disabled = false;
      confirmButton.textContent = "Restore now";
    }
    if (cancelButton && cancelButton.textContent !== "Close") {
      cancelButton.disabled = false;
    }
    if (closeButton) {
      closeButton.disabled = false;
    }
    if (passwordField && confirmButton?.classList.contains("hidden") !== true) {
      passwordField.disabled = false;
    }
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
initMotionReveals();
updateRestoreSelectedFileLabel();
void loadNotificationSettings();
void loadScheduledServerOptions();
void loadBackupList();

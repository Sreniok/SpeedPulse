(() => {
  const legacyStorageKey = "speedpulse-theme";
  const modeStorageKey = "speedpulse-theme-mode";
  const lightStorageKey = "speedpulse-theme-light";
  const darkStorageKey = "speedpulse-theme-dark";
  const lightThemes = new Set([
    "default-light",
    "paper-slate",
    "linen-sage",
    "soft-coral",
    "mist-violet",
    "liquid-glass-light",
    "neumorphism-light",
    "retrofuturism-light",
  ]);
  const darkThemes = new Set([
    "default-dark",
    "cyber-matrix",
    "stealth-protocol",
    "carbon-amber",
    "night-orchid",
    "liquid-glass-dark",
    "neumorphism-dark",
    "retrofuturism-dark",
  ]);
  const removedLightThemes = new Set([
    "kinetic-circuit",
    "solar-boost",
    "arctic-flow",
    "quantum-edge",
    "skyline-draft",
  ]);
  const removedDarkThemes = new Set([
    "obsidian-velocity",
    "nebula-runner",
    "rogue-signal",
  ]);

  function normalizeMode(value) {
    const raw = String(value || "").trim().toLowerCase();
    return raw === "light" || raw === "dark" || raw === "system"
      ? raw
      : "system";
  }

  function resolveThemeId(themeId, mode) {
    const raw = String(themeId || "").trim();
    if (mode === "light") {
      if (raw === "light" || lightThemes.has(raw)) return raw === "light" ? "default-light" : raw;
      if (removedLightThemes.has(raw)) return "default-light";
      return "default-light";
    }
    if (raw === "dark" || darkThemes.has(raw)) return raw === "dark" ? "default-dark" : raw;
    if (removedDarkThemes.has(raw)) return "default-dark";
    return "default-dark";
  }

  try {
    const legacyTheme = String(window.localStorage.getItem(legacyStorageKey) || "").trim();
    let mode = normalizeMode(window.localStorage.getItem(modeStorageKey) || "");
    let lightTheme = resolveThemeId(window.localStorage.getItem(lightStorageKey), "light");
    let darkTheme = resolveThemeId(window.localStorage.getItem(darkStorageKey), "dark");

    if (legacyTheme) {
      if (!window.localStorage.getItem(modeStorageKey)) {
        if (legacyTheme === "light") {
          mode = "light";
        } else if (legacyTheme === "dark") {
          mode = "dark";
        } else if (lightThemes.has(legacyTheme) || removedLightThemes.has(legacyTheme)) {
          mode = "light";
          lightTheme = resolveThemeId(legacyTheme, "light");
        } else if (darkThemes.has(legacyTheme) || removedDarkThemes.has(legacyTheme)) {
          mode = "dark";
          darkTheme = resolveThemeId(legacyTheme, "dark");
        }
      }
    }

    const resolvedMode =
      mode === "system"
        ? window.matchMedia("(prefers-color-scheme: light)").matches
          ? "light"
          : "dark"
        : mode;

    document.documentElement.dataset.theme =
      resolvedMode === "light" ? lightTheme : darkTheme;
  } catch (error) {
    document.documentElement.dataset.theme = "default-dark";
  }
})();

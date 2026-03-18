(() => {
  const legacyStorageKey = "speedpulse-theme";
  const modeStorageKey = "speedpulse-theme-mode";
  const lightStorageKey = "speedpulse-theme-light";
  const darkStorageKey = "speedpulse-theme-dark";

  const catalog = window.SpeedPulseThemeCatalog || {};

  function themeIdsFrom(values) {
    if (!Array.isArray(values)) return [];
    return values
      .map((entry) => {
        if (typeof entry === "string") return entry;
        if (entry && typeof entry === "object") return String(entry.id || "").trim();
        return "";
      })
      .filter(Boolean);
  }

  const lightThemes = new Set(
    themeIdsFrom(catalog.allLightThemes).length
      ? themeIdsFrom(catalog.allLightThemes)
      : themeIdsFrom(catalog.lightThemes).length
        ? themeIdsFrom(catalog.lightThemes)
        : ["default-light"],
  );
  const darkThemes = new Set(
    themeIdsFrom(catalog.allDarkThemes).length
      ? themeIdsFrom(catalog.allDarkThemes)
      : themeIdsFrom(catalog.darkThemes).length
        ? themeIdsFrom(catalog.darkThemes)
        : ["default-dark"],
  );

  const removedLightThemes = new Set(
    Array.isArray(catalog.removedLightThemes) ? catalog.removedLightThemes : [],
  );
  const removedDarkThemes = new Set(
    Array.isArray(catalog.removedDarkThemes) ? catalog.removedDarkThemes : [],
  );

  const defaultLightTheme = String(
    catalog.defaultLightTheme || catalog.recommendedLightTheme || "default-light",
  );
  const defaultDarkTheme = String(
    catalog.defaultDarkTheme || catalog.recommendedDarkTheme || "default-dark",
  );

  function readMetaThemePreference(name, fallback = "") {
    const value = document
      .querySelector(`meta[name="${name}"]`)
      ?.getAttribute("content");
    return String(value || fallback).trim();
  }

  function normalizeMode(value) {
    const raw = String(value || "").trim().toLowerCase();
    return raw === "light" || raw === "dark" || raw === "system"
      ? raw
      : "system";
  }

  function resolveThemeId(themeId, mode) {
    const raw = String(themeId || "").trim();
    if (mode === "light") {
      if (raw === "light") return defaultLightTheme;
      if (lightThemes.has(raw)) return raw;
      if (removedLightThemes.has(raw)) return defaultLightTheme;
      return defaultLightTheme;
    }

    if (raw === "dark") return defaultDarkTheme;
    if (darkThemes.has(raw)) return raw;
    if (removedDarkThemes.has(raw)) return defaultDarkTheme;
    return defaultDarkTheme;
  }

  try {
    const serverMode = normalizeMode(
      readMetaThemePreference("speedpulse-theme-mode", String(catalog.defaultMode || "system")),
    );
    const serverLightTheme = resolveThemeId(
      readMetaThemePreference("speedpulse-theme-light", defaultLightTheme),
      "light",
    );
    const serverDarkTheme = resolveThemeId(
      readMetaThemePreference("speedpulse-theme-dark", defaultDarkTheme),
      "dark",
    );

    const legacyTheme = String(window.localStorage.getItem(legacyStorageKey) || "").trim();
    let mode = normalizeMode(window.localStorage.getItem(modeStorageKey) || serverMode);
    let lightTheme = resolveThemeId(window.localStorage.getItem(lightStorageKey) || serverLightTheme, "light");
    let darkTheme = resolveThemeId(window.localStorage.getItem(darkStorageKey) || serverDarkTheme, "dark");

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
    document.documentElement.dataset.theme = defaultDarkTheme;
  }
})();

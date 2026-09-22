(() => {
  const legacyStorageKey = "speedpulse-theme";
  const themeModeStorageKey = "speedpulse-theme-mode";
  const lightThemeStorageKey = "speedpulse-theme-light";
  const darkThemeStorageKey = "speedpulse-theme-dark";
  const systemColorScheme = window.matchMedia("(prefers-color-scheme: light)");

  const catalog = window.SpeedPulseThemeCatalog || {};

  const fallbackLightThemes = [
    { id: "default-light", name: "Default Light", mode: "light" },
  ];
  const fallbackDarkThemes = [
    { id: "default-dark", name: "Default Dark", mode: "dark" },
  ];

  const allLightThemes =
    Array.isArray(catalog.allLightThemes) && catalog.allLightThemes.length
      ? catalog.allLightThemes
      : Array.isArray(catalog.lightThemes) && catalog.lightThemes.length
        ? catalog.lightThemes
        : fallbackLightThemes;
  const allDarkThemes =
    Array.isArray(catalog.allDarkThemes) && catalog.allDarkThemes.length
      ? catalog.allDarkThemes
      : Array.isArray(catalog.darkThemes) && catalog.darkThemes.length
        ? catalog.darkThemes
        : fallbackDarkThemes;

  const lightThemes =
    Array.isArray(catalog.lightThemes) && catalog.lightThemes.length
      ? catalog.lightThemes
      : allLightThemes;
  const darkThemes =
    Array.isArray(catalog.darkThemes) && catalog.darkThemes.length
      ? catalog.darkThemes
      : allDarkThemes;

  const themeMap = Object.fromEntries(
    [...allLightThemes, ...allDarkThemes].map((theme) => [theme.id, theme]),
  );
  const removedLightThemes = new Set(
    Array.isArray(catalog.removedLightThemes) ? catalog.removedLightThemes : [],
  );
  const removedDarkThemes = new Set(
    Array.isArray(catalog.removedDarkThemes) ? catalog.removedDarkThemes : [],
  );

  const recommendedLightTheme = String(
    catalog.recommendedLightTheme || catalog.defaultLightTheme || "default-light",
  );
  const recommendedDarkTheme = String(
    catalog.recommendedDarkTheme || catalog.defaultDarkTheme || "default-dark",
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

  function normalizeThemeId(themeId, mode) {
    const raw = String(themeId || "").trim();
    if (!raw) return mode === "dark" ? recommendedDarkTheme : recommendedLightTheme;
    if (raw === "light") return recommendedLightTheme;
    if (raw === "dark") return recommendedDarkTheme;
    const theme = themeMap[raw];
    if (theme && theme.mode === mode) return raw;
    if (mode === "light" && removedLightThemes.has(raw)) return recommendedLightTheme;
    if (mode === "dark" && removedDarkThemes.has(raw)) return recommendedDarkTheme;
    return mode === "dark" ? recommendedDarkTheme : recommendedLightTheme;
  }

  function migratedLegacyPreferences() {
    let legacyTheme = "";
    try {
      legacyTheme = String(window.localStorage.getItem(legacyStorageKey) || "").trim();
    } catch (error) {
      return null;
    }
    if (!legacyTheme) return null;

    const preferences = {
      mode: "system",
      lightTheme: recommendedLightTheme,
      darkTheme: recommendedDarkTheme,
    };

    if (legacyTheme === "light") {
      preferences.mode = "light";
      return preferences;
    }
    if (legacyTheme === "dark") {
      preferences.mode = "dark";
      return preferences;
    }

    if (themeMap[legacyTheme]) {
      preferences.mode = themeMap[legacyTheme].mode;
      if (preferences.mode === "light") {
        preferences.lightTheme = legacyTheme;
      } else {
        preferences.darkTheme = legacyTheme;
      }
      return preferences;
    }

    if (removedLightThemes.has(legacyTheme)) {
      preferences.mode = "light";
      return preferences;
    }
    if (removedDarkThemes.has(legacyTheme)) {
      preferences.mode = "dark";
      return preferences;
    }

    return preferences;
  }

  function serverDefaults() {
    const defaultMode = normalizeMode(catalog.defaultMode || "system");
    const mode = normalizeMode(
      readMetaThemePreference("speedpulse-theme-mode", String(defaultMode)),
    );

    return {
      mode,
      lightTheme: normalizeThemeId(
        readMetaThemePreference("speedpulse-theme-light", recommendedLightTheme),
        "light",
      ),
      darkTheme: normalizeThemeId(
        readMetaThemePreference("speedpulse-theme-dark", recommendedDarkTheme),
        "dark",
      ),
    };
  }

  function loadPreferences() {
    const defaults = serverDefaults();
    const migrated = migratedLegacyPreferences();

    let mode = migrated?.mode || defaults.mode;
    let lightTheme = migrated?.lightTheme || defaults.lightTheme;
    let darkTheme = migrated?.darkTheme || defaults.darkTheme;

    try {
      mode = normalizeMode(window.localStorage.getItem(themeModeStorageKey) || mode);
      lightTheme = normalizeThemeId(
        window.localStorage.getItem(lightThemeStorageKey) || lightTheme,
        "light",
      );
      darkTheme = normalizeThemeId(
        window.localStorage.getItem(darkThemeStorageKey) || darkTheme,
        "dark",
      );
    } catch (error) {
      return {
        mode,
        lightTheme,
        darkTheme,
      };
    }

    return {
      mode,
      lightTheme,
      darkTheme,
    };
  }

  function effectiveMode(mode) {
    if (mode === "light" || mode === "dark") return mode;
    return systemColorScheme.matches ? "light" : "dark";
  }

  function activeThemeId(preferences) {
    const resolvedMode = effectiveMode(preferences.mode);
    return resolvedMode === "light"
      ? normalizeThemeId(preferences.lightTheme, "light")
      : normalizeThemeId(preferences.darkTheme, "dark");
  }

  function persistPreferences(preferences) {
    try {
      window.localStorage.setItem(themeModeStorageKey, preferences.mode);
      window.localStorage.setItem(lightThemeStorageKey, preferences.lightTheme);
      window.localStorage.setItem(darkThemeStorageKey, preferences.darkTheme);
      window.localStorage.removeItem(legacyStorageKey);
    } catch (error) {
      // Ignore storage access failures after the active theme is applied.
    }
  }

  function applyPreferences(preferences, persist = true) {
    const normalized = {
      mode: normalizeMode(preferences.mode),
      lightTheme: normalizeThemeId(preferences.lightTheme, "light"),
      darkTheme: normalizeThemeId(preferences.darkTheme, "dark"),
    };
    const resolvedMode = effectiveMode(normalized.mode);
    const resolvedTheme = activeThemeId(normalized);

    document.documentElement.dataset.theme = resolvedTheme;
    if (document.body) {
      document.body.dataset.theme = resolvedTheme;
    }

    if (persist) {
      persistPreferences(normalized);
    }

    document.dispatchEvent(
      new CustomEvent("speedpulse:themechange", {
        detail: {
          ...normalized,
          resolvedMode,
          activeTheme: resolvedTheme,
          activeDefinition: themeMap[resolvedTheme] || null,
        },
      }),
    );

    return {
      ...normalized,
      resolvedMode,
      activeTheme: resolvedTheme,
      activeDefinition: themeMap[resolvedTheme] || null,
    };
  }

  function currentPreferences() {
    const preferences = loadPreferences();
    return {
      ...preferences,
      resolvedMode: effectiveMode(preferences.mode),
      activeTheme: activeThemeId(preferences),
    };
  }

  function preferredTheme() {
    return currentPreferences().activeTheme;
  }

  function setMode(mode) {
    const preferences = loadPreferences();
    return applyPreferences({ ...preferences, mode });
  }

  function setTheme(mode, themeId) {
    const preferences = loadPreferences();
    if (mode === "light") {
      return applyPreferences({
        ...preferences,
        lightTheme: normalizeThemeId(themeId, "light"),
      });
    }
    return applyPreferences({
      ...preferences,
      darkTheme: normalizeThemeId(themeId, "dark"),
    });
  }

  applyPreferences(loadPreferences(), false);

  systemColorScheme.addEventListener("change", () => {
    const preferences = loadPreferences();
    if (preferences.mode === "system") {
      applyPreferences(preferences, false);
    }
  });

  window.SpeedPulseTheme = {
    themeModeStorageKey,
    lightThemeStorageKey,
    darkThemeStorageKey,
    lightThemes,
    darkThemes,
    allLightThemes,
    allDarkThemes,
    themeMap,
    recommendedThemes: {
      light: recommendedLightTheme,
      dark: recommendedDarkTheme,
    },
    currentPreferences,
    preferredTheme,
    applyPreferences,
    setMode,
    setTheme,
  };
})();

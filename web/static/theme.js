(() => {
  const legacyStorageKey = "speedpulse-theme";
  const themeModeStorageKey = "speedpulse-theme-mode";
  const lightThemeStorageKey = "speedpulse-theme-light";
  const darkThemeStorageKey = "speedpulse-theme-dark";
  const systemColorScheme = window.matchMedia("(prefers-color-scheme: light)");

  const lightThemes = [
    { id: "default-light", name: "Default Light", mode: "light" },
    { id: "paper-slate", name: "Paper Slate", mode: "light" },
    { id: "linen-sage", name: "Linen Sage", mode: "light" },
    { id: "soft-coral", name: "Soft Coral", mode: "light" },
    { id: "mist-violet", name: "Mist Violet", mode: "light" },
    { id: "liquid-glass-light", name: "Liquid Glass", mode: "light" },
    { id: "neumorphism-light", name: "Neumorphism", mode: "light" },
    { id: "retrofuturism-light", name: "Retrofuturism", mode: "light" },
    { id: "nordic-frost-light", name: "Nordic Frost", mode: "light" },
    { id: "desert-bloom-light", name: "Desert Bloom", mode: "light" },
    { id: "ocean-notebook-light", name: "Ocean Notebook", mode: "light" },
    { id: "citrus-print-light", name: "Citrus Print", mode: "light" },
    { id: "alpine-ink-light", name: "Alpine Ink", mode: "light" },
    { id: "rose-paper-light", name: "Rose Paper", mode: "light" },
    { id: "copper-haze-light", name: "Copper Haze", mode: "light" },
  ];
  const darkThemes = [
    { id: "default-dark", name: "Default Dark", mode: "dark" },
    { id: "cyber-matrix", name: "Cyber Matrix", mode: "dark" },
    { id: "stealth-protocol", name: "Stealth Protocol", mode: "dark" },
    { id: "carbon-amber", name: "Carbon Amber", mode: "dark" },
    { id: "night-orchid", name: "Night Orchid", mode: "dark" },
    { id: "liquid-glass-dark", name: "Liquid Glass", mode: "dark" },
    { id: "neumorphism-dark", name: "Neumorphism", mode: "dark" },
    { id: "retrofuturism-dark", name: "Retrofuturism", mode: "dark" },
    { id: "midnight-terminal-dark", name: "Midnight Terminal", mode: "dark" },
    { id: "ember-forge-dark", name: "Ember Forge", mode: "dark" },
    { id: "abyss-current-dark", name: "Abyss Current", mode: "dark" },
    { id: "jade-circuit-dark", name: "Jade Circuit", mode: "dark" },
    { id: "noir-slate-dark", name: "Noir Slate", mode: "dark" },
    { id: "crimson-radar-dark", name: "Crimson Radar", mode: "dark" },
    { id: "lunar-vault-dark", name: "Lunar Vault", mode: "dark" },
  ];

  const themeMap = Object.fromEntries(
    [...lightThemes, ...darkThemes].map((theme) => [theme.id, theme]),
  );
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

  function defaultThemeId(mode) {
    return mode === "dark" ? "default-dark" : "default-light";
  }

  function normalizeThemeId(themeId, mode) {
    const raw = String(themeId || "").trim();
    if (!raw) return defaultThemeId(mode);
    if (raw === "light") return "default-light";
    if (raw === "dark") return "default-dark";
    const theme = themeMap[raw];
    if (theme && theme.mode === mode) return raw;
    if (mode === "light" && removedLightThemes.has(raw)) return "default-light";
    if (mode === "dark" && removedDarkThemes.has(raw)) return "default-dark";
    return defaultThemeId(mode);
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
      lightTheme: "default-light",
      darkTheme: "default-dark",
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

  function loadPreferences() {
    const migrated = migratedLegacyPreferences();
    let mode = migrated?.mode || "system";
    let lightTheme = migrated?.lightTheme || "default-light";
    let darkTheme = migrated?.darkTheme || "default-dark";

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
    themeMap,
    currentPreferences,
    preferredTheme,
    applyPreferences,
    setMode,
    setTheme,
  };
})();

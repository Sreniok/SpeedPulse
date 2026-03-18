(() => {
  const allLightThemes = [
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
    { id: "github-light", name: "GitHub Light", mode: "light" },
    { id: "atom-light", name: "Atom Light", mode: "light" },
    { id: "monokai-light", name: "Monokai Light", mode: "light" },
  ];

  const allDarkThemes = [
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
    { id: "github-dark", name: "GitHub Dark", mode: "dark" },
    { id: "atom-dark", name: "Atom Dark", mode: "dark" },
    { id: "monokai-dark", name: "Monokai Dark", mode: "dark" },
  ];

  const featuredLightThemeIds = [
    "github-light",
    "default-light",
    "paper-slate",
    "nordic-frost-light",
    "atom-light",
    "monokai-light",
  ];
  const featuredDarkThemeIds = [
    "github-dark",
    "default-dark",
    "noir-slate-dark",
    "midnight-terminal-dark",
    "atom-dark",
    "monokai-dark",
  ];

  const lightMap = Object.fromEntries(allLightThemes.map((theme) => [theme.id, theme]));
  const darkMap = Object.fromEntries(allDarkThemes.map((theme) => [theme.id, theme]));

  const lightThemes = featuredLightThemeIds
    .map((themeId) => lightMap[themeId])
    .filter(Boolean);
  const darkThemes = featuredDarkThemeIds
    .map((themeId) => darkMap[themeId])
    .filter(Boolean);

  window.SpeedPulseThemeCatalog = Object.freeze({
    defaultMode: "system",
    defaultLightTheme: "github-light",
    defaultDarkTheme: "github-dark",
    recommendedLightTheme: "github-light",
    recommendedDarkTheme: "github-dark",
    lightThemes,
    darkThemes,
    allLightThemes,
    allDarkThemes,
    removedLightThemes: [
      "kinetic-circuit",
      "solar-boost",
      "arctic-flow",
      "quantum-edge",
      "skyline-draft",
    ],
    removedDarkThemes: [
      "obsidian-velocity",
      "nebula-runner",
      "rogue-signal",
    ],
  });
})();

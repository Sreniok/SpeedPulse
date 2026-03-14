# Dashboard Improvements

## High Impact, Low Effort

- [x] **1. CSV Export for results table** — Add an "Export CSV" button above the results table so users can download speed test data. *(already existed)*
- [x] **2. Save chart as PNG** — Add a download icon on each chart to save it as a PNG image using Chart.js `toBase64Image()`.
- [x] **3. Auto-refresh toggle** — Add a polling toggle on the topbar that refreshes dashboard data every 60 seconds.
- [x] **4. Time-of-day heatmap** — Add a heatmap grid (hour × day-of-week) color-coded by average download speed to reveal ISP throttling patterns.

## Medium Impact, Medium Effort

- [x] **5. Period comparison cards** — Extend hero metric cards with "This week vs. last week" percentage change and trend arrows.
- [x] **6. Annotations on speed chart** — Mark incidents and threshold breaches directly on the line chart as coloured markers or bands.
- [x] **7. Mobile sidebar collapse** — Hamburger menu + slide-in sidebar for small screens. *(already existed)*
- [x] **8. Notification history panel** — Show recent sent alerts, weekly reports, and health-check emails with timestamps.

## Nice to Have

- [x] **9. Keyboard shortcuts** — `R` to run test, `Esc` to close modals, number keys to switch time ranges.
- [x] **10. Sparkline mini-charts in hero cards** — Tiny inline trend lines in the download/upload/ping cards.
- [x] **11. Dark/light theme persistence** — Persist chosen theme to localStorage across sessions. *(already existed)*

# SPATAIL Developer Log

A self-contained, on-brand website for tracking what we change — summarized
and readable, not raw commit noise. Built in the SPATAIL design system
(the iOS app's exact palette, gradient wordmark, monospaced meta).

This folder is standalone: it touches **nothing** in the rest of the codebase.

## Open it

Just open `index.html` — double-click it, or drag it into a browser. No build
step, no server, no dependencies. (The log data is a JS file, so it works over
`file://` with no CORS issues.)

## How it works

```
mydevelopertools/
├── index.html          ← the page shell
├── styles.css          ← SPATAIL design system
├── app.js              ← render, filter, search, expand
├── data/
│   └── entries.js      ← THE LOG (window.SPATAIL_LOG = [...]) — edit this
├── scripts/
│   └── gen-log.mjs     ← pull new git commits in as draft entries
└── README.md
```

`data/entries.js` is the single source of truth. Each entry:

```js
{
  date: "2026-06-26",                 // YYYY-MM-DD
  title: "Live perception pipeline",
  category: "feature",                // feature | fix | refactor | tooling | docs | direction
  status: "shipped",                  // shipped | in-progress | draft
  area: "iOS / SPATAILMobileAR",      // free-text grouping
  summary: "One readable sentence.",  // the headline you'll skim
  details: ["what changed", "..."],   // bullets (expandable)
  why: "Why we did it.",              // optional
  tags: ["ios", "ARKit"],             // clickable filters
  files: ["path/to/file"],            // optional
  commits: ["8278a1a"]                // optional short hashes
}
```

## Adding entries

**By hand (the summarized way):** open `data/entries.js`, add an object to the
top of the array, reload the page. Keep `summary` to one clear sentence.

**From git (the low-effort way):**

```bash
node mydevelopertools/scripts/gen-log.mjs          # or  --count 100
```

This reads `git log`, and for any commit not already referenced by an entry it
prepends a **draft** entry (marked with a dashed `draft` flag in the UI). Open
the page, find the drafts, and edit them into clear summaries — then change
`status` from `"draft"` to `"shipped"`. It skips commits you've already logged,
so it's safe to run anytime. It only writes `data/entries.js`.

## Features

- Filter by category, click any `#tag` to filter, free-text search.
- Cards expand to show what changed, why, files, and commits.
- Timeline grouped by month; stats strip up top.
- Fully responsive, dark, reduced-motion aware.

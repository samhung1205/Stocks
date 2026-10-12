# K1A Palette Bake-off

## Scope and comparison controls

This bake-off compares color direction only. Layout, typography, spacing, component structure, interaction, research disclosures, and chart data semantics are identical across all three candidates.

- The candidate palettes were injected only into the Playwright browser sessions as semantic color token overrides.
- No candidate was written into production CSS or JavaScript.
- All screenshots use a 1280px viewport width and the same local database state.
- Dashboard data is shown as of the 2026-09-23 close.
- Strategy Portfolio screenshots use Portfolio 25, pinned version 31, completed research run `729e3b9aa387467db82bed320903c753`.
- Planner screenshots use that same portfolio, version, and research run. The planning return remains blank and independent from research evidence.
- Journal uses the current real empty state: no closed trade sample, no calculated realized-performance cards.
- Existing chart series and category colors were left unchanged. The bake-off changes the surrounding semantic UI palette only.

No winner is selected in this document.

## Candidate palettes

### A. Current Research Desk

Warm paper, soft parchment canvas, dark blue-gray ink, and sea-pine action/evidence color. The direction feels personal and notebook-like. The temporary candidate separates `status-info` from the brand/action token so informational state does not depend on the brand accent.

### B. Cool Research Desk

Cool stone canvas, near-white paper, dark ink, and a muted ink-blue action/evidence color. The temperature is cooler and more operational while keeping chroma low enough to avoid bright SaaS blue.

### C. Academic Research Desk

Cool neutral canvas, off-white paper, graphite text, and a desaturated indigo action/evidence color. The direction reads closer to an academic file or research publication than a trading terminal. The indigo remains gray-biased and does not use gradients.

## Semantic token comparison

| Semantic role | A · Current | B · Cool | C · Academic |
|---|---:|---:|---:|
| `canvas` | `#F5F3ED` | `#F0F2F3` | `#F1F1EF` |
| `paper` | `#FFFEFA` | `#FCFDFD` | `#FEFDF9` |
| `inset` | `#EEEEE8` | `#E9EDEF` | `#EBEBE7` |
| `text-primary` | `#243039` | `#202C33` | `#2B2D31` |
| `text-secondary` | `#526068` | `#52636D` | `#5B5E66` |
| `rule` | `#D3D7D2` | `#CCD3D6` | `#D0D1CD` |
| `action` | `#315F68` | `#385E73` | `#555D7A` |
| `focus` | `#175D79` | `#155C79` | `#3E4C79` |
| `evidence-primary` | `#315F68` | `#385E73` | `#555D7A` |
| `evidence-secondary` | `#66757B` | `#637781` | `#6C7180` |
| `status-neutral` | `#59666B` | `#59686E` | `#5D636B` |
| `status-info` | `#2F6665` | `#2E6769` | `#35666A` |
| `status-caution` | `#835516` | `#7D571E` | `#80591C` |
| `status-critical` | `#9A3730` | `#963C35` | `#963C36` |
| `price-up` | `#A63732` | `#A23A34` | `#A23A35` |
| `price-down` | `#247048` | `#247048` | `#246D48` |

The Taiwan-market red-up/green-down convention is independent from action, evidence, and status colors in every candidate. Status text also retains explicit labels such as `INFO`, `CAUTION`, and `CRITICAL`; price movement retains signs and values, so color is never the only cue.

## Contrast verification

Ratios below use each candidate's `paper` surface. Filled action buttons use white text. Decorative rules are intentionally quiet separators; text, controls, status labels, price direction, and focus indicators meet or exceed their relevant WCAG AA thresholds.

| Pair | A | B | C | Threshold |
|---|---:|---:|---:|---:|
| Primary text / paper | 13.37:1 | 14.02:1 | 13.55:1 | 4.5:1 |
| Secondary text / paper | 6.44:1 | 6.13:1 | 6.37:1 | 4.5:1 |
| White text / action | 7.08:1 | 6.96:1 | 6.50:1 | 4.5:1 |
| Focus / paper | 7.23:1 | 7.26:1 | 8.20:1 | 3:1 |
| Evidence secondary / paper | 4.73:1 | 4.59:1 | 4.79:1 | 4.5:1 |
| Status neutral / paper | 5.88:1 | 5.68:1 | 5.96:1 | 4.5:1 |
| Status info / paper | 6.48:1 | 6.32:1 | 6.33:1 | 4.5:1 |
| Status caution / paper | 6.35:1 | 6.33:1 | 6.13:1 | 4.5:1 |
| Status critical / paper | 7.02:1 | 6.88:1 | 6.89:1 | 4.5:1 |
| Price up / paper | 6.47:1 | 6.46:1 | 6.46:1 | 4.5:1 |
| Price down / paper | 5.97:1 | 5.91:1 | 6.15:1 | 4.5:1 |

## Comparative assessment

| Criterion | A · Current Research Desk | B · Cool Research Desk | C · Academic Research Desk |
|---|---|---|---|
| Identity | Strong personal notebook character. Warmth and sea-pine feel calm and owned. | Clear research workstation character. Cool stone and ink-blue feel disciplined and contemporary. | Strong publication and archive character. Graphite and restrained indigo feel studied and institutional. |
| Readability | High. Warm surfaces soften long pages; paper/canvas separation is subtle but clear. | High. Cool surface separation is most immediately legible in dense forms and large result pages. | High. Graphite text has a crisp editorial tone; the indigo hierarchy remains visible without high saturation. |
| Long-session comfort | High. Warmth reduces clinical glare, though a fully warm environment can feel visually similar across long pages. | High. Neutral temperature is steady for extended analysis; it can feel more utilitarian during personal journaling. | High. Calm neutrals support long reading; the slightly more formal tone can feel less intimate in daily note-taking. |
| Financial / research fit | Strong for a personal research notebook and discretionary review. | Strong for repeatable market and quantitative workflows. It communicates tools, evidence, and operating state clearly. | Strong for versioned evidence, methodology, and long-form research review. It gives disclosures an academic-document context. |
| Similarity to generic SaaS | Low to medium. The warm paper world resists dashboard-template styling; sea-pine can still resemble common modern productivity products if overused. | Medium. Cool canvas plus blue action is a familiar software pattern. Muted chroma and editorial density keep it away from bright SaaS, but future blue expansion would raise the risk. | Low to medium. Desaturated indigo is distinctive at this dosage; higher chroma, gradients, or decorative purple would quickly create an AI-product association. |
| Risk of resembling another warm-toned personal product | High relative to B and C because warmth is the dominant atmosphere. | Low. Cool stone clearly separates the product from warm personal-product palettes. | Low to medium. The off-white paper retains some editorial warmth, while the cool neutral canvas and graphite/indigo system reduce direct resemblance. |

## Page observations

### Dashboard

- A reinforces the daily-notebook feeling and makes the market summary feel personal rather than institutional.
- B gives data date, stale-data caution, and the market strip the clearest operational separation.
- C makes the page feel closer to a research brief; the indigo action line is quieter than conventional financial-product blue.

### Journal

- A is the most diary-like and makes the long trading-plan form feel approachable.
- B makes form boundaries and the empty-performance state feel neutral and procedural.
- C gives the plan the tone of a structured research protocol and separates the primary action with restrained authority.

### Strategy Portfolio Result

- All three preserve `Historical / OOS ≠ forecast`, `no_candidate`, unresolved corporate-action warnings, assumed-publication cautions, and holdings-as-of-last-rebalance language.
- A integrates the result with the rest of the personal notebook.
- B supports quick scanning across metrics, evidence notices, charts, and rebalance records.
- C gives version, provenance, and warning blocks a formal research-record tone.

### Planner Evidence / Assumption

- All three keep Research Evidence and Planning Assumption visually separate. The planning return remains blank and is not populated from CAGR.
- A makes the pair feel like two sections in the same personal planning notebook.
- B creates the strongest cool-paper separation between the source record and the planning decision.
- C emphasizes the distinction as evidence versus authored assumption, similar to an academic method and conclusion split.

## Representative screenshots

### Dashboard · 1280px

| A · Current | B · Cool | C · Academic |
|---|---|---|
| A Current Research Desk Dashboard (`output/playwright/k1a-palette/a-dashboard.png`, local untracked artifact) | B Cool Research Desk Dashboard (`output/playwright/k1a-palette/b-dashboard.png`, local untracked artifact) | C Academic Research Desk Dashboard (`output/playwright/k1a-palette/c-dashboard.png`, local untracked artifact) |

### Journal · 1280px

| A · Current | B · Cool | C · Academic |
|---|---|---|
| A Current Research Desk Journal (`output/playwright/k1a-palette/a-journal.png`, local untracked artifact) | B Cool Research Desk Journal (`output/playwright/k1a-palette/b-journal.png`, local untracked artifact) | C Academic Research Desk Journal (`output/playwright/k1a-palette/c-journal.png`, local untracked artifact) |

### Strategy Portfolio Result · 1280px

| A · Current | B · Cool | C · Academic |
|---|---|---|
| A Current Research Desk Portfolio Result (`output/playwright/k1a-palette/a-portfolio-result.png`, local untracked artifact) | B Cool Research Desk Portfolio Result (`output/playwright/k1a-palette/b-portfolio-result.png`, local untracked artifact) | C Academic Research Desk Portfolio Result (`output/playwright/k1a-palette/c-portfolio-result.png`, local untracked artifact) |

### Planner Evidence / Assumption · 1280px

| A · Current | B · Cool | C · Academic |
|---|---|---|
| A Current Research Desk Planner Evidence and Assumption (`output/playwright/k1a-palette/a-planner-evidence-assumption.png`, local untracked artifact) | B Cool Research Desk Planner Evidence and Assumption (`output/playwright/k1a-palette/b-planner-evidence-assumption.png`, local untracked artifact) | C Academic Research Desk Planner Evidence and Assumption (`output/playwright/k1a-palette/c-planner-evidence-assumption.png`, local untracked artifact) |

## Preserved semantics and implementation boundary

- Taiwan-stock price direction remains red-up and green-down.
- Neutral, info, caution, and critical statuses use their own semantic tokens rather than action or evidence tokens.
- Research and planner disclosures remain present and unchanged.
- Chart values, series, axes, and data-color meanings remain unchanged.
- Dark mode is outside this bake-off.
- Candidates B and C exist only in screenshot sessions. Candidate A also remains a comparison-session override; production tokens were not changed by this bake-off.
- The next implementation stage must make an explicit palette decision before changing production tokens.

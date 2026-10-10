# UX Design

> **Status:** Accepted (approved design, not yet implemented) · **Owner:** TecHaust Technologies (@techaust) · **Last reviewed:** 2026-10-11 · **Audience:** front-end engineers, designers, the owner

This document defines the information architecture, the key screens, the interaction patterns and the accessibility requirements of the browser application. High-fidelity designs are produced in Milestone 4c from these wireframes and must not contradict them without updating this document.

## 1. Design principles

1. **Status at a glance.** The user should always know what is waiting, what is running and what needs a decision.
2. **Plan before change.** Every consequential action shows exactly what will happen before it happens.
3. **Decisions, not errors.** Problems the user can resolve appear as actionable needs-input items with context.
4. **Keyboard-first and accessible.** Everything is operable without a mouse and meets WCAG 2.2 AA.
5. **Calm density.** Tables and lists are information-dense but not visually noisy. Colour is never the only signal.

## 2. Information architecture

```text
Sidebar (collapsible; bottom tab bar under 768 px)
├── Dashboard                  /
├── Inbox                      /inbox, /inbox/:archiveId
├── Runs                       /runs, /runs/:runId
├── Needs input (badge)        /needs-input, /needs-input/:id
├── Catalog                    /catalog?store=&q=
├── Rules                      /rules/aliases | /rules/overrides | /rules/download-names
├── Audit                      /audit
├── Health                     /health
├── Cleanup (admin)            /cleanup
└── Settings (admin)           /settings/{general,stores,s3,ai,users,secrets,backups,import}
Assistant (drawer)             /assistant, /assistant/:conversationId, /assistant/memory, /assistant/usage
Account (all roles)            /account  (password, preferences, notification settings)
Header: instance name · lease indicator · command palette (Ctrl+K) · notification centre · assistant (Ctrl+/) · user menu
```

*Text description:* Navigation is a left sidebar with ten sections, which becomes a bottom tab bar with a "More" menu on small screens. The assistant opens as a drawer with its own pages for memory and usage. Every user has an Account page for password and preferences. The header shows the instance name, the lease status, a command palette, the notification centre (a popover listing recent events), the AI assistant toggle and the user menu. Admin-only sections are hidden for other roles.

## 3. Key screens

### 3.1 Dashboard

Requirement: FR-UX-01.

```text
┌ Inbox ─────────┐ ┌ Needs input ───┐ ┌ Active run ─────────────────┐
│ 12 archives    │ │ 3 open         │ │ R-418 live · 7/12 · 01:42   │
│ 10 ready       │ │ 2 unmapped     │ │ ███████░░░░░  S3 5 · WC 10  │
│ [Plan run]     │ │ 1 downgrade    │ │ [Open]                      │
└────────────────┘ └────────────────┘ └─────────────────────────────┘
┌ Recent runs ───────────────────────────┐ ┌ Health ────────────────┐
│ R-417 live  succeeded  25 items 44 s   │ │ ● S3        ok         │
│ R-416 dry   succeeded  25 items  3 s   │ │ ● GPLFamily ok         │
│ R-415 live  partial    6 items  23 s   │ │ ● PluginWay breaker 42s│
└────────────────────────────────────────┘ │ ● AI        $0.31/2.00 │
                                           │ Catalog refreshed 6 h  │
                                           │ Lease: free            │
                                           └────────────────────────┘
```

*Text description:* Three summary cards across the top (inbox, needs input, active run with a progress bar). Below them are a recent-runs list and a health panel showing S3, each store, AI budget, catalog age and lease state. Every card links to its section.

### 3.2 Inbox and archive detail

Requirements: FR-INBOX-01 to FR-INBOX-05.

- A drop zone covers the page while files are dragged, and an **Upload** button opens the file picker. Each upload row shows a progress bar, then the inspection status.
- The table columns are: filename · detected slug · version (with a layer badge, e.g. "header") · match (per-store dots plus method) · status · actions. Rows can be selected for planning.
- Archive detail shows the candidates list (the chosen one highlighted), match explanation ("Matched by alias `latepoint → latepoint-pro`"), planned operations, and history (earlier runs for this slug).

### 3.3 Plan and confirm live run

Requirement: FR-RUN-04.

```text
Plan R-419 (dry) · created 10:01 · valid until 10:16
───────────────────────────────────────────────────────────
 10 archives → 10 S3 uploads (1 deduplicated) · 20 store updates
 9 superseded objects to delete · 1 recorded as pending delete
 2 archives excluded: needs input (see queue)
 [Show details ▾]  per archive: slug, version, from → to per store
───────────────────────────────────────────────────────────
 Options (from the plan): Force off · Keep in inbox off · Concurrency 4   [Change options → re-plan]
 [Cancel]                                [Run live (10 archives)]
```

*Text description:* The plan summary lists counts of uploads, store updates, deletions and excluded archives. Details can be expanded per archive. The options are shown read-only because they shaped the plan; changing them creates a new plan. The primary button states exactly how many archives the live run will process. The plan expires 15 minutes after creation and must then be regenerated.

### 3.4 Run detail

Requirements: FR-RUN-01 to FR-RUN-03.

- Header: run ID, kind, mode, status badge, actor, instance, duration, counters, and actions (cancel, resume, export).
- Item list with a stage stepper per item (inspect → revalidate → upload → stores → finalize). Per-store chips show status. Selecting an item reveals its events and errors, with the error code linked to help text and an **Explain** (AI-3) button.
- The log panel is collapsible. Item failures are announced through the assertive live region; other progress uses the polite region (section 7).

### 3.5 Needs-input item

Requirements: FR-NI-01 to FR-NI-04.

```text
Unmapped archive: prime-slider-pro-v4.7.0.zip      detected slug: bdthemes-prime-slider
───────────────────────────────────────────────────────────────────────────────────
 Candidates                                  score  stores
 ◉ prime-slider-pro   "Prime Slider Pro"       88   GPLFamily, PluginWay   AI: Suggested 0.93
 ○ prime-slider       "Prime Slider"           81   GPLFamily, PluginWay
 ○ …                                                     [Search catalog…]
 AI rationale: "bdthemes- is the vendor prefix; title and filename both read 'Prime Slider Pro'."
 Save as: ◉ Alias (bdthemes-prime-slider → …)  ○ Filename override (this exact file)
 [Dismiss]                                              [Accept and re-inspect]
```

*Text description:* The page lists ranked candidates with scores and the stores each exists on. The AI suggestion and its rationale are shown when available. The user chooses whether to save an alias or a filename override, then accepts, which re-inspects the archive automatically.

### 3.6 Cleanup

Requirements: FR-CLN-01 to FR-CLN-03.

The preview result has two tabs, *Orphans* and *Pending deletes* (with the recording instance and reason). Each table (key, size, last modified, reason) has checkboxes. Pending deletes can also be discarded with **Keep object**. Execution opens a dialog that lists the selected keys and requires typing `DELETE <n>`. The confirm button stays disabled until the typed text matches.

### 3.7 Assistant panel

Requirements: FR-AI-05 to FR-AI-08.

- A right-hand drawer (full screen on mobile) shows the conversation list and the current conversation.
- Responses stream with a stop button. Tool calls appear as cards: name, arguments in readable form, status. `approve`-class cards have **Approve** and **Reject** buttons, show the impact ("Creates a live run for 3 archives"), and show the expiry countdown.
- The footer shows budget remaining and the model name.

### 3.8 Settings

- Each section is a form with inline validation and a **Save** button that is disabled until something changes.
- Secret fields are always empty, with a source badge (`keyring`, `environment`, `unset`) and **Replace** / **Remove** actions.
- **Test connection** buttons show per-capability results.

## 4. Interaction patterns

| Pattern | Rule |
|---|---|
| **Loading** | Skeletons for content that takes more than 300 ms; never block the whole page except at first load |
| **Empty** | Explain what belongs here and offer the next action (e.g. "Inbox is empty – drop ZIP files here or copy them to the watched folder", with the folder's full path shown and a copy button) |
| **Error** | Inline Problem Details title and detail, error code, **Retry** where meaningful, and the request ID for support |
| **Success** | Toast for 8 s, paused while hovered or focused, with an undo where possible (trash, rule delete). The undo stays available from the notification centre, so no action depends on reacting within a time limit (WCAG 2.2.1). |
| **Confirmation** | Live run: plan confirmation. Deletion: typed confirmation. Downgrade: reason required. Other destructive actions: dialog naming the object |
| **Optimistic UI** | Only for preferences and reversible list actions; server-confirmed for everything else |
| **Real-time** | SSE updates lists and counters in place; the user's scroll position and selection are preserved |
| **Concurrency conflicts** | `config.conflict` shows both values with "Keep theirs" / "Apply mine" |
| **Time** | Relative times ("2 min ago") with the absolute local time and UTC in a tooltip |

## 5. Visual design system

**Theme:** shadcn/ui (Nova preset, [ADR-0014](adr/0014-milestone-3-implementation-adjustments.md)) on Radix UI primitives and Tailwind CSS, with zinc neutrals and an indigo accent; follows the OS light/dark setting by default ([ADR-0013](adr/0013-design-system-shadcn-indigo.md)).

### 5.1 Colour tokens

Contrast ratios were computed with the WCAG 2.x relative-luminance formula on 2026-10-11. Required: text ≥ 4.5:1 (1.4.3); UI-component boundaries and focus indicators ≥ 3:1 (1.4.11).

Surface tokens:

| Token | Use | Light | Dark |
|---|---|---|---|
| `background` | Page | `#FFFFFF` | `#09090B` (zinc-950) |
| `card` / `popover` | Cards, menus, dialogs | `#FFFFFF` | `#18181B` (zinc-900) |
| `muted` / `secondary` / `accent` | Inactive tabs, hovered rows, secondary buttons, status-badge backgrounds | `#F4F4F5` (zinc-100) | `#27272A` (zinc-800) |
| `border` | Decorative dividers and card outlines (never the only way to identify a control) | `#E4E4E7` (zinc-200) | `#27272A` (zinc-800) |

**Foreground tokens.** Ratios are given against every surface each token is used on. Light surfaces are background/card (`#FFFFFF`) and muted (`#F4F4F5`); dark surfaces are background, card and muted.

| Token | Use | Light value | Light ratios (bg · muted) | Dark value | Dark ratios (bg · card · muted) |
|---|---|---|---|---|---|
| `foreground` | Body text | `#09090B` | 19.90 · 18.10 | `#FAFAFA` | 19.06 · 16.97 · 14.27 |
| `muted-foreground` | Secondary text, captions | `#52525B` (zinc-600) | 7.73 · 7.03 | `#A1A1AA` (zinc-400) | 7.76 · 6.91 · 5.81 |
| `input` | Form-control borders (≥ 3:1) | `#71717A` (zinc-500) | 4.83 · 4.40 | `#71717A` (zinc-500) | 4.12 · 3.67 · 3.08 |
| `primary` (fill) with `primary-foreground` text | Primary buttons, selected state | `#4F46E5` with `#FFFFFF` | 6.29 (text on fill) | `#818CF8` with `#09090B` | 6.67 (text on fill) |
| `ring` / `link` | Focus ring (2 px, offset 2 px), links | `#4F46E5` | 6.29 · 5.72 | `#818CF8` | 6.67 · 5.94 · 4.99 |
| `destructive` (fill) with `destructive-foreground` text | Destructive buttons | `#B91C1C` with `#FFFFFF` | 6.47 (text on fill) | `#F87171` with `#09090B` | 7.19 (text on fill) |
| `success` | Completed | `#15803D` (green-700) | 5.02 · 4.56 | `#4ADE80` (green-400) | 11.42 · 10.17 · 8.55 |
| `warning` | Needs input, partial | `#B45309` (amber-700) | 5.02 · 4.57 | `#FBBF24` (amber-400) | 11.92 · 10.61 · 8.92 |
| `danger` | Failed | `#B91C1C` (red-700) | 6.47 · 5.89 | `#F87171` (red-400) | 7.19 · 6.40 · 5.38 |
| `info` | Running, informational | `#2563EB` (blue-600) | 5.17 · 4.70 | `#60A5FA` (blue-400) | 7.83 · 6.97 · 5.86 |

Status badges render the status colour as text and icon on the `muted` surface.

Departures from shadcn/ui defaults, made for contrast:

- `input` uses zinc-500 in both modes. The stock light border zinc-200 measures 1.27:1 and the stock dark zinc-800 measures 1.34:1 (Tailwind v3 variant). The newer Tailwind v4 variant uses 10–15% translucent white in dark mode, which is equally low. Intermediate choices also fail: zinc-400 on light measures 2.56:1 and zinc-600 on dark 2.57:1.
- `muted-foreground` in light mode uses zinc-600, because the stock zinc-500 measures only 4.40:1 on the `muted` surface used by inactive tabs and palette rows.
- The dark `primary` and `destructive` fills use near-black text, because white on indigo-500 measures 4.47:1 and white on red-400 measures 2.77:1.

### 5.2 Other tokens

| Token group | Definition |
|---|---|
| Typography | **Inter** for UI text and **JetBrains Mono** for slugs, versions, file names, object keys and code. Both are self-hosted (OFL 1.1, no CDN). Scale 12/14/16/20/24/30 px; body 14 px (16 px on mobile); tabular numerals in tables. |
| Icons | Lucide (ISC), 16 px in tables, 20 px in navigation; decorative icons are `aria-hidden`, meaningful icons have labels |
| Spacing | 4 px base grid |
| Density | Compact (default; table rows 36 px) or comfortable (44 px), per user (FR-UX-04) |
| Radius and elevation | 6 px radius (`--radius: 0.375rem`); two elevation levels (popover, dialog) |
| Status | Every status badge combines an icon, a text label and a colour token. Colour is never the only signal. |
| Motion | 150–200 ms transitions; disabled when `prefers-reduced-motion` is set |
| Colour mode | `system` (default), `light`, `dark`; stored per user. Applied before first paint without any inline script (which the CSP forbids, C-22): the server renders `index.html` per request and writes `class="light"` or `class="dark"` on `<html>` from the signed-in user's preference. `system` uses no class and is resolved purely in CSS (`@media (prefers-color-scheme: dark) { :root:not(.light) { … } }`). |

Tokens are defined once as CSS variables in `frontend/src/styles/tokens.css` and mapped into Tailwind. A unit test recomputes every foreground/surface ratio in section 5.1 from the token file and fails below threshold. Components are shadcn/ui copies (dialog, dropdown, tabs, tooltip, toast, table, command palette, and others), documented in a component catalogue page available in development builds.

## 6. Responsive behaviour

Requirement: NFR-UX-01.

| Width | Layout |
|---|---|
| ≥ 1280 px | Sidebar expanded; detail views open as a side panel next to lists |
| 768–1279 px | Sidebar collapsed to icons; detail views replace the list |
| 320–767 px | Bottom tab bar; tables become stacked cards; assistant full-screen; uploads via the file picker (drag-and-drop where supported). 320 CSS px is the minimum supported width (WCAG 1.4.10). |

## 7. Accessibility

Requirement: NFR-A11Y-01.

- Use semantic landmarks (`header`, `nav`, `main`, `aside`), a **Skip to content** link, and one `h1` per page.
- Every interactive element is reachable with Tab in a logical order, with a visible focus ring of at least 2 px and 3:1 contrast.
- Dialogs trap focus and return it to the trigger when closed. `Esc` closes dialogs and drawers.
- Data tables use proper `th`/`scope` markup, sortable headers announce their sort state, and row selection works with Space.
- Run progress uses `role="progressbar"` with values. Status changes are announced through an `aria-live="polite"` region and failures through `assertive`.
- Forms label every field, link errors via `aria-describedby`, and focus the error summary after a failed submit.
- Content holds at 200% zoom and 400% reflow without loss.
- Keyboard shortcuts (`Ctrl+K`, `Ctrl+/`, `g d`, `g i`, `g r`) are listed on the help screen (`?`) and can be disabled.
- Testing: axe on every route in Playwright (zero serious or critical violations), a manual keyboard-only pass, and an NVDA screen-reader pass on Windows before release.

# UX & Accessibility Audit — Meridian Pay Knowledge Brain

Measured 2026-10-13 against WCAG 2.2 AA, from the actual hex values and DOM in `static/index.html`. All fixes listed below are applied in the shipped UI.

## Contrast (computed from real hex values)

| Pair | Ratio | AA (≥4.5 normal text) |
|---|---|---|
| White on brand purple `#7C3AED` (hero, buttons) | 5.70:1 | ✅ |
| White on gradient pink `#DB2777` | 4.60:1 | ✅ |
| Ink `#1E1B2E` on white cards | 14.68:1 | ✅ |
| Muted `#6B7280` on white | 4.83:1 | ✅ |
| Hint text `#475569` on white / page bg `#F7F6FB` | 7.58:1 / 7.05:1 | ✅ **fixed** (was `#94A3B8` = 2.56:1 ✗) |
| Error red `#B91C1C` on `#FFF7ED` | 6.09:1 | ✅ |
| Purple `#7C3AED` on `#F5F3FF` (tags) | 5.20:1 | ✅ |

## Structure & semantics

- Login form: real `<label for>` pairs; `autocomplete="username"` / `"current-password"` ✅
- Chat thread: `role="log" aria-live="polite" aria-relevant="additions"` — new messages are announced to screen readers (**added this audit**)
- Workflow segment buttons: `aria-pressed` reflects the selected workflow (**added this audit**)
- Audit drawer: `aria-modal` + labelled close button + Esc-to-close ✅
- Avatars are real text ("我" / "AI") — readable by assistive tech ✅
- All interactive elements are native `<button>` — keyboard-focusable with default focus ring; inputs have a visible focus state (`box-shadow` ring) ✅

## Touch & interaction targets

- Primary send button: 42px height ✅ (WCAG 2.5.5 minimum 24px; HIG 44pt target met on the main action)
- Chips ≈ 34px, audit buttons ≈ 32px — acceptable desktop targets
- Colour never alone: answer-mode tags carry text + colour; the no-context card pairs an icon with text; error states are text, not colour

## Known boundaries (stated honestly)

- Single desktop web surface; no mobile layout — deliberate scope. The accessibility surface is keyboard + screen reader
- Meta text (hints, source indices) at 11–12px: AA-legible after the contrast fix, but below large-print comfort
- Decorative borders (`#BFDBFE`) are low-contrast by design — borders never carry meaning alone

## How this was measured

Contrast ratios computed from the exact hex values in `static/index.html` (sRGB relative luminance, WCAG formula). Structure checks by direct DOM inspection. Fixes verified in the running app.

# Tender Intelligence Admin — Design System

This is an internal B2B operations console for reviewing tender pipeline status and configuration. It is not a marketing site. Apply these rules to all console screens unless a page override adds a specific requirement.

## Design direction

- **Style:** Minimalism & Swiss Style for an enterprise dashboard: restrained surfaces, compact hierarchy, legible tabular data, and obvious state.
- **Dials:** variance 3/10, motion 1/10, density 7/10.
- **Pattern:** persistent left navigation with a compact utility bar, page title/description, operational alerts, and task-focused content. Avoid hero sections, lead-generation CTAs, and decorative charts.
- **Framework:** semantic HTML, modern ES modules, and CSS. No UI framework requirement.

## Color tokens

| Role | Value | Use |
|---|---|---|
| Primary | `#1E40AF` | Primary actions and active navigation |
| Primary strong | `#1E3A8A` | Heading/text emphasis |
| Secondary | `#3B82F6` | Supporting data and focus states |
| Accent | `#D97706` | Attention accent, sparingly |
| Background | `#F5F7FB` | Application canvas |
| Surface | `#FFFFFF` | Cards, forms, tables, dialogs |
| Muted surface | `#EEF2F8` | Read-only data blocks |
| Foreground | `#172554` | Main text |
| Muted text | `#475569` | Help and secondary text |
| Border | `#D7DFEC` | Dividers and control boundaries |
| Success | `#166534` / `#ECFDF3` | Successful/healthy status |
| Warning | `#854D0E` / `#FFF7E6` | Warnings and Test Mode indicator |
| Danger | `#991B1B` / `#FEF2F2` | Failure and destructive state |
| Info | `#1E40AF` / `#EFF6FF` | Informational notices |
| Focus | `#1D4ED8` | Visible keyboard focus |

Status must include text or an icon with meaning; color alone is not sufficient.

## Typography

- Body: Fira Sans, then system sans-serif fallbacks.
- Headings and compact identifiers: Fira Code, then system monospace fallbacks.
- Do not download remote fonts at runtime; the console must remain useful offline.
- Body text starts at 16px. Supporting text should remain comfortably readable and not carry critical instructions below 12px.
- Keep long prose to a readable measure and use tabular alignment for operational values.

## Spacing, shape, and elevation

Use a 4px base rhythm and prefer 4, 8, 12, 16, 24, and 32px spacing steps. Use small/medium/large radii consistently (6/10/14px). Cards use a subtle border and low shadow; dialogs receive the strongest elevation. Avoid glass effects, gradients, oversized rounded cards, and layout-shifting hover transforms.

## Components and interaction

- **Shell:** persistent sidebar on wide screens; horizontally scrollable compact navigation on narrow screens; sticky utility bar where space permits.
- **Page header:** title, concise context, then one primary action if applicable.
- **Cards and sections:** group by task. Use consistent headings and avoid unnecessary nested cards.
- **Tables:** semantic header cells, readable rows, overflow within the table container on narrow viewports, pagination when needed.
- **Forms:** visible labels, persistent help, inline validation, clear disabled/read-only state, and predictable save/cancel actions.
- **Status:** shared success, warning, danger, info, pending, unavailable, disabled, incomplete, and Test Mode treatments.
- **Dialogs:** native labelled dialogs with keyboard dismissal and explicit cancel/confirm actions for consequential changes.
- **Feedback:** show loading, empty, success, validation, server-error, unavailable, and forbidden states without exposing stack traces or secret values.
- **Motion:** short, subtle transitions only. Respect `prefers-reduced-motion` and never hide content behind animation.
- **Touch:** primary controls should have at least 40px height, comfortable spacing, and distinct focus/hover/disabled states.

## Responsive and accessibility rules

- Use mobile-first breakpoints around 520px, 760px, and 1100px.
- Do not create page-level horizontal overflow; tables may scroll within their own wrapper.
- Preserve primary actions and readable form controls on tablet/narrow widths.
- Provide a skip link, semantic landmarks, visible `:focus-visible`, associated labels, table headers, and live status announcements where appropriate.
- Keyboard order follows the visual order. After client-side navigation, focus the main content heading/container.
- Keep status meaning available to assistive technology and do not rely on hover-only interaction.

## Security and demo-data rules

- Demo fixtures use synthetic names, addresses, and IDs only.
- Do not place secrets in the DOM, browser storage, URLs, logs, screenshots, or generated evidence.
- In the Prompt 16A offline preview, all writes and provider/source/mail tests remain in-memory simulations; do not connect these controls to real services.
- A preview role selector is illustrative and is not authentication or authorization.

## Source and verification

The UI/UX Pro Max search produced a Minimalism & Swiss Style recommendation for the dashboard category. Its unrelated landing-page pattern was rejected and replaced with the product's actual internal-console information architecture. Palette and typography are persisted here as the reusable product design system; the console-specific responsive/accessibility constraints are in `pages/console.md`.

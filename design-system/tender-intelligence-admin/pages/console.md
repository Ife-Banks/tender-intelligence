# Operations Console Page Rules

This override applies to the shared shell and all ten destinations.

- Keep navigation, utility controls, Test Mode state, status feedback, and content width consistent across screens.
- Use the Master blue palette; reserve success, warning, and danger colors for their matching states.
- Keep tables and forms usable at 375px. Constrain horizontal scrolling to table containers.
- Use system font fallbacks and no runtime font download, so the offline preview renders consistently.
- Keep preview role selection explicitly demonstrative; it does not establish identity or authorize real actions.
- All Prompt 16A actions are synthetic and in-memory. Do not call the real Admin API, worker, source sites, LLM providers, or mail provider.
- KB preview uses only a selected filename and note; it must not read file bytes.
- Provider test controls return fixed fixture outcomes and never send or transmit content.
- Use native controls and labelled dialogs. After route changes, move focus to the main content; preserve visible keyboard focus.
- Respect `prefers-reduced-motion`; no motion may hide data.

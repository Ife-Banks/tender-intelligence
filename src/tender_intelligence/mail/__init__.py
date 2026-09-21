"""Mail subsystem: provider abstraction, message model, and the attachment planner.

Follows docs/08 and PROJECT_RULES #9: business logic never depends on a concrete provider.
The planner picks what can be attached vs. what must become secure links based on provider
capabilities (v1.1 §5.7). The Sendlib adapter is deferred to Phase 1 (open decision O4).
"""
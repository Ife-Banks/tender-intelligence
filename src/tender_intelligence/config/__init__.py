"""Phase 0 configuration: env bootstrap, runtime config, YAML seed.

Design (``docs/12`` deployment, ``docs/04`` pipeline):
- Env prefixed ``TI_`` carries the operator-supplied, non-managed config
  (database URL, master key, dev recipient, storage dir, log level).
- The database ``Settings`` + ``ConfigChangeLog`` tables carry runtime-managed config;
  the worker re-reads it at the start of every run (PROJECT_RULES #20).
- ``seed_from_yaml`` loads the bootstrap YAML (sources, providers, recipients) into the
  database — the "write-only" config seed command.
"""

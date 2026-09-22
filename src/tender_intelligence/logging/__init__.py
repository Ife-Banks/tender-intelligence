"""Structured, machine-readable logging with correlation-ID context and secret redaction.

Implements PROJECT_RULES #15/#16 and ``docs/10`` security: every record carries timestamp,
correlation ID, source, stage, status and a machine-readable error code; secret values are
never emitted.
"""

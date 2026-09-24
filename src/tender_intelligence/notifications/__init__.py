"""Notification layer: routing, delivery service, Test Mode safety, alerts.

Test Mode is ON by default; while ON, tender emails may only go to the dev-alert list and
must carry the ``[TEST]`` subject prefix (PROJECT_RULES #11). The last active dev recipient
can never be removed (docs/03 Recipient). The notification service prepares messages from
verified verdict rows, plans attachments, delivers through the provider chain, persists every
attempt, and keeps undelivered messages as durable ``pending_retry`` outbox entries with a
persisted red-banner alert event (prompt 11).
"""

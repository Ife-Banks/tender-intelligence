"""Notification routing policies: Test Mode and recipient-list guards (docs/04, docs/08).

Test Mode is ON by default; while ON, tender emails may only go to the dev-alert list and
must carry the ``[TEST]`` subject prefix (PROJECT_RULES #11). The last active dev recipient
can never be removed (docs/03 Recipient).
"""

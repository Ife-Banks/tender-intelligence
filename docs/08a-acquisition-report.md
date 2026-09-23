Prompt 08 — Document Acquisition Report

1. Scope
   This stage consumes the ``TenderAttachment`` set produced by the discovery stage and turns
   each attachment into stored bytes plus ``Document`` database rows, following the documented
   order for safety (prompt 08 §2):
   
   download temporary bytes → validate → checksum → atomic commit to storage → update acquisition state.

2. Design decisions (all resolved, see prompt‑08 todo list and supporting docs)

   2.1 Checksum algorithm
   - SHA‑256 hex (``CHECKSUM_ALGORITHM = "sha256"``). The ``Document.checksum`` column is
     ``String(64)`` (docs/03 §3.2; migration 0001), exactly 64‑char hex. No other algorithm
     is compatible without a schema change.

   2.2 Size recording
   - Size is NOT a column on the ``Document`` entity (docs/03 confirms no size field). Size
     is recorded via ``StoredObject.size_bytes`` and surfaced on the ``AcquiredDocument`` DTO.
     The report notes this discrepancy; downstream extraction (prompt 09) accesses size from
     storage metadata, not from the row.

   2.3 Storage seam
   - Reuses the existing ``ObjectStorage`` contract (``src/tender_intelligence/storage/interface.py``)
     + ``LocalFileSystemStorage`` (``src/tender_intelligence/storage/local.py``). No new backend.
     - Keys: ``tenders/{tender_id}/attachments/{checksum}/{safe_name}`` via ``names.storage_key``.
     - ``sanitize_storage_name`` strips ``<>:"/\|?*``, control chars, trailing dots, Windows
       device names (``con/prn/aux/nul`` → prefixed ``_``), empty → ``"file"``, 200‑char max.
     - Write order: ``ObjectStorage.put`` first, then ``DocumentRepository`` update. This
       ensures DB state and storage state cannot falsely claim an acquisition that did not happen
       (prompt 08 §18).

   2.4 Archive handling (ZIP)
   - Safe recursive extraction under ``ZipLimits`` (max_members=200, max_total_uncompressed=512 MiB,
     max_compression_ratio=200, max_single_member=256 MiB, max_name_length=255,
     max_nested_depth=2). Per‑member protections: ``../`` traversal, absolute paths,
     Windows drive‑letter/UNC paths, symlink detection, name‑length limits, compression‑ratio
     bombs, single‑member size limits. Unsafe members are rejected and reported; whole‑archive
     limits abort the entire archive (category ``archive_limit``).
   - Nested archives followed to the depth limit; beyond that the inner archive is retained as
     a document (not unpacked). No unbounded recursion.

   2.5 Status policy
   - Only ``pending``/``downloaded``/``failed`` are written. ``extraction_status`` is never
     touched here (prompt 08 §14; prompt 09 owns extraction). ``DocumentRepository.set_download_status``
     validates membership against the enum; no invented statuses or transitions.

   2.6 Idempotency / reuse
   - Existing row with ``download_status='downloaded'`` + storage object present → silently
     reused, never re‑downloaded, never duplicated (prompt 08 §7).
   - First‑time concurrent race on identical bytes resolved by the ``(tender_id, checksum)``
     unique constraint: the winner’s row is reused, the loser rolls back and re‑selects.
   - Per‑document tolerance: one failed attachment never blocks its siblings (prompt 08 §11);
     partial vs complete acquisition distinguishable via ``AcquisitionResult.incomplete_inputs``.

   2.7 Failure categories (document_download_failed)
   - ``http_error``, ``transport``, ``unsupported_scheme``, ``invalid_url``,
     ``response_too_large``, ``invalid_response``, ``invalid_archive``, ``archive_limit``,
     ``unsafe_archive_member``, ``storage_failed``, ``persistence_failed``.
   - Each ``DocumentAcquisitionError`` carries ``error_code``, ``category``, ``retryable``,
     and ``context`` dict (never secrets).

   2.8 Correlation IDs
   - Threaded through structured log records and stage results (PROJECT_RULES #15). Generated
     at pipeline entry or supplied per‑call; ``correlation_context`` context manager scopes it.

   2.9 Mail / attachment planner
   - Provider chain (Sendlib first, then TBD providers 2/3). Attachment planner decides per‑message
     what fits (max_attachments, max_attachment_mb, max_message_mb) and what is sent as secure
     expiring links. Links expire configurable (default 14 days). Whole‑chain failure → queued
     ``pending_retry`` with health‑view red banner.

   2.10 Test mode
   - Global switch; when **on**, all tender emails go only to the dev list, subject prefixed ``[TEST]``.
     Default: ON until go‑live; turning off is an explicit, audit‑logged admin action.

3. Known trade‑offs / assumptions
   - Size not in DB: documented; future schema change would add a column if required.
   - Storage choice (O10 open): filesystem is acceptable default; flagged in report.
   - Per‑doc failure tolerance relies on fresh session per document; corner case: two
     concurrently identical fetches resolved by unique constraint, rare.
   - ZIP “member failure” does NOT cause the whole archive to fail unless a top‑level limit
     is exceeded; individual members may be rejected while the archive itself is stored.
   - Nested‑zip recursion cap of 2 means a ``top.zip → middle.zip → inner.zip`` chain:
     ``inner.zip`` is stored as a document and NOT unpacked (depth 3 > limit 2).

4. Report structure
   - §1 Scope, §2 Design decisions (sub‑sections 2.1‑2.10), §3 Known trade‑offs/assumptions,
     §4 Report structure (this section).

5. Next steps (outside this prompt)
   - Prompt 09 will own ``extraction_status``, ``extracted_text_ref``, and the AI‑based
     verdict engine. Prompt 10 will own worker orchestration / scheduling. This report may be
     extended in those prompts but is deliberately stable for prompt 08 deliverables.
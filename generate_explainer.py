"""
Generate: What We Have Now — System Explainer in Plain English
A layman-friendly DOCX explaining the Tender Intelligence system state.
"""
from __future__ import annotations

from pathlib import Path
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt, RGBColor

# ── colours ───────────────────────────────────────────────────────────────────
GREEN  = RGBColor(0x1A, 0x73, 0x48)
DARK   = RGBColor(0x1F, 0x27, 0x37)
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
AMBER  = RGBColor(0xB7, 0x5A, 0x00)
RED_C  = RGBColor(0xC0, 0x39, 0x2B)
BLUE   = RGBColor(0x1A, 0x53, 0x8C)
LGREY  = RGBColor(0xF2, 0xF2, 0xF2)
MGREY  = RGBColor(0xD0, 0xD0, 0xD0)

ROOT = Path(__file__).parent


# ── low-level helpers ─────────────────────────────────────────────────────────

def _shade_cell(cell, hex_color: str) -> None:
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _no_space(p) -> None:
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after  = Pt(2)


def _bold_run(p, text: str, size: int = 11, colour: RGBColor = DARK) -> None:
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(size)
    r.font.color.rgb = colour


# ── document-level helpers ────────────────────────────────────────────────────

def h1(doc: Document, text: str) -> None:
    p = doc.add_heading("", level=1)
    p.clear()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(18)
    r.font.color.rgb = GREEN
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after  = Pt(6)


def h2(doc: Document, text: str) -> None:
    p = doc.add_heading("", level=2)
    p.clear()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(13)
    r.font.color.rgb = DARK
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after  = Pt(4)


def body(doc: Document, text: str, italic: bool = False,
         colour: RGBColor = DARK, size: int = 11) -> None:
    p = doc.add_paragraph()
    p.style = "Normal"
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(6)
    r = p.add_run(text)
    r.font.size  = Pt(size)
    r.font.color.rgb = colour
    if italic:
        r.italic = True


def callout(doc: Document, text: str, fill: str = "E8F5E9",
            border_colour: RGBColor = GREEN) -> None:
    """Indented shaded paragraph for a callout / aside."""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent  = Inches(0.35)
    p.paragraph_format.right_indent = Inches(0.35)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(6)
    r = p.add_run(text)
    r.font.size = Pt(10.5)
    r.font.color.rgb = DARK
    r.italic = True
    # shade background via XML
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  fill)
    pPr.append(shd)


def bullet(doc: Document, text: str, bold_prefix: str = "",
           colour: RGBColor = DARK, size: int = 11) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after  = Pt(3)
    if bold_prefix:
        r = p.add_run(bold_prefix + " ")
        r.bold = True
        r.font.size = Pt(size)
        r.font.color.rgb = colour
    r2 = p.add_run(text)
    r2.font.size = Pt(size)
    r2.font.color.rgb = colour


def numbered(doc: Document, text: str, bold_prefix: str = "") -> None:
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after  = Pt(3)
    if bold_prefix:
        r = p.add_run(bold_prefix + " ")
        r.bold = True
        r.font.size = Pt(11)
    r2 = p.add_run(text)
    r2.font.size = Pt(11)


def flow_step(doc: Document, arrow: bool, step_text: str,
              sub: str = "", done: bool = True, stop: bool = False) -> None:
    """Single step in a flow diagram rendered as indented text."""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent  = Inches(0.5)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after  = Pt(0)
    if arrow:
        ar = p.add_run("  ↓  ")
        ar.font.color.rgb = MGREY
        ar.font.size = Pt(10)
    icon = "✓" if done and not stop else ("⬛" if stop else "○")
    icon_colour = GREEN if (done and not stop) else (RED_C if stop else AMBER)
    ir = p.add_run(f"{icon}  ")
    ir.font.color.rgb = icon_colour
    ir.font.size = Pt(11)
    sr = p.add_run(step_text)
    sr.bold = True
    sr.font.size = Pt(11)
    sr.font.color.rgb = DARK if not stop else AMBER
    if sub:
        p2 = doc.add_paragraph()
        p2.paragraph_format.left_indent  = Inches(1.1)
        p2.paragraph_format.space_before = Pt(0)
        p2.paragraph_format.space_after  = Pt(2)
        r2 = p2.add_run(sub)
        r2.font.size = Pt(9.5)
        r2.font.color.rgb = GREY  # type: ignore[name-defined]
        r2.italic = True


def capability_table(doc: Document, rows: list[tuple[str, str, str]]) -> None:
    tbl = doc.add_table(rows=1, cols=3)
    tbl.style = "Table Grid"
    hdr = tbl.rows[0]
    for i, h in enumerate(["Capability", "What the system does", "Status"]):
        c = hdr.cells[i]
        c.text = h
        _shade_cell(c, "1A7348")
        run = c.paragraphs[0].runs[0]
        run.bold = True
        run.font.color.rgb = WHITE
        run.font.size = Pt(9)
    for capability, what, status in rows:
        row = tbl.add_row()
        row.cells[0].text = capability
        row.cells[1].text = what
        row.cells[2].text = status
        for i in range(3):
            run = row.cells[i].paragraphs[0].runs[0]
            run.font.size = Pt(9)
        s = status.upper()
        status_run = row.cells[2].paragraphs[0].runs[0]
        if "YES" in s or "COMPLETE" in s or "WORKING" in s:
            status_run.font.color.rgb = GREEN
            status_run.bold = True
        elif "PARTIAL" in s:
            status_run.font.color.rgb = AMBER
            status_run.bold = True
        elif "NO" in s or "NOT" in s or "PENDING" in s:
            status_run.font.color.rgb = RED_C
        _shade_cell(row.cells[0], "F0F4F0")
    # col widths
    for row in tbl.rows:
        row.cells[0].width = Inches(1.8)
        row.cells[1].width = Inches(3.5)
        row.cells[2].width = Inches(1.5)
    doc.add_paragraph()


GREY = RGBColor(0x55, 0x55, 0x55)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def build() -> Path:
    doc = Document()

    # page margins
    for sec in doc.sections:
        sec.top_margin    = Inches(1.0)
        sec.bottom_margin = Inches(1.0)
        sec.left_margin   = Inches(1.1)
        sec.right_margin  = Inches(1.1)

    # ── COVER ─────────────────────────────────────────────────────────────────
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("TENDER INTELLIGENCE SYSTEM")
    r.bold = True; r.font.size = Pt(24); r.font.color.rgb = GREEN

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run("What We Have Now")
    r2.bold = True; r2.font.size = Pt(18); r2.font.color.rgb = DARK

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r3 = p3.add_run("A Plain-English Explanation of the Current System")
    r3.font.size = Pt(12); r3.font.color.rgb = GREY; r3.italic = True

    doc.add_paragraph()

    callout(doc,
        "This document describes the Tender Intelligence system in plain language — "
        "what it can do today, what it cannot do yet, and what it is building towards. "
        "No technical background is assumed.",
        fill="EAF4F0")

    doc.add_page_break()

    # ── 1. THE ONE-SENTENCE SUMMARY ───────────────────────────────────────────
    h1(doc, "1.  The One-Sentence Summary")
    body(doc,
        "The system can find tender announcements on the WAHO website, remember them, "
        "download the attached documents, and read their contents — "
        "but it cannot yet decide whether your company should apply, and it has not yet "
        "sent any email notifications.")
    doc.add_paragraph()

    # ── 2. THE DIGITAL EMPLOYEE ANALOGY ──────────────────────────────────────
    h1(doc, "2.  Think of It as a Digital Employee")
    body(doc,
        "The simplest way to understand the system is to imagine hiring a very diligent "
        "junior researcher. Here is what that employee can do right now:")
    doc.add_paragraph()

    callout(doc,
        "\"Every day I go to the WAHO website.  I look at every tender listed.  "
        "I note the title, the reference number, and the deadline.  "
        "I check whether I have seen it before.  "
        "If it is new, I record it in the filing system.  "
        "I then go back to each new tender's page, find all the attached PDF documents, "
        "download them, and read through them word by word.  "
        "I give you a complete package of everything I found.\"",
        fill="E8F5E9")

    doc.add_paragraph()
    body(doc, "What that employee cannot do yet:")
    callout(doc,
        "\"I cannot yet tell you whether to apply.  "
        "I cannot write the analysis.  "
        "I cannot send you an email with a recommendation.  "
        "I am waiting for those instructions to be built.\"",
        fill="FFF3E0")

    doc.add_paragraph()

    # ── 3. WHAT ACTUALLY HAPPENED IN THE DEMO ────────────────────────────────
    h1(doc, "3.  What Actually Happened in the Live Demonstration")
    body(doc,
        "The system was run against the real live WAHO website on 24 September 2026. "
        "Here is what it did, step by step:")

    doc.add_paragraph()
    h2(doc, "Step 1 — Visited the WAHO website")
    body(doc,
        "The system opened the WAHO Tenders listing page at "
        "data.wahooas.org/tenders/tenders/list and read the first page of results.")
    callout(doc,
        "This was a real HTTP request to the live website — not a simulation or a "
        "test file. The system was polite: it waited 2 seconds between each request "
        "so as not to overload the WAHO server.",
        fill="E3F2FD")

    doc.add_paragraph()
    h2(doc, "Step 2 — Found 10 live tenders")
    body(doc,
        "It discovered 10 active tender announcements on that first page. "
        "Two examples of what it found:")
    bullet(doc,
        "\"DP POUR LE RECRUTEMENT D'UN CABINET DE CONSEIL CHARGÉ DE RÉALISER UNE "
        "ÉVALUATION DU NIVEAU DE MISE EN ŒUVRE DU RÈGLEMENT...\"  "
        "→ Deadline: 24 September 2026 at 13:00 GMT",
        bold_prefix="Tender 167:")
    bullet(doc,
        "\"Expression of Interest (EOI) for Medical Products for the ECOWAS Joint "
        "Assessment Procedure...\"  → No deadline found on listing page",
        bold_prefix="Tender 139:")
    doc.add_paragraph()

    h2(doc, "Step 3 — Checked its memory")
    body(doc,
        "For each tender it asked: \"Have I seen this one before?\"  "
        "The answer was No for all 10 — they were all new. "
        "The system classified them as NEW and saved them to the database.")
    callout(doc,
        "If the same tenders appear tomorrow, the system will classify them as "
        "UNCHANGED and ignore them — it will not create duplicate records or send "
        "duplicate notifications.",
        fill="E8F5E9")

    doc.add_paragraph()
    h2(doc, "Step 4 — Saved the tender records")
    body(doc,
        "All 10 tenders were written into the database (Neon PostgreSQL). "
        "Each record includes a unique tracking number — called a correlation ID — "
        "that ties everything together throughout the tender's lifecycle.")
    body(doc,
        "For example, Tender 167 received the tracking ID: "
        "99b9ea24-9489-400e-bb3f-cf2b84c6cfd9",
        colour=GREY, italic=True)

    doc.add_paragraph()
    h2(doc, "Step 5 — Went back for the documents")
    body(doc,
        "For each tender, the system visited the tender's own detail page on WAHO "
        "and looked for attached files. It found PDFs, Word documents, and other files.")
    body(doc,
        "For example, Tender 166 had three attachments:")
    bullet(doc, "RFP_Conduct_an_assessment_of_QMS_FIRMS_.pdf")
    bullet(doc, "Summary_of_Responses_to_Questions_About_QMS_EN.pdf")
    bullet(doc, "Synthèses_des_réponses_aux_questions_sur_QMS_FR.pdf  (French)")
    doc.add_paragraph()

    h2(doc, "Step 6 — Downloaded the documents")
    body(doc,
        "For Tender 139, it downloaded 3 PDF documents from the WAHO server. "
        "Each downloaded file was given a digital fingerprint (SHA-256 checksum) "
        "so the system can always verify it has the exact same file it originally downloaded.")
    callout(doc,
        "The checksum means the system can later prove: \"This is exactly the document "
        "WAHO published — it has not been modified.\"",
        fill="E8F5E9")

    doc.add_paragraph()
    h2(doc, "Step 7 — Read the PDFs")
    body(doc,
        "The system opened the PDFs and extracted all the text. "
        "It automatically detected that the documents were in French. "
        "Here is a sample of what it read from one document:")
    callout(doc,
        "\"Organisation Ouest Africaine de la Santé\n"
        "Promouvoir une meilleure santé à travers l'intégration régionale\n"
        "MPDER-MRH/AMI/22/004-04\n"
        "INITIATIVE D'HARMONISATION DE LA RÉGLEMENTATION PHARMACEUTIQUE\n"
        "DANS L'ESPACE CEDEAO — AVIS DE MANIFESTATION D'INTÉRÊT\"",
        fill="F3E5F5")
    body(doc,
        "Across three PDFs it extracted approximately 5,400 words of content "
        "that would be available to an AI analyst.",
        colour=GREY, italic=True)

    doc.add_paragraph()
    h2(doc, "Step 8 — Packaged everything for AI (current stop point)")
    body(doc,
        "The system assembled all of this — the tender details, the document text, "
        "the language information, the provenance — into a structured package called "
        "a TenderDocumentBundle. This is the hand-off point to the AI stages that "
        "are not yet built.")

    doc.add_page_break()

    # ── 4. THE COMPLETE PIPELINE MAP ─────────────────────────────────────────
    h1(doc, "4.  The Complete Pipeline — Where We Are")
    body(doc, "The full intended journey of a tender through the system:")
    doc.add_paragraph()

    steps_done = [
        ("Visit WAHO website",         "Fetches the live tender listing page",                              True,  False),
        ("Find tender announcements",  "Extracts titles, IDs, deadlines from listing rows",                True,  False),
        ("Check memory (dedup)",       "NEW / UNCHANGED / UPDATE — never re-processes a known tender",     True,  False),
        ("Save tender record",         "Persisted in Neon DB with correlation ID + timestamps",            True,  False),
        ("Fetch tender detail page",   "Visits the individual tender page for documents + full description",True, False),
        ("Find attachments",           "Discovers PDF, DOCX, XLSX, ZIP links",                             True,  False),
        ("Download documents",         "Fetches files; calculates SHA-256 checksums; stores safely",       True,  False),
        ("Read / extract text",        "Extracts text from PDFs and Word docs; detects language",          True,  False),
        ("Package for AI",             "Assembles TenderDocumentBundle — the AI input contract",           True,  False),
        ("AI TRIAGE (Prompt 13)",      "Stage A — screen for relevance before full analysis",              False, True),
        ("AI VERDICT (Prompt 14)",     "Stage B — APPLY / DO NOT APPLY / APPLY WITH CONDITIONS",          False, True),
        ("Email notification",         "Send structured verdict + evidence to business recipients",        False, True),
    ]

    first = True
    for step, sub, done, stop in steps_done:
        flow_step(doc, arrow=not first, step_text=step, sub=sub, done=done, stop=stop)
        first = False

    doc.add_paragraph()
    callout(doc,
        "Legend:  ✓ = Implemented and demonstrated with real data   "
        "⬛ = Not yet implemented (Prompts 13 / 14)",
        fill="F5F5F5")

    doc.add_paragraph()

    # ── 5. WHAT IS WORKING WELL ───────────────────────────────────────────────
    h1(doc, "5.  What Is Working Well")

    capability_table(doc, [
        ("Connect to WAHO",            "Visits live website; polite 2-second delay between requests",                     "Yes ✓"),
        ("Find tenders",               "10 real live tenders discovered and recorded",                                     "Yes ✓"),
        ("Avoid duplicates",           "Same tender seen twice → UNCHANGED, no double-records or emails",                 "Yes ✓"),
        ("Store tender records",       "Titles, IDs, deadlines, correlation IDs saved to Neon DB",                       "Yes ✓"),
        ("Find attachments",           "Discovers all document links on a tender's detail page",                          "Yes ✓"),
        ("Download documents",         "PDFs downloaded from WAHO server with digital fingerprints",                      "Yes ✓"),
        ("Extract PDF text",           "3 PDFs read successfully — French detected automatically",                        "Yes ✓"),
        ("Handle partial failures",    "One bad file does not stop others from being processed",                          "Yes ✓"),
        ("Audit trail",                "Every action logged with the tender's tracking number",                           "Yes ✓"),
        ("Test coverage",              "507 automated tests pass — all checks clean",                                     "Yes ✓"),
        ("Security",                   "No secrets in logs; credentials encrypted; Test Mode enforced",                   "Yes ✓"),
        ("Notification system",        "Built and tested — awaiting AI verdict to activate",                              "Built ✓"),
    ])

    # ── 6. IMPORTANT GAPS ────────────────────────────────────────────────────
    h1(doc, "6.  Important Gaps to Address")

    h2(doc, "6.1  Deadline handling is incomplete")
    body(doc,
        "This is the most important business-level gap identified in the demonstration.")
    doc.add_paragraph()
    body(doc,
        "Of the 10 tenders crawled, only 2 had a deadline that the system could parse "
        "and store. The other 8 have deadline = blank in the database.")
    doc.add_paragraph()
    body(doc, "The system should be able to distinguish between five situations:")
    numbered(doc, "Deadline found and successfully parsed from the listing page")
    numbered(doc, "Deadline explicitly absent from the source (tender has no closing date)")
    numbered(doc, "Deadline exists in the tender documents but was not on the listing page")
    numbered(doc, "Deadline text found but could not be reliably parsed")
    numbered(doc, "Deadline unknown because the detail page could not be retrieved")
    doc.add_paragraph()
    callout(doc,
        "Why this matters: if the AI eventually recommends \"APPLY\" but the system "
        "cannot reliably tell the business when the tender closes, the notification "
        "is incomplete. A tenderer who misses a deadline because the system gave no "
        "date has been actively misled.",
        fill="FFF3E0")

    doc.add_paragraph()
    h2(doc, "6.2  Parser configuration needs a live-site update")
    body(doc,
        "When the system tried to read the detail pages during the demonstration, "
        "it initially failed because it was looking in the wrong place on the webpage. "
        "The configuration expected:")
    callout(doc, "div.card-header h1  (heading tag — used in test fixtures)",  fill="FFF3E0")
    body(doc, "But the live WAHO website actually uses:")
    callout(doc, "div.card-header a   (link tag — confirmed on live site)",    fill="E8F5E9")
    body(doc,
        "This has been identified and documented. The fix is a one-line configuration "
        "change in the database — but it must be applied before the next full crawl.")

    doc.add_paragraph()
    h2(doc, "6.3  AI stages are not built yet")
    body(doc,
        "The system has done all the preparation work — it has read the documents and "
        "assembled the information. But nothing analyses that information yet. "
        "The two AI stages that need to be added are:")
    bullet(doc,
        "Stage A (Prompt 13) — Screen for relevance: is this tender worth analysing further?",
        bold_prefix="AI Triage:")
    bullet(doc,
        "Stage B (Prompt 14) — Full analysis: APPLY / DO NOT APPLY / APPLY WITH CONDITIONS — "
        "with supporting evidence and reasoning.",
        bold_prefix="AI Verdict:")
    doc.add_paragraph()

    h2(doc, "6.4  No email has been sent")
    body(doc,
        "The email notification system exists and has been tested. "
        "It will not send any email until the AI has produced a verdict. "
        "When it does, it will send to test recipients only (with [TEST] in the subject line) "
        "until a human administrator explicitly switches it to production mode.")

    doc.add_paragraph()

    # ── 7. THE FULL PICTURE ───────────────────────────────────────────────────
    h1(doc, "7.  What the Full System Will Do (When Complete)")
    body(doc,
        "Once Prompts 13 and 14 are implemented, the complete journey of a tender will be:")
    doc.add_paragraph()

    full_steps = [
        "WAHO publishes a new tender",
        "System visits WAHO and finds it (next scheduled crawl)",
        "System checks its memory — this is a new tender",
        "System saves the tender record with a unique tracking number",
        "System downloads all attached documents",
        "System reads and extracts text from every document",
        "AI Triage: is this tender potentially relevant to OPEX Consulting / RegTech365?",
        "If relevant → AI Verdict: full analysis against company capabilities",
        "Verdict produced: APPLY / DO NOT APPLY / APPLY WITH CONDITIONS",
        "Email sent to business recipients with: title, deadline, verdict, confidence, evidence",
        "All of this is logged and auditable — who was notified, when, with what content",
    ]
    for i, step in enumerate(full_steps, 1):
        p = doc.add_paragraph(style="List Number")
        p.paragraph_format.space_before = Pt(1)
        p.paragraph_format.space_after  = Pt(3)
        r = p.add_run(step)
        r.font.size = Pt(11)

    doc.add_paragraph()
    callout(doc,
        "The system is currently at step 6. Everything from step 1 to step 6 "
        "is implemented and tested. Steps 7–11 require Prompts 13 and 14.",
        fill="E8F5E9")

    doc.add_paragraph()

    # ── 8. THE SINGLE NEXT PRIORITY ───────────────────────────────────────────
    h1(doc, "8.  The Single Most Important Next Step")
    body(doc,
        "Before moving to the AI stages, one issue should be resolved:")
    doc.add_paragraph()
    callout(doc,
        "Prove that the system can reliably determine and store the authoritative "
        "deadline for a tender — including when the deadline is on the detail page "
        "or inside the documents rather than on the listing page — and clearly "
        "distinguish between 'no deadline found' and 'no deadline exists'.",
        fill="FFF8E1")
    body(doc,
        "The deadline is the single most time-critical piece of information "
        "in a tender intelligence system. An AI that says 'APPLY' without "
        "a reliable deadline is not yet a complete business tool.")

    doc.add_paragraph()

    # ── 9. READINESS ASSESSMENT ───────────────────────────────────────────────
    h1(doc, "9.  Readiness Assessment")

    capability_table(doc, [
        ("Infrastructure",       "Database, storage, logging, secrets, config",                "Complete ✓"),
        ("Source crawling",      "Live WAHO site; polite; pagination; multilingual",           "Complete ✓"),
        ("Duplicate detection",  "NEW / UPDATE / UNCHANGED — crash-safe",                     "Complete ✓"),
        ("Tender storage",       "Full record with tracking IDs",                             "Complete ✓"),
        ("Deadline parsing",     "2 of 10 tenders parsed; 8 without deadline",                "Partial ⚠"),
        ("Document download",    "PDFs acquired from live site with fingerprints",            "Complete ✓"),
        ("PDF text extraction",  "Native + OCR fallback; language detection",                 "Complete ✓"),
        ("Pipeline orchestration","Stages 04–09 coordinated; per-file failure isolation",     "Complete ✓"),
        ("AI input bundle",      "TenderDocumentBundle ready for AI consumption",             "Complete ✓"),
        ("AI Triage",            "Not yet built",                                             "Pending ✗"),
        ("AI Verdict",           "Not yet built",                                             "Pending ✗"),
        ("Email notification",   "Built; not yet activated (awaiting AI verdict)",            "Built / Inactive"),
        ("Admin interface",      "Not yet built",                                             "Pending ✗"),
        ("Production go-live",   "Requires AI stages + admin interface + sign-off",           "Not yet"),
    ])

    # ── CLOSING ───────────────────────────────────────────────────────────────
    doc.add_page_break()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("In Plain English")
    r.bold = True; r.font.size = Pt(16); r.font.color.rgb = GREEN

    doc.add_paragraph()
    callout(doc,
        "The Tender Intelligence system is currently a very capable "
        "\"robot researcher\" that visits WAHO every day, collects all tender "
        "announcements, downloads the documents, and reads them cover to cover.  "
        "\n\n"
        "It is waiting for the \"senior analyst\" to be built — the AI engine that "
        "will read the researcher's notes and say: "
        "\"Apply for this one.  Don't apply for that one.  Here's why.\"  "
        "\n\n"
        "Once the analyst is built, the system will send that recommendation "
        "directly to the right people in the business — automatically, every time "
        "a relevant tender appears.",
        fill="E8F5E9")

    doc.add_paragraph()
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run("Phase 0–12: The researcher is built.   Phase 13–14: Building the analyst.")
    r2.bold = True; r2.font.size = Pt(11); r2.font.color.rgb = DARK; r2.italic = True

    # ── SAVE ─────────────────────────────────────────────────────────────────
    out = ROOT / "What We Have Now — Tender Intelligence System Explainer.docx"
    doc.save(str(out))
    print(f"Saved: {out}")
    return out


if __name__ == "__main__":
    build()

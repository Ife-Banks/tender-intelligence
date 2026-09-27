"""Business role of an extracted table (prompt 12 §7).

Why this exists
---------------
Prompt 12 §7 requires that tables carrying *deadlines, eligibility, experience requirements,
evaluation criteria and financial requirements* are not reduced to unusable text when structured
extraction is available. Preserving the matrix is only half of that: a matrix of
``["Weight", "40%"]`` rows is structurally intact and still unreadable unless a consumer can tell a
weighting scheme from a milestone schedule. So every
:class:`~tender_intelligence.processing.representation.ExtractedTable` carries a ``role`` in the
intermediate form, derived from the table's own vocabulary rather than from its position or
filename.

What the classification is, and is not
---------------------------------------
It is a **vocabulary hint**, not a detection claim. PDF and DOCX carry no machine-readable
semantics for "this table is the evaluation matrix" — nothing in either format says so. The
classifier therefore reports what the table's words suggest, records that provenance in
``role_source``, and returns ``"other"`` whenever the evidence is thin. ``"other"`` is the common
and unremarkable case and is never a failure. A consumer that disagrees with a role still has
``rows``; the role only tells it where to look first.

Three languages
---------------
Tender tables are published in the working language of the portal, and this project's required
languages are English, French and Portuguese (docs/06 §6.2). The vocabulary below carries all
three, because a French-only WAHO annex must not classify as ``"other"`` merely because its
headers are in French. Accents are stripped before comparison so ``"Critère"`` matches
``critere``, and matching is on whole words only, avoiding the substring collisions documented in
:mod:`tender_intelligence.processing.languages`.

Vocabulary is deliberately functional and domain-general: no company names, no portal vocabulary,
nothing that would tie this to one source (PROJECT_RULES #10). Generic words that appear across
several roles (``total``, ``reference``, ``date``) are included only where a role genuinely owns
them, and :data:`MIN_ROLE_HITS` prevents any single such word from deciding a role on its own.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

#: The roles a table can carry.
TABLE_ROLES: Final[tuple[str, ...]] = (
    "deadline",
    "eligibility",
    "evaluation",
    "financial",
    "experience",
    "other",
)

#: Roles prompt 12 §7 names explicitly. A consumer rendering content for a model should keep these
#: structured rather than collapsing them into prose, because losing a row loses a requirement, a
#: weight or a date.
BUSINESS_CRITICAL_ROLES: Final[frozenset[str]] = frozenset(
    {"deadline", "eligibility", "evaluation", "financial", "experience"}
)

#: Score needed before a role is reported. One matching word is not evidence: a schedule table
#: contains "date" and an eligibility table contains "required" incidentally. Two independent hits
#: on the same role is the floor.
MIN_ROLE_HITS: Final[int] = 2

#: A header cell naming the column counts double when *ranking* candidate roles. Tender tables put
#: the subject in the caption or the column headings, not in the body, and the caption is not part
#: of the extracted matrix. This weight never supplies evidence on its own — see
#: :func:`classify_table` — it only breaks ties between roles that have already cleared
#: :data:`MIN_ROLE_HITS` independently.
_HEADER_WEIGHT: Final[int] = 2

#: Data rows inspected below the header. The first body row carries the values ("40%", "30/03/2026")
#: and is the strongest content signal; deeper rows are prose and start matching everything.
_BODY_ROWS_INSPECTED: Final[int] = 1

_WORD: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+", re.UNICODE)

_ROLE_VOCABULARY: Final[dict[str, frozenset[str]]] = {
    "deadline": frozenset(
        {
            # en
            "deadline", "deadlines", "due", "milestone", "milestones", "submission",
            "clarification", "questions", "opening", "closing", "schedule", "calendar",
            "notification", "award",
            # fr
            "limite", "echeance", "echeances", "delai", "delais", "jalon", "jalons",
            "depot", "soumission", "reponse", "ouverture", "cloture", "fermeture",
            "calendrier", "attribution",
            # pt
            "prazo", "entrega", "marco", "abertura", "encerramento", "vigencia",
            "esclarecimento", "perguntas",
        }
    ),
    "eligibility": frozenset(
        {
            # en
            "eligibility", "eligible", "mandatory", "requirement", "requirements", "shall",
            "qualification", "qualifications", "disqualification", "exclusion", "exclusions",
            "excluded", "certificate", "registration", "registered", "licence", "license",
            "evidence", "required", "incapable",
            # fr
            "eligibilite", "conditions", "attestation", "certificat", "immatriculation",
            "inscription", "obligatoire", "obligatoires", "requis", "requisite", "requisites",
            "justificatif", "interdit", "dequalifiant", "exclu",
            # pt
            "elegibilidade", "elegivelidade", "requisito", "requisitos", "obrigatorio",
            "exclusao", "certidao", "comprovacao", "impedimento", "desqualificado",
        }
    ),
    "evaluation": frozenset(
        {
            # en
            "criterion", "criteria", "weight", "weighting", "score", "scoring", "points",
            "threshold", "rating", "assessment", "shortlist", "benchmark", "percentage",
            "marks", "subtotal",
            # fr
            "critere", "ponderation", "note", "notes", "seuil", "evaluation", "notation",
            "classement", "pourcentage", "bareme", "sous-total",
            # pt
            "criterio", "criterios", "ponderacao", "nota", "pontuacao", "limiar", "avaliacao",
            "classificacao", "percentual",
        }
    ),
    "financial": frozenset(
        {
            # en
            "financial", "price", "cost", "turnover", "revenue", "budget", "fee", "fees",
            "amount", "currency", "invoice", "payment", "guarantee", "capital", "balance",
            "audited", "unit-price", "bid-bond",
            # fr
            "financier", "financiere", "prix", "cout", "chiffre", "affaires", "montant",
            "devise", "facture", "paiement", "garantie", "offre", "valeur", "bilan", "comptable",
            # pt
            "financeiro", "financeira", "preco", "custo", "faturamento", "receita",
            "orcamento", "moeda", "fatura", "pagamento", "garantia", "oferta", "balanco",
            "auditoria",
        }
    ),
    "experience": frozenset(
        {
            # en
            "experience", "experienced", "years", "reference", "references", "contract",
            "contracts", "portfolio", "track-record", "history", "similar", "comparable",
            "staff", "personnel", "certification", "certified", "specialist", "specialists",
            # fr
            "experiences", "annees", "contrat", "contrats", "realisation", "realisations",
            "portefeuille", "equipe", "certifie", "expert", "specialiste", "referencement",
            # pt
            "experiencia", "experiente", "anos", "referencia", "referencias", "contrato",
            "contratos", "comparavel", "certificacao", "certificado", "especialista",
        }
    ),
}


def _fold(text: str) -> frozenset[str]:
    """Lowercase, accent-stripped whole-word tokens, so ``Critère`` matches ``critere``.

    Digits are excluded by the pattern: ``40%`` and ``150000`` are values, not vocabulary, and
    counting them would let any table of numbers claim a role.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return frozenset(token.lower() for token in _WORD.findall(plain))


def classify_table(headers: list[str], rows: list[list[str]]) -> tuple[str, str]:
    """Classify a table as ``(role, role_source)``.

    *rows* is the **complete** matrix, exactly as
    :class:`~tender_intelligence.processing.representation.ExtractedTable.rows` stores it, so when
    *headers* is non-empty the header row is ``rows[0]`` and the body begins after it. Reading the
    header again from ``rows[0]`` would double-count the same evidence and skip every data row.

    ``role`` is a member of :data:`TABLE_ROLES`; ``role_source`` is ``"vocabulary"`` when the
    table's own words decided it and ``"none"`` when they did not. Headers and the first
    :data:`BODY_ROWS_INSPECTED` body rows are considered — tender tables put the subject in the
    caption or the column headings, and the caption is not available in the extracted matrix.

    Two rules, deliberately kept apart:

    * **Evidence** is the count of *distinct* matching tokens a role finds. That count must reach
      :data:`MIN_ROLE_HITS`. A word repeated across a header row is one piece of evidence, and a
      single generic word ("required", "total") is never enough.
    * **Ranking** among roles that clear the evidence bar is the header-weighted score, so a term
      in a column heading outranks the same term in a body cell.

    Ties resolve to the earliest role in :data:`TABLE_ROLES`, so the result is deterministic rather
    than dependent on dictionary ordering.
    """
    header_tokens: frozenset[str] = frozenset()
    for cell in headers:
        header_tokens |= _fold(cell)
    body_tokens: frozenset[str] = frozenset()
    start = 1 if headers else 0
    for row in rows[start : start + _BODY_ROWS_INSPECTED]:
        for cell in row:
            body_tokens |= _fold(cell)
    tokens = header_tokens | body_tokens
    if not tokens:
        return "other", "none"

    matches = {
        role: tokens & words for role, words in _ROLE_VOCABULARY.items()
    }
    qualified = {
        role: _HEADER_WEIGHT * len(found & header_tokens) + len(found & body_tokens)
        for role, found in matches.items()
        if len(found) >= MIN_ROLE_HITS
    }
    if not qualified:
        return "other", "none"
    best = max(qualified, key=lambda role: (qualified[role], -TABLE_ROLES.index(role)))
    return best, "vocabulary"


def role_is_business_critical(role: str) -> bool:
    """Whether *role* is one of the table kinds prompt 12 §7 protects from textual flattening."""
    return role in BUSINESS_CRITICAL_ROLES

""":mod:`tender_intelligence.processing.languages` — document language tagging (prompt 09 §10).

Language detection here is **metadata only**: it never alters, normalises or translates the
source content (prompt 09 §10; docs/06 §6.2). Tenders are frequently multilingual, so detection
runs per document and the bundle aggregates the distinct languages found.

Detection is token-based stopword scoring rather than substring matching. Substring matching is
unusable for this job: a French set containing ``le`` matches inside ``able``/``simple``/``table``,
and a Portuguese set containing the single letters ``o``/``a``/``e`` matches nearly every English
word, so Portuguese would "win" on any document. Tokens are compared whole.

Limitations (documented, not hidden): short documents and documents dominated by
numbers/proper nouns fall below :data:`MIN_TOKENS` and are reported as unknown rather than
guessed. Only EN/FR/PT are supported because docs/06 §6.2 names exactly those three.
"""

from __future__ import annotations

import re
from typing import Final

#: Languages the specification requires (docs/06 §6.2, prompt 09 §10).
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("en", "fr", "pt")

#: Below this many word tokens no guess is trustworthy, so the result is unknown.
MIN_TOKENS: Final[int] = 20

#: Minimum fraction of tokens that must match a language's stopword set.
MIN_SCORE: Final[float] = 0.04

#: Unicode-aware word tokens: letters only, so digits and punctuation never form tokens.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+", re.UNICODE)

# Deliberately function words only (no domain vocabulary), so the detector stays general.
# Ambiguous single letters are excluded: English "a" is also Portuguese/French; including it in
# any set would systematically skew short English text.
_STOPWORDS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset(
        {
            "the", "and", "of", "to", "for", "in", "on", "at", "is", "are", "was", "were",
            "be", "been", "being", "shall", "should", "will", "would", "with", "by", "or",
            "as", "not", "this", "that", "these", "those", "from", "all", "must", "may",
            "can", "which", "has", "have", "had", "any", "it", "its", "their", "they", "we",
            "you", "if", "when", "where", "who", "whom", "than", "then", "there", "here",
            "such", "other", "more", "most", "no", "only", "also", "under", "over", "between",
            "within", "without", "after", "before", "during", "each", "both", "some", "into",
        }
    ),
    "fr": frozenset(
        {
            "le", "la", "les", "des", "du", "de", "et", "est", "sont", "etait", "etaient",
            "etre", "pour", "dans", "sur", "par", "que", "qui", "quoi", "dont", "ou", "avec",
            "aux", "au", "un", "une", "ne", "pas", "plus", "cette", "ces", "ce", "cet", "son",
            "sa", "ses", "leur", "leurs", "notre", "nos", "votre", "vos", "il", "elle", "ils",
            "elles", "nous", "vous", "se", "si", "comme", "mais", "donc", "car", "ni", "aussi",
            "tres", "tout", "tous", "toute", "toutes", "meme", "entre", "sous", "sans", "apres",
            "avant", "pendant", "chaque", "deux", "trois", "peut", "peuvent", "doit", "doivent",
            "avoir", "ont", "ete", "fait", "faire",
        }
    ),
    "pt": frozenset(
        {
            "o", "os", "um", "uma", "uns", "umas", "do", "da", "dos", "das", "no", "na",
            "nos", "nas", "ao", "aos", "pelo", "pela", "pelos", "pelas", "e", "sao", "era",
            "eram", "ser", "estar", "para", "por", "que", "quem", "cujo", "onde", "com", "sem",
            "sob", "sobre", "entre", "se", "nao", "mais", "ou", "este", "esta", "estes",
            "estas", "esse", "essa", "isso", "aquele", "aquela", "seu", "sua", "seus", "suas",
            "nosso", "nossa", "eu", "tu", "ele", "ela", "eles", "elas", "como", "mas",
            "porque", "pois", "tambem", "muito", "todo", "todos", "toda", "todas", "mesmo",
            "depois", "antes", "durante", "cada", "dois", "tres", "pode", "podem", "deve",
            "devem", "ter", "tem", "foi", "foram", "estao", "ha",
        }
    ),
}


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def detect_language(text: str) -> tuple[str | None, float | None]:
    """Best-guess language of *text* as ``(language, confidence)``.

    Returns ``(None, None)`` when there is too little text to judge (prompt 09 §10 requires
    metadata, not a confident-looking fabrication).
    """
    tokens = _tokens(text)
    if len(tokens) < MIN_TOKENS:
        return None, None

    total = len(tokens)
    scores = {
        language: sum(1 for token in tokens if token in stopwords) / total
        for language, stopwords in _STOPWORDS.items()
    }
    best = max(scores, key=lambda language: scores[language])
    best_score = scores[best]
    if best_score < MIN_SCORE:
        return None, None
    return best, round(best_score, 4)


def normalize_language(value: str | None) -> str | None:
    """Normalise a language tag to a lowercase primary subtag (``fr-FR`` → ``fr``)."""
    if not value:
        return None
    primary = value.replace("_", "-").split("-")[0].strip().lower()
    return primary or None


def bundle_languages(languages: list[str | None]) -> list[str]:
    """Sorted distinct language tags for a bundle, ignoring unknown documents (prompt 09 §10)."""
    return sorted({normalized for value in languages if (normalized := normalize_language(value))})

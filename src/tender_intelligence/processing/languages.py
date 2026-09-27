""":mod:`tender_intelligence.processing.languages` — document language tagging (prompt 09 §10).

Language detection here is **metadata only**: it never alters, normalises or translates the
source content (prompt 09 §10; docs/06 §6.2). Tenders are frequently multilingual, so detection
runs per document and the bundle aggregates the distinct languages found.

Detection is token-based stopword scoring rather than substring matching. Substring matching is
unusable for this job: a French set containing ``le`` matches inside ``able``/``simple``/``table``,
and a Portuguese set containing the single letters ``o``/``a``/``e`` matches nearly every English
word, so Portuguese would "win" on any document. Tokens are compared whole.

The initial acceptance-test languages are EN/FR/PT (docs/06 §6.2). Additional languages can be
added to :data:`_STOPWORDS` without source-code changes to the detection logic. The system can
represent any valid language code; detection coverage depends on the stopword sets configured.

Limitations (documented, not hidden): short documents and documents dominated by
numbers/proper nouns fall below :data:`MIN_TOKENS` and are reported as unknown rather than
guessed. Languages without a configured stopword set cannot be detected (returns unknown).
"""

from __future__ import annotations

import re
from typing import Final

#: Initial acceptance-test languages (docs/06 §6.2, prompt 09 §10).
#: These are the languages tested during acceptance, NOT a hard-coded limitation.
#: Additional languages can be added to _STOPWORDS without changing detection logic.
INITIAL_ACCEPTANCE_LANGUAGES: Final[tuple[str, ...]] = ("en", "fr", "pt")

#: Backwards-compatible alias.
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = INITIAL_ACCEPTANCE_LANGUAGES

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
    # Additional languages — extensible without code changes to detection logic.
    "de": frozenset(
        {
            "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines",
            "einem", "einen", "und", "oder", "aber", "auch", "als", "am", "an", "auf",
            "aus", "bei", "bis", "durch", "für", "gegen", "im", "in", "ist", "mit",
            "nach", "nicht", "noch", "nur", "ohne", "sehr", "sich", "sie",
            "so", "über", "um", "unter", "vom", "von", "vor", "wenn", "werden",
            "wie", "wird", "zu", "zum", "zur", "zwei", "drei", "hat", "haben", "kann",
            "können", "muss", "müssen", "soll", "sollen", "will", "wollen", "dass",
            "daß", "wurde", "wurden", "worden", "sein", "war", "waren",
        }
    ),
    "es": frozenset(
        {
            "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "en",
            "y", "o", "u", "a", "al", "con", "sin", "por", "para", "que", "quien",
            "cuyo", "cuya", "donde", "cuando", "como", "pero", "mas", "aunque",
            "si", "no", "sí", "también", "tampoco", "muy", "mucho", "muchos",
            "mucha", "muchas", "todo", "todos", "toda", "todas", "otro", "otros",
            "otra", "otras", "mismo", "misma", "mismos", "mismas", "ser", "estar",
            "fue", "fueron", "era", "eran", "ha", "han", "hay", "tiene", "tienen",
            "puede", "pueden", "debe", "deben", "hacer", "hecho", "entre", "sobre",
            "desde", "hasta", "durante", "cada", "dos", "tres", "es", "son", "está",
            "están", "estaba", "estaban",
        }
    ),
    "it": frozenset(
        {
            "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "del",
            "della", "dei", "delle", "in", "con", "su", "per", "tra", "fra", "e",
            "o", "a", "al", "allo", "alla", "ai", "agli", "alle", "che", "chi",
            "cui", "dove", "quando", "come", "ma", "più", "meno", "molto", "tanto",
            "troppo", "tutto", "tutti", "tutta", "tutte", "altro", "altri", "altra",
            "altre", "stesso", "stessa", "stessi", "stesse", "essere", "sono", "è",
            "era", "erano", "stato", "stata", "stati", "state", "ha", "hanno",
            "può", "possono", "deve", "devono", "fare", "fatto", "senza", "sotto",
            "sopra", "dopo", "prima", "ogni",
            "due", "tre", "questo", "questa", "questi", "queste", "quello", "quella",
            "quelli", "quelle", "suo", "sua", "suoi", "sue", "mio", "mia", "miei",
            "mie", "tuo", "tua", "tuoi", "tue", "nostro", "nostra", "nostri", "nostre",
            "vostro", "vostra", "vostri", "vostre",
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

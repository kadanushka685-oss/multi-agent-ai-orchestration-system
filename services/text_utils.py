"""Small, deterministic text helpers shared by the agents."""
from __future__ import annotations

import re

# Common English words plus generic task and analysis vocabulary. These carry no evidential
# content, so they are ignored when matching statements to tasks and claims to sources.
STOPWORDS = frozenset(
    """
    a about above across after again against all also although an and any are as at be because
    been before being between both but by can could did do does each either few for from further
    had has have having he her here hers him his how i if in into is it its itself just more most
    much must my no nor not now of off on once only or other our out over own same she should so
    some such than that the their them then there these they this those through to too under until
    up upon us very was we were what when where which while who whom why will with within without
    would you your
    compare comparison write writing written report brief summary summarise summarize analyse
    analyze analysis analyst recommendation recommend executive create produce provide give list
    identify highlight highlighting main key difference differences using based short use include
    includes ignore previous instruction reveal poem source sources statement statements finding
    findings insight insights figure figures topic topics document documents sample synthetic
    frequently discussed reported retrieved span range versus disagree disagrees related different
    units bases care trend trends highest lowest percentage percent among share
    """.split()
)

# Domain synonyms so that "Europe" and "European" match the same evidence.
SYNONYMS = {"europe": "european", "asia": "asian"}

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_PERCENT = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s?%")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def normalise_word(word: str) -> str:
    """Map synonyms and strip a plural 's' so 'vehicles' matches 'vehicle'."""
    word = SYNONYMS.get(word, word)
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def keywords(text: str) -> list[str]:
    """Distinct content words in first-seen order, normalised and stopword-free."""
    seen: dict[str, None] = {}
    for raw in _WORD.findall(text or ""):
        word = raw.lower().strip("-'")
        if len(word) < 3 or word in STOPWORDS:
            continue
        normalised = normalise_word(word)
        if len(normalised) < 3 or normalised in STOPWORDS:
            continue
        seen.setdefault(normalised, None)
    return list(seen)


def keyword_set(text: str) -> set[str]:
    return set(keywords(text))


def split_sentences(text: str) -> list[str]:
    flat = " ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    return [part.strip() for part in _SENTENCE_END.split(flat) if part.strip()]


def extract_numbers(text: str) -> list[float]:
    """Every number in the text as a float. Thousand separators are removed."""
    values: list[float] = []
    for token in _NUMBER.findall(text or ""):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def _is_year(token: str, value: float) -> bool:
    """A bare 4-digit number between 1900 and 2100 is a year. '2,000' is an amount, not a year."""
    return "," not in token and 1900 <= value <= 2100


def primary_figure(text: str) -> tuple[float, str] | None:
    """The figure a statement is mainly about, with its kind: 'percent' or 'number'.

    The first percentage wins. Otherwise the first number that is not a year is used.
    """
    percent = _PERCENT.search(text or "")
    if percent:
        return float(percent.group(1).replace(",", "")), "percent"
    for token in _NUMBER.findall(text or ""):
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if not _is_year(token, value):
            return value, "number"
    return None


def primary_number(text: str) -> float | None:
    figure = primary_figure(text)
    return figure[0] if figure else None


def jaccard(first: set, second: set) -> float:
    union = first | second
    return len(first & second) / len(union) if union else 0.0

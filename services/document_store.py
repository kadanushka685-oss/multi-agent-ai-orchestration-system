"""Local knowledge base. The Research Agent searches these sample documents only.

No web access is performed. Every document is split into statements that the Research Agent
can cite by id, so the report can always show where a claim came from.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.settings import DOCUMENTS_DIR
from services.text_utils import keyword_set, split_sentences

MIN_STATEMENT_CHARS = 25


@dataclass(frozen=True)
class Statement:
    document: str
    position: int
    text: str


def _plain_text(markdown: str) -> str:
    """Drop headings and list markers, keeping the sentence text."""
    lines = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line.lstrip("-*").strip())
    return " ".join(lines)


class DocumentStore:
    """In-memory index of the sample documents."""

    def __init__(self, directory=DOCUMENTS_DIR):
        self.directory = Path(directory)
        self._statements: list[Statement] = []
        self._document_terms: dict[str, set[str]] = {}
        paths = []
        if self.directory.exists():
            paths = sorted(self.directory.glob("*.md")) + sorted(self.directory.glob("*.txt"))
        for path in paths:
            sentences = split_sentences(_plain_text(path.read_text(encoding="utf-8")))
            terms: set[str] = set()
            for position, sentence in enumerate(sentences):
                if len(sentence) < MIN_STATEMENT_CHARS:
                    continue
                self._statements.append(Statement(path.name, position, sentence))
                terms |= keyword_set(sentence)
            self._document_terms[path.name] = terms

    @property
    def documents(self) -> list[str]:
        return list(self._document_terms)

    def __len__(self) -> int:
        return len(self._statements)

    def search(self, terms, top_k: int = 6) -> list[dict]:
        """Statements that share at least one term, best matches first."""
        wanted = {term for term in terms if term}
        if not wanted:
            return []
        scored = []
        for statement in self._statements:
            overlap = wanted & keyword_set(statement.text)
            if overlap:
                scored.append((len(overlap), statement))
        scored.sort(key=lambda item: (-item[0], item[1].document, item[1].position))
        return [
            {"score": score, "document": s.document, "position": s.position, "text": s.text}
            for score, s in scored[:top_k]
        ]

    def document_frequency(self, term: str) -> int:
        """In how many documents a normalised term appears."""
        return sum(1 for terms in self._document_terms.values() if term in terms)

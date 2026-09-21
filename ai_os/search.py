"""Brute-force semantic search over normalized embedding vectors."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Doc:
    id: str
    text: str
    vector: list[float]


class Index:
    def __init__(self) -> None:
        self.docs: list[Doc] = []

    def add(self, id: str, text: str, vector: list[float]) -> None:
        self.docs.append(Doc(id, text, normalize(vector)))

    def search(self, query_vector: list[float], k: int = 5) -> list[tuple[float, str, str]]:
        q = normalize(query_vector)
        ranked = sorted(self.docs, key=lambda d: (dot(q, d.vector), d.id))
        return [(dot(q, d.vector), d.id, d.text) for d in ranked[-k:]][::-1]


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def magnitude(v: list[float]) -> float:
    return dot(v, v) ** 0.5


def normalize(v: list[float]) -> list[float]:
    m = magnitude(v)
    return [x / m for x in v] if m else v
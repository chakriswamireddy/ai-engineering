"""Day 10 §7 — the offline code stub: a mini Saleor source tree + builder.

Mirrors `tools/rag_debug.py`'s `STUB_DOCS` / `build_stub_store` for the code
corpus. The sources are a *representative* slice of Saleor's layout — the
modules and symbols the code fixtures (`ai_os/code_questions.yaml`) name — so
the mechanics (AST extraction, `module#Symbol` membership, filter axes, the
fused-score gate) are provable with zero API calls and zero network.

Embedding is a deterministic token-hash: each `code_tokens` token maps to a
fixed pseudo-random vector (sha256), a text's vector is the normalized sum.
Identical text → identical vector; texts sharing identifiers land nearer each
other — enough to assert on *which symbol* retrieves without any model. It is
deliberately meaning-free (semantics need the real embedder), but it is
identifier-aware, which is exactly the property Day 10 introduced.
"""
from __future__ import annotations

import hashlib

from ai_os.contract import Completion, Embedding, Usage
from ai_os.ingest_code import interface_text, symbols_from_source
from ai_os.ledger import Ledger
from ai_os.rag import Chunk, RagStore
from ai_os.retrieval import code_tokens

EMBED_DIM = 64


def _token_vector(token: str, dim: int) -> list[float]:
    vec: list[float] = []
    counter = 0
    while len(vec) < dim:
        h = hashlib.sha256(f"{token}:{counter}".encode()).digest()
        vec.extend((b - 127.5) / 127.5 for b in h)
        counter += 1
    return vec[:dim]


class HashEmbedGateway:
    """Deterministic identifier-hash embedder + canned chat. No network.

    Same contract shape as `ModelGateway`/`StubGateway`: `embed` returns
    `Embedding`s with a usage line, `chat` returns a grounded-looking
    `Completion`, and a `Ledger` rides along so cost accounting is uniform.
    """

    MODEL = "stub-code-hash"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim
        self.ledger = Ledger()
        self.last_provider = "openai"
        self.last_attempts = 1
        self.last_latency_ms = 1.0

    def embed(self, texts: list[str]) -> list[Embedding]:
        out: list[Embedding] = []
        for text in texts:
            toks = code_tokens(text)
            vec = [0.0] * self.dim
            for tok in toks:
                tv = _token_vector(tok, self.dim)
                for i in range(self.dim):
                    vec[i] += tv[i]
            norm = sum(x * x for x in vec) ** 0.5
            if norm:
                vec = [x / norm for x in vec]
            out.append(Embedding(text=text, vector=vec, model=self.MODEL,
                                 usage=Usage(len(toks), 0, len(toks))))
        return out

    def chat(self, messages, **kw) -> Completion:
        last = messages[-1]["content"]
        return Completion(
            text=f"stub code answer: grounded on the supplied source ({last[:40]}\u2026)",
            model="gpt-4o-mini", finish_reason="stop", usage=Usage(12, 24, 36))


# --------------------------------------------------------------- stub corpus

CODE_SOURCES: dict[str, str] = {
    "saleor/checkout/complete_checkout.py": '''"""Checkout completion: turn a Checkout into an Order."""
from saleor.order.utils import create_order_from_checkout
from saleor.payment.gateway import process_payment
from saleor.webhook.event_types import WebhookEventAsyncType

ORDER_CREATED = WebhookEventAsyncType.ORDER_CREATED


def create_order_from_checkout(checkout, order_data):
    """Create an Order from a completed Checkout — the order creation step."""
    order = Order.objects.create_from_checkout(checkout, order_data)
    return order


def complete_checkout(checkout, payment_data, order_data):
    """Complete a checkout: validate, charge the payment, create the order.

    This is the domain service behind the CheckoutComplete mutation — the
    mechanism, where the mutation is the API entry point.
    """
    order = create_order_from_checkout(checkout, order_data)
    process_payment(payment_data)
    return order
''',
    "saleor/graphql/checkout/mutations/checkout_complete.py": '''"""GraphQL mutation: complete a checkout."""
from saleor.checkout.complete_checkout import complete_checkout


class CheckoutComplete(BaseMutation):
    """Completes the checkout and returns the created order."""

    def perform_mutation(self, checkout_token):
        """Execute the mutation: complete the checkout into an order."""
        return complete_checkout(checkout_token, None, None)
''',
    "saleor/payment/gateway.py": '''"""Payment gateway: process payments and request actions."""
from saleor.webhook.event_types import WebhookEventAsyncType


def process_payment(payment_data):
    """Process a payment and fire the success webhook when it succeeds."""
    fire_webhook(WebhookEventAsyncType.PAYMENT_SUCCESS)
    return payment_data
''',
    "saleor/webhook/event_types.py": '''"""Async webhook event types."""


class WebhookEventAsyncType:
    """The set of async webhook event types fired by Saleor."""

    ORDER_CREATED = "order_created"
    PAYMENT_SUCCESS = "payment_success"
''',
    "saleor/checkout/models.py": '''"""The Checkout Django model."""
from django.db import models


class Checkout(models.Model):
    """The Checkout identifier here names the Django model."""

    token = models.UUIDField()
    email = models.EmailField()

    def __str__(self):
        return self.token
''',
    "saleor/graphql/checkout/types.py": '''"""GraphQL types for checkout."""
import graphene


class Checkout(graphene.ObjectType):
    """The Checkout identifier here names the GraphQL API type."""

    token = graphene.String()
''',
    "saleor/checkout/tests/test_checkout_complete.py": '''"""Tests for checkout completion."""
from saleor.checkout.complete_checkout import complete_checkout


def test_complete_checkout_when_checkout_doesnt_exists(checkout):
    """Test that completing a missing checkout does not create an order."""
    assert complete_checkout(checkout, None, None) is not None
''',
    "saleor/checkout/factories.py": '''"""Test factories for checkout."""
from saleor.checkout.models import Checkout


class CheckoutFactory:
    """Factory that builds a Checkout for tests."""

    def create_checkout(self):
        """Create a checkout for a test."""
        return Checkout()

    def create_checkout_with_lines(self):
        """Create a checkout with lines for a test."""
        return Checkout()
''',
}


def build_stub_code_store(gw: HashEmbedGateway,
                          sources: dict[str, str] | None = None) -> RagStore:
    """Extract every stub module's symbols and embed each interface text.

    The interface (name + signature + docstring) is embedded; the body rides as
    `text` for the context builder — the Day-10 split, exercised on the stub.
    """
    store = RagStore()
    for module, source in (sources or CODE_SOURCES).items():
        for sym in symbols_from_source(source, module):
            vec = gw.embed([interface_text(sym)])[0].vector
            if not store.embedding_model:
                store.embedding_model = gw.MODEL
            store.add(Chunk(
                chunk_id=sym.doc_id, doc_id=sym.doc_id, source=module,
                heading=sym.symbol, position=sym.first_line, text=sym.body,
                vector=vec, symbol=sym.symbol, kind=sym.kind,
                signature=sym.signature, docstring=sym.docstring,
                imports=sym.imports, dependencies=sym.dependencies))
    return store

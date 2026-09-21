"""Deterministic verify of ai_os.search: no embedding model, no deps."""
import sys

sys.path.insert(0, ".")
from ai_os.search import Index, dot, normalize

docs = {
    "commerce": [1.0, 0.2, 0.1],
    "headless": [0.9, 0.3, 0.0],
    "cookbook": [0.0, -0.2, 1.0],
}

idx = Index()
for id_, v in docs.items():
    idx.add(id_, f"text of {id_}", v)

# both unit length -> cosine == dot
a, b = normalize(docs["commerce"]), normalize(docs["headless"])
assert abs(dot(a, b) - 0.9875414397573882) < 1e-9, dot(a, b)

# scale invariance: 2*commerce is the same direction -> cosine stays 1.0
two = [2 * x for x in docs["commerce"]]
assert abs(dot(normalize(docs["commerce"]), normalize(two)) - 1.0) < 1e-9

# verified pairs -- TRUE values for THESE vectors (recomputed, not from the sheet)
pairs = {
    ("commerce", "commerce"): 1.0,
    ("commerce", "headless"): 0.9875,
    ("commerce", "cookbook"): 0.0574,
    ("headless", "cookbook"): -0.0620,
}
for (id1, id2), expected in pairs.items():
    scored = idx.search(docs[id1], k=100)
    score = next(s for s, sid, _ in scored if sid == id2)
    assert abs(score - expected) < 0.002, (id1, id2, score, expected)

# search top-1 matches itself
for id_ in docs:
    top = idx.search(docs[id_], k=1)[0]
    assert top[1] == id_, (id_, top)

# edge cases
assert Index().search([1.0, 0.0, 0.0]) == []
assert len(idx.search(docs["commerce"], k=100)) == 3          # k > n clamps
assert idx.search([0.0, 0.0, 0.0])[0][0] == 0.0               # zero vector scores 0
assert idx.search([100 * x for x in docs["cookbook"]])[0][1] == "cookbook"  # raw (non-normalized) query

# the paraphrase demo: query with no shared meaning in the obvious doc
studio = Index()
studio.add("doc-3", "Headless Saleor separates backend from storefront", docs["headless"])
studio.add("doc-1", "How to configure payment providers", docs["commerce"])
top = studio.search([0.9, 0.3, 0.0])
assert top[0][1] == "doc-3"

print("hand-built vectors:")
idx = Index()
for id_, v in docs.items():
    idx.add(id_, f"text of {id_}", v)
fmt = "{:<18} {:<24} {:>7}".format("pair", "query->", "top1?")
print(fmt)
for id_, v in docs.items():
    top = idx.search(v, k=1)[0]
    print(f"{id_:<18} {id_:<24} {'YES' if top[1]==id_ else 'NO'}  score={top[0]:.3f}")

print("\nparaphrase demo: query == headless vector (no shared keywords case)")
for qid, q in docs.items():
    top = studio.search(q)[0]
    print(f"  query({qid}) -> doc-3 correct: {top[1]=='doc-3'}  score={top[0]:.3f}")

print("\nscale invariance: 2*commerce vs commerce ->", round(dot(normalize(docs["commerce"]), normalize(two)), 9))
print("\nALL ASSERTIONS PASSED")
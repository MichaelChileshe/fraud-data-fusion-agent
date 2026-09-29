import math

from fusion.notes import HashEmbedder, to_pgvector


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))  # vectors are unit length


def test_hash_embeddings_are_unit_length_and_the_configured_size():
    (v,) = HashEmbedder(dim=768).embed(["Customer handed over the banking app PIN."])
    assert len(v) == 768 and math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-9)


def test_texts_sharing_words_are_closer_than_unrelated_texts():
    a, b, c = HashEmbedder(dim=768).embed([
        "many small deposits moved out within hours",
        "small deposits were moved out within a few hours",
        "customer asked for a new debit card",
    ])
    assert cosine(a, b) > cosine(a, c)


def test_pgvector_text_format():
    assert to_pgvector([0.1, -0.25]) == "[0.100000,-0.250000]"

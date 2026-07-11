"""Core schema tests: entity/relationship types, ids, provenance, alias normalization, validation."""

from __future__ import annotations

from uci.core import (
    Entity,
    EntityType,
    Provenance,
    Relationship,
    RelationType,
    entity_id,
    normalize_entity,
    normalize_relation,
    relationship_id,
    repo_id,
    validate_relationship,
)
from uci.core.normalize import looks_like_identifier, split_identifier, tokenize


def test_entity_ids_are_deterministic():
    a = entity_id(EntityType.FUNCTION, "repo1", "a/b.py", "a.b.foo")
    b = entity_id(EntityType.FUNCTION, "repo1", "a/b.py", "a.b.foo")
    assert a == b
    assert a.startswith("function:repo1:")


def test_repo_id_stable_and_slugged():
    rid = repo_id("My Repo", "/abs/path")
    assert rid == repo_id("My Repo", "/abs/path")
    assert rid.startswith("my-repo-")


def test_relationship_id_ordinal():
    base = relationship_id(RelationType.CALLS, "s", "d")
    assert base == "calls:s->d"
    assert relationship_id(RelationType.CALLS, "s", "d", 2) == "calls:s->d#2"


def test_entity_serialization_roundtrip():
    prov = Provenance("repo1", "a.py", 1, 5, "python_parser", 0.9)
    ent = Entity("id1", EntityType.CLASS, "Foo", "a.Foo", prov, {"lang": "python"})
    restored = Entity.from_dict(ent.to_dict())
    assert restored == ent
    assert restored.provenance.location() == "a.py:1-5"


def test_relationship_serialization_roundtrip():
    prov = Provenance("repo1", "a.py", 3, 3)
    rel = Relationship("r1", RelationType.CALLS, "s", "d", prov, {"receiver": "self"})
    restored = Relationship.from_dict(rel.to_dict())
    assert restored == rel


def test_entity_equality_by_id_only():
    p = Provenance("r")
    a = Entity("x", EntityType.FUNCTION, "a", "a", p, {"k": 1})
    b = Entity("x", EntityType.FUNCTION, "b", "b", p, {"k": 2})
    assert a == b and hash(a) == hash(b)


def test_normalize_entity_aliases():
    assert normalize_entity("func") is EntityType.FUNCTION
    assert normalize_entity("struct") is EntityType.CLASS
    assert normalize_entity("protocol") is EntityType.INTERFACE
    assert normalize_entity("class") is EntityType.CLASS
    assert normalize_entity("totally-unknown") is EntityType.SYMBOL


def test_normalize_relation_aliases():
    assert normalize_relation("inherits") is RelationType.EXTENDS
    assert normalize_relation("reads_from") is RelationType.READS
    assert normalize_relation("tested_by") is RelationType.TESTS
    assert normalize_relation("import") is RelationType.IMPORTS
    assert normalize_relation("weird") is RelationType.RELATES_TO


def test_validate_relationship_warns_on_bad_kinds():
    ok = validate_relationship(RelationType.EXTENDS, EntityType.CLASS, EntityType.CLASS)
    assert ok == []
    warnings = validate_relationship(RelationType.EXTENDS, EntityType.FILE, EntityType.CONFIG_KEY)
    assert warnings  # soft warnings, never raises


def test_all_relationship_types_have_specs():
    from uci.core.schema import RELATION_SPECS

    for rtype in RelationType:
        assert rtype in RELATION_SPECS, f"missing spec for {rtype}"


def test_identifier_helpers():
    assert split_identifier("PricingCalculator") == ["pricing", "calculator"]
    assert split_identifier("place_order") == ["place", "order"]
    assert "pricing" in tokenize("PricingCalculator.calculate()")
    assert looks_like_identifier("place_order")
    assert looks_like_identifier("Foo.bar")
    assert not looks_like_identifier("where is the pricing logic")


def test_doc_section_kind_exists_and_is_not_a_symbol():
    from uci.core.entities import SYMBOL_KINDS, EntityType

    assert EntityType.DOC_SECTION.value == "doc_section"
    # sections must not win resolve_symbol over the code artifact they describe
    assert EntityType.DOC_SECTION not in SYMBOL_KINDS


def test_describes_relation_exists_and_never_drives_impact():
    from uci.core.relationships import DEPENDENCY_LIKE, RelationType

    assert RelationType.DESCRIBES.value == "describes"
    # a README mentioning a program must not inflate its blast radius
    assert RelationType.DESCRIBES not in DEPENDENCY_LIKE


def test_describes_relation_spec_allows_doc_sources():
    from uci.core.entities import EntityType
    from uci.core.relationships import RelationType
    from uci.core.schema import RELATION_SPECS, validate_relationship

    spec = RELATION_SPECS[RelationType.DESCRIBES]
    assert spec.directed
    assert EntityType.DOC_SECTION in spec.sources
    assert not spec.targets  # any target kind
    assert validate_relationship(
        RelationType.DESCRIBES, EntityType.DOC_SECTION, EntityType.LEGACY_PROGRAM
    ) == []


def test_doc_aliases_normalize():
    from uci.core.entities import EntityType
    from uci.core.relationships import RelationType
    from uci.core.schema import normalize_entity, normalize_relation

    assert normalize_entity("doc") is EntityType.DOC_SECTION
    assert normalize_entity("doc_section") is EntityType.DOC_SECTION
    assert normalize_relation("documents") is RelationType.DESCRIBES
    assert normalize_relation("describes") is RelationType.DESCRIBES

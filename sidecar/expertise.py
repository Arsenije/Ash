"""Photo-domain extraction config for khora.

The builtin ``general_entities`` skill is people/organization centric, which is
the wrong shape for a photo gallery. This expertise extracts the visual axes the
user filters on: PLACE / OBJECT / ANIMAL / SCENE, and links them so the same
place or object across photos resolves into one graph node (the "connections").
"""

from __future__ import annotations

from khora.extraction.skills.base import (
    EntityTypeConfig,
    ExpertiseConfig,
    RelationshipTypeConfig,
)

ENTITY_TYPES = ["PLACE", "OBJECT", "ANIMAL", "SCENE"]
RELATIONSHIP_TYPES = ["LOCATED_IN", "DEPICTS", "CONTAINS", "ASSOCIATED_WITH"]

PHOTO_EXPERTISE = ExpertiseConfig(
    name="photo_gallery",
    description="Visual entities extracted from photo descriptions.",
    system_prompt=(
        "You extract visual entities from a photograph's description.\n"
        "- PLACE: the location or setting (beach, kitchen, Paris street, forest).\n"
        "- OBJECT: notable things or structures (car, oak tree, dining table, bridge).\n"
        "- ANIMAL: any animals present (dog, cardinal, horse).\n"
        "- SCENE: the activity or scene type (wedding, hiking, sunset, birthday party).\n"
        "Relationships: OBJECT/ANIMAL LOCATED_IN PLACE; SCENE DEPICTS OBJECT/ANIMAL; "
        "PLACE CONTAINS OBJECT. Prefer canonical, reusable names so the same entity "
        "recurs across photos."
    ),
    entity_types=[EntityTypeConfig(name=n) for n in ENTITY_TYPES],
    relationship_types=[
        RelationshipTypeConfig(name=r, source_types=["*"], target_types=["*"])
        for r in RELATIONSHIP_TYPES
    ],
)

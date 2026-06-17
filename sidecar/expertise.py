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

# Explicit extraction prompt (Jinja, rendered by khora's ExpertiseComposer).
# Without this, khora's fallback multi-section prompt never shows the per-entity
# JSON shape, so smaller local models emit entities with an empty "name" field and
# khora drops them all. We spell out the schema — note especially the required
# "name" / "entity_type" keys. ``{{ text }}`` is injected by khora and already
# carries the input sections + the "return {sections:[...]}" wrapper.
PHOTO_EXTRACTION_PROMPT = """\
You extract visual entities from photo descriptions for a searchable gallery.

Entity types to extract:
- PLACE: the location or setting (beach, kitchen, forest, Paris street)
- OBJECT: notable things or structures (car, oak tree, bridge, dining table)
- ANIMAL: animals present (dog, horse, cardinal)
- SCENE: the activity or scene type (wedding, hiking, sunset, birthday party)

Use canonical, reusable names so the same place/object/animal/scene recurs across photos.

Each entity MUST be a JSON object shaped exactly like this:
  {"name": "<canonical name>", "entity_type": "PLACE|OBJECT|ANIMAL|SCENE", "description": "<short>", "attributes": {}, "aliases": []}
Each relationship MUST be shaped like this:
  {"source_entity": "<name>", "target_entity": "<name>", "relationship_type": "LOCATED_IN|DEPICTS|CONTAINS|ASSOCIATED_WITH"}

{{ text }}

Return ONLY valid JSON, no prose, no markdown fences."""

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
    extraction_prompt=PHOTO_EXTRACTION_PROMPT,
    entity_types=[EntityTypeConfig(name=n) for n in ENTITY_TYPES],
    relationship_types=[
        RelationshipTypeConfig(name=r, source_types=["*"], target_types=["*"])
        for r in RELATIONSHIP_TYPES
    ],
)

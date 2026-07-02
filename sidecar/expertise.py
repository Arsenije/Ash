"""Photo-domain extraction config for khora — user-configurable.

The ontology (entity + relationship types) and the extraction/system prompts are
NOT hardcoded: they load from an editable JSON file so users can tune them for
their own libraries without touching source. The built-in values below are just
defaults; on first run they're written to the config file so they're easy to find
and edit.

Config file (JSON):
  $PHOTO_EXPERTISE_CONFIG, else  $KHORA_PHOTO_DATA_DIR/expertise.json  (default ./data/expertise.json)

  {
    "entity_types": [{"name": "PERSON", "description": "..."}, ...],   // or just ["PERSON", ...]
    "relationship_types": ["LOCATED_IN", ...],
    "system_prompt": "...",
    "extraction_prompt": "... {{ text }} ..."   // {{ text }} is injected by khora
  }

The five default types are a well-defined core (they drive the Themes groups), but
the prompt is deliberately open: the model may coin its own short type for things
that don't fit. Related-photo retrieval matches on shared entity *names* and is
type-agnostic, so extra/edited types enrich connections without code changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from khora.extraction.skills.base import (
    EntityTypeConfig,
    ExpertiseConfig,
    RelationshipTypeConfig,
)

# ---------------------------------------------------------------------------
# Defaults (seed values; the live config is loaded from the JSON file below).
# ---------------------------------------------------------------------------
DEFAULT_ENTITY_TYPES: list[dict[str, str]] = [
    {"name": "PERSON", "description": "a human being — a person, child, the bride, a crowd. People are PERSON, never ANIMAL."},
    {"name": "PLACE", "description": "a location or setting — beach, kitchen, Paris street, the backyard, a forest."},
    {"name": "OBJECT", "description": "a notable thing or structure — car, oak tree, bridge, whiteboard, birthday cake."},
    {"name": "ANIMAL", "description": "a non-human animal — dog, horse, cardinal, fish."},
    {"name": "SCENE", "description": "an activity, occasion, or scene type — wedding, hiking, sunset, birthday party, concert."},
]
DEFAULT_RELATIONSHIP_TYPES = ["LOCATED_IN", "DEPICTS", "CONTAINS", "ASSOCIATED_WITH"]

DEFAULT_SYSTEM_PROMPT = (
    "You extract entities from a photograph's description for a gallery that connects "
    "photos sharing the same subject. Prefer these core types:\n"
    "- PERSON: a human (people are PERSON, never ANIMAL).\n"
    "- PLACE: the location or setting (beach, kitchen, Paris street, forest).\n"
    "- OBJECT: notable things or structures (car, oak tree, dining table, bridge).\n"
    "- ANIMAL: a non-human animal (dog, cardinal, horse).\n"
    "- SCENE: the activity or scene type (wedding, hiking, sunset, birthday party).\n"
    "If nothing fits, coin a short UPPERCASE type rather than mis-filing it. "
    "Use canonical, reusable names (singular, common) so the same entity recurs "
    "across photos — 'fireworks' not 'fireworks display', 'castle' not 'historic castle'. "
    "Relationships: OBJECT/ANIMAL/PERSON LOCATED_IN PLACE; SCENE DEPICTS OBJECT/ANIMAL/PERSON; "
    "PLACE CONTAINS OBJECT."
)

# {{ text }} is injected by khora's ExpertiseComposer (input sections + JSON wrapper).
DEFAULT_EXTRACTION_PROMPT = """\
You extract entities and relationships from a photo's description for a searchable,
browsable gallery. The aim is to connect photos that share the same person, place,
object, animal, or scene.

Prefer these core entity types (they drive the gallery's groups):
- PERSON: a human — a person, child, the bride, a crowd. People are PERSON, never ANIMAL.
- PLACE: a location or setting — beach, kitchen, Paris street, the backyard.
- OBJECT: a notable thing or structure — car, oak tree, bridge, whiteboard, birthday cake.
- ANIMAL: a non-human animal — dog, horse, cardinal.
- SCENE: an activity, occasion, or scene type — wedding, hiking, sunset, birthday party.
If something clearly important fits none of these, give it a short UPPERCASE type of
your own (e.g. EVENT, FOOD, VEHICLE, PLANT, ARTWORK, DOCUMENT). Don't force a bad fit.

Naming matters most: use the singular, common, CANONICAL name so the SAME thing
recurs across photos and connects them. Prefer "fireworks" over "fireworks display",
"castle" over "historic castle", "dog" over "small brown dog". Lowercase common nouns;
keep proper names as written. Put extra detail in "description"/"aliases", not the name.

Each entity MUST be a JSON object shaped exactly like this:
  {"name": "<canonical name>", "entity_type": "<PERSON|PLACE|OBJECT|ANIMAL|SCENE or your own>", "description": "<short>", "attributes": {}, "aliases": []}
Each relationship MUST be shaped like this:
  {"source_entity": "<name>", "target_entity": "<name>", "relationship_type": "<LOCATED_IN|DEPICTS|CONTAINS|ASSOCIATED_WITH or your own>"}

{{ text }}

Return ONLY valid JSON, no prose, no markdown fences."""

DEFAULT_CONFIG: dict[str, Any] = {
    "entity_types": DEFAULT_ENTITY_TYPES,
    "relationship_types": DEFAULT_RELATIONSHIP_TYPES,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "extraction_prompt": DEFAULT_EXTRACTION_PROMPT,
}


# ---------------------------------------------------------------------------
# Load (and seed) the user-editable config.
# ---------------------------------------------------------------------------
def _config_path() -> Path:
    explicit = os.environ.get("PHOTO_EXPERTISE_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    data_dir = Path(os.environ.get("KHORA_PHOTO_DATA_DIR", "./data")).expanduser()
    return data_dir / "expertise.json"


def _load_config() -> dict[str, Any]:
    path = _config_path()
    try:
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            return dict(DEFAULT_CONFIG)
        # Merge over defaults so a partial/edited file still yields a full config.
        return {**DEFAULT_CONFIG, **{k: v for k, v in loaded.items() if v}}
    except FileNotFoundError:
        _seed_defaults(path)  # write defaults so the user can discover + edit them
        return dict(DEFAULT_CONFIG)
    except Exception:
        return dict(DEFAULT_CONFIG)  # malformed edit -> fall back, don't crash


def _seed_defaults(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    except Exception:
        pass  # best-effort; read-only data dir just means defaults stay in memory


def _normalize_types(types: Any) -> list[dict[str, str]]:
    """Accept ["PERSON", ...] or [{"name": ..., "description": ...}, ...]."""
    out: list[dict[str, str]] = []
    for t in types or []:
        if isinstance(t, str) and t.strip():
            out.append({"name": t.strip(), "description": ""})
        elif isinstance(t, dict) and t.get("name"):
            out.append({"name": str(t["name"]).strip(), "description": str(t.get("description", ""))})
    return out or _normalize_types(DEFAULT_ENTITY_TYPES)


_CONFIG = _load_config()
_ENTITY_TYPE_DEFS = _normalize_types(_CONFIG.get("entity_types"))

# Public surface (imported by server.py) — derived from the loaded config.
ENTITY_TYPES = [t["name"] for t in _ENTITY_TYPE_DEFS]
RELATIONSHIP_TYPES = [str(r) for r in (_CONFIG.get("relationship_types") or DEFAULT_RELATIONSHIP_TYPES) if str(r).strip()]

PHOTO_EXPERTISE = ExpertiseConfig(
    name="photo_gallery",
    version="2.0.0",
    description="People, places, objects, animals and scenes extracted from photo descriptions.",
    system_prompt=_CONFIG.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
    extraction_prompt=_CONFIG.get("extraction_prompt") or DEFAULT_EXTRACTION_PROMPT,
    entity_types=[EntityTypeConfig(name=t["name"], description=t["description"]) for t in _ENTITY_TYPE_DEFS],
    relationship_types=[
        RelationshipTypeConfig(name=r, source_types=["*"], target_types=["*"]) for r in RELATIONSHIP_TYPES
    ],
)

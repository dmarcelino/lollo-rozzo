"""Where an item is: the place map (phrases naming a place, such as a venue, each
with its town) and the rule that places an item for a monitor's `where`. The map
is data: literal phrases compared without case, accents, spaces or punctuation,
never patterns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .extract import fold
from .spec import MAX_PLACE, town
from .store import PlaceEntry, Store, StoredItem

MAX_PLACES = 500
MAX_PER_CALL = 50
MIN_KEY, MAX_KEY = 4, 80


class PlaceError(ValueError):
    pass


def compact(text: str) -> str:
    """Letters and digits only, without case or accents: "Grand Coliseum Hall",
    "grand_coliseum_hall" and "grand-coliseum-hall" are all "grandcoliseumhall"."""
    return "".join(c for c in fold(text) if c.isalnum())


def check_entry(phrase: Any, locality: Any) -> tuple[str, str, str]:
    """(key, phrase, locality) of one entry, or PlaceError saying what is wrong."""
    key = compact(phrase) if isinstance(phrase, str) else ""
    if not MIN_KEY <= len(key) <= MAX_KEY or len(phrase) > 2 * MAX_KEY:
        raise PlaceError(
            f"a place's phrase needs {MIN_KEY} to {MAX_KEY} letters or digits (spaces and punctuation are ignored), "
            "and should name one place: a short one matches inside other words"
        )
    if not town(locality):
        raise PlaceError(f"a place's locality is its town, at most {MAX_PLACE} characters")
    return key, " ".join(phrase.split()), town(locality)


@dataclass(frozen=True)
class Placed:
    """Where an item is: the text where terms are looked for in (folded), and the
    map entries or source place that placed it (for what a preview says)."""

    text: str
    by: tuple[str, ...]


class PlaceMap:
    """The place map and the sources' places, as loaded for one report or preview;
    it places each item once."""

    def __init__(self, entries: list[PlaceEntry], source_places: dict[str, str]) -> None:
        self.entries = [(e.key, e.phrase, e.locality) for e in entries]
        self.source_places = source_places
        self._placed: dict[tuple[str, str], Placed | None] = {}

    @classmethod
    def load(cls, store: Store) -> PlaceMap:
        specs = store.source_specs()
        return cls(store.places(), {name: str(s["place"]) for name, s in specs.items() if s.get("place")})

    def found(self, text: str) -> list[tuple[str, str]]:
        """(phrase, locality) of every entry whose phrase is in the text."""
        if not text or not self.entries:
            return []
        packed = compact(text)
        return [(phrase, locality) for key, phrase, locality in self.entries if key in packed]

    def place_of(self, item: StoredItem) -> Placed | None:
        key = (item.source, item.key)
        if key not in self._placed:
            self._placed[key] = self._place(item)
        return self._placed[key]

    def _place(self, item: StoredItem) -> Placed | None:
        """An item is placed by its venue and locality, the towns of the entries its
        venue names, and its source's place, all together: a tour lists several
        venues, and a one-venue agenda names its rooms. With none of them, by the
        entries its title, link or linked extras name; None when nothing says where."""
        d = item.data
        venue, locality = str(d.get("venue") or ""), str(d.get("locality") or "")
        place = self.source_places.get(item.source, "")
        if venue or locality or place:
            hits = self.found(venue)
            by = _phrases(hits) + ((f"place of {item.source}",) if place else ())
            return Placed(fold(" ".join([locality, venue, place, *(town for _, town in hits)])), by)
        texts = [str(d.get("title") or ""), str(d.get("url") or "")]
        texts += [str(v) for v in (d.get("extra") or {}).values() if str(v).startswith(("http://", "https://"))]
        hits = [hit for text in texts for hit in self.found(text)]
        if hits:
            return Placed(fold(" ".join(town for _, town in hits)), _phrases(hits))
        return None


def _phrases(hits: list[tuple[str, str]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(phrase for phrase, _ in hits))

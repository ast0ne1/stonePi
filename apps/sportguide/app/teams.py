"""Team names for Teams to Watch: one normaliser for both sides of the match.

Listings only carry a title ("Arsenal vs Chelsea", "Sydney Roosters - Newcastle
Knights Rugby League", "Odi: South Africa V Australia G3 | ..."), so team names
are pulled out of titles here. Watched teams are stored as a normalised key plus
the display name; matching compares keys, never raw free text.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

CATALOG_PATH = Path(__file__).resolve().parent / "team_catalog.json"
# Picker values look like "Arsenal · Football" so the same name in two sports stays distinct.
VALUE_SEP = " · "

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_SIDES = re.compile(r"\s+(?:vs\.?|v)\s+|\s+[-–—]\s+", re.I)
_SPORT_SUFFIX = re.compile(
    r"\s+(?:rugby union|rugby league|rugby|cricket|aflw|afl|nrlw|nrl|football|soccer)\s*$", re.I
)
_GAME_SUFFIX = re.compile(r"\s+(?:g|game|test|match)\s*\d+\s*$", re.I)
_JUNK = {"ft", "ht", "tba", "tbc", "tbd", "live"}


def team_key(name: str) -> str:
    """Normalised match key: casefold, drop accents and punctuation, collapse spaces."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_WORD.sub(" ", text.casefold()).replace("_", " ")
    return " ".join(text.split())


def clean_name(name: str) -> str:
    """Display name with whitespace collapsed (case kept)."""
    return " ".join(str(name or "").split())[:80]


def teams_in_title(title: str) -> list[str]:
    """The two sides of a listing title, or [] when it isn't a head-to-head."""
    text = clean_name(title)
    if "|" in text:
        text = text.split("|", 1)[0].strip()
    if ":" in text:
        text = text.rsplit(":", 1)[1].strip()
    text = _SPORT_SUFFIX.sub("", text)
    sides = _SIDES.split(text)
    if len(sides) != 2:
        return []
    out: list[str] = []
    for side in sides:
        side = clean_name(_GAME_SUFFIX.sub("", side))
        key = team_key(side)
        if len(key) < 2 or key in _JUNK or not any(ch.isalpha() for ch in key):
            return []
        out.append(side)
    return out


def title_matches(title: str, key: str) -> bool:
    """True when the watched key is one of the title's teams, or appears in it as whole words.

    The whole-word fallback keeps short legacy names working ("Collingwood" still
    matches "Collingwood Magpies vs ...") without matching inside other words.
    """
    key = team_key(key)
    if len(key) < 2:
        return False
    if key in {team_key(t) for t in teams_in_title(title)}:
        return True
    return f" {key} " in f" {team_key(title)} "


def known_teams(rows: Iterable[dict[str, Any]], sports: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """Teams seen in listings, grouped by sport: [{sport, label, teams: [{key, name}]}].

    The display name is the spelling seen most often for that key.
    """
    spellings: dict[str, dict[str, Counter]] = {}
    for row in rows:
        sport = str(row.get("sport") or "other")
        for name in teams_in_title(str(row.get("title") or "")):
            spellings.setdefault(sport, {}).setdefault(team_key(name), Counter())[name] += 1
    labels = {s["id"]: s["label"] for s in sports if s.get("id") != "all"}
    order = list(labels) + sorted(s for s in spellings if s not in labels)
    groups: list[dict[str, Any]] = []
    for sport in order:
        found = spellings.get(sport)
        if not found:
            continue
        teams = [{"key": k, "name": c.most_common(1)[0][0]} for k, c in found.items()]
        teams.sort(key=lambda t: t["key"])
        groups.append({"sport": sport, "label": labels.get(sport, sport.title()), "teams": teams})
    return groups


@lru_cache(maxsize=1)
def catalog() -> dict[str, list[dict[str, Any]]]:
    """Standard teams per sport id from team_catalog.json: {sport: [{name, key, aliases}]}."""
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for sport, items in raw.items():
        if sport.startswith("_") or not isinstance(items, list):
            continue
        for item in items:
            name = clean_name(str(item.get("name") or ""))
            key = team_key(name)
            if len(key) < 2:
                continue
            aliases = sorted({team_key(a) for a in item.get("aliases") or []} - {key, ""})
            out.setdefault(sport, []).append({"name": name, "key": key, "aliases": aliases})
    return out


def entry_keys(entry: dict[str, Any]) -> set[str]:
    """Match keys for a watched entry: its key plus catalog aliases (in its sport, or any sport)."""
    key = team_key(str(entry.get("key") or entry.get("name") or ""))
    keys = {key} if len(key) >= 2 else set()
    sport = str(entry.get("sport") or "")
    for cat_sport, items in catalog().items():
        if sport and cat_sport != sport:
            continue
        for item in items:
            if item["key"] == key or key in item["aliases"]:
                keys.add(item["key"])
                keys.update(item["aliases"])
    return keys


def _side_matches(side_key: str, key: str) -> bool:
    # "Collingwood" matches the side "Collingwood Magpies"; "Melbourne" does not match "North Melbourne".
    return side_key == key or side_key.startswith(key + " ")


def entry_matches(title: str, sport: str, entry: dict[str, Any]) -> bool:
    """True when a listing (title + sport id) is a game for this watched entry.

    A team picked in a sport only matches that sport's listings; entries without
    a sport (legacy free text) match any sport. Head-to-head titles compare each
    side; other titles fall back to whole words.
    """
    entry_sport = str(entry.get("sport") or "")
    if entry_sport and sport and entry_sport != sport:
        return False
    keys = entry_keys(entry)
    if not keys:
        return False
    sides = [team_key(t) for t in teams_in_title(title)]
    if sides:
        return any(_side_matches(side, key) for side in sides for key in keys)
    padded = f" {team_key(title)} "
    return any(f" {key} " in padded for key in keys)


def first_matching_entry(title: str, sport: str, entries: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in entries:
        if entry_matches(title, sport, entry):
            return entry
    return None


def picker_groups(rows: Iterable[dict[str, Any]], sports: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """Teams to offer, grouped by sport: the standard catalog plus teams seen in listings.

    [{sport, label, teams: [{key, name, value, listed}]}]. ``value`` is what the
    picker submits ("Arsenal · Football"); ``listed`` means it has current listings.
    """
    rows = list(rows)
    sports = list(sports)
    labels = {s["id"]: s["label"] for s in sports if s.get("id") != "all"}
    listed = {g["sport"]: {t["key"]: t["name"] for t in g["teams"]} for g in known_teams(rows, sports)}
    cat = catalog()
    order = list(labels) + sorted((set(cat) | set(listed)) - set(labels))
    groups: list[dict[str, Any]] = []
    for sport in order:
        label = labels.get(sport, sport.title())
        teams: dict[str, dict[str, Any]] = {}
        seen_keys = listed.get(sport, {})
        for item in cat.get(sport, []):
            names = (item["key"], *item["aliases"])
            is_listed = any(_side_matches(k, n) for k in seen_keys for n in names)
            teams[item["key"]] = {"key": item["key"], "name": item["name"], "listed": is_listed}
        covered = {n for item in cat.get(sport, []) for n in (item["key"], *item["aliases"])}
        for key, name in seen_keys.items():
            if key in teams or key in covered:
                continue
            teams[key] = {"key": key, "name": name, "listed": True}
        if not teams:
            continue
        ordered = sorted(teams.values(), key=lambda t: t["key"])
        for team in ordered:
            team["value"] = f"{team['name']}{VALUE_SEP}{label}"
        groups.append({"sport": sport, "label": label, "teams": ordered})
    return groups


def resolve_pick(text: str, groups: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """Turn what the person typed or picked into one team {key, name, sport}.

    Accepts a picker value ("Arsenal · Football"), a team name, or an alias
    ("Man Utd"). Returns (team, "") or (None, reason).
    """
    raw = clean_name(text)
    if not raw:
        return None, "Pick a team from the list."
    wanted_label = ""
    sep = VALUE_SEP.strip()
    if sep in raw:
        head, _, tail = raw.rpartition(sep)
        raw, wanted_label = head.strip(), tail.strip()
    key = team_key(raw)
    matches: list[dict[str, Any]] = []
    for group in groups:
        if wanted_label and team_key(group["label"]) != team_key(wanted_label):
            continue
        aliases = {item["key"]: item["aliases"] for item in catalog().get(group["sport"], [])}
        for team in group["teams"]:
            if key == team["key"] or key in aliases.get(team["key"], ()):
                matches.append({"key": team["key"], "name": team["name"], "sport": group["sport"], "label": group["label"]})
    if not matches:
        return None, "We don't know that team yet. Pick one from the suggestions."
    if len(matches) > 1:
        options = " or ".join(f"{m['name']}{VALUE_SEP}{m['label']}" for m in matches[:3])
        return None, f"{matches[0]['name']} plays more than one sport. Pick {options}."
    return matches[0], ""

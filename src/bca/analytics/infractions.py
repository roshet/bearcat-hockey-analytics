"""Infraction names as a coach would say them, shared by the simulator and the reports."""

from __future__ import annotations

#: Feed wordings that name the same infraction a coach would name. Checked
#: against every description that appears 20+ times across seven seasons.
_ALIASES: tuple[tuple[str, str], ...] = (
    ("clearing the area", "Clearing the area of a fight"),
    ("fighting", "Fighting"),
    ("instigat", "Instigating"),
    ("bench minor-coach", "Bench minor (coach)"),
    ("disqualification w/major", "Disqualification (major)"),
    ("roughing", "Roughing"),
    ("holding the stick", "Holding the stick"),
    ("goalie interference", "Goalie interference"),
    ("too many men", "Too many men"),
    ("delay of game", "Delay of game"),
    ("diving", "Diving / embellishment"),
    ("taunt", "Taunting"),
    ("abuse of official", "Abuse of official"),
    ("player to official", "Abuse of official"),
    ("contact to the head", "Head contact"),
    ("hit to the head", "Head contact"),
    ("face mask", "Face mask"),
    ("facemask", "Face mask"),
    ("unsportsmanlike", "Unsportsmanlike conduct"),
)
_SEVERITY_SUFFIXES = (" w/game misconduct", " w/disqualification dq", " w/misconduct",
                      " - disqualification dq", " dq")


def infraction_name(minutes: int, description: str) -> str:
    """The infraction a coach would name, merging the feed's wording variants.

    "Major-Cross Checking w/Game Misconduct" and "Cross Checking" are both cross
    checking; severity is already in the penalty minutes. A 10-minute penalty
    is a misconduct, whatever else its description says.
    """
    text = description.strip()
    lower = text.lower()
    if minutes == 10:
        return "Game misconduct" if "game" in lower else "Misconduct"
    for phrase, name in _ALIASES:
        if phrase in lower:
            return name
    for prefix in ("major-", "bench minor-", "penalty shot-"):
        if lower.startswith(prefix):
            text, lower = text[len(prefix):], lower[len(prefix):]
    for suffix in _SEVERITY_SUFFIXES:
        if lower.endswith(suffix):
            text = text[: -len(suffix)]
            lower = lower[: -len(suffix)]
    text = text.strip(" -")
    return text[:1].upper() + text[1:].lower() if text else description

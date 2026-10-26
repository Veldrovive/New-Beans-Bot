"""Helpers for the lists of message "scripts" in the config that the bot picks from at random."""

import random


class _KeepMissing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def pick(scripts: list[str], **values: object) -> str | None:
    """A random script with placeholders like {jailed_user} filled in, or None if there are none."""
    if not scripts:
        return None
    return random.choice(scripts).format_map(_KeepMissing(values))


def join_names(names: list[str]) -> str:
    """ "a", "a and b", "a, b and c"."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"

"""Keep `strings.json` and the English translation from drifting apart.

Home Assistant reads only `translations/<lang>.json` at runtime; `strings.json` is
never opened by a running instance. It is the source of record -- what hassfest
validates, and what other languages would be derived from.

So the two disagree silently in both directions, and each direction is its own
kind of wrong:

    only strings.json edited     every check still passes and the interface keeps
                                 showing the old text
    only en.json edited          the interface is right and the source is not, so
                                 the next translation carries the old text

Nothing else catches either. hassfest reads `strings.json` alone, and no test
asserts on user-facing wording -- deliberately, since a test that pinned the text
would have to be edited every time the wording improved.

Compared as parsed JSON rather than as bytes, so indentation and key order are
free to differ while the strings themselves must not. Core resolves `[%key:...]`
references between these two files, which would make them legitimately different;
this integration uses none, and this check is what would notice if one arrived.
"""

# PEP 723 inline metadata, so `uv run --script` runs this without the project
# environment. It needs nothing but the standard library, and requiring the
# project's 3.14 and its 129 packages to compare two JSON files would mean CI
# installing Home Assistant before it could run a git hook.
# `>=3.11` matches the other hook tools, and is below what any runner ships.
#
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import sys
from pathlib import Path

SOURCE = "custom_components/stiebel_eltron_ir/strings.json"
TRANSLATION = "custom_components/stiebel_eltron_ir/translations/en.json"


def load(path: str) -> object | None:
    """Return a file's parsed JSON, or None if it cannot be read or parsed.

    Read and parsed in two steps rather than under one `except (OSError,
    json.JSONDecodeError)`. `ruff format` targets the project's Python, 3.14,
    where PEP 758 drops the parentheses -- and that spelling is a syntax error on
    the 3.11 this script declares, which is the interpreter a CI runner is likely
    to hand it.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def differences(source: object, translation: object, path: str = "") -> list[str]:
    """Return one line per key that differs, named by its full path.

    Named rather than counted: "3 keys differ" sends somebody diffing two files by
    eye, where `config.error.emitter_in_use` is the edit itself.
    """
    if isinstance(source, dict) and isinstance(translation, dict):
        found = []
        for key in sorted(source.keys() | translation.keys()):
            here = f"{path}.{key}" if path else key
            if key not in source:
                found.append(f"  {here}: missing from strings.json")
            elif key not in translation:
                found.append(f"  {here}: missing from the English translation")
            else:
                found.extend(differences(source[key], translation[key], here))
        return found
    if source != translation:
        return [f"  {path}: the two files say different things"]
    return []


def main() -> int:
    """Fail unless the two files carry the same strings."""
    source = load(SOURCE)
    translation = load(TRANSLATION)
    if source is None or translation is None:
        unreadable = [
            name
            for name, value in ((SOURCE, source), (TRANSLATION, translation))
            if value is None
        ]
        print(f"Unreadable or not valid JSON: {', '.join(unreadable)}", file=sys.stderr)
        return 1

    if not (found := differences(source, translation)):
        return 0

    print(f"{SOURCE} and {TRANSLATION} disagree:", file=sys.stderr)
    print("\n".join(found), file=sys.stderr)
    print(
        "\nHome Assistant shows the translation and hassfest validates the source,\n"
        "so an edit to one of them alone is invisible until somebody reads the\n"
        "wrong text. Copy the change across.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

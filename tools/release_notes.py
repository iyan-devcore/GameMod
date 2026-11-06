"""Write the notes for one GitHub release from its section in CHANGELOG.md.

    python tools/release_notes.py v1.0.0 release/notes.md
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOW_TO = """
### Download

Download **GameMod.zip** below, unzip it, and open **GameMod.exe**. Keep the exe and the
`subtitles` folder together. Full steps are in the [README](https://github.com/{repo}#how-to-use).
"""


def section(changelog: str, tag: str) -> str:
    lines, inside = [], False
    for line in changelog.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].split()[0] == tag
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines).strip()


def main() -> None:
    tag, output = sys.argv[1], Path(sys.argv[2])
    body = section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), tag)
    if not body:
        raise SystemExit(f"CHANGELOG.md has no section for {tag}. Add one starting with '## {tag}'.")
    repo = sys.argv[3] if len(sys.argv) > 3 else "OWNER/GameMod"
    output.write_text(body + "\n" + HOW_TO.format(repo=repo), encoding="utf-8")


if __name__ == "__main__":
    main()

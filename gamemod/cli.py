"""Command line for running the same steps the app uses.

    python -m gamemod.cli status
    python -m gamemod.cli translate --from 5850 --to 6070
    python -m gamemod.cli translate
    python -m gamemod.cli reset --from 5850 --to 6070
    python -m gamemod.cli next --count 250
    python -m gamemod.cli export data/tlou1/batches --count 350
    python -m gamemod.cli export data/msmr/batches_story/in --game msmr --keys "^[A-Z0-9]+_(M|CINE)_"
    python -m gamemod.cli apply translations.tsv
    python -m gamemod.cli fill
    python -m gamemod.cli export-pack
    python -m gamemod.cli install
    python -m gamemod.cli uninstall

Add --game re2 or --game msmr to work on another game. The default is tlou1.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from gamemod.adapters.base import GameAdapter
from gamemod.adapters.registry import ADAPTERS
from gamemod.core import store as st
from gamemod.core.pack import pack_path, write_pack
from gamemod.core.paths import data_dir
from gamemod.core.pipeline import (
    Progress,
    apply_translations,
    can_share_translation,
    install_translations,
    lines_to_translate,
    mentions_person,
    run_translation,
    split_into_batches,
)
from gamemod.core.settings import get_api_key, load_settings
from gamemod.core.store import Item, Store
from gamemod.core.translate.gemini import GeminiTranslator

GAMES = {adapter.id: adapter for adapter in ADAPTERS.values()}


def _escape(text: str) -> str:
    return text.replace("\t", " ").replace("\n", "\\n")


def _open(adapter: GameAdapter) -> Store:
    store = Store(data_dir(adapter.id) / "hindi.sqlite")
    store.sync(adapter.extract_lines(), adapter.brief().fixed)
    return store


def status(adapter: GameAdapter) -> None:
    store = _open(adapter)
    print("Install state:", adapter.state().value)
    print("Lines:", dict(store.counts()))
    print("Usage:", store.usage_totals())


def translate(adapter: GameAdapter, args: argparse.Namespace) -> None:
    store = _open(adapter)
    order_range = (args.start, args.end) if args.start is not None else None

    def show(progress: Progress) -> None:
        print(f"  {progress.done}/{progress.total} {progress.message}", flush=True)

    result = run_translation(
        store,
        GeminiTranslator(get_api_key(), models=[args.model])
        if args.model
        else GeminiTranslator(get_api_key()),
        adapter.brief(),
        order_range=order_range,
        batch_size=args.batch_size,
        max_requests=args.max_requests,
        on_progress=show,
    )
    print(result)
    print("Lines:", dict(store.counts()))
    print("Usage:", store.usage_totals())
    if result.stopped in ("daily_limit", "busy"):
        sys.exit(3)


def next_batch(adapter: GameAdapter, args: argparse.Namespace) -> None:
    """Print untranslated lines as: id, English, and Russian when it can show gender."""
    store = _open(adapter)
    order_range = (args.start, args.end) if args.start is not None else None
    items = store.pending(args.count, order_range)
    if len(items) < args.count:
        items += store.pending(args.count - len(items), order_range, st.RETRY)
    print(f"# {len(items)} lines here, {store.count_pending(order_range):,} unfinished in this range")
    for item in items:
        print(_work_line(item, adapter.lines_depend_on_speaker))


def _work_line(item: Item, always_hint: bool = False) -> str:
    """id, English, and the hint when the line's grammar could depend on who is speaking."""
    parts = [str(item.id), _escape(item.source)]
    if item.hint and (always_hint or mentions_person(item.source)):
        parts.append(_escape(item.hint))
    return "\t".join(parts)


def export_batches(adapter: GameAdapter, args: argparse.Namespace) -> None:
    """Write unfinished lines to separate files so helpers can work without overlapping."""
    store = _open(adapter)
    folder = Path(args.file)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("part_*.tsv"):
        old.unlink()
    items = lines_to_translate(store, share_repeats=not adapter.lines_depend_on_speaker)
    if args.redo:
        # Machine translations made earlier, to be done again by another translator.
        redo = store.pending(10**7, status=st.DONE) + store.pending(10**7, status=st.REVIEW)
        items = sorted(items + redo, key=lambda item: item.first_order)
    if args.keys or args.skip_keys:
        keys = store.item_keys()
        wanted = re.compile(args.keys) if args.keys else None
        unwanted = re.compile(args.skip_keys) if args.skip_keys else None
        items = [
            item
            for item in items
            if (wanted is None or any(wanted.search(key) for key in keys.get(item.id, ())))
            and not (unwanted and any(unwanted.search(key) for key in keys.get(item.id, ())))
        ]
    batches = split_into_batches(items, max_lines=args.count, max_chars=args.max_chars)
    for number, batch in enumerate(batches, 1):
        text = "\n".join(_work_line(item, adapter.lines_depend_on_speaker) for item in batch) + "\n"
        (folder / f"part_{number:04d}.tsv").write_text(text, encoding="utf-8")
    story = sum(1 for item in items if item.first_order < 1_000_000)
    print(
        f"{len(items):,} lines to translate ({story:,} story, {len(items) - story:,} combat) "
        f"in {len(batches)} files. {store.count_pending():,} unfinished in total."
    )


def export_pack(adapter: GameAdapter) -> None:
    """Write the subtitle pack that goes beside GameMod.exe."""
    store = _open(adapter)
    path = pack_path(adapter.id)
    count = write_pack(path, adapter.id, adapter.title, store.pack_lines())
    print(f"Wrote {count:,} lines to {path} ({path.stat().st_size // 1024:,} KB)")


def fill(adapter: GameAdapter) -> None:
    store = Store(data_dir(adapter.id) / "hindi.sqlite")
    print(f"Copied to {store.fill_duplicates(can_share_translation):,} repeated lines.")
    print("Lines:", dict(store.counts()))


def apply_file(adapter: GameAdapter, args: argparse.Namespace) -> None:
    """Save a UTF-8 file of lines in the form: id, tab, Hindi."""
    translations: dict[int, str] = {}
    for number, line in enumerate(Path(args.file).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        item_id, separator, text = line.partition("\t")
        if not separator or not item_id.strip().isdigit():
            print(f"Line {number} of the file is not in 'id<TAB>text' form: {line[:60]!r}")
            continue
        translations[int(item_id)] = text.replace("\\n", "\n")
    # Skips re-reading the game, so several helpers can save at once without slowing down.
    store = Store(data_dir(adapter.id) / "hindi.sqlite")
    result = apply_translations(store, translations, args.model or "claude")
    print(f"Saved {result.saved} of {len(translations)}. Unfinished: {store.count_pending():,}")
    for item_id, problem in result.rejected:
        print(f"  REJECTED {item_id}: {problem}")
    for item_id, problem in result.needs_review:
        print(f"  check {item_id}: {problem}")


def install(adapter: GameAdapter) -> None:
    store = _open(adapter)
    install_translations(
        adapter, store, load_settings().hindi_scale, lambda message: print(" ", message, flush=True)
    )
    counts = store.counts()
    usable = sum(counts[s] for s in st.USABLE)
    print(f"Hindi lines in this install: {usable:,} of {sum(counts.values()):,}")
    print(adapter.where_to_enable)


def uninstall(adapter: GameAdapter) -> None:
    adapter.uninstall(lambda message: print(" ", message, flush=True))
    print("Install state:", adapter.state().value)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "status", "translate", "reset", "next", "export", "apply", "fill",
            "export-pack", "install", "uninstall",
        ],
    )
    parser.add_argument("file", nargs="?", help="apply: the translations file. export: the folder")
    parser.add_argument("--game", choices=sorted(GAMES), default="tlou1", help="which game to work on")
    parser.add_argument("--count", type=int, default=250, help="next and export: lines per batch")
    parser.add_argument("--max-chars", type=int, default=9000, help="export: English characters per file")
    parser.add_argument("--from", dest="start", type=int, help="first scene position to translate")
    parser.add_argument("--to", dest="end", type=int, help="last scene position to translate")
    parser.add_argument("--keys", help="export: only lines whose game key matches this pattern")
    parser.add_argument("--skip-keys", help="export: leave out lines whose game key matches this pattern")
    parser.add_argument("--redo", action="store_true", help="export: include machine translations already done")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--model")
    args = parser.parse_args()

    adapter = GAMES[args.game]()
    if args.action == "translate":
        translate(adapter, args)
    elif args.action == "next":
        next_batch(adapter, args)
    elif args.action == "export":
        if not args.file:
            parser.error("export needs a folder")
        export_batches(adapter, args)
    elif args.action == "fill":
        fill(adapter)
    elif args.action == "export-pack":
        export_pack(adapter)
    elif args.action == "apply":
        if not args.file:
            parser.error("apply needs a file")
        apply_file(adapter, args)
    elif args.action == "reset":
        if args.start is None or args.end is None:
            parser.error("reset needs --from and --to")
        print("Queued for redoing:", _open(adapter).reset_range(args.start, args.end))
    elif args.action == "status":
        status(adapter)
    elif args.action == "install":
        install(adapter)
    else:
        uninstall(adapter)


if __name__ == "__main__":
    main()

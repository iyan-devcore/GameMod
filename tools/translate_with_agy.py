"""Translate batch files with the Antigravity command line, one prompt per file.

    python tools/translate_with_agy.py data/re2/batches --model gemini-3.8-flash-high --workers 3

Antigravity only translates here. It runs in an empty folder with no permission to use tools,
gets the brief and the lines as text, and its reply is saved to out/part_NNNN.tsv. Checking and
saving into the database are separate steps. Files already in out/ are skipped, so a stopped
run can simply be started again.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_REPLY_LINE = re.compile(r"^\s*(\d+)\t(.*\S)\s*$")
_CONTEXT_LINES = 15
_ATTEMPTS = 3
_log_lock = threading.Lock()


def _log(folder: Path, message: str) -> None:
    stamped = f"{time.strftime('%H:%M:%S')} {message}"
    with _log_lock:
        print(stamped, flush=True)
        with open(folder / "agy_log.txt", "a", encoding="utf-8") as log:
            log.write(stamped + "\n")


def _ask(prompt: str, model: str, workspace: Path, timeout: int) -> str:
    # No permission flags: print mode cannot ask, so any tool needing permission is refused.
    # Do not add --mode plan: the agent then tries a tool first and replies with nothing.
    result = subprocess.run(
        ["agy", "-p", prompt, "--model", model, "--disable-slash-commands", "--output-format", "text"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=workspace,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "no output").strip()[:400])
    return result.stdout


def parse_reply(reply: str, wanted: set[int]) -> dict[int, str]:
    found: dict[int, str] = {}
    for line in reply.splitlines():
        match = _REPLY_LINE.match(line)
        if match and int(match.group(1)) in wanted:
            found[int(match.group(1))] = match.group(2)
    return found


def _translate_file(path: Path, previous: str, brief: str, args, workspace: Path) -> bool:
    folder = path.parent.parent
    output = folder / "out" / path.name
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {int(line.split("\t", 1)[0]): line for line in lines}
    done: dict[int, str] = {}
    for attempt in range(1, _ATTEMPTS + 1):
        missing = [i for i in by_id if i not in done]
        if not missing:
            break
        body = "\n".join(by_id[i] for i in missing)
        context = f"Lines just before these, for context only. Do not translate them:\n{previous}\n\n" if previous else ""
        prompt = f"{brief}\n\n{context}Translate these lines now:\n{body}\n"
        started = time.time()
        try:
            reply = _ask(prompt, args.model, workspace, args.timeout)
        except (RuntimeError, subprocess.TimeoutExpired) as problem:
            _log(folder, f"{path.name}: attempt {attempt} failed: {problem}")
            time.sleep(60 * attempt)
            continue
        done.update(parse_reply(reply, set(missing)))
        _log(folder, f"{path.name}: attempt {attempt} has {len(done)}/{len(by_id)} lines, {time.time() - started:.0f}s")
    if not done:
        return False
    output.parent.mkdir(exist_ok=True)
    text = "".join(f"{i}\t{done[i]}\n" for i in by_id if i in done)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    if len(done) < len(by_id):
        _log(folder, f"{path.name}: saved with {len(by_id) - len(done)} lines missing")
    return len(done) == len(by_id)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="holds TRANSLATOR_BRIEF.md and in/")
    parser.add_argument("--model", default="gemini-3.8-flash-high")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900, help="seconds for one reply")
    parser.add_argument("--only", help="translate only this file, such as part_0001.tsv")
    args = parser.parse_args()

    brief = (args.folder / "TRANSLATOR_BRIEF.md").read_text(encoding="utf-8")
    files = sorted((args.folder / "in").glob("part_*.tsv"))
    contexts: dict[Path, str] = {}
    previous = ""
    for path in files:
        contexts[path] = previous
        tail = path.read_text(encoding="utf-8").splitlines()[-_CONTEXT_LINES:]
        previous = "\n".join(line.split("\t", 1)[1] for line in tail if "\t" in line)
    todo = [p for p in files if not (args.folder / "out" / p.name).exists()]
    if args.only:
        todo = [p for p in files if p.name == args.only]

    with tempfile.TemporaryDirectory(prefix="agy_empty_") as workspace, ThreadPoolExecutor(args.workers) as pool:
        results = list(
            pool.map(lambda p: _translate_file(p, contexts[p], brief, args, Path(workspace)), todo)
        )
    print(f"Finished {sum(results)} of {len(todo)} files completely.")
    sys.exit(0 if all(results) else 3)


if __name__ == "__main__":
    main()

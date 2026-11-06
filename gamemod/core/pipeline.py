"""Translate pending lines with retries, quality checks and clean stopping."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from gamemod.adapters.base import GameAdapter
from gamemod.core import store as st
from gamemod.core.paths import data_dir
from gamemod.core.store import Item, Store
from gamemod.core.translate.base import (
    Brief,
    ContentBlocked,
    Overloaded,
    RateLimited,
    RequestLine,
    Translator,
)
from gamemod.core.translate.qa import check_translation

_CONTEXT_LINES = 8
# Gemini counts "busy" replies against the daily limit, so these waits are deliberately long.
_BUSY_WAITS = (60, 180, 300, 300)
_MAX_MINUTE_WAITS = 12
_SMALLEST_SPLIT = 25
# Lines that mention a person can need different Hindi depending on who is speaking.
_MENTIONS_PERSON = re.compile(r"\b(I|I'm|I'll|I've|I'd|me|my|mine|myself|you|you're|you've|your)\b", re.I)


def mentions_person(text: str) -> bool:
    return bool(_MENTIONS_PERSON.search(text))


def can_share_translation(text: str) -> bool:
    return not mentions_person(text)


def lines_to_translate(store: Store, share_repeats: bool = True) -> list[Item]:
    """Every unfinished line in scene order, leaving out repeats that can share a translation."""
    items = store.pending(10**7) + store.pending(10**7, status=st.RETRY)
    items.sort(key=lambda item: item.first_order)
    seen: set[str] = set()
    unique: list[Item] = []
    for item in items:
        if share_repeats and can_share_translation(item.source):
            if item.source in seen:
                continue
            seen.add(item.source)
        unique.append(item)
    return unique


def split_into_batches(items: Sequence[Item], max_lines: int, max_chars: int) -> list[list[Item]]:
    batches: list[list[Item]] = []
    current: list[Item] = []
    chars = 0
    for item in items:
        if current and (len(current) >= max_lines or chars + len(item.source) > max_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(item)
        chars += len(item.source)
    if current:
        batches.append(current)
    return batches


@dataclass
class RunResult:
    translated: int = 0
    needs_review: int = 0
    failed: int = 0
    requests: int = 0
    stopped: str = ""  # "", "finished", "cancelled", "daily_limit", "busy", or "request_limit"
    message: str = ""


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    message: str = ""


def content_revision(targets: Mapping[str, Mapping[str, str]], hindi_scale: float) -> str:
    """An identity for the text and settings, used to tell whether an install is out of date."""
    digest = hashlib.sha1(str(hindi_scale).encode())
    for table in sorted(targets):
        for key in sorted(targets[table]):
            digest.update(f"{table}{key}{targets[table][key]}".encode("utf-8"))
    return digest.hexdigest()


def _install_record(game_id: str):
    return data_dir(game_id) / "installed.json"


def installed_revision(game_id: str) -> str:
    try:
        return json.loads(_install_record(game_id).read_text(encoding="utf-8")).get("revision", "")
    except (OSError, ValueError):
        return ""


def install_translations(
    adapter: GameAdapter,
    store: Store,
    hindi_scale: float,
    progress: Callable[[str], None] = lambda _: None,
) -> None:
    targets = store.line_targets()
    adapter.install(targets, progress, hindi_scale)
    _install_record(adapter.id).write_text(
        json.dumps({"revision": content_revision(targets, hindi_scale)}), encoding="utf-8"
    )


@dataclass
class ManualResult:
    saved: int = 0
    needs_review: list[tuple[int, str]] = None
    rejected: list[tuple[int, str]] = None


def apply_translations(store: Store, translations: Mapping[int, str], model: str) -> ManualResult:
    """Save translations made outside the translator services, with the same quality checks.

    Rejected lines stay unfinished, so the next batch offers them again.
    """
    result = ManualResult(needs_review=[], rejected=[])
    items = store.items_by_id(list(translations))
    for item_id, target in translations.items():
        item = items.get(item_id)
        if item is None:
            result.rejected.append((item_id, "unknown line id"))
            continue
        check = check_translation(item.source, target)
        if check.blocking:
            result.rejected.append((item_id, check.describe()))
        elif check.warnings:
            store.save(item_id, target.strip(), st.REVIEW, check.describe(), model)
            result.needs_review.append((item_id, check.describe()))
            result.saved += 1
        else:
            store.save(item_id, target.strip(), st.DONE, model=model)
            result.saved += 1
    return result


def run_translation(
    store: Store,
    translator: Translator,
    brief: Brief,
    order_range: tuple[int, int] | None = None,
    batch_size: int = 500,  # free Gemini limits count requests, not lines
    max_requests: int | None = None,
    on_progress: Callable[[Progress], None] = lambda _: None,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> RunResult:
    result = RunResult()
    total = store.count_pending(order_range)
    done = 0
    on_progress(Progress(0, total))

    while True:
        if should_stop():
            result.stopped = "cancelled"
            break
        if max_requests is not None and result.requests >= max_requests:
            result.stopped = "request_limit"
            break
        # Rejected lines wait until the end and go together, because the free limit counts
        # requests. Retrying after each batch would nearly double the number of requests.
        batch = store.pending(batch_size, order_range) or store.pending(batch_size, order_range, st.RETRY)
        if not batch:
            result.stopped = "finished"
            break
        context = store.context_before(batch[0].first_order, _CONTEXT_LINES)
        try:
            _process(batch, context, store, translator, brief, result, sleep, should_stop, on_progress, done, total)
        except RateLimited as limit:
            result.stopped = "daily_limit"
            result.message = str(limit)
            break
        except Overloaded as busy:
            result.stopped = "cancelled" if should_stop() else "busy"
            result.message = str(busy)
            break
        done = total - store.count_pending(order_range)
        on_progress(Progress(done, total))
    return result


def _process(
    batch: Sequence[Item],
    context,
    store: Store,
    translator: Translator,
    brief: Brief,
    result: RunResult,
    sleep,
    should_stop,
    on_progress,
    done: int,
    total: int,
) -> None:
    lines = [
        RequestLine(i, item.source, item.hint, problem=item.note if item.status == st.RETRY else "")
        for i, item in enumerate(batch, 1)
    ]
    try:
        replies = _request(
            translator, brief, lines, context, store, result, sleep, should_stop, on_progress, done, total
        )
    except ContentBlocked as blocked:
        _split_blocked(batch, context, store, translator, brief, result, sleep, should_stop, on_progress, done, total, blocked)
        return

    for line, item in zip(lines, batch):
        target = replies.get(line.id) or item.target  # a retry can fall back on its first attempt
        check = check_translation(item.source, target)
        if check.clean:
            store.save(item.id, target.strip(), st.DONE, model=translator.model)
            result.translated += 1
        elif item.status == st.PENDING:
            store.save(item.id, target, st.RETRY, check.describe(), translator.model)
        elif not check.blocking:
            store.save(item.id, target.strip(), st.REVIEW, check.describe(), translator.model)
            result.needs_review += 1
        else:
            store.save(item.id, None, st.FAILED, check.describe(), translator.model)
            result.failed += 1


def _split_blocked(batch, context, store, translator, brief, result, sleep, should_stop, on_progress, done, total, blocked) -> None:
    """Narrow down a refused batch, but not so far that it uses up the day's requests."""
    if len(batch) <= _SMALLEST_SPLIT:
        for item in batch:
            store.save(item.id, None, st.FAILED, f"refused by translator: {blocked}", translator.model)
        result.failed += len(batch)
        return
    middle = len(batch) // 2
    for part in (batch[:middle], batch[middle:]):
        _process(part, context, store, translator, brief, result, sleep, should_stop, on_progress, done, total)


def _request(translator, brief, lines, context, store, result, sleep, should_stop, on_progress, done, total) -> dict[int, str]:
    busy_tries = 0
    minute_waits = 0
    while True:
        try:
            result.requests += 1
            replies, usage = translator.translate(brief.system_prompt, lines, context)
        except RateLimited as limit:
            minute_waits += 1
            if limit.daily or minute_waits > _MAX_MINUTE_WAITS:
                raise
            on_progress(Progress(done, total, f"Pausing {limit.retry_after:.0f}s for the rate limit"))
            _wait(limit.retry_after + 1, sleep, should_stop)
            if should_stop():
                raise Overloaded("Stopped by the user") from None
            continue
        except Overloaded:
            if busy_tries >= len(_BUSY_WAITS):
                raise
            delay = _BUSY_WAITS[busy_tries]
            busy_tries += 1
            on_progress(Progress(done, total, f"Service is busy, retrying in {delay}s"))
            _wait(delay, sleep, should_stop)
            if should_stop():
                raise Overloaded("Stopped by the user") from None
            continue
        store.log_usage(
            translator.model, len(lines), usage.prompt_tokens, usage.output_tokens, usage.thought_tokens
        )
        return replies


def _wait(seconds: float, sleep, should_stop) -> None:
    remaining = seconds
    while remaining > 0 and not should_stop():
        step = min(1.0, remaining)
        sleep(step)
        remaining -= step

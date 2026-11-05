"""Translation pipeline, quality checks and the Hindi slot install, without calling Gemini."""
from pathlib import Path

import pytest
from conftest import open_original_core

from gamemod.adapters.base import InstallState, SourceLine
from gamemod.adapters.tlou1 import psarc
from gamemod.adapters.tlou1.adapter import _LANGUAGE_NAME_KEY, Tlou1Adapter, split_engine_tags
from gamemod.adapters.tlou1.text_table import TextTable
from gamemod.core import pipeline
from gamemod.core import store as st
from gamemod.core.pipeline import run_translation
from gamemod.core.store import Store
from gamemod.core.translate.base import Brief, ContentBlocked, Overloaded, RateLimited, Usage
from gamemod.core.translate.qa import check_translation

GAME = Path(r"D:\SteamLibrary\steamapps\common\The Last of Us Part I")
MAIN = GAME / "build" / "pc" / "main"
needs_game = pytest.mark.skipif(not MAIN.exists(), reason="game not installed")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("|@46| Oh shit.", ("|@46| ", "Oh shit.", "")),
        ("|#-225| Ellie! Stay back!", ("|#-225| ", "Ellie! Stay back!", "")),
        ("Yeah?\n", ("", "Yeah?", "\n")),
        ("|@66| Let's go.\n", ("|@66| ", "Let's go.", "\n")),
        ("  spaced  ", ("  ", "spaced", "  ")),
        ("plain", ("", "plain", "")),
        ("\n", ("", "", "\n")),
    ],
)
def test_engine_tags_are_separated_and_rejoin_exactly(text, expected):
    assert split_engine_tags(text) == expected
    assert "".join(split_engine_tags(text)) == text


@pytest.mark.parametrize(
    "source,target,blocks,warns",
    [
        ("Let's go.", "चलो।", False, False),
        ("Let's go.", "", True, False),
        ("Let's go.", "Let's go.", True, True),
        ("Let's go.", "चलो Давай", True, False),
        ("What about me?", "और मेरा क्या?ో", True, False),  # a real reply: stray Telugu letter
        ("Great idea.", "کسی کے دماغ", True, False),  # a real reply: Urdu script
        ("Great idea.", "बढ़िया कसी कॉफ़ी — हाँ… “ठीक”!", False, False),
        ("Let's go.", " ा", True, False),
        ("Hold |gen:fire| to heal.", "ठीक होने के लिए दबाओ।", True, False),
        ("Hold |gen:fire| to heal.", "ठीक होने के लिए |gen:fire| दबाओ।", False, False),
        ("Line<br>two", "पंक्ति दो", True, False),
        ("Go.", "चलो OK।", False, True),
        ("Only 3 left.", "बस तीन बचे हैं।", False, True),
        ("Only 3 left.", "बस 3 बचे हैं।", False, False),
        ("Only 3 left.", "बस ३ बचे हैं।", False, False),
    ],
)
def test_quality_checks(source, target, blocks, warns):
    check = check_translation(source, target)
    assert bool(check.blocking) == blocks
    assert bool(check.warnings) == warns


def _lines(*texts):
    return [SourceLine("t", f"k{i}", i, text, "") for i, text in enumerate(texts)]


class FakeTranslator:
    model = "fake"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def translate(self, system_prompt, lines, context):
        self.calls.append(lines)
        return self.behaviour(lines, len(self.calls)), Usage(10, 20, 5)


def _good(lines, _call):
    return {line.id: f"अनुवाद {line.text}".replace("Line", "पंक्ति") for line in lines}


def _store(tmp_path, *texts, fixed=None):
    store = Store(tmp_path / "t.sqlite")
    store.sync(_lines(*texts), fixed)
    return store


BRIEF = Brief("system")


def test_sync_classifies_lines_and_keeps_translations(tmp_path):
    store = _store(tmp_path, "Line one", "Joel", "...", "Line one", fixed={"Joel": "जोएल"})
    counts = store.counts()
    assert counts[st.PENDING] == 1 and counts[st.FIXED] == 1 and counts[st.SKIP] == 1

    item = store.pending(1)[0]
    store.save(item.id, "पहली", st.DONE)
    store.sync(_lines("Line one", "Joel", "...", "Line one"), {"Joel": "जोएल"})
    assert store.counts()[st.DONE] == 1
    assert store.line_targets()["t"]["k0"] == store.line_targets()["t"]["k3"] == "पहली"


def test_same_english_with_different_hint_stays_separate(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.sync(
        [
            SourceLine("t", "a", 0, "I'm hurt.", "Я ранен."),
            SourceLine("t", "b", 1, "I'm hurt.", "Я ранена."),
        ]
    )
    assert store.counts()[st.PENDING] == 2


def test_prefix_and_suffix_return_around_the_translation(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.sync([SourceLine("t", "a", 0, "Oh no", "", prefix="|@46| ", suffix="\n")])
    store.save(store.pending(1)[0].id, "अरे नहीं", st.DONE)
    assert store.line_targets()["t"]["a"] == "|@46| अरे नहीं\n"


def test_run_translates_everything_and_resumes_cleanly(tmp_path):
    store = _store(tmp_path, *[f"Line {i}" for i in range(95)])
    fake = FakeTranslator(_good)
    result = run_translation(store, fake, BRIEF, batch_size=40, max_requests=1)
    assert result.stopped == "request_limit" and result.translated == 40

    result = run_translation(store, fake, BRIEF, batch_size=40)
    assert result.stopped == "finished" and result.translated == 55
    assert len(fake.calls) == 3
    assert store.counts()[st.DONE] == 95
    assert store.usage_totals()["requests"] == 3


def test_rejected_line_is_retried_with_the_reason(tmp_path):
    store = _store(tmp_path, "Hold |gen:fire|")

    def behaviour(lines, call):
        return {lines[0].id: "दबाओ" if call == 1 else "|gen:fire| दबाओ"}

    fake = FakeTranslator(behaviour)
    result = run_translation(store, fake, BRIEF)
    assert result.translated == 1 and result.failed == 0
    assert "tags" in fake.calls[1][0].problem


def test_line_that_keeps_failing_falls_back_and_others_survive(tmp_path):
    store = _store(tmp_path, "Good one", "Bad one")

    def behaviour(lines, _call):
        return {line.id: "ठीक" for line in lines if line.text == "Good one"}

    result = run_translation(store, FakeTranslator(behaviour), BRIEF)
    assert result.translated == 1 and result.failed == 1
    targets = store.line_targets()["t"]
    assert "k0" in targets and "k1" not in targets
    assert store.retry_failed() == 1


def test_warning_only_line_is_kept_for_review(tmp_path):
    store = _store(tmp_path, "Only 3 left")
    result = run_translation(store, FakeTranslator(lambda l, _c: {l[0].id: "कुछ बचे हैं"}), BRIEF)
    assert result.needs_review == 1
    assert store.counts()[st.REVIEW] == 1
    assert store.line_targets()["t"]["k0"] == "कुछ बचे हैं"


class _RefusesLine5(FakeTranslator):
    def translate(self, system_prompt, lines, context):
        self.calls.append(lines)
        if any(line.text == "Line 5" for line in lines):
            raise ContentBlocked("refused")
        return _good(lines, 0), Usage()


def test_refused_batch_narrows_down_but_stops_before_using_many_requests(tmp_path):
    store = _store(tmp_path, *[f"Line {i}" for i in range(100)])
    translator = _RefusesLine5(None)
    result = run_translation(store, translator, BRIEF, batch_size=100)
    # 100 -> 50 -> 25 lines. The 25 containing the bad line show in English and can be retried.
    assert result.failed == 25 and result.translated == 75
    assert len(translator.calls) == 5
    assert store.retry_failed() == 25


def test_refused_batch_can_reach_the_single_bad_line(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_SMALLEST_SPLIT", 1)
    store = _store(tmp_path, *[f"Line {i}" for i in range(8)])
    result = run_translation(store, _RefusesLine5(None), BRIEF)
    assert result.translated == 7 and result.failed == 1


def test_rejected_lines_are_retried_together_at_the_end(tmp_path):
    """The free limit counts requests, so rejects must not cost a request per batch."""
    store = _store(tmp_path, *[f"Line {i}" for i in range(30)])

    def behaviour(lines, _call):
        return {
            line.id: "अनुवाद " + line.text.split()[1]
            for line in lines
            if line.problem or line.text not in ("Line 0", "Line 15", "Line 25")
        }

    fake = FakeTranslator(behaviour)
    result = run_translation(store, fake, BRIEF, batch_size=10)
    assert result.stopped == "finished" and result.translated == 30
    assert len(fake.calls) == 4, "three batches plus one shared retry"
    assert [line.text for line in fake.calls[3]] == ["Line 0", "Line 15", "Line 25"]


def test_lines_waiting_for_retry_still_count_as_unfinished_when_stopped(tmp_path):
    store = _store(tmp_path, "First line", "Second line")

    def behaviour(lines, _call):
        return {lines[0].id: "ठीक"}

    run_translation(store, FakeTranslator(behaviour), BRIEF, max_requests=1)
    assert store.counts()[st.RETRY] == 1
    assert store.count_pending() == 1
    assert "k1" not in store.line_targets()["t"], "unfinished lines stay in English"


def test_daily_limit_stops_cleanly_and_keeps_progress(tmp_path):
    store = _store(tmp_path, *[f"Line {i}" for i in range(50)])

    class Limited(FakeTranslator):
        def translate(self, system_prompt, lines, context):
            self.calls.append(lines)
            if len(self.calls) > 1:
                raise RateLimited("limit", 60, daily=True)
            return _good(lines, 0), Usage()

    result = run_translation(store, Limited(None), BRIEF, batch_size=40)
    assert result.stopped == "daily_limit" and result.translated == 40
    assert store.count_pending() == 10


def test_minute_limit_and_busy_wait_then_continue(tmp_path):
    store = _store(tmp_path, "Line 1")
    waits = []

    class Flaky(FakeTranslator):
        def translate(self, system_prompt, lines, context):
            self.calls.append(lines)
            if len(self.calls) == 1:
                raise RateLimited("limit", 2, daily=False)
            if len(self.calls) == 2:
                raise Overloaded("busy")
            return _good(lines, 0), Usage()

    result = run_translation(store, Flaky(None), BRIEF, sleep=waits.append)
    assert result.stopped == "finished" and result.translated == 1
    assert sum(waits) == 63  # 3 seconds for the rate limit, 60 for the first busy reply


def test_manual_translations_go_through_the_same_checks(tmp_path):
    store = _store(tmp_path, "First line", "Hold |gen:fire|", "Only 3 left")
    ids = {item.source: item.id for item in store.pending(10)}
    result = pipeline.apply_translations(
        store,
        {
            ids["First line"]: "पहली पंक्ति।",
            ids["Hold |gen:fire|"]: "दबाओ।",  # the tag is missing
            ids["Only 3 left"]: "कुछ ही बचे हैं।",  # the number is missing
            999999: "चलो।",
        },
        "claude",
    )
    assert result.saved == 2
    assert {item_id for item_id, _ in result.rejected} == {ids["Hold |gen:fire|"], 999999}
    assert [item_id for item_id, _ in result.needs_review] == [ids["Only 3 left"]]
    # The rejected line stays unfinished, so it is offered again.
    assert [item.source for item in store.pending(10)] == ["Hold |gen:fire|"]
    assert store.counts()[st.REVIEW] == 1


def test_repeats_are_translated_once_unless_gender_could_matter(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.sync(
        [
            SourceLine("t", "a", 0, "Okay.", "Ладно."),
            SourceLine("t", "b", 1, "Okay.", "Хорошо."),
            SourceLine("t", "c", 2, "I'm hurt.", "Я ранен."),
            SourceLine("t", "d", 3, "I'm hurt.", "Я ранена."),
            SourceLine("t", "e", 4, "Run!", ""),
        ]
    )
    units = pipeline.lines_to_translate(store)
    assert [(u.source, u.hint) for u in units] == [
        ("Okay.", "Ладно."),
        ("I'm hurt.", "Я ранен."),
        ("I'm hurt.", "Я ранена."),
        ("Run!", ""),
    ]

    pipeline.apply_translations(store, {units[0].id: "ठीक है।"}, "claude")
    assert store.fill_duplicates(pipeline.can_share_translation) == 1
    targets = store.line_targets()["t"]
    assert targets["a"] == targets["b"] == "ठीक है।"

    # A gender-dependent line is never copied onto its twin.
    pipeline.apply_translations(store, {units[1].id: "मैं घायल हूँ।"}, "claude")
    assert store.fill_duplicates(pipeline.can_share_translation) == 0
    assert "d" not in store.line_targets()["t"]


def test_batches_respect_both_limits_and_keep_order():
    item = lambda n, size: st.Item(n, "x" * size, "", None, st.PENDING, "", n)  # noqa: E731
    items = [item(1, 10), item(2, 10), item(3, 10), item(4, 100), item(5, 5)]
    batches = pipeline.split_into_batches(items, max_lines=2, max_chars=50)
    assert [[i.id for i in b] for b in batches] == [[1, 2], [3], [4], [5]]


def test_hand_edits_are_never_overwritten(tmp_path):
    store = _store(tmp_path, "Line one")
    item = store.pending(1)[0]
    store.edit(item.id, "मेरा अनुवाद")
    store.save(item.id, "मशीन", st.DONE)
    assert store.line_targets()["t"]["k0"] == "मेरा अनुवाद"


@needs_game
def test_slot_files_leave_english_and_menus_alone():
    adapter = Tlou1Adapter(GAME)
    key = None
    with open_original_core() as core:
        english = TextTable.parse(core.read("text2/eng.subtitles"))
        record = next(r for r in english.records if r.text == b"Oh shit.")
        key = record.key
        files = adapter.build_files(core, {"subtitles": {f"{key:016x}": "अरे धत्।"}})

        # English subtitles are not touched at all.
        assert "text2/eng.subtitles" not in files
        assert "text2/eng.subtitles-systemic" not in files
        assert "text2/hrv.subtitles" in files and "text2/hrv.subtitles-systemic" in files

        # Only the one language name differs in the English menus.
        before = TextTable.parse(core.read("text2/eng.common")).as_dict()
        after = TextTable.parse(files["text2/eng.common"]).as_dict()
        changed = [k for k in before if before[k] != after[k]]
        assert changed == [_LANGUAGE_NAME_KEY]
        assert before[_LANGUAGE_NAME_KEY] == "Hrvatski"

        # Choosing Hindi as the text language keeps menus in English.
        slot_menus = TextTable.parse(files["text2/hrv.common"]).as_dict()
        assert all(slot_menus[k] == after[k] for k in after)

        # The translated line is Hindi and an untranslated one falls back to English.
        slot = TextTable.parse(files["text2/hrv.subtitles"]).as_dict()
        assert all(0xE900 <= ord(c) <= 0xF6BF or c in " ।" for c in slot[key])
        other = next(r for r in english.records if r.text and r.key != key)
        assert slot[other.key] == other.text.decode("utf-8")


@needs_game
def test_lines_extract_and_cover_both_tables():
    lines = Tlou1Adapter(GAME).extract_lines()
    tables = {line.table for line in lines}
    assert tables == {"subtitles", "subtitles-systemic"}
    assert all(line.text and line.text == line.text.strip() for line in lines)
    assert all(not line.text.startswith("|@") for line in lines)


@needs_game
def test_install_state_on_a_clean_game(tmp_path):
    # With no backup of its own, a copy of the app still notices a patch made by another copy.
    expected = (
        InstallState.INSTALLED_ELSEWHERE
        if psarc.is_patched(MAIN / "core.psarc")
        else InstallState.NOT_INSTALLED
    )
    assert Tlou1Adapter(GAME, work_dir=tmp_path).state() is expected
    assert psarc.read_patch_state(tmp_path / "core.backup") is None

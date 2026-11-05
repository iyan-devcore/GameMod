"""Tests for RE Engine archives, localization (.msg), font encryption, and timed entries."""
from __future__ import annotations

import re
from pathlib import Path
import pytest

from gamemod.adapters.re_engine.pak import PakReader, write_pak, murmur3_32, hash_re_path
from gamemod.adapters.re_engine.msg import MsgFile, decrypt_msg, encrypt_msg
from gamemod.adapters.re_engine.font import encrypt_font, decrypt_font
from gamemod.adapters.re2.paths import ACTIVE_MSG_PATHS, HEALTHWARNING_PATH_PL, HEALTHWARNING_PATH_EN, FONTS
from gamemod.adapters.re2.timed import is_timed_entry, split_timed_entry, build_timed_entry

RE2_GAME = Path(r"D:\SteamLibrary\steamapps\common\RESIDENT EVIL 2  BIOHAZARD RE2")
needs_re2 = pytest.mark.skipif(not RE2_GAME.exists(), reason="RE2 game not installed")


@pytest.fixture(scope="module")
def re2_reader():
    if not RE2_GAME.exists():
        pytest.skip("RE2 game not installed")
    pak_files = sorted(RE2_GAME.glob("*.pak"))
    return PakReader(pak_files)


def test_murmur3_32():
    # Test vector against Murmur3-32 with seed 0 and 0xFFFFFFFF
    assert murmur3_32(b"", 0) == 0
    assert murmur3_32(b"", 0xFFFFFFFF) == 2180083513
    h1 = murmur3_32(b"test", 0xFFFFFFFF)
    assert isinstance(h1, int) and 0 <= h1 <= 0xFFFFFFFF
    # Case differences produce different hashes
    h_low, h_hi = hash_re_path("natives/stm/test.msg")
    assert h_low != h_hi


def test_pak_reader_and_writer_synthetic(tmp_path: Path):
    target_pak = tmp_path / "test.pak"
    files = {
        "natives/stm/message/test.msg": b"hello test message",
        "natives/stm/textures/art.tex": b"\x00\x01\x02\x03\x04\x05",
    }
    write_pak(target_pak, files)
    assert target_pak.exists() and target_pak.stat().st_size > 16

    reader = PakReader([target_pak])
    assert reader.contains("natives/stm/message/test.msg")
    assert reader.contains("natives/stm/textures/art.tex")
    assert not reader.contains("natives/stm/nonexistent.msg")

    assert reader.get("natives/stm/message/test.msg") == b"hello test message"
    assert reader.get("natives/stm/textures/art.tex") == b"\x00\x01\x02\x03\x04\x05"


def test_font_cipher_roundtrip():
    original_data = b"OpenTypeFontSampleData\x01\x02\x03\x04\xFF\xFE\xFD" * 16
    encrypted = encrypt_font(original_data)
    assert encrypted[:4] == b"FBFO"
    assert encrypted != original_data
    decrypted = decrypt_font(encrypted)
    assert decrypted == original_data


def test_timed_entry_split_and_rebuild_synthetic():
    sample = "<TIME 10.0, 12.5><TIME 13.0, 15.0>Hello world!<PAGE>\r\nHow are you?<PAGE>\r\nGoodbye!"
    assert is_timed_entry(sample)
    leading, times, pieces = split_timed_entry(sample)
    assert len(times) == 2
    assert len(pieces) == 3
    rebuilt = build_timed_entry(leading, pieces)
    assert rebuilt == sample

    # Test with modified translated pieces
    hi_pieces = [
        (pieces[0][0], "नमस्ते दुनिया!", pieces[0][2]),
        (pieces[1][0], "आप कैसे हैं?", pieces[1][2]),
        (pieces[2][0], "अलविदा!", pieces[2][2]),
    ]
    hi_rebuilt = build_timed_entry(leading, hi_pieces)
    assert "<TIME 10.0, 12.5><TIME 13.0, 15.0>" in hi_rebuilt
    assert "<PAGE>" in hi_rebuilt
    assert "नमस्ते दुनिया!" in hi_rebuilt
    assert "आप कैसे हैं?" in hi_rebuilt


@needs_re2
def test_all_100_timed_entries_match_english_tags_and_pages(re2_reader: PakReader):
    """Verify that <TIME> and <PAGE> counts and order match English in all 100 timed entries."""
    timed_count = 0
    time_tag_pattern = re.compile(r"<TIME [^>]+>")

    for path in ACTIVE_MSG_PATHS:
        raw = re2_reader.get(path)
        msg = MsgFile(raw)
        for entry in msg.entries:
            eng_text = entry.texts[1]
            if is_timed_entry(eng_text):
                timed_count += 1
                leading, times, pieces = split_timed_entry(eng_text)
                assert len(pieces) > 0

                # Check exact reconstruction on original
                rebuilt_orig = build_timed_entry(leading, pieces)
                assert rebuilt_orig == eng_text, f"Rebuild mismatch on {entry.name}"

                # Mock translated pieces with Hindi text
                mock_pieces = [
                    (prefix, f"हिन्दी_{i}_{core}", suffix)
                    for i, (prefix, core, suffix) in enumerate(pieces)
                ]
                rebuilt_hi = build_timed_entry(leading, mock_pieces)

                # Check <TIME> tag counts and sequence match English
                eng_times = time_tag_pattern.findall(eng_text)
                hi_times = time_tag_pattern.findall(rebuilt_hi)
                assert hi_times == eng_times, f"<TIME> mismatch in {entry.name}"

                # Check <PAGE> tag count matches English
                assert rebuilt_hi.count("<PAGE>") == eng_text.count("<PAGE>"), f"<PAGE> count mismatch in {entry.name}"

    assert timed_count == 100, f"Expected exactly 100 timed entries, found {timed_count}"


@needs_re2
def test_all_234_msg_files_unmodified_rebuild_byte_perfect(re2_reader: PakReader):
    """Rebuilding without modification must match original raw bytes across all 234 files."""
    assert len(ACTIVE_MSG_PATHS) == 234
    for path in ACTIVE_MSG_PATHS:
        raw = re2_reader.get(path)
        msg = MsgFile(raw)
        rebuilt = msg.rebuild()
        assert rebuilt == raw, f"Rebuild mismatch on unmodified file: {path}"


@needs_re2
def test_all_234_msg_files_slots_unchanged_except_7(re2_reader: PakReader):
    """Only slot 7 (Polish) may differ from original. Slots 0-6 and 8-31 must remain byte-identical."""
    for path in ACTIVE_MSG_PATHS:
        raw = re2_reader.get(path)
        msg_orig = MsgFile(raw)
        first_entry_name = msg_orig.entries[0].name if msg_orig.entries else None
        slot_7_trans = {first_entry_name: "परीक्षण अनुवाद"} if first_entry_name else {}

        # Rebuild with mock translation and copy English for remaining
        mod_raw = msg_orig.rebuild(slot_7_translations=slot_7_trans, copy_english_for_remaining=True)
        msg_mod = MsgFile(mod_raw)

        assert len(msg_mod.entries) == len(msg_orig.entries)
        for orig_e, mod_e in zip(msg_orig.entries, msg_mod.entries):
            # All other language slots must be 100% identical to original
            for slot_idx in range(msg_orig.lang_count):
                if slot_idx == 7:
                    if orig_e.name == first_entry_name:
                        assert mod_e.texts[slot_idx] == "परीक्षण अनुवाद"
                    else:
                        assert mod_e.texts[slot_idx] == orig_e.texts[1]
                else:
                    assert mod_e.texts[slot_idx] == orig_e.texts[slot_idx], (
                        f"Slot {slot_idx} was modified in {orig_e.name} of {path}"
                    )


@needs_re2
def test_healthwarning_textures_exist(re2_reader: PakReader):
    """Check that both Polish and English health warning textures exist in the game archives."""
    assert re2_reader.contains(HEALTHWARNING_PATH_PL)
    assert re2_reader.contains(HEALTHWARNING_PATH_EN)
    en_bytes = re2_reader.get(HEALTHWARNING_PATH_EN)
    assert len(en_bytes) > 0

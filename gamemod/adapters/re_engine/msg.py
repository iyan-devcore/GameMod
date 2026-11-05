"""RE Engine GMSG (.msg) file parser, decryptor, and builder."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Mapping, Sequence

# 16-byte XOR feedback key used for RE Engine message files
MSG_KEY = bytes([
    0xCF, 0xCE, 0xFB, 0xF8, 0xEC, 0x0A, 0x33, 0x66,
    0x93, 0xA9, 0x1D, 0x93, 0x50, 0x39, 0x5F, 0x09,
])

GMSG_MAGIC = b"GMSG"


def decrypt_msg(data: bytes) -> tuple[bytearray, int]:
    """Decrypt the string pool portion of an RE Engine GMSG file."""
    data_off = struct.unpack_from("<Q", data, 0x20)[0]
    out = bytearray(data)
    prev = 0
    for i in range(len(data) - data_off):
        cur = data[data_off + i]
        out[data_off + i] = cur ^ prev ^ MSG_KEY[i & 0x0F]
        prev = cur
    return out, data_off


def encrypt_msg(data: bytearray, data_off: int) -> bytes:
    """Encrypt the string pool portion of an RE Engine GMSG file."""
    out = bytearray(data)
    cur = 0
    for i in range(len(data) - data_off):
        prev = data[data_off + i]
        enc = cur ^ prev ^ MSG_KEY[i & 0x0F]
        out[data_off + i] = enc
        cur = enc
    return bytes(out)


@dataclass(frozen=True)
class MsgEntry:
    uuid: bytes
    sound_id: int
    hash_or_idx: int
    name: str
    texts: tuple[str, ...]  # text per language slot


class MsgFile:
    """Parses and updates RE Engine .msg localization files."""

    def __init__(self, raw: bytes):
        self._raw = raw
        self._dec, self._data_off = decrypt_msg(raw)

        (
            self.version,
            self.magic,
            self.hdr_off,
            self.entry_count,
            self.attr_count,
            self.lang_count,
            _,
        ) = struct.unpack_from("<I4sQIIII", self._dec, 0)

        if self.magic != GMSG_MAGIC:
            raise ValueError(f"Invalid GMSG magic: {self.magic}")

        self.data_off = self._data_off
        self.zero_off, self.lang_off, self.attr_off, self.attr_name_off = struct.unpack_from(
            "<QQQQ", self._dec, 0x28
        )

        self.languages = [
            struct.unpack_from("<i", self._dec, self.lang_off + i * 4)[0]
            for i in range(self.lang_count)
        ]
        self.entry_offsets = [
            struct.unpack_from("<Q", self._dec, 0x48 + i * 8)[0]
            for i in range(self.entry_count)
        ]

        self.entries: list[MsgEntry] = []
        for e_off in self.entry_offsets:
            uuid = bytes(self._dec[e_off : e_off + 16])
            sound_id, hash_or_idx = struct.unpack_from("<II", self._dec, e_off + 16)
            name_off, _ = struct.unpack_from("<QQ", self._dec, e_off + 24)
            name = self._read_str(name_off)

            texts: list[str] = []
            for l_idx in range(self.lang_count):
                t_off = struct.unpack_from("<Q", self._dec, e_off + 40 + l_idx * 8)[0]
                texts.append(self._read_str(t_off))

            self.entries.append(
                MsgEntry(
                    uuid=uuid,
                    sound_id=sound_id,
                    hash_or_idx=hash_or_idx,
                    name=name,
                    texts=tuple(texts),
                )
            )

    def _read_str(self, offset: int) -> str:
        if offset >= len(self._dec):
            return ""
        end = offset
        while end + 1 < len(self._dec) and not (
            self._dec[end] == 0 and self._dec[end + 1] == 0
        ):
            end += 2
        return self._dec[offset:end].decode("utf-16le", errors="replace")

    def rebuild(
        self,
        slot_7_translations: Mapping[str, str] | None = None,
        copy_english_for_remaining: bool = False,
    ) -> bytes:
        """Rebuild the message file.

        If `slot_7_translations` is None and `copy_english_for_remaining` is False,
        returns the original file byte-for-byte.
        Otherwise, only slot 7 (Polish) is modified; all other language slots
        and original string tables are preserved exactly.
        """
        if slot_7_translations is None and not copy_english_for_remaining:
            return encrypt_msg(self._dec, self._data_off)

        # Clone the decrypted file structure
        modified = bytearray(self._dec)
        appended_pool = bytearray()
        str_offset_cache: dict[str, int] = {}

        for idx, e_off in enumerate(self.entry_offsets):
            entry = self.entries[idx]
            if slot_7_translations is not None and entry.name in slot_7_translations:
                # Put Hindi translation into slot 7
                text = slot_7_translations[entry.name]
                # Convert \n to \r\n for RE Engine text if needed
                text_clean = text.replace("\r\n", "\n").replace("\n", "\r\n")
                if text_clean in str_offset_cache:
                    new_off = str_offset_cache[text_clean]
                else:
                    new_off = len(modified) + len(appended_pool)
                    encoded = text_clean.encode("utf-16le") + b"\x00\x00"
                    appended_pool.extend(encoded)
                    str_offset_cache[text_clean] = new_off
                struct.pack_into("<Q", modified, e_off + 40 + 7 * 8, new_off)
            elif copy_english_for_remaining:
                # Set slot 7 pointer directly to slot 1 (English) pointer
                eng_off = struct.unpack_from("<Q", self._dec, e_off + 40 + 1 * 8)[0]
                struct.pack_into("<Q", modified, e_off + 40 + 7 * 8, eng_off)

        full_data = modified + appended_pool
        return encrypt_msg(full_data, self._data_off)

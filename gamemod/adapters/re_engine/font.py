"""RE Engine encrypted font (.oft.1) cipher and utilities."""
from __future__ import annotations

import struct

FONT_FBFO_MAGIC = b"FBFO"
FONT_CIPHER_DELTA = 0xAE6E39B58A355F45


def crypt_font(buffer: bytearray) -> bytearray:
    """Symmetric cipher for RE Engine font assets."""
    dw_seed = 1
    dw_size = len(buffer) & 0x3F
    if dw_size > 0:
        for _ in range(dw_size):
            dw_seed = (2 * dw_seed + 1) & 0xFFFFFFFFFFFFFFFF

    part1 = (FONT_CIPHER_DELTA >> dw_size) & 0xFFFFFFFFFFFFFFFF
    part2 = ((dw_seed & FONT_CIPHER_DELTA) << (64 - dw_size)) & 0xFFFFFFFFFFFFFFFF
    dw_key = (part1 | part2) & 0xFFFFFFFFFFFFFFFF

    lp_key = struct.pack("<Q", dw_key)
    out = bytearray(len(buffer))
    for i in range(len(buffer)):
        out[i] = buffer[i] ^ lp_key[i % 8]
    return out


def decrypt_font(file_bytes: bytes) -> bytes:
    """Decrypt an RE Engine .oft.1 font file into standard TTF/OTF bytes."""
    if file_bytes[:4] == FONT_FBFO_MAGIC:
        encrypted_body = bytearray(file_bytes[4:])
        return bytes(crypt_font(encrypted_body))
    return file_bytes


def encrypt_font(font_bytes: bytes) -> bytes:
    """Encrypt standard TTF/OTF bytes into an RE Engine .oft.1 font file."""
    encrypted_body = crypt_font(bytearray(font_bytes))
    return FONT_FBFO_MAGIC + bytes(encrypted_body)


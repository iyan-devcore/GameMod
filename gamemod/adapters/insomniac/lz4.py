"""LZ4 block decompression in plain Python.

Only a few assets are ever read (the text tables and fonts), so speed matters little and no
compiled package is needed.
"""
from __future__ import annotations


def decompress_block(src: bytes, size: int) -> bytes:
    """Decode one raw LZ4 block that unpacks to `size` bytes."""
    dst = bytearray()
    i = 0
    end = len(src)
    while i < end:
        token = src[i]
        i += 1
        literals = token >> 4
        if literals == 15:
            while True:
                extra = src[i]
                i += 1
                literals += extra
                if extra != 255:
                    break
        dst += src[i : i + literals]
        i += literals
        if i >= end or len(dst) >= size:
            break
        distance = src[i] | (src[i + 1] << 8)
        i += 2
        length = token & 15
        if length == 15:
            while True:
                extra = src[i]
                i += 1
                length += extra
                if extra != 255:
                    break
        length += 4
        start = len(dst) - distance
        if distance <= 0 or start < 0:
            raise ValueError("corrupt LZ4 block")
        if distance >= length:
            dst += dst[start : start + length]
        else:  # the copy overlaps what it is writing, so it repeats a short pattern
            pattern = bytes(dst[start:])
            repeats, remainder = divmod(length, distance)
            dst += pattern * repeats + pattern[:remainder]
    if len(dst) != size:
        raise ValueError(f"LZ4 block gave {len(dst)} bytes, expected {size}")
    return bytes(dst)

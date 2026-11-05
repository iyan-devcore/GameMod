"""Make Hindi display in game engines that cannot shape complex scripts.

Each syllable cluster is drawn ahead of time as one whole glyph and given a Private
Use code. The engine then treats every cluster as a single ordinary character.
"""
from .clusters import (
    assign_codepoints,
    encode_text,
    find_broken_marks,
    iter_clusters,
    normalize,
    unique_clusters,
)
from .fontbuild import FontStyle, merge_into_font

__all__ = [
    "FontStyle",
    "assign_codepoints",
    "encode_text",
    "find_broken_marks",
    "iter_clusters",
    "merge_into_font",
    "normalize",
    "unique_clusters",
]

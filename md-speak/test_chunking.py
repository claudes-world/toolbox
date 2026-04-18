#!/usr/bin/env python3
"""Tests for TTS chunking in md-speak."""
import html
import importlib.machinery
import importlib.util
import sys
import xml.etree.ElementTree as ET

# Load md-speak module (hyphenated filename, not directly importable)
from importlib.machinery import SourceFileLoader
loader = SourceFileLoader('md_speak', '/home/claude/code/toolbox/md-speak/md-speak')
mod = loader.load_module()

segments_to_ssml_batches = mod.segments_to_ssml_batches
split_large_segment = mod.split_large_segment
SSML_BYTE_LIMIT = mod.SSML_BYTE_LIMIT
Segment = mod.Segment


def test_byte_limit_compliance():
    """All batches must be <= SSML_BYTE_LIMIT bytes."""
    big_text = "This is a test sentence. " * 300  # ~7500 bytes
    segs = [Segment(text=big_text, voice='en-US-Neural2-A', pause_before=0, pause_after=0)]
    batches = segments_to_ssml_batches(segs)
    for ssml, _ in batches:
        size = len(ssml.encode('utf-8'))
        assert size <= SSML_BYTE_LIMIT, f"Batch exceeds limit: {size} bytes"
    print(f"✓ byte_limit_compliance: {len(batches)} batches")


def test_content_preservation():
    """All words must appear in output with no truncation or duplication.

    Word-token comparison: single spaces may be lost at sentence-boundary chunk
    breaks (Segment.strip() eats leading/trailing spaces), but this has no effect
    on TTS output quality — Google TTS naturally pauses at sentence boundaries.
    """
    import re as _re
    big_text = "Hello world. " * 400
    segs = [Segment(text=big_text, voice='en-US-Neural2-A', pause_before=0, pause_after=0)]
    batches = segments_to_ssml_batches(segs)
    # Extract text from all SSML batches (space-separated across batches/paragraphs)
    parts_text = []
    for ssml, _ in batches:
        root = ET.fromstring(ssml)
        for p in root.iter('p'):
            if p.text:
                parts_text.append(p.text)
    combined = ' '.join(parts_text)
    # Compare word tokens (whitespace-insensitive)
    tokenize = lambda s: _re.findall(r'\S+', html.unescape(s))
    got_tokens = tokenize(combined)
    expected_tokens = tokenize(big_text)
    assert got_tokens == expected_tokens, (
        f"Word-token mismatch: expected {len(expected_tokens)} tokens, got {len(got_tokens)}"
    )
    print(f"✓ content_preservation")


def test_ssml_well_formedness():
    """Every batch must be valid XML."""
    big_text = "Test sentence with & ampersand and <brackets>. " * 200
    segs = [Segment(text=big_text, voice='en-US-Neural2-A', pause_before=0, pause_after=0)]
    batches = segments_to_ssml_batches(segs)
    for ssml, _ in batches:
        ET.fromstring(ssml)  # raises if invalid XML
    print(f"✓ ssml_well_formedness")


def test_short_doc_single_batch():
    """Short docs (<4500 bytes) produce exactly 1 batch."""
    short_text = "Short text."
    segs = [Segment(text=short_text, voice='en-US-Neural2-A', pause_before=0, pause_after=0)]
    batches = segments_to_ssml_batches(segs)
    assert len(batches) == 1, f"Expected 1 batch, got {len(batches)}"
    print(f"✓ short_doc_single_batch")


def test_split_large_segment_no_truncation():
    """split_large_segment preserves all words (word-token comparison)."""
    import re as _re
    long_text = "Word " * 2000  # ~10000 bytes
    seg = Segment(text=long_text, voice='en-US-Neural2-A', pause_before=0.5, pause_after=1.0)
    result = split_large_segment(seg)
    assert result[0].pause_before == 0.5, f"First sub-seg should inherit pause_before"
    assert result[-1].pause_after == 1.0, f"Last sub-seg should inherit pause_after"
    tokenize = lambda s: _re.findall(r'\S+', s)
    rejoined_tokens = [tok for s in result for tok in tokenize(s.text)]
    expected_tokens = tokenize(long_text)
    assert rejoined_tokens == expected_tokens, (
        f"Word tokens not fully preserved: got {len(rejoined_tokens)}, expected {len(expected_tokens)}"
    )
    print(f"✓ split_large_segment_no_truncation: {len(result)} sub-segments")


if __name__ == '__main__':
    test_short_doc_single_batch()
    test_byte_limit_compliance()
    test_content_preservation()
    test_ssml_well_formedness()
    test_split_large_segment_no_truncation()
    print("All tests passed.")

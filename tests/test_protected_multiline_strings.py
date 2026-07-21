from __future__ import annotations

from loommux.source_transform import prepare_protected_multiline_strings


def _run(source: str) -> dict[str, object]:
    namespace: dict[str, object] = {}
    exec(prepare_protected_multiline_strings(source).submitted_source, namespace)
    return namespace


def test_converts_complete_literal_without_interpreting_its_contents() -> None:
    source = '''name = "Ada"
payload = f"""
*** Begin Patch suffix
text = r"""
C:\new\temp {name}
~~~python
# loommux: timeout_seconds=120
~~~
*** End Patch suffix
"""
'''

    prepared = prepare_protected_multiline_strings(source)

    assert prepared.applied is True
    assert prepared.literal_count == 1
    assert prepared.submitted_source != source
    assert _run(source)["payload"] == '\n*** Begin Patch suffix\ntext = r"""\nC:\new\temp {name}\n~~~python\n# loommux: timeout_seconds=120\n~~~\n*** End Patch suffix\n'
    assert prepared.author_ranges[0].as_dict() == {"start": {"line": 1, "column": 10}, "end": {"line": 10, "column": 3}}
    assert prepared.line_map == tuple({"author_line": line, "submitted_line": line} for line in range(11))


def test_recognizes_markers_only_at_physical_column_zero_and_preserves_suffixes() -> None:
    source = '''payload = """
*** Begin Splice metadata
    *** Begin Patch
text *** End Patch
*** End Splice metadata
"""
'''

    assert _run(source)["payload"] == '\n*** Begin Splice metadata\n    *** Begin Patch\ntext *** End Patch\n*** End Splice metadata\n'


def test_converts_multiple_literals_in_source_order() -> None:
    source = '''first = r"""
*** Begin one
first
*** End one
"""
second = """
*** Begin two
second
*** End two
"""
'''

    prepared = prepare_protected_multiline_strings(source)

    assert prepared.literal_count == 2
    assert _run(source)["first"] == "\n*** Begin one\nfirst\n*** End one\n"
    assert _run(source)["second"] == "\n*** Begin two\nsecond\n*** End two\n"
    assert [source_range.start.line for source_range in prepared.author_ranges] == [0, 5]


def test_unclosed_candidate_leaves_the_entire_cell_unchanged() -> None:
    source = '''valid = """
*** Begin valid
text
*** End valid
"""
broken = """
*** Begin broken
text
'''

    prepared = prepare_protected_multiline_strings(source)

    assert prepared.applied is False
    assert prepared.literal_count == 0
    assert prepared.submitted_source == source
    assert prepared.author_ranges == ()


def test_non_column_zero_begin_is_not_a_candidate() -> None:
    source = '''payload = """
    *** Begin Patch
text
*** End Patch
"""
'''

    prepared = prepare_protected_multiline_strings(source)

    assert prepared.applied is False
    assert prepared.submitted_source == source


def test_marker_shaped_text_inside_an_ordinary_string_or_comment_is_unchanged() -> None:
    source = '''payload = \'\'\'prefix
"""
*** Begin
contents
*** End
"""
suffix\'\'\'
# """
# *** Begin
# *** End
# """
'''

    prepared = prepare_protected_multiline_strings(source)

    assert prepared.applied is False
    assert prepared.submitted_source == source
    assert _run(source)["payload"] == 'prefix\n"""\n*** Begin\ncontents\n*** End\n"""\nsuffix'

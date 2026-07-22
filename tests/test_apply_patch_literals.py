from __future__ import annotations

from loommux.source_transform import prepare_apply_patch_literals


def _run(source: str) -> dict[str, object]:
    namespace: dict[str, object] = {}
    exec(prepare_apply_patch_literals(source).submitted_source, namespace)
    return namespace


def test_converts_a_valid_apply_patch_literal_without_interpreting_its_contents() -> None:
    source = '''name = "Ada"
payload = f"""
*** Begin Patch
*** Update File: example.py
@@
+message = r"""
+C:\\new\\temp {name}
+"""
*** End Patch
"""
'''

    prepared = prepare_apply_patch_literals(source)

    assert prepared.applied is True
    assert prepared.literal_count == 1
    assert prepared.submitted_source != source
    assert _run(source)["payload"] == '\n*** Begin Patch\n*** Update File: example.py\n@@\n+message = r"""\n+C:\\new\\temp {name}\n+"""\n*** End Patch\n'
    assert prepared.line_map == tuple({"author_line": line, "submitted_line": line} for line in range(10))


def test_converts_multiple_valid_apply_patch_literals_in_source_order() -> None:
    source = '''first = """
*** Begin Patch
*** Add File: first.txt
+first
*** End Patch
"""
second = """
*** Begin Patch
*** Delete File: second.txt
*** End Patch
"""
'''

    prepared = prepare_apply_patch_literals(source)

    assert prepared.literal_count == 2
    assert _run(source)["first"] == "\n*** Begin Patch\n*** Add File: first.txt\n+first\n*** End Patch\n"
    assert _run(source)["second"] == "\n*** Begin Patch\n*** Delete File: second.txt\n*** End Patch\n"


def test_begin_end_shaped_text_with_invalid_patch_grammar_is_not_converted() -> None:
    source = '''payload = """
*** Begin Patch
this is not a patch hunk
*** End Patch
"""
'''

    prepared = prepare_apply_patch_literals(source)

    assert prepared.applied is False
    assert prepared.submitted_source == source


def test_normal_python_triple_quoted_string_is_not_an_apply_patch_literal() -> None:
    source = '''payload = """
ordinary text
"""
'''

    prepared = prepare_apply_patch_literals(source)

    assert prepared.applied is False
    assert prepared.submitted_source == source
    assert _run(source)["payload"] == "\nordinary text\n"


def test_incomplete_or_non_column_zero_patch_candidate_is_not_converted() -> None:
    source = '''payload = """
    *** Begin Patch
*** Add File: example.txt
+text
*** End Patch
"""
'''

    prepared = prepare_apply_patch_literals(source)

    assert prepared.applied is False
    assert prepared.submitted_source == source


def test_invalid_update_operations_are_not_converted() -> None:
    source = '''payload = """
*** Begin Patch
*** Update File: example.py
*** End Patch
"""
'''

    assert prepare_apply_patch_literals(source).applied is False


def test_directive_line_cannot_be_part_of_a_valid_apply_patch_program() -> None:
    source = '''payload = """
*** Begin Patch
# loommux: --full-output
*** End Patch
"""
'''

    prepared = prepare_apply_patch_literals(source)

    assert prepared.applied is False
    assert prepared.submitted_source == source

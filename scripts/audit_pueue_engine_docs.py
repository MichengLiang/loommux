#!/usr/bin/env python3
"""Audit structural and traceability invariants of the Pueue engine docs."""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import tiktoken

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs" / "pueue-engine"
INDEX_PATH = DOCS_DIR / "index.adoc"
SCALE_FILES = (
    "design-specification.adoc",
    "phase-1-delivery-plan.adoc",
    "documentation-quality-standard.adoc",
    "evidence-register.adoc",
)
DIAGRAM_NAMES = (
    "execution-lifecycle",
    "phase-1-dependencies",
    "system-context",
)
PROHIBITED_CHAT_EXAMPLES = (
    "我觉得",
    "你说得对",
    "上一轮",
    "这一次",
    "我们先",
    "以后再说",
    "大概",
    "可能可以",
    "比较好",
    "最小版本",
    "巴拉巴拉",
    "大家都知道",
)


@dataclass(frozen=True)
class Scale:
    lines: int
    characters: int
    tokens: int


class Audit:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)

    def fail(self, message: str) -> None:
        self.errors.append(message)


def read_documents() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(DOCS_DIR.glob("*.adoc"))}


def read_diagram_sources() -> dict[str, str]:
    return {f"diagrams/{path.name}": path.read_text(encoding="utf-8") for path in sorted((DOCS_DIR / "diagrams").glob("*.puml"))}


def section_bodies(text: str, heading_pattern: str, boundary_pattern: str) -> dict[str, str]:
    matches = list(re.finditer(heading_pattern, text, flags=re.MULTILINE))
    result: dict[str, str] = {}
    for match in matches:
        boundary = re.search(
            boundary_pattern,
            text[match.end() :],
            flags=re.MULTILINE,
        )
        end = match.end() + boundary.start() if boundary else len(text)
        result[match.group(1)] = text[match.start() : end]
    return result


def expected_ids(prefix: str, first: int, last: int, width: int) -> set[str]:
    return {f"{prefix}-{number:0{width}d}" for number in range(first, last + 1)}


def audit_anchors_and_xrefs(audit: Audit, documents: dict[str, str]) -> None:
    anchors_by_file: dict[str, set[str]] = {}
    anchor_locations: dict[str, list[str]] = {}
    for name, text in documents.items():
        anchors = re.findall(r"^\[#([^\]]+)\]\s*$", text, flags=re.MULTILINE)
        anchors_by_file[name] = set(anchors)
        audit.require(
            len(anchors) == len(set(anchors)),
            f"{name}: duplicate explicit anchor",
        )
        for anchor in anchors:
            anchor_locations.setdefault(anchor, []).append(name)

    for anchor, files in sorted(anchor_locations.items()):
        audit.require(
            len(files) == 1,
            f"anchor {anchor!r} is defined in multiple documents: {', '.join(files)}",
        )

    for source_name, text in documents.items():
        for target in re.findall(r"xref:([^\[\s]+)\[", text):
            if "#" in target:
                target_file, target_anchor = target.split("#", 1)
                resolved_file = target_file or source_name
            elif target.endswith(".adoc"):
                resolved_file, target_anchor = target, ""
            else:
                resolved_file, target_anchor = source_name, target

            audit.require(
                resolved_file in documents,
                f"{source_name}: xref target file does not exist: {resolved_file}",
            )
            if resolved_file in documents and target_anchor:
                audit.require(
                    target_anchor in anchors_by_file[resolved_file],
                    f"{source_name}: xref target is missing: {target}",
                )


def audit_id_definitions(audit: Audit, documents: dict[str, str]) -> None:
    design = documents["design-specification.adoc"]
    plan = documents["phase-1-delivery-plan.adoc"]
    quality = documents["documentation-quality-standard.adoc"]
    evidence = documents["evidence-register.adoc"]

    definitions = {
        "VER": set(re.findall(r"^\|(VER-\d{3})\s*$", design, flags=re.MULTILINE)),
        "WP": set(re.findall(r"^== (WP-\d{2})：", plan, flags=re.MULTILINE)),
        "SYS-DOD": set(re.findall(r"^\|(SYS-DOD-\d{3})\s*$", plan, flags=re.MULTILINE)),
        "SYS-EXC": set(re.findall(r"^\|(SYS-EXC-\d{3})\s*$", plan, flags=re.MULTILINE)),
        "DOC-DOD": set(re.findall(r"^\|(DOC-DOD-\d{3})\s*$", quality, flags=re.MULTILINE)),
        "DOC-EXC": set(re.findall(r"^\|(DOC-EXC-\d{3})\s*$", quality, flags=re.MULTILINE)),
        "DEC-PUEUE": set(re.findall(r"^=== (DEC-PUEUE-\d{3})：", design, flags=re.MULTILINE)),
        "EVD-PUEUE": set(re.findall(r"^=== (EVD-PUEUE-\d{3})：", evidence, flags=re.MULTILINE)),
    }
    expected = {
        "VER": expected_ids("VER", 1, 41, 3),
        "WP": expected_ids("WP", 0, 11, 2),
        "SYS-DOD": expected_ids("SYS-DOD", 1, 20, 3),
        "SYS-EXC": expected_ids("SYS-EXC", 1, 15, 3),
        "DOC-DOD": expected_ids("DOC-DOD", 1, 20, 3),
        "DOC-EXC": expected_ids("DOC-EXC", 1, 15, 3),
        "DEC-PUEUE": expected_ids("DEC-PUEUE", 1, 12, 3),
        "EVD-PUEUE": expected_ids("EVD-PUEUE", 1, 10, 3),
    }
    for kind, actual in definitions.items():
        missing = sorted(expected[kind] - actual)
        extra = sorted(actual - expected[kind])
        audit.require(not missing, f"{kind}: missing definitions: {', '.join(missing)}")
        audit.require(not extra, f"{kind}: unexpected definitions: {', '.join(extra)}")

    definition_patterns = (
        r"^\|(VER-\d{3}|SYS-DOD-\d{3}|SYS-EXC-\d{3}|DOC-DOD-\d{3}|DOC-EXC-\d{3})\s*$",
        r"^== (WP-\d{2})：",
        r"^=== (DEC-PUEUE-\d{3}|EVD-PUEUE-\d{3})：",
    )
    definition_counts: Counter[str] = Counter()
    for text in documents.values():
        for pattern in definition_patterns:
            definition_counts.update(match.group(1) for match in re.finditer(pattern, text, flags=re.MULTILINE))
    for identifier, count in sorted(definition_counts.items()):
        audit.require(count == 1, f"{identifier}: expected one definition, found {count}")


def audit_record_shapes(audit: Audit, documents: dict[str, str]) -> None:
    design = documents["design-specification.adoc"]
    plan = documents["phase-1-delivery-plan.adoc"]
    evidence = documents["evidence-register.adoc"]

    decisions = section_bodies(
        design,
        r"^=== (DEC-PUEUE-\d{3})：.*$",
        r"^={1,3} ",
    )
    for identifier, body in decisions.items():
        for field in ("决定::", "逻辑推导::", "拒绝方案::", "后果::"):
            audit.require(field in body, f"{identifier}: missing {field}")
        audit.require(
            "事实前提::" in body or "约束前提::" in body,
            f"{identifier}: missing facts/constraints field",
        )

    work_packages = section_bodies(
        plan,
        r"^== (WP-\d{2})：.*$",
        r"^={1,2} ",
    )
    for identifier, body in work_packages.items():
        for field in (
            "Purpose::",
            "Inputs::",
            "Dependencies::",
            "Outputs::",
            "Tasks::",
            "Acceptance evidence::",
            "Not done when::",
        ):
            audit.require(field in body, f"{identifier}: missing {field}")

    evidence_records = section_bodies(
        evidence,
        r"^=== (EVD-PUEUE-\d{3})：.*$",
        r"^={1,3} ",
    )
    for identifier, body in evidence_records.items():
        for field in (
            "Status::",
            "Source::",
            "Observation::",
            "Scope::",
            "Supports::",
            "Does not prove::",
            "Verification::",
        ):
            audit.require(field in body, f"{identifier}: missing {field}")


def anchored_section(text: str, start_anchor: str, end_anchor: str) -> str:
    start_marker = f"[#{start_anchor}]"
    end_marker = f"[#{end_anchor}]"
    start = text.index(start_marker)
    end = text.index(end_marker, start + len(start_marker))
    return text[start:end]


def parse_single_line_table(
    audit: Audit,
    section: str,
    identifier_pattern: str,
    expected_columns: int,
) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    row_pattern = re.compile(identifier_pattern)
    for line in section.splitlines():
        cells = line[1:].split(" |") if line.startswith("|") else []
        if not cells or not row_pattern.fullmatch(cells[0]):
            continue
        identifier = cells[0]
        audit.require(
            len(cells) == expected_columns,
            f"{identifier}: expected {expected_columns} traceability columns, found {len(cells)}",
        )
        audit.require(identifier not in rows, f"{identifier}: duplicate traceability row")
        rows[identifier] = cells
    return rows


def audit_traceability(audit: Audit, documents: dict[str, str]) -> None:
    plan = documents["phase-1-delivery-plan.adoc"]
    verification_section = anchored_section(
        plan,
        "verification-ownership",
        "system-gate-ownership",
    )
    gate_section = anchored_section(plan, "system-gate-ownership", "system-done")

    expected_ver = expected_ids("VER", 1, 41, 3)
    expected_wp = expected_ids("WP", 0, 11, 2)
    expected_dod = expected_ids("SYS-DOD", 1, 20, 3)
    expected_exc = expected_ids("SYS-EXC", 1, 15, 3)
    expected_gates = expected_dod | expected_exc
    expected_evidence = expected_ids("EVD-PUEUE", 1, 10, 3)

    verification_rows = parse_single_line_table(
        audit,
        verification_section,
        r"VER-\d{3}",
        4,
    )
    gate_rows = parse_single_line_table(
        audit,
        gate_section,
        r"SYS-(?:DOD|EXC)-\d{3}",
        3,
    )
    audit.require(
        set(verification_rows) == expected_ver,
        "verification ownership table must contain exactly VER-001 through VER-041",
    )
    audit.require(
        set(gate_rows) == expected_gates,
        "system gate ownership table must contain every SYS-DOD and SYS-EXC exactly once",
    )

    traced_work_packages: set[str] = set()
    for identifier, cells in verification_rows.items():
        owners = set(re.findall(r"WP-\d{2}", cells[1]))
        gates = set(re.findall(r"SYS-(?:DOD|EXC)-\d{3}", cells[2]))
        audit.require(bool(owners), f"{identifier}: traceability row has no WP owner")
        audit.require(bool(gates), f"{identifier}: traceability row has no SYS gate")
        audit.require(
            owners <= expected_wp,
            f"{identifier}: traceability row contains unknown WP owner",
        )
        audit.require(
            gates <= expected_gates,
            f"{identifier}: traceability row contains unknown SYS gate",
        )
        audit.require(
            re.search(r"xref:[^\[]+\[[^\]]+\]", cells[3]) is not None,
            f"{identifier}: traceability row has no primary-authority xref",
        )
        traced_work_packages.update(owners)

    for identifier, cells in gate_rows.items():
        owners = set(re.findall(r"WP-\d{2}", cells[1]))
        audit.require(bool(owners), f"{identifier}: ownership row has no WP owner")
        audit.require(
            owners <= expected_wp,
            f"{identifier}: ownership row contains unknown WP owner",
        )
        audit.require(
            bool(cells[2].strip()),
            f"{identifier}: ownership row has no required evidence",
        )
        traced_work_packages.update(owners)

    audit.require(
        traced_work_packages == expected_wp,
        "traceability tables must assign at least one relation to every WP-00 through WP-11",
    )

    relation_corpus = documents["design-specification.adoc"] + "\n" + documents["phase-1-delivery-plan.adoc"]
    evidence_anchor_by_id = {
        identifier: anchor
        for anchor, identifier in re.findall(
            r"^\[#([^\]]+)\]\s*\n=== (EVD-PUEUE-\d{3})：",
            documents["evidence-register.adoc"],
            flags=re.MULTILINE,
        )
    }
    for identifier in sorted(expected_evidence):
        expected_anchor = evidence_anchor_by_id.get(identifier)
        audit.require(
            expected_anchor is not None,
            f"{identifier}: evidence definition has no adjacent explicit anchor",
        )
        if expected_anchor is None:
            continue
        audit.require(
            re.search(
                rf"xref:evidence-register\.adoc#{re.escape(expected_anchor)}"
                rf"\[{re.escape(identifier)}\]",
                relation_corpus,
            )
            is not None,
            f"{identifier}: no design or delivery-plan evidence xref",
        )

    known_ids = expected_ver | expected_wp | expected_gates | expected_evidence | expected_ids("DEC-PUEUE", 1, 12, 3) | expected_ids("DOC-DOD", 1, 20, 3) | expected_ids("DOC-EXC", 1, 15, 3)
    id_pattern = re.compile(
        r"(?:VER|WP|SYS-DOD|SYS-EXC|EVD-PUEUE|DEC-PUEUE|DOC-DOD|DOC-EXC)-"
        r"[A-Za-z0-9]+"
    )
    referenced_ids = {match.group(0) for text in documents.values() for match in id_pattern.finditer(text)}
    unknown_ids = sorted(referenced_ids - known_ids)
    audit.require(not unknown_ids, f"unknown canonical IDs: {', '.join(unknown_ids)}")


def audit_residue(audit: Audit, artifacts: dict[str, str]) -> None:
    residue = re.compile("|".join(re.escape(term) for term in ("TODO", "TBD", "FIXME", "turn0search", "也许") + PROHIBITED_CHAT_EXAMPLES))
    exemption_rules = (
        (
            "documentation-quality-standard.adoc",
            "prohibited-residue",
            "transformation-example",
            set(PROHIBITED_CHAT_EXAMPLES),
        ),
        (
            "documentation-quality-standard.adoc",
            "citation-rules",
            "traceability",
            {"外部技术断言通过 `EVD-*` xref 引用 evidence register。evidence record 必须定位版本、repository、file/symbol 或可重复 command。`turn0search0`、搜索结果序号、聊天截图、无版本博客和作者记忆不是有效 citation。"},
        ),
        (
            "documentation-quality-standard.adoc",
            "block-selection",
            "cross-reference-quality",
            {"* comment block 可以记录作者 TODO，但最终 G5 时必须为零。"},
        ),
        (
            "documentation-quality-standard.adoc",
            "documentation-done",
            "documentation-excellence",
            {"|零 TODO、TBD、FIXME、placeholder anchor、placeholder source locator 或未关闭 OPEN。"},
        ),
        (
            "documentation-quality-standard.adoc",
            "documentation-excellence",
            "documentation-review",
            {"|零模糊评价词承担设计理由，零“可能/大概/以后”等未关闭不确定性。"},
        ),
        (
            "evidence-register.adoc",
            "evidence-raw-pueue",
            "evidence-raw-terminal",
            {"Pueue、rmcp、MCP 或 Loommux runtime 的任何外部技术行为。文件中的 `turn0search*` 不是有效 citation。"},
        ),
    )
    exempt_locations: set[tuple[str, int]] = set()
    for name, start_anchor, end_anchor, expected_lines in exemption_rules:
        text = artifacts[name]
        start = text.index(f"[#{start_anchor}]")
        end = text.index(f"[#{end_anchor}]", start)
        start_line = text.count("\n", 0, start) + 1
        section_lines = text[start:end].splitlines()
        for expected_line in expected_lines:
            offsets = [offset for offset, line in enumerate(section_lines) if line.strip() == expected_line]
            audit.require(
                len(offsets) == 1,
                f"{name}: controlled residue exemption must occur once in {start_anchor}: {expected_line}",
            )
            if len(offsets) == 1:
                exempt_locations.add((name, start_line + offsets[0]))
    unresolved_marker = re.compile(
        r"^(?:Status::\s*(?:OPEN|ASSUMPTION)|(?:OPEN|ASSUMPTION):{1,2})",
        flags=re.IGNORECASE,
    )
    placeholder_marker = re.compile(
        r"^\[#.*placeholder.*\]$|"
        r"^=+\s+Placeholder(?:\s|$)|"
        r"^(?:Source|Status)::.*(?:placeholder|待补充?|待定)|"
        r"<(?:PLACEHOLDER|TODO|TBD|FIXME)>",
        flags=re.IGNORECASE,
    )
    for name, text in artifacts.items():
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if residue.search(line) and (name, line_number) not in exempt_locations:
                audit.fail(f"{name}:{line_number}: prohibited residue: {stripped}")
            if unresolved_marker.search(stripped):
                audit.fail(f"{name}:{line_number}: unresolved claim marker: {stripped}")
            if placeholder_marker.search(stripped):
                audit.fail(f"{name}:{line_number}: placeholder marker: {stripped}")

    evidence = artifacts["evidence-register.adoc"]
    delta_start = evidence.index("=== EVD-PUEUE-010：")
    delta_end = evidence.index("\n== MCP Rust SDK", delta_start)
    for name, text in artifacts.items():
        for match in re.finditer(r"4\.0\.1|0\.30\.0|5fd17c9f|4e948cac", text):
            if name == "evidence-register.adoc" and delta_start <= match.start() < delta_end:
                continue
            line_number = text.count("\n", 0, match.start()) + 1
            audit.fail(f"{name}:{line_number}: stale target-version residue: {match.group(0)}")


def audit_diagrams(audit: Audit, diagram_sources: dict[str, str]) -> None:
    expected_sources = {f"diagrams/{name}.puml" for name in DIAGRAM_NAMES}
    audit.require(
        set(diagram_sources) == expected_sources,
        "diagram source set must contain exactly the three documented PlantUML files",
    )
    for diagram_name in DIAGRAM_NAMES:
        source_name = f"diagrams/{diagram_name}.puml"
        image_path = DOCS_DIR / "images" / f"{diagram_name}.svg"
        audit.require(image_path.is_file(), f"missing generated diagram: {image_path.name}")
        if source_name not in diagram_sources or not image_path.is_file():
            continue
        try:
            completed = subprocess.run(
                ["plantuml", "-tsvg", "-pipe"],
                input=diagram_sources[source_name].encode("utf-8"),
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:
            audit.fail("plantuml executable is unavailable")
            return
        audit.require(
            completed.returncode == 0,
            f"{source_name}: PlantUML generation failed: {completed.stderr.decode('utf-8', errors='replace').strip()}",
        )
        if completed.returncode == 0:
            audit.require(
                completed.stdout == image_path.read_bytes(),
                f"{image_path.name}: generated SVG is stale",
            )


def audit_guard_self_tests(
    audit: Audit,
    documents: dict[str, str],
    diagram_sources: dict[str, str],
) -> None:
    missing_relation = dict(documents)
    missing_relation["phase-1-delivery-plan.adoc"] = re.sub(
        r"^\|VER-002 .*\n",
        "",
        missing_relation["phase-1-delivery-plan.adoc"],
        count=1,
        flags=re.MULTILINE,
    )
    trace_probe = Audit()
    audit_traceability(trace_probe, missing_relation)
    audit.require(
        any("verification ownership table" in error for error in trace_probe.errors),
        "self-test: a missing VER traceability row was not rejected",
    )

    unknown_id = dict(documents)
    unknown_id["phase-1-delivery-plan.adoc"] += "\nVER-999\n"
    unknown_probe = Audit()
    audit_traceability(unknown_probe, unknown_id)
    audit.require(
        any("unknown canonical IDs" in error for error in unknown_probe.errors),
        "self-test: an unknown canonical ID was not rejected",
    )

    malformed_relation = dict(documents)
    malformed_relation["phase-1-delivery-plan.adoc"] = malformed_relation["phase-1-delivery-plan.adoc"].replace("|VER-002 |WP-04 |", "|VER-002 |WP-04、WP-99 |", 1)
    malformed_probe = Audit()
    audit_traceability(malformed_probe, malformed_relation)
    audit.require(
        any("unknown canonical IDs" in error for error in malformed_probe.errors),
        "self-test: a malformed relation ID was not rejected",
    )

    empty_evidence = dict(documents)
    empty_evidence["phase-1-delivery-plan.adoc"] = re.sub(
        r"^(\|SYS-DOD-001 \|WP-01 )\|.*$",
        r"\1|",
        empty_evidence["phase-1-delivery-plan.adoc"],
        count=1,
        flags=re.MULTILINE,
    )
    empty_evidence_probe = Audit()
    audit_traceability(empty_evidence_probe, empty_evidence)
    audit.require(
        any("no required evidence" in error for error in empty_evidence_probe.errors),
        "self-test: an empty gate evidence cell was not rejected",
    )

    bare_xref = dict(documents)
    bare_xref["phase-1-delivery-plan.adoc"] = re.sub(
        r"^(\|VER-001 \|[^\n]* \|[^\n]* \|).*$",
        r"\1xref:",
        bare_xref["phase-1-delivery-plan.adoc"],
        count=1,
        flags=re.MULTILINE,
    )
    bare_xref_probe = Audit()
    audit_traceability(bare_xref_probe, bare_xref)
    audit.require(
        any("no primary-authority xref" in error for error in bare_xref_probe.errors),
        "self-test: a bare primary-authority xref was not rejected",
    )

    wrong_evidence_anchor = dict(documents)
    wrong_evidence_anchor["design-specification.adoc"] = wrong_evidence_anchor["design-specification.adoc"].replace(
        "#evidence-pueue-version[EVD-PUEUE-001]",
        "#evidence-pueue-task-model[EVD-PUEUE-001]",
        1,
    )
    wrong_evidence_probe = Audit()
    audit_traceability(wrong_evidence_probe, wrong_evidence_anchor)
    audit.require(
        any("no design or delivery-plan evidence xref" in error for error in wrong_evidence_probe.errors),
        "self-test: an evidence label linked to the wrong anchor was not rejected",
    )

    artifact_name = "diagrams/execution-lifecycle.puml"
    base_artifacts = documents | diagram_sources
    residue_cases = tuple((term, "prohibited residue") for term in PROHIBITED_CHAT_EXAMPLES) + (
        ("我觉得不是有效 citation", "prohibited residue"),
        ("Status:: OPEN", "unresolved claim marker"),
        ("OPEN: unresolved", "unresolved claim marker"),
        ("[#placeholder-source]", "placeholder marker"),
        ("== Placeholder", "placeholder marker"),
        ("Source:: https://placeholder.invalid/x", "placeholder marker"),
        ("Status:: 待补充", "placeholder marker"),
    )
    for injected_line, expected_error in residue_cases:
        mutated_artifacts = dict(base_artifacts)
        mutated_artifacts[artifact_name] += f"\n{injected_line}\n"
        residue_probe = Audit()
        audit_residue(residue_probe, mutated_artifacts)
        audit.require(
            any(expected_error in error for error in residue_probe.errors),
            f"self-test: {injected_line!r} was not rejected",
        )

    duplicate_exemption = dict(base_artifacts)
    duplicate_exemption["documentation-quality-standard.adoc"] += "\n我觉得\n"
    duplicate_probe = Audit()
    audit_residue(duplicate_probe, duplicate_exemption)
    audit.require(
        any("prohibited residue" in error for error in duplicate_probe.errors),
        "self-test: a controlled example duplicated outside its section was not rejected",
    )


def calculate_scale(text: str, encoding: tiktoken.Encoding) -> Scale:
    return Scale(
        lines=len(text.splitlines()),
        characters=len(text),
        tokens=len(encoding.encode(text)),
    )


def audit_scale(audit: Audit, documents: dict[str, str]) -> dict[str, Scale]:
    encoding = tiktoken.get_encoding("o200k_base")
    actual = {name: calculate_scale(documents[name], encoding) for name in SCALE_FILES}
    index = documents["index.adoc"]
    for name, scale in actual.items():
        pattern = re.compile(
            rf"\|`{re.escape(name)}`\s*\n"
            rf"\|(\d+)\s*\n\|(\d+)\s*\n\|(\d+)\s*$",
            flags=re.MULTILINE,
        )
        match = pattern.search(index)
        audit.require(match is not None, f"index: missing scale row for {name}")
        if match is None:
            continue
        recorded = Scale(*(int(value) for value in match.groups()))
        audit.require(
            recorded == scale,
            f"index: stale scale for {name}: recorded={recorded}, actual={scale}",
        )
    audit.require(
        actual["design-specification.adoc"].tokens >= 15_000,
        "design specification is below the 15,000 o200k_base token gate",
    )
    return actual


def main() -> int:
    audit = Audit()
    documents = read_documents()
    diagram_sources = read_diagram_sources()
    audit.require(len(documents) == 5, f"expected 5 AsciiDoc files, found {len(documents)}")
    audit_anchors_and_xrefs(audit, documents)
    audit_id_definitions(audit, documents)
    audit_record_shapes(audit, documents)
    audit_traceability(audit, documents)
    audit_residue(audit, documents | diagram_sources)
    audit_diagrams(audit, diagram_sources)
    audit_guard_self_tests(audit, documents, diagram_sources)
    scale = audit_scale(audit, documents)

    if audit.errors:
        print(f"Pueue engine documentation audit failed ({len(audit.errors)} error(s)):")
        for error in audit.errors:
            print(f"- {error}")
        return 1

    print("Pueue engine documentation audit passed.")
    for name in SCALE_FILES:
        item = scale[name]
        print(f"- {name}: {item.lines} lines, {item.characters} characters, {item.tokens} o200k_base tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())

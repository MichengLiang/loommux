#!/usr/bin/env python3
"""Audit structural and traceability invariants of the Pueue engine docs."""

from __future__ import annotations

import ast
import hashlib
import io
import re
import subprocess
import sys
import tokenize
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
CANONICAL_TERMS = (
    "engine",
    "adapter",
    "execution",
    "execution record",
    "task",
    "daemon instance",
    "active execution",
    "terminal state",
    "observation",
    "combined transcript",
    "initial wait",
    "cancel",
)
PROHIBITED_TERM_ALIASES = (
    "进程编号",
    "当前任务",
    "全机唯一 daemon",
)
EXECUTION_STATUSES = (
    "locked",
    "stashed",
    "queued",
    "running",
    "paused",
    "completed",
    "failed",
    "killed",
    "cancelled",
)
PUBLIC_TOOLS = (
    "run_shell",
    "status",
    "execution_status",
    "read_output",
    "search_output",
    "wait",
    "cancel",
)
RUNTIME_ERROR_KINDS = (
    "invalid_script",
    "invalid_loommux_directive",
    "execution_not_found",
    "backend_unavailable",
    "backend_timeout",
    "backend_protocol_incompatible",
    "unexpected_backend_response",
    "backend_request_rejected",
    "submission_outcome_unknown",
    "backend_task_missing",
    "backend_task_modified",
    "backend_log_unavailable",
    "backend_log_replaced",
    "invalid_line_range",
    "invalid_query",
    "invalid_context",
    "invalid_max_chars",
    "invalid_timeout",
    "cancel_blocked",
    "cancel_rejected",
    "cancel_outcome_unknown",
)
WORKSPACE_ERROR_KINDS = (
    "workspace_config_not_absolute",
    "workspace_config_not_found",
    "workspace_config_not_file",
    "workspace_config_parse_failed",
    "workspace_config_version_unsupported",
    "workspace_config_invalid_rule",
    "workspace_not_found",
    "workspace_not_directory",
    "workspace_canonicalize_failed",
)
STALE_WORKSPACE_CLAIMS = (
    re.compile(r"trusted Python resolver defining", flags=re.IGNORECASE),
    re.compile(r"trusted Python file defining", flags=re.IGNORECASE),
    re.compile(r"resolve_workspace\(launch_cwd:\s*Path\)"),
    re.compile(r"workspace-resolvers/(?:codex|generic)\.py"),
    re.compile(r"explicitly configured Python resolver", flags=re.IGNORECASE),
)
TERMINOLOGY_FINGERPRINTS = {
    "design-specification.adoc": "181576b91abceb4e6f1dd857132589764dd4095f26968348ce94394a39dcedc8",
    "documentation-quality-standard.adoc": "80a75bd4c153cfb2d8ddae445d3f28cad0535efbedc83a6cced40be832716cb3",
    "evidence-register.adoc": "bc93f67694f1570178f3f133cbca979d3efb1930cae678cb26a1148042cac8c2",
    "index.adoc": "449fe8108c97b82cc6e0214ca72879b512d5306d3b2c5faf6e962188ec213823",
    "phase-1-delivery-plan.adoc": "5990487b38dc29734272a67e50184cb30e465eab7ec2cc3068f85934e50e35ab",
}
ADOPTED_LEDGER_CLAIMS = {
    "每个 MCP client 维护本地 execution 到 Pueue task ID 的映射。",
    "workspace 应成为跨 engine 声明式 contract。",
}
STALE_CLAIM_SUFFIXES = {
    ".adoc",
    ".md",
    ".py",
    ".rs",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
}


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
        "EVD-RAW": set(re.findall(r"^=== (EVD-RAW-\d{3})：", evidence, flags=re.MULTILINE)),
        "EVD-LOOMMUX": set(re.findall(r"^=== (EVD-LOOMMUX-\d{3})：", evidence, flags=re.MULTILINE)),
        "EVD-PUEUE": set(re.findall(r"^=== (EVD-PUEUE-\d{3})：", evidence, flags=re.MULTILINE)),
        "EVD-RMCP": set(re.findall(r"^=== (EVD-RMCP-\d{3})：", evidence, flags=re.MULTILINE)),
        "EVD-DOC": set(re.findall(r"^=== (EVD-DOC-\d{3})：", evidence, flags=re.MULTILINE)),
    }
    expected = {
        "VER": expected_ids("VER", 1, 41, 3),
        "WP": expected_ids("WP", 0, 11, 2),
        "SYS-DOD": expected_ids("SYS-DOD", 1, 20, 3),
        "SYS-EXC": expected_ids("SYS-EXC", 1, 15, 3),
        "DOC-DOD": expected_ids("DOC-DOD", 1, 20, 3),
        "DOC-EXC": expected_ids("DOC-EXC", 1, 15, 3),
        "DEC-PUEUE": expected_ids("DEC-PUEUE", 1, 12, 3),
        "EVD-RAW": expected_ids("EVD-RAW", 1, 2, 3),
        "EVD-LOOMMUX": expected_ids("EVD-LOOMMUX", 1, 4, 3),
        "EVD-PUEUE": expected_ids("EVD-PUEUE", 1, 10, 3),
        "EVD-RMCP": expected_ids("EVD-RMCP", 1, 1, 3),
        "EVD-DOC": expected_ids("EVD-DOC", 1, 1, 3),
    }
    for kind, actual in definitions.items():
        missing = sorted(expected[kind] - actual)
        extra = sorted(actual - expected[kind])
        audit.require(not missing, f"{kind}: missing definitions: {', '.join(missing)}")
        audit.require(not extra, f"{kind}: unexpected definitions: {', '.join(extra)}")

    definition_patterns = (
        r"^\|(VER-\d{3}|SYS-DOD-\d{3}|SYS-EXC-\d{3}|DOC-DOD-\d{3}|DOC-EXC-\d{3})\s*$",
        r"^== (WP-\d{2})：",
        r"^=== (DEC-PUEUE-\d{3}|EVD-(?:RAW|LOOMMUX|PUEUE|RMCP|DOC)-\d{3})：",
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
        r"^=== (EVD-(?:RAW|LOOMMUX|PUEUE|RMCP|DOC)-\d{3})：.*$",
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


def audit_claim_ledger(audit: Audit, documents: dict[str, str]) -> None:
    evidence = documents["evidence-register.adoc"]
    ledger = anchored_section(evidence, "claim-ledger", "evidence-maintenance")
    allowed = {"adopted", "rejected", "superseded", "out of scope"}
    cells = [line[1:] for line in ledger.splitlines() if line.startswith("|") and line != "|==="]
    rows = list(zip(cells[1::3], cells[2::3], cells[3::3], strict=True))
    dispositions = [disposition for _, disposition, _ in rows]
    audit.require(bool(dispositions), "claim ledger contains no dispositions")
    for disposition in dispositions:
        audit.require(
            disposition in allowed,
            f"claim ledger: unsupported disposition {disposition!r}",
        )
    for claim, disposition, _ in rows:
        if disposition == "adopted":
            audit.require(
                claim in ADOPTED_LEDGER_CLAIMS,
                f"claim ledger: unrecognized adopted claim: {claim}",
            )
        is_stale_workspace = any(pattern.search(claim) for pattern in STALE_WORKSPACE_CLAIMS)
        if stale_architecture_family(claim) is not None or is_stale_workspace:
            audit.require(
                disposition != "adopted",
                f"claim ledger: stale claim cannot have adopted disposition: {claim}",
            )


def terminology_usage_fingerprint(text: str) -> str:
    # Fingerprinting the complete lexical corpus catches additions as well as
    # substitutions, including previously unseen plain, snake, and hyphen forms.
    occurrences = re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[\u3400-\u9fff]+", text)
    return hashlib.sha256("\n".join(occurrences).encode()).hexdigest()


def audit_terminology(audit: Audit, documents: dict[str, str]) -> None:
    design = documents["design-specification.adoc"]
    terminology = anchored_section(design, "terminology", "system-facts")
    defined_terms = tuple(match.group(1) for match in re.finditer(r"^\|([^=\n][^\n]*)$", terminology, flags=re.MULTILINE))[1::3]
    audit.require(
        defined_terms == CANONICAL_TERMS,
        "terminology table must define the exact canonical term set in stable order",
    )

    quality = documents["documentation-quality-standard.adoc"]
    canonical_rules = anchored_section(quality, "canonical-terms", "normative-language")
    for term in ("execution", "task", "task ID", "daemon instance", "combined transcript"):
        audit.require(
            f"`{term}`" in canonical_rules,
            f"terminology control does not constrain canonical term {term!r}",
        )

    artifacts = dict(documents)
    artifacts["design-specification.adoc"] = design.replace(terminology, "", 1)
    artifacts["documentation-quality-standard.adoc"] = quality.replace(canonical_rules, "", 1)
    for name, text in artifacts.items():
        for alias in PROHIBITED_TERM_ALIASES:
            for match in re.finditer(re.escape(alias), text):
                line_number = text.count("\n", 0, match.start()) + 1
                audit.fail(f"{name}:{line_number}: prohibited canonical-term alias: {alias}")

    controlled_sets = (
        (
            "execution status",
            anchored_section(design, "execution-statuses", "state-projection"),
            EXECUTION_STATUSES,
            None,
        ),
        (
            "public tool",
            anchored_section(design, "dec-pueue-008", "status-tool"),
            PUBLIC_TOOLS,
            None,
        ),
        (
            "runtime error kind",
            anchored_section(design, "error-taxonomy", "sensitive-data"),
            RUNTIME_ERROR_KINDS,
            None,
        ),
        (
            "workspace error kind",
            anchored_section(design, "workspace-contract", "dec-pueue-012"),
            WORKSPACE_ERROR_KINDS,
            "workspace_",
        ),
    )
    for label, section, expected, prefix in controlled_sets:
        values = tuple(match.group(1) for match in re.finditer(r"^\|`([a-z_]+)`\s*$", section, flags=re.MULTILINE) if prefix is None or match.group(1).startswith(prefix))
        audit.require(
            values == expected,
            f"{label} table must define the exact authored surface in stable order",
        )

    known_error_kinds = set(RUNTIME_ERROR_KINDS) | set(WORKSPACE_ERROR_KINDS)
    error_namespace = re.compile(
        r"`((?:backend_(?:task|log|protocol|request)|cancel_|workspace_config_|"
        r"workspace_(?:not|canonicalize)|submission_|invalid_)[a-z_]*)`"
    )
    # Traceability reports name executable tests in code spans; these symbols
    # share public error prefixes but do not extend the error-kind namespace.
    non_error_schema_names = {
        "backend_previous_status",
        "invalid_directive_has_no_backend_or_namespace_side_effect",
        "cancel_uses_only_task_specific_remove_or_kill",
        "cancel_rejection_refreshes_races_without_replaying_control",
    }
    for name, text in documents.items():
        actual_fingerprint = terminology_usage_fingerprint(text)
        audit.require(
            actual_fingerprint == TERMINOLOGY_FINGERPRINTS[name],
            f"{name}: controlled terminology usage fingerprint changed: {actual_fingerprint}",
        )
        for match in error_namespace.finditer(text):
            value = match.group(1)
            if value in known_error_kinds | non_error_schema_names:
                continue
            line_number = text.count("\n", 0, match.start()) + 1
            audit.fail(f"{name}:{line_number}: unknown error-kind namespace term: {value}")


def repository_authored_texts() -> dict[str, str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    texts: dict[str, str] = {}
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        if relative.suffix not in STALE_CLAIM_SUFFIXES:
            continue
        path = PROJECT_ROOT / relative
        if path.is_file():
            texts[relative.as_posix()] = path.read_text(encoding="utf-8")
    return texts


def exemption_ranges(name: str, text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if name == "docs/pueue-engine/design-specification.adoc":
        ranges.extend(
            (match.start(), match.end())
            for match in re.finditer(
                r"^拒绝方案::\n.*?(?=^后果::)",
                text,
                flags=re.MULTILINE | re.DOTALL,
            )
        )
        explicit_rejections = anchored_section(text, "explicit-rejections", "decision-index")
        start = text.index(explicit_rejections)
        ranges.append((start, start + len(explicit_rejections)))
    if name == "docs/pueue-engine/phase-1-delivery-plan.adoc":
        phase_exclusions = anchored_section(text, "phase-exclusions", "initial-conditions")
        start = text.index(phase_exclusions)
        ranges.append((start, start + len(phase_exclusions)))
        ranges.extend(
            (match.start(), match.end())
            for match in re.finditer(
                r"^Not done when::\n.*?(?=^\[#|^== )",
                text,
                flags=re.MULTILINE | re.DOTALL,
            )
        )
    if name != "docs/pueue-engine/evidence-register.adoc":
        return ranges

    implemented_baseline = anchored_section(text, "evidence-loommux", "evidence-pueue")
    baseline_start = text.index(implemented_baseline)
    ranges.append((baseline_start, baseline_start + len(implemented_baseline)))
    claim_ledger = anchored_section(text, "claim-ledger", "evidence-maintenance")
    ledger_start = text.index(claim_ledger)
    cells = [line for line in claim_ledger.splitlines(keepends=True) if line.startswith("|") and line.strip() != "|==="]
    for claim_line, disposition_line, result_line in zip(cells[1::3], cells[2::3], cells[3::3], strict=True):
        if disposition_line[1:].strip() == "adopted":
            continue
        row = claim_line + disposition_line + result_line
        row_start = text.index(row, ledger_start)
        ranges.append((row_start, row_start + len(row)))
    ranges.extend(
        (match.start(), match.end())
        for match in re.finditer(
            r"^Does not prove::\n.*?(?=^Verification::)",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
    )
    return ranges


def is_exempt(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def source_prose_segments(name: str, text: str) -> list[tuple[int, str]]:
    suffix = Path(name).suffix
    if suffix == ".py":
        return python_prose_segments(text)
    if suffix == ".rs":
        return rust_prose_segments(text)
    if suffix == ".toml":
        description_key = r'(?:description|"description"|\'description\')'
        string_value = '""".*?"""' + r"|'''.*?'''|" + r'"(?:\\.|[^"])*"|' + r"'[^']*'"
        pattern_text = rf"(?m)^\s*#[^\n]*(?:\n\s*#[^\n]*)*|{description_key}\s*=\s*(?:{string_value})"
        pattern = re.compile(pattern_text, re.DOTALL)
        return [(match.start(), match.group(0)) for match in pattern.finditer(text)]
    return [(0, text)]


def python_prose_segments(text: str) -> list[tuple[int, str]]:
    line_offsets = [0]
    for match in re.finditer("\n", text):
        line_offsets.append(match.end())
    comments: list[tuple[int, int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            line, column = token.start
            comments.append((line_offsets[line - 1] + column, line, token.string))
    except (IndentationError, tokenize.TokenError):
        return []
    segments: list[tuple[int, str]] = []
    previous_line: int | None = None
    for offset, line, comment in comments:
        if segments and previous_line is not None and line == previous_line + 1:
            prior_offset, prior = segments[-1]
            segments[-1] = (prior_offset, f"{prior}\n{comment}")
        else:
            segments.append((offset, comment))
        previous_line = line

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return segments
    for node in ast.walk(tree):
        value: ast.Constant | None = None
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                candidate = node.body[0].value
                if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                    value = candidate
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            candidate = node.value
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str) and any(target_name(target).endswith("description") for target in targets):
                value = candidate
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                candidate = keyword.value
                if keyword.arg == "description" and isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                    offset = line_offsets[candidate.lineno - 1] + candidate.col_offset
                    segments.append((offset, candidate.value))
        if value is not None:
            offset = line_offsets[value.lineno - 1] + value.col_offset
            segments.append((offset, value.value))
    return segments


def target_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id.casefold()
    if isinstance(node, ast.Attribute):
        return node.attr.casefold()
    return ""


def rust_prose_segments(text: str) -> list[tuple[int, str]]:
    segments = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("//", index):
            start = index
            end = text.find("\n", index)
            end = length if end < 0 else end
            while end < length:
                next_start = end + 1
                while next_start < length and text[next_start] in " \t":
                    next_start += 1
                if not text.startswith("//", next_start):
                    break
                next_end = text.find("\n", next_start)
                end = length if next_end < 0 else next_end
            segments.append((start, text[start:end]))
            index = end
            continue
        if text.startswith("/*", index):
            start = index
            depth = 1
            index += 2
            while index < length and depth:
                if text.startswith("/*", index):
                    depth += 1
                    index += 2
                elif text.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            segments.append((start, text[start:index]))
            continue
        raw = re.match(r'r(#+)?"', text[index:])
        if raw is not None:
            hashes = raw.group(1) or ""
            end_marker = f'"{hashes}'
            content_start = index + raw.end()
            end = text.find(end_marker, content_start)
            end = length if end < 0 else end + len(end_marker)
            if rust_string_is_prose(text[max(0, index - 80) : index]):
                segments.append((index, text[content_start : end - len(end_marker)]))
            index = end
            continue
        if text[index] == '"':
            start = index
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                elif text[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            if rust_string_is_prose(text[max(0, start - 80) : start]):
                segments.append((start, text[start:index]))
            continue
        index += 1
    return segments


def rust_string_is_prose(prefix: str) -> bool:
    return bool(re.search(r"(?:description\s*=|#\s*\[\s*doc\s*=)\s*$", prefix))


def architecture_claim_units(text: str) -> list[tuple[int, str]]:
    units = []
    paragraph_pattern = re.compile(r"\S(?:.*?\S)?(?=\n[ \t]*\n|\s*\Z)", re.DOTALL)
    sentence_pattern = re.compile(r"[^。.!?；;]+(?:[。.!?；;]+|$)", re.DOTALL)
    clause_pattern = re.compile(
        r"\s*(?:,|，|(?:but|however|yet|while|whereas|although|though)\s+|"
        r"(?:但(?:是)?|而|然而|尽管|虽然)\s*)",
        re.IGNORECASE,
    )
    for paragraph in paragraph_pattern.finditer(text):
        for sentence in sentence_pattern.finditer(paragraph.group(0)):
            raw_sentence = sentence.group(0)
            leading_whitespace = len(raw_sentence) - len(raw_sentence.lstrip())
            normalized = re.sub(r"\s+", " ", raw_sentence).strip()
            sentence_offset = paragraph.start() + sentence.start() + leading_whitespace
            clauses = list(clause_pattern.split(normalized))
            search_from = 0
            task_subject_seen = False
            architecture_subject: str | None = None
            selection_context: str | None = None
            for clause in clauses:
                clause_start = normalized.find(clause, search_from)
                search_from = clause_start + len(clause)
                candidate = clause
                subject_match = re.search(r"\b(adapter|server|loommux)\b", clause, re.IGNORECASE)
                if subject_match is not None:
                    architecture_subject = subject_match.group(1)
                elif architecture_subject is not None:
                    candidate = f"{architecture_subject} {candidate}"
                has_task_subject = "pueue task id" in clause.casefold() or "pueue 任务编号" in clause
                if task_subject_seen and not has_task_subject:
                    candidate = f"Pueue task IDs {clause}"
                task_subject_seen |= has_task_subject
                if selection_context is not None:
                    candidate = f"{selection_context}, {candidate}"
                    selection_context = None
                if re.search(r"\b(?:omitted|missing|absent)\b|(?:省略|缺省)", clause, re.IGNORECASE):
                    selection_context = clause
                units.append((sentence_offset + max(clause_start, 0), candidate))
    return units


def stale_architecture_family(line: str) -> str | None:
    normalized = f" {line.casefold()} "
    public_task_id = bool(
        re.search(
            r"(?:pueue task ids?.{0,18}"
            r"(?:is|are|becomes?|serves? as|used as|exposed|returned).{0,24}"
            r"(?:public|agent-facing|mcp|address|coordinate|handle)|"
            r"(?:expose|publish|return).{0,24}pueue task ids?|"
            r"public.{0,16}pueue task ids?|"
            r"(?:pueue task id|pueue 任务编号).{0,18}(?:作为|是|成为).{0,18}"
            r"(?:公共|公开|面向|地址|坐标|句柄)|"
            r"(?:公开|暴露|返回).{0,30}(?:pueue task id|pueue 任务编号))",
            normalized,
        )
    )
    task_id_is_private = bool(
        re.search(
            r"(?:pueue task ids?.{0,24}(?:private|internal|not\s+(?:public|exposed))|"
            r"(?:private|internal|no[_ -]?public).{0,24}pueue task ids?|"
            r"pueue task id.{0,18}(?:不|不得|不能|禁止).{0,12}(?:公开|暴露|地址|坐标)|"
            r"(?:不|不得|不能|禁止|无).{0,18}(?:公开|暴露).{0,18}pueue task id)",
            normalized,
        )
    )
    if public_task_id and not task_id_is_private:
        return "public task ID"

    implicit_execution = bool(
        re.search(
            r"(?:current execution|当前 execution|"
            r"(?:omitted|missing|absent).{0,40}(?:newest|latest|recent) execution|"
            r"(?:newest|latest|recent|最近).{0,30}execution.{0,30}(?:select|choose|default)|"
            r"(?:默认|隐式).{0,24}(?:选择|选中|使用).{0,24}execution|"
            r"(?:默认|隐式).{0,24}execution)",
            normalized,
        )
    )
    no_implicit_execution = bool(
        re.search(
            r"(?:no|not|without|never).{0,18}(?:current|implicit).{0,12}execution|"
            r"rather than.{0,18}(?:scalar )?current execution|"
            r"(?:current|implicit).{0,12}execution.{0,18}(?:does not|is not|不存在|不得|不能)|"
            r"(?:不存在|不得|不能|禁止|非).{0,18}(?:current|隐式).{0,12}execution|"
            r"无.{0,80}(?:current|隐式).{0,12}execution",
            normalized,
        )
    )
    if implicit_execution and not no_implicit_execution:
        return "scalar current execution"

    cli_wrapper = bool(
        re.search(
            r"(?:adapter|server|loommux).{0,30}"
            r"(?:spawns?|launches?|shells? out to|calls?|invokes?|runs?|wraps?).{0,24}"
            r"(?:the )?pueue (?:binary|cli|executable|command line)|"
            r"(?:adapter|server|loommux).{0,30}(?:调用|执行|包装|启动).{0,18}"
            r"pueue (?:cli|命令行|命令行程序)|"
            r"pueue adapter.{0,24}(?:通过|使用).{0,12}cli",
            normalized,
        )
    )
    no_cli_wrapper = bool(
        re.search(
            r"(?:does not|do not|must not|never|without).{0,24}"
            r"(?:invoke|run|spawn|shell|call).{0,24}pueue|"
            r"(?:不|不得|不会|禁止).{0,18}(?:调用|执行|启动|包装).{0,18}pueue",
            normalized,
        )
    )
    if cli_wrapper and not no_cli_wrapper:
        return "Pueue CLI wrapper"

    daemon_ownership = bool(
        re.search(
            r"(?:adapter|server|loommux).{0,30}"
            r"(?:owns?|manages?|supervises?|starts?|stops?|shuts? down|runs?).{0,20}"
            r"(?:daemon|pueued)|"
            r"(?:daemon|pueued).{0,24}(?:is|are).{0,12}"
            r"(?:owned|managed|supervised|started|stopped|run).{0,12}by.{0,12}"
            r"(?:adapter|server|loommux)|"
            r"(?:adapter|server|loommux).{0,24}(?:负责|拥有|管理|维护|启动|关闭|停止).{0,20}"
            r"(?:daemon|pueued)|"
            r"(?:daemon|pueued).{0,20}(?:由|归).{0,12}(?:adapter|server|loommux).{0,12}"
            r"(?:负责|拥有|管理|维护|启动|关闭|停止)",
            normalized,
        )
    )
    no_daemon_ownership = bool(
        re.search(
            r"(?:adapter|server|loommux).{0,30}"
            r"(?:does not|doesn't|do not|must not|never).{0,20}"
            r"(?:own|manage|supervise|start|stop).{0,24}(?:daemon|pueued)|"
            r"(?:adapter|server|loommux).{0,30}(?:不|不得|不会|禁止).{0,16}"
            r"(?:拥有|负责|管理|维护|启动|关闭|停止).{0,24}(?:daemon|pueued)|"
            r"(?:adapter|server|loommux).{0,24}不.{0,12}"
            r"(?:shutdown|reset|clean|stop).{0,24}(?:daemon|pueued)|"
            r"无.{0,80}adapter-owned daemon|"
            r"(?:daemon|pueued).{0,24}(?:不|不得|不会).{0,20}"
            r"(?:由|归).{0,16}(?:adapter|server|loommux)",
            normalized,
        )
    )
    if daemon_ownership and not no_daemon_ownership:
        return "adapter-owned daemon"
    return None


def audit_stale_claims(audit: Audit, artifacts: dict[str, str]) -> None:
    for name, text in artifacts.items():
        # This checker necessarily contains the literal patterns it rejects.
        if name == "scripts/audit_pueue_engine_docs.py":
            continue
        ranges = exemption_ranges(name, text)
        for pattern in STALE_WORKSPACE_CLAIMS:
            for match in pattern.finditer(text):
                if is_exempt(match.start(), ranges):
                    continue
                line_number = text.count("\n", 0, match.start()) + 1
                audit.fail(f"{name}:{line_number}: unqualified stale claim: {match.group(0)}")
        for segment_offset, prose in source_prose_segments(name, text):
            for unit_offset, claim in architecture_claim_units(prose):
                offset = segment_offset + unit_offset
                family = stale_architecture_family(claim)
                if family is not None and not is_exempt(offset, ranges):
                    line_number = text.count("\n", 0, offset) + 1
                    audit.fail(f"{name}:{line_number}: unqualified stale {family} claim")


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


def audit_migration_records(audit: Audit, documents: dict[str, str]) -> None:
    evidence = documents["evidence-register.adoc"]
    workspace_migration = anchored_section(
        evidence,
        "evidence-loommux-migration",
        "evidence-pueue",
    )
    for relation in (
        "#wp-workspace[WP-02]",
        "#wp-release[WP-11]",
        "repository coordinate `5cb6662`",
    ):
        audit.require(
            relation in workspace_migration,
            f"EVD-LOOMMUX-004: missing migration relation {relation}",
        )


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
    missing_verification = dict(documents)
    missing_verification["evidence-register.adoc"] = missing_verification["evidence-register.adoc"].replace(
        "Verification::\n运行 `uv run pytest tests/test_terminal_text.py -q`",
        "运行 `uv run pytest tests/test_terminal_text.py -q`",
        1,
    )
    record_probe = Audit()
    audit_record_shapes(record_probe, missing_verification)
    audit.require(
        any("EVD-LOOMMUX-003: missing Verification::" in error for error in record_probe.errors),
        "self-test: a missing non-Pueue evidence Verification field was not rejected",
    )

    invalid_disposition = dict(documents)
    invalid_disposition["evidence-register.adoc"] = invalid_disposition["evidence-register.adoc"].replace("|adopted\n", "|corrected\n", 1)
    ledger_probe = Audit()
    audit_claim_ledger(ledger_probe, invalid_disposition)
    audit.require(
        any("unsupported disposition" in error for error in ledger_probe.errors),
        "self-test: an unsupported claim-ledger disposition was not rejected",
    )

    adopted_stale_claim = dict(documents)
    adopted_stale_claim["evidence-register.adoc"] = adopted_stale_claim["evidence-register.adoc"].replace(
        "|Pueue adapter 应通过 CLI 操作 daemon。\n|superseded\n",
        "|Pueue adapter 应通过 CLI 操作 daemon。\n|adopted\n",
        1,
    )
    adopted_probe = Audit()
    audit_claim_ledger(adopted_probe, adopted_stale_claim)
    audit.require(
        any("stale claim cannot have adopted disposition" in error for error in adopted_probe.errors),
        "self-test: an adopted stale claim-ledger row was not rejected",
    )

    new_adopted_claim = dict(documents)
    new_adopted_claim["evidence-register.adoc"] = new_adopted_claim["evidence-register.adoc"].replace(
        "|===\n\n[#evidence-maintenance]",
        "|The server launches the pueue binary.\n|adopted\n|Alternative public contract.\n|===\n\n[#evidence-maintenance]",
        1,
    )
    new_adopted_probe = Audit()
    audit_claim_ledger(new_adopted_probe, new_adopted_claim)
    audit.require(
        any("unrecognized adopted claim" in error for error in new_adopted_probe.errors),
        "self-test: a newly phrased adopted claim-ledger row was not rejected",
    )

    missing_term = dict(documents)
    missing_term["design-specification.adoc"] = missing_term["design-specification.adoc"].replace("|initial wait\n", "|startup wait\n", 1)
    terminology_probe = Audit()
    audit_terminology(terminology_probe, missing_term)
    audit.require(
        any("exact canonical term set" in error for error in terminology_probe.errors),
        "self-test: a changed canonical term was not rejected",
    )

    alias_term = dict(documents)
    alias_term["index.adoc"] += "\n当前任务\n"
    alias_probe = Audit()
    audit_terminology(alias_probe, alias_term)
    audit.require(
        any("prohibited canonical-term alias" in error for error in alias_probe.errors),
        "self-test: a prohibited canonical-term alias was not rejected",
    )

    terminology_mutations = (
        ("`run_shell`", "`execute_shell`", "public tool"),
        ("`completed`", "`succeeded`", "execution status"),
        (
            "`workspace_config_not_found`",
            "`workspace_config_missing`",
            "workspace error kind",
        ),
        (
            "`backend_request_rejected`",
            "`backend_rejected`",
            "runtime error kind",
        ),
    )
    for old, new, expected_error in terminology_mutations:
        mutated = dict(documents)
        mutated["design-specification.adoc"] = mutated["design-specification.adoc"].replace(old, new)
        probe = Audit()
        audit_terminology(probe, mutated)
        audit.require(
            any(expected_error in error for error in probe.errors),
            f"self-test: authored surface mutation {old} -> {new} was not rejected",
        )

    usage_mutations = (
        ("index.adoc", "`run_shell`", "`submit_shell`"),
        ("documentation-quality-standard.adoc", "`cancelled`", "`aborted`"),
        ("index.adoc", "adapter-local", "server-local"),
    )
    for name, old, new in usage_mutations:
        mutated = dict(documents)
        mutated[name] = mutated[name].replace(old, new, 1)
        probe = Audit()
        audit_terminology(probe, mutated)
        audit.require(
            any("controlled terminology usage fingerprint changed" in error for error in probe.errors),
            f"self-test: cross-document surface mutation was not rejected: {old} -> {new}",
        )

    identifier_mutations = (
        "backend_unreachable",
        "unexpected_backend_message",
        "execution_missing",
        "malformed_script",
        "workspace_missing",
        "backend_operation_rejected",
    )
    for mutation in identifier_mutations:
        mutated = dict(documents)
        mutated["index.adoc"] += f"\n`{mutation}`\n"
        probe = Audit()
        audit_terminology(probe, mutated)
        audit.require(
            any("controlled terminology usage fingerprint changed" in error for error in probe.errors),
            f"self-test: new public-looking identifier was not rejected: {mutation}",
        )

    stale_claim_cases = (
        "Use a trusted Python resolver defining resolve_workspace.",
        "Pueue task ID is the public execution coordinate.",
        "Pueue task ID 作为面向 agent 的公开地址。",
        "The current execution is the implicit selection.",
        "默认选择最近一次 execution。",
        "The adapter shells out to the Pueue CLI.",
        "adapter 调用 pueue 命令行程序。",
        "The adapter owns the daemon.",
        "daemon 的生命周期由 adapter 管理。",
        "The server starts and stops pueued.",
        "Pueue task IDs are exposed to MCP clients.",
        "When omitted, Loommux chooses the newest execution automatically.",
        "Loommux runs the pueue executable.",
        "Loommux supervises pueued.",
        "向 MCP 调用方返回 Pueue 任务编号作为句柄。",
        "server 负责维护 pueued 进程。",
        "The adapter invokes the Pueue CLI, not the typed protocol.",
    )
    for claim in stale_claim_cases:
        stale_claim_probe = Audit()
        audit_stale_claims(stale_claim_probe, {"README.md": claim})
        audit.require(
            any("unqualified stale" in error and "claim" in error for error in stale_claim_probe.errors),
            f"self-test: unqualified stale claim was not rejected: {claim}",
        )

    legitimate_negative_cases = (
        "The adapter doesn't own the daemon.",
        "adapter 不负责 daemon 生命周期。",
    )
    for claim in legitimate_negative_cases:
        negative_probe = Audit()
        audit_stale_claims(negative_probe, {"README.md": claim})
        audit.require(
            not negative_probe.errors,
            f"self-test: legitimate negative claim was rejected: {claim}",
        )

    mixed_polarity_cases = (
        "The adapter doesn't own the daemon, but the server starts pueued.",
        "The adapter does not invoke Pueue, but Loommux runs the pueue executable.",
        "Pueue task IDs remain internal for logs but are exposed to MCP clients.",
        "The adapter doesn't own the daemon, while the server starts pueued.",
        "Although the adapter doesn't own the daemon, the server starts pueued.",
        "The adapter does not invoke Pueue, whereas Loommux runs the pueue executable.",
        "Pueue task IDs remain internal for logs, although they are exposed to MCP clients.",
        "adapter 不负责 daemon 生命周期，而 server 启动 pueued。",
        "The adapter, for compatibility, owns the daemon.",
        "Loommux, when configured, runs the pueue executable.",
        "server，按配置，启动 pueued。",
    )
    for claim in mixed_polarity_cases:
        mixed_probe = Audit()
        audit_stale_claims(mixed_probe, {"README.md": claim})
        audit.require(
            any("unqualified stale" in error for error in mixed_probe.errors),
            f"self-test: mixed-polarity stale claim was not rejected: {claim}",
        )

    source_claim_cases = {
        "fixture.py": '"""Runtime notes.\nLoommux runs the\npueue executable.\n"""',
        "comments.py": "# Loommux runs the\n# pueue executable.",
        "fixture.rs": "/* Runtime notes.\nThe adapter owns\nthe daemon.\n*/",
        "comments.rs": "/// The adapter owns\n/// the daemon.",
        "nested.rs": "/* outer /* The adapter owns\nthe daemon. */ outer */",
        "raw.rs": 'description = r"Loommux runs the pueue executable."',
        "doc-attribute.rs": '#[doc = "The adapter owns the daemon."]',
        "raw-doc-attribute.rs": '#[doc = r"Loommux runs the pueue executable."]',
        "fixture.toml": 'description = """Pueue task IDs are\nexposed to MCP clients."""',
        "comments.toml": "# Pueue task IDs are exposed\n# to MCP clients.",
        "single.toml": "description = 'The adapter owns the daemon.'",
        "literal.toml": "description = '''Loommux runs the\npueue executable.'''",
        "quoted-key.toml": '"description" = "The adapter owns the daemon."',
        "literal-key.toml": "'description' = 'Loommux runs the pueue executable.'",
        "call-description.py": 'Parser(description="The adapter owns the daemon.")',
        "decorator-description.py": '@tool(description="Loommux runs the pueue executable.")\ndef run(): ...',
    }
    for name, source in source_claim_cases.items():
        source_probe = Audit()
        audit_stale_claims(source_probe, {name: source})
        audit.require(
            any("unqualified stale" in error for error in source_probe.errors),
            f"self-test: multiline source claim was not rejected: {name}",
        )

    raw_source_probe = Audit()
    audit_stale_claims(
        raw_source_probe,
        {
            "fixture.py": "adapter = server\nowns = False\ndaemon = connection\n",
            "fixture_data.py": 'STALE_EXAMPLE = "The adapter owns the daemon."\n',
            "fixture.rs": "let adapter = server;\nlet manager_owns = false;\nlet daemon = connection;\n",
        },
    )
    audit.require(
        not raw_source_probe.errors,
        "self-test: raw source identifiers were misclassified as prose claims",
    )

    evidence_line_probe = dict(documents)
    injected_line = evidence_line_probe["evidence-register.adoc"].count("\n") + 2
    evidence_line_probe["evidence-register.adoc"] += "\nThe server starts and stops pueued.\n"
    line_probe = Audit()
    audit_stale_claims(
        line_probe,
        {"docs/pueue-engine/evidence-register.adoc": evidence_line_probe["evidence-register.adoc"]},
    )
    audit.require(
        any(f":{injected_line}:" in error for error in line_probe.errors),
        "self-test: stale-claim diagnostic did not preserve the source line number",
    )

    missing_release_mapping = dict(documents)
    missing_release_mapping["evidence-register.adoc"] = missing_release_mapping["evidence-register.adoc"].replace("#wp-release[WP-11]", "#wp-workspace[WP-02]", 1)
    migration_probe = Audit()
    audit_migration_records(migration_probe, missing_release_mapping)
    audit.require(
        any("missing migration relation" in error for error in migration_probe.errors),
        "self-test: missing release migration mapping was not rejected",
    )

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
    audit_claim_ledger(audit, documents)
    audit_terminology(audit, documents)
    audit_migration_records(audit, documents)
    audit_traceability(audit, documents)
    audit_stale_claims(audit, repository_authored_texts())
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

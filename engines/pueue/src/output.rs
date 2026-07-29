//! Append-only line reads and searches over normalized task output.

use std::{
    fs::{File, Metadata},
    io::{Read, Seek, SeekFrom},
    path::Path,
};

#[cfg(unix)]
use std::os::unix::fs::MetadataExt;

use regex::{Regex, RegexBuilder};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::terminal_text::{TerminalTextNormalizer, Utf8StreamDecoder};

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum OutputError {
    #[error("line range is invalid")]
    InvalidLineRange,
    #[error("query is invalid")]
    InvalidQuery,
    #[error("context is invalid")]
    InvalidContext,
    #[error("maximum line length is invalid")]
    InvalidMaxChars,
    #[error("backend log is unavailable")]
    BackendLogUnavailable,
    #[error("backend log was replaced or truncated")]
    BackendLogReplaced,
}

impl OutputError {
    #[must_use]
    pub const fn kind(self) -> &'static str {
        match self {
            Self::InvalidLineRange => "invalid_line_range",
            Self::InvalidQuery => "invalid_query",
            Self::InvalidContext => "invalid_context",
            Self::InvalidMaxChars => "invalid_max_chars",
            Self::BackendLogUnavailable => "backend_log_unavailable",
            Self::BackendLogReplaced => "backend_log_replaced",
        }
    }
}

#[cfg(test)]
mod error_tests {
    use super::OutputError;

    #[test]
    fn output_error_kinds_are_exhaustive() {
        let cases = [
            (OutputError::InvalidLineRange, "invalid_line_range"),
            (OutputError::InvalidQuery, "invalid_query"),
            (OutputError::InvalidContext, "invalid_context"),
            (OutputError::InvalidMaxChars, "invalid_max_chars"),
            (
                OutputError::BackendLogUnavailable,
                "backend_log_unavailable",
            ),
            (OutputError::BackendLogReplaced, "backend_log_replaced"),
        ];
        for (error, expected) in cases {
            assert_eq!(error.kind(), expected);
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OutputRead {
    pub line_range: Option<String>,
    pub total_lines: usize,
    pub returned_lines: usize,
    pub omitted_before: usize,
    pub omitted_after: usize,
    pub partial_final_line: bool,
    pub text: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum QueryMode {
    #[default]
    Auto,
    Literal,
    Regex,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OutputSearch {
    pub query: String,
    pub query_interpretation: QueryMode,
    pub matched_lines: usize,
    pub matches: usize,
    pub context_before: usize,
    pub context_after: usize,
    pub total_lines: usize,
    pub text: String,
}

#[derive(Debug, Default, Clone)]
pub struct LineLog {
    text: String,
}

#[derive(Debug, Default)]
pub struct OutputCollector {
    next_offset: u64,
    log_identity: Option<LogIdentity>,
    decoder: Utf8StreamDecoder,
    normalizer: TerminalTextNormalizer,
    log: LineLog,
    finalized: bool,
    observed_bytes: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct LogIdentity {
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(not(unix))]
    created: Option<std::time::SystemTime>,
}

impl LogIdentity {
    fn from_metadata(metadata: &Metadata) -> Self {
        Self {
            #[cfg(unix)]
            device: metadata.dev(),
            #[cfg(unix)]
            inode: metadata.ino(),
            #[cfg(not(unix))]
            created: metadata.created().ok(),
        }
    }
}

impl OutputCollector {
    /// Append only bytes not consumed by earlier refreshes.
    ///
    /// `log_expected` is true after a process has started or any log byte was
    /// already observed. A missing pre-start log remains a valid empty transcript.
    ///
    /// # Errors
    ///
    /// Returns a stable unavailable or replacement category; it never rebuilds
    /// an existing transcript from a shortened file.
    pub fn refresh(
        &mut self,
        path: &Path,
        terminal: bool,
        log_expected: bool,
    ) -> Result<(), OutputError> {
        if self.finalized {
            return Ok(());
        }
        let mut file = match File::open(path) {
            Ok(file) => file,
            Err(error)
                if error.kind() == std::io::ErrorKind::NotFound
                    && !log_expected
                    && !self.observed_bytes =>
            {
                if terminal {
                    self.finish();
                }
                return Ok(());
            }
            Err(_) => return Err(OutputError::BackendLogUnavailable),
        };
        let metadata = file
            .metadata()
            .map_err(|_| OutputError::BackendLogUnavailable)?;
        let identity = LogIdentity::from_metadata(&metadata);
        if self.log_identity.is_some_and(|current| current != identity) {
            return Err(OutputError::BackendLogReplaced);
        }
        self.log_identity = Some(identity);
        let length = metadata.len();
        if length < self.next_offset {
            return Err(OutputError::BackendLogReplaced);
        }
        file.seek(SeekFrom::Start(self.next_offset))
            .map_err(|_| OutputError::BackendLogUnavailable)?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes)
            .map_err(|_| OutputError::BackendLogUnavailable)?;
        self.next_offset +=
            u64::try_from(bytes.len()).map_err(|_| OutputError::BackendLogUnavailable)?;
        self.observed_bytes |= !bytes.is_empty();
        let decoded = self.decoder.push(&bytes);
        self.log.append(&self.normalizer.push(&decoded));
        if terminal {
            self.finish();
        }
        Ok(())
    }

    #[must_use]
    pub const fn finalized(&self) -> bool {
        self.finalized
    }

    #[must_use]
    pub const fn decode_replacements(&self) -> u64 {
        self.decoder.replacements()
    }

    #[must_use]
    pub const fn log(&self) -> &LineLog {
        &self.log
    }

    pub(crate) fn freeze(&mut self) {
        if !self.finalized {
            self.finish();
        }
    }

    fn finish(&mut self) {
        let decoded = self.decoder.finish();
        self.log.append(&self.normalizer.push(&decoded));
        self.normalizer.finish();
        self.finalized = true;
    }
}

impl LineLog {
    pub fn append(&mut self, text: &str) {
        self.text.push_str(text);
    }

    #[must_use]
    pub fn text(&self) -> &str {
        &self.text
    }

    #[must_use]
    pub fn line_count(&self) -> usize {
        self.lines().len()
    }

    #[must_use]
    pub fn partial_final_line(&self) -> bool {
        !self.text.is_empty() && !ends_with_splitlines_boundary(&self.text)
    }

    /// Read an inclusive authored line range without consuming stored output.
    ///
    /// # Errors
    ///
    /// Returns a stable range or clipping validation error.
    pub fn read(
        &self,
        line_range: Option<&str>,
        max_chars: Option<usize>,
    ) -> Result<OutputRead, OutputError> {
        validate_max_chars(max_chars)?;
        let lines = self.lines();
        let (start, stop) = resolve_range(line_range, lines.len())?;
        let selected = if start > stop {
            &[][..]
        } else {
            &lines[start - 1..stop]
        };
        let text = selected
            .iter()
            .map(|line| clip_line(line, max_chars))
            .collect::<Vec<_>>()
            .join("\n");
        Ok(OutputRead {
            line_range: line_range.map(str::to_owned),
            total_lines: lines.len(),
            returned_lines: selected.len(),
            omitted_before: if selected.is_empty() {
                lines.len()
            } else {
                start - 1
            },
            omitted_after: if selected.is_empty() {
                0
            } else {
                lines.len() - stop
            },
            partial_final_line: self.partial_final_line(),
            text,
        })
    }

    /// Search current output with stable original line coordinates.
    ///
    /// # Errors
    ///
    /// Returns stable query, context, or clipping validation errors.
    pub fn search(&self, request: &SearchRequest<'_>) -> Result<OutputSearch, OutputError> {
        validate_max_chars(request.max_chars)?;
        let (matcher, interpretation) =
            compile_matcher(request.query, request.query_mode, request.ignore_case)?;
        let lines = self.lines();
        let match_counts = lines
            .iter()
            .map(|line| matcher.count(line))
            .collect::<Vec<_>>();
        let mut selected = vec![false; lines.len()];
        for (index, count) in match_counts.iter().enumerate() {
            if *count == 0 {
                continue;
            }
            let first = index.saturating_sub(request.context_before);
            let last = (index + request.context_after).min(lines.len().saturating_sub(1));
            selected[first..=last].fill(true);
        }
        let text = selected
            .iter()
            .enumerate()
            .filter(|(_, include)| **include)
            .map(|(index, _)| {
                format!(
                    "{} {} | {}",
                    if match_counts[index] > 0 { 'M' } else { 'C' },
                    index + 1,
                    clip_line(lines[index], request.max_chars)
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(OutputSearch {
            query: request.query.into(),
            query_interpretation: interpretation,
            matched_lines: match_counts.iter().filter(|count| **count > 0).count(),
            matches: match_counts.iter().sum(),
            context_before: request.context_before,
            context_after: request.context_after,
            total_lines: lines.len(),
            text,
        })
    }

    fn lines(&self) -> Vec<&str> {
        python_splitlines(&self.text)
    }
}

fn python_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut characters = text.char_indices().peekable();
    while let Some((index, character)) = characters.next() {
        let is_boundary = matches!(
            character,
            '\n' | '\r'
                | '\u{000b}'
                | '\u{000c}'
                | '\u{001c}'
                | '\u{001d}'
                | '\u{001e}'
                | '\u{0085}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !is_boundary {
            continue;
        }
        lines.push(&text[start..index]);
        let mut next = index + character.len_utf8();
        if character == '\r'
            && characters
                .peek()
                .is_some_and(|(_, next_char)| *next_char == '\n')
        {
            let (newline_index, newline) = characters.next().expect("peeked newline exists");
            next = newline_index + newline.len_utf8();
        }
        start = next;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

fn ends_with_splitlines_boundary(text: &str) -> bool {
    text.chars().next_back().is_some_and(|character| {
        matches!(
            character,
            '\n' | '\r'
                | '\u{000b}'
                | '\u{000c}'
                | '\u{001c}'
                | '\u{001d}'
                | '\u{001e}'
                | '\u{0085}'
                | '\u{2028}'
                | '\u{2029}'
        )
    })
}

pub struct SearchRequest<'a> {
    pub query: &'a str,
    pub query_mode: QueryMode,
    pub context_before: usize,
    pub context_after: usize,
    pub ignore_case: bool,
    pub max_chars: Option<usize>,
}

enum Matcher {
    Literal { needle: String, ignore_case: bool },
    Regex(Regex),
}

impl Matcher {
    fn count(&self, line: &str) -> usize {
        match self {
            Self::Literal {
                needle,
                ignore_case,
            } => {
                if needle.is_empty() {
                    return 1;
                }
                if *ignore_case {
                    line.to_lowercase().matches(needle).count()
                } else {
                    line.matches(needle).count()
                }
            }
            Self::Regex(pattern) => pattern.find_iter(line).count(),
        }
    }
}

fn compile_matcher(
    query: &str,
    mode: QueryMode,
    ignore_case: bool,
) -> Result<(Matcher, QueryMode), OutputError> {
    if mode == QueryMode::Literal {
        return Ok((
            Matcher::Literal {
                needle: if ignore_case {
                    query.to_lowercase()
                } else {
                    query.into()
                },
                ignore_case,
            },
            QueryMode::Literal,
        ));
    }
    match RegexBuilder::new(query)
        .case_insensitive(ignore_case)
        .build()
    {
        Ok(pattern) => Ok((Matcher::Regex(pattern), QueryMode::Regex)),
        Err(_) if mode == QueryMode::Auto => Ok((
            Matcher::Literal {
                needle: if ignore_case {
                    query.to_lowercase()
                } else {
                    query.into()
                },
                ignore_case,
            },
            QueryMode::Literal,
        )),
        Err(_) => Err(OutputError::InvalidQuery),
    }
}

fn validate_max_chars(value: Option<usize>) -> Result<(), OutputError> {
    if value == Some(0) {
        Err(OutputError::InvalidMaxChars)
    } else {
        Ok(())
    }
}

fn resolve_range(authored: Option<&str>, total: usize) -> Result<(usize, usize), OutputError> {
    if total == 0 {
        return Ok((1, 0));
    }
    let Some(authored) = authored else {
        return Ok((1, total));
    };
    let (start, stop) = authored
        .split_once(':')
        .ok_or(OutputError::InvalidLineRange)?;
    let start = resolve_endpoint(start, total, 1)?;
    let stop = resolve_endpoint(stop, total, total)?;
    Ok((start.clamp(1, total + 1), stop.clamp(0, total)))
}

fn resolve_endpoint(authored: &str, total: usize, default: usize) -> Result<usize, OutputError> {
    if authored.is_empty() {
        return Ok(default);
    }
    let value = authored
        .parse::<isize>()
        .map_err(|_| OutputError::InvalidLineRange)?;
    let resolved = if value < 0 {
        isize::try_from(total).unwrap_or(isize::MAX) + value + 1
    } else {
        value
    };
    Ok(usize::try_from(resolved.max(0)).expect("nonnegative isize always fits usize"))
}

fn clip_line(line: &str, max_chars: Option<usize>) -> String {
    let Some(max_chars) = max_chars else {
        return line.into();
    };
    let length = line.chars().count();
    if length <= max_chars {
        return line.into();
    }
    let prefix = line.chars().take(max_chars).collect::<String>();
    format!("{prefix}...[{} chars omitted]", length - max_chars)
}

#[cfg(test)]
mod tests {
    use super::{LineLog, OutputCollector, OutputError, QueryMode, SearchRequest};
    use serde::Deserialize;
    use std::{
        fs,
        io::Write,
        sync::atomic::{AtomicUsize, Ordering},
    };

    static NEXT_FILE: AtomicUsize = AtomicUsize::new(0);

    #[derive(Deserialize)]
    struct Fixture {
        line_log: Vec<LineCase>,
    }

    #[derive(Deserialize)]
    struct LineCase {
        name: String,
        text: String,
        reads: Vec<ReadCase>,
        searches: Vec<SearchCase>,
    }

    #[derive(Deserialize)]
    struct ReadCase {
        line_range: Option<String>,
        max_chars: Option<usize>,
        expected_text: String,
        total_lines: usize,
        returned_lines: usize,
        omitted_before: usize,
        omitted_after: usize,
    }

    #[derive(Deserialize)]
    struct SearchCase {
        query: String,
        query_mode: QueryMode,
        context_before: usize,
        context_after: usize,
        ignore_case: bool,
        max_chars: Option<usize>,
        expected_text: String,
        query_interpretation: QueryMode,
        matched_lines: usize,
        matches: usize,
        total_lines: usize,
    }

    fn log() -> LineLog {
        let mut log = LineLog::default();
        log.append("alpha\nbeta match\ngamma match\ndelta");
        log
    }

    #[test]
    fn inclusive_and_negative_ranges_are_repeatable() {
        let log = log();
        let first = log.read(Some("2:3"), None).unwrap();
        let second = log.read(Some("-3:-2"), None).unwrap();
        assert_eq!(first.text, "beta match\ngamma match");
        assert_eq!(first.text, second.text);
        assert_eq!(first.returned_lines, second.returned_lines);
        assert_eq!(first.omitted_before, second.omitted_before);
        assert_eq!(first.omitted_after, second.omitted_after);
        assert_eq!(
            log.read(None, Some(2)).unwrap().text.lines().next(),
            Some("al...[3 chars omitted]")
        );
        assert_eq!(
            log.read(Some("bad"), None).unwrap_err(),
            OutputError::InvalidLineRange
        );
    }

    #[test]
    fn search_deduplicates_overlapping_context_and_falls_back_to_literal() {
        let log = log();
        let automatic = log
            .search(&SearchRequest {
                query: "match",
                query_mode: QueryMode::Auto,
                context_before: 1,
                context_after: 1,
                ignore_case: false,
                max_chars: None,
            })
            .unwrap();
        assert_eq!(automatic.query_interpretation, QueryMode::Regex);
        assert_eq!(automatic.matched_lines, 2);
        assert_eq!(automatic.matches, 2);
        assert_eq!(automatic.text.lines().count(), 4);
        assert_eq!(
            automatic.text,
            "C 1 | alpha\nM 2 | beta match\nM 3 | gamma match\nC 4 | delta"
        );

        let regex = log
            .search(&SearchRequest {
                query: "m.tch",
                query_mode: QueryMode::Regex,
                context_before: 0,
                context_after: 0,
                ignore_case: false,
                max_chars: None,
            })
            .unwrap();
        assert_eq!(regex.query_interpretation, QueryMode::Regex);
        assert_eq!(regex.matched_lines, 2);
        assert_eq!(regex.matches, 2);
        assert!(regex.text.lines().all(|line| line.starts_with("M ")));

        let literal = log
            .search(&SearchRequest {
                query: "MATCH",
                query_mode: QueryMode::Literal,
                context_before: 0,
                context_after: 0,
                ignore_case: true,
                max_chars: None,
            })
            .unwrap();
        assert_eq!(literal.query_interpretation, QueryMode::Literal);
        assert_eq!(literal.matches, 2);

        let fallback = log
            .search(&SearchRequest {
                query: "[",
                query_mode: QueryMode::Auto,
                context_before: 0,
                context_after: 0,
                ignore_case: false,
                max_chars: None,
            })
            .unwrap();
        assert_eq!(fallback.query_interpretation, QueryMode::Literal);
    }

    #[test]
    fn collector_reads_only_growth_and_rejects_truncation() {
        let id = NEXT_FILE.fetch_add(1, Ordering::Relaxed);
        let directory =
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/output-tests");
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join(format!("{}-{id}.log", std::process::id()));
        fs::write(&path, b"one\n\x1b[31mtwo").unwrap();
        let mut collector = OutputCollector::default();
        collector.refresh(&path, false, true).unwrap();
        fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(b"\x1b[0m\nthree")
            .unwrap();
        collector.refresh(&path, true, true).unwrap();
        assert_eq!(collector.log().text(), "one\ntwo\nthree");
        assert!(collector.finalized());

        let mut replaced = OutputCollector::default();
        fs::write(&path, b"long output").unwrap();
        replaced.refresh(&path, false, true).unwrap();
        fs::write(&path, b"x").unwrap();
        assert_eq!(
            replaced.refresh(&path, false, true).unwrap_err(),
            OutputError::BackendLogReplaced
        );
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn collector_rejects_missing_expected_unreadable_and_same_length_replacement() {
        let id = NEXT_FILE.fetch_add(1, Ordering::Relaxed);
        let directory =
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/output-tests");
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join(format!("robustness-{}-{id}.log", std::process::id()));

        let mut missing = OutputCollector::default();
        assert_eq!(
            missing.refresh(&path, false, true).unwrap_err(),
            OutputError::BackendLogUnavailable
        );
        let mut pre_start = OutputCollector::default();
        pre_start.refresh(&path, false, false).unwrap();
        assert!(!pre_start.finalized());

        let mut unreadable = OutputCollector::default();
        assert_eq!(
            unreadable.refresh(&directory, false, true).unwrap_err(),
            OutputError::BackendLogUnavailable
        );

        fs::write(&path, b"first").unwrap();
        let mut replaced = OutputCollector::default();
        replaced.refresh(&path, false, true).unwrap();
        let replacement = path.with_extension("replacement");
        fs::write(&replacement, b"other").unwrap();
        fs::rename(&replacement, &path).unwrap();
        assert_eq!(
            replaced.refresh(&path, false, true).unwrap_err(),
            OutputError::BackendLogReplaced
        );
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn repeated_incremental_refresh_matches_one_shot_normalization() {
        let id = NEXT_FILE.fetch_add(1, Ordering::Relaxed);
        let directory =
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/output-tests");
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join(format!("incremental-{}-{id}.log", std::process::id()));
        let chunks: [&[u8]; 5] = [
            b"one\n\x1b[3",
            b"1mtwo",
            b"\x1b[0m\ninvalid \xf0\x28",
            b"\x8c\xbc\nosc \x1b]0;title",
            b"\x07done",
        ];

        fs::write(&path, []).unwrap();
        let mut incremental = OutputCollector::default();
        for (index, chunk) in chunks.iter().enumerate() {
            fs::OpenOptions::new()
                .append(true)
                .open(&path)
                .unwrap()
                .write_all(chunk)
                .unwrap();
            incremental
                .refresh(&path, index + 1 == chunks.len(), true)
                .unwrap();
        }

        let all = chunks.concat();
        fs::write(&path, &all).unwrap();
        let mut one_shot = OutputCollector::default();
        one_shot.refresh(&path, true, true).unwrap();
        assert_eq!(incremental.log().text(), one_shot.log().text());
        assert_eq!(
            incremental.decode_replacements(),
            one_shot.decode_replacements()
        );
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn collector_handles_ten_mibibytes_of_mixed_terminal_output() {
        let id = NEXT_FILE.fetch_add(1, Ordering::Relaxed);
        let directory =
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/output-tests");
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join(format!("large-{}-{id}.log", std::process::id()));
        let fragment = b"alpha \x1b[31mbeta\x1b[0m \xff\n";
        let repetitions = 10 * 1024 * 1024 / fragment.len() + 1;
        let mut bytes = Vec::with_capacity(repetitions * fragment.len());
        for _ in 0..repetitions {
            bytes.extend_from_slice(fragment);
        }
        fs::write(&path, &bytes).unwrap();

        let mut collector = OutputCollector::default();
        collector.refresh(&path, true, true).unwrap();
        assert!(collector.finalized());
        assert_eq!(collector.log().line_count(), repetitions);
        assert_eq!(collector.decode_replacements(), repetitions as u64);
        assert!(!collector.log().text().contains('\u{001b}'));
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn line_boundaries_match_python_splitlines() {
        let mut log = LineLog::default();
        log.append(
            "a\rb\r\nc\u{000b}d\u{000c}e\u{001c}f\u{001d}g\u{001e}h\u{0085}i\u{2028}j\u{2029}",
        );
        assert_eq!(log.line_count(), 10);
        assert_eq!(
            log.read(None, None).unwrap().text,
            "a\nb\nc\nd\ne\nf\ng\nh\ni\nj"
        );
        assert!(!log.partial_final_line());
        log.append("tail");
        assert!(log.partial_final_line());
    }

    #[test]
    fn shared_line_log_contract_vectors_match_python() {
        let fixture: Fixture = serde_json::from_str(include_str!(
            "../../../tests/fixtures/text_contract/cases.json"
        ))
        .unwrap();
        for case in fixture.line_log {
            let mut log = LineLog::default();
            log.append(&case.text);
            for request in case.reads {
                let result = log
                    .read(request.line_range.as_deref(), request.max_chars)
                    .unwrap();
                assert_eq!(result.text, request.expected_text, "{} read", case.name);
                assert_eq!(result.total_lines, request.total_lines);
                assert_eq!(result.returned_lines, request.returned_lines);
                assert_eq!(result.omitted_before, request.omitted_before);
                assert_eq!(result.omitted_after, request.omitted_after);
            }
            for request in case.searches {
                let result = log
                    .search(&SearchRequest {
                        query: &request.query,
                        query_mode: request.query_mode,
                        context_before: request.context_before,
                        context_after: request.context_after,
                        ignore_case: request.ignore_case,
                        max_chars: request.max_chars,
                    })
                    .unwrap();
                assert_eq!(result.text, request.expected_text, "{} search", case.name);
                assert_eq!(result.query_interpretation, request.query_interpretation);
                assert_eq!(result.matched_lines, request.matched_lines);
                assert_eq!(result.matches, request.matches);
                assert_eq!(result.total_lines, request.total_lines);
            }
        }
    }
}

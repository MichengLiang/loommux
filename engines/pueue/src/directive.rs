//! Shell control-preamble scanning, validation, and exact consumption.

use std::{ops::Range, time::Duration};

use crate::deadline::positive_duration;

use thiserror::Error;

const DIRECTIVE_PREFIX: &str = "# loommux:";
const DEFAULT_WAIT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, PartialEq)]
pub struct PreparedRunShell {
    pub script: String,
    pub initial_wait: Duration,
    pub full_output_requested: bool,
}

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum DirectiveError {
    #[error("invalid Loommux control directive")]
    InvalidDirective,
    #[error("shell script is empty")]
    InvalidScript,
}

impl DirectiveError {
    #[must_use]
    pub const fn kind(self) -> &'static str {
        match self {
            Self::InvalidDirective => "invalid_loommux_directive",
            Self::InvalidScript => "invalid_script",
        }
    }
}

/// Validate the leading control preamble and return the exact remaining script.
///
/// # Errors
///
/// Returns `invalid_loommux_directive` for malformed active directives and
/// `invalid_script` when their removal leaves only whitespace.
pub fn prepare_run_shell(source: &str) -> Result<PreparedRunShell, DirectiveError> {
    let scan = scan_preamble(source)?;
    let script = delete_ranges(source, &scan.ranges);
    if script.trim().is_empty() {
        return Err(DirectiveError::InvalidScript);
    }
    Ok(PreparedRunShell {
        script,
        initial_wait: scan.wait.unwrap_or(DEFAULT_WAIT),
        full_output_requested: scan.full_output,
    })
}

#[derive(Debug, Default)]
struct PreambleScan {
    ranges: Vec<Range<usize>>,
    wait: Option<Duration>,
    full_output: bool,
}

fn scan_preamble(source: &str) -> Result<PreambleScan, DirectiveError> {
    let mut scan = PreambleScan::default();
    let mut offset = 0;
    for inclusive_line in source.split_inclusive('\n') {
        let physical_line = inclusive_line.strip_suffix('\n').unwrap_or(inclusive_line);
        let physical_line = physical_line.strip_suffix('\r').unwrap_or(physical_line);
        if physical_line.is_empty() {
            offset += inclusive_line.len();
            continue;
        }
        let Some(options) = physical_line.strip_prefix(DIRECTIVE_PREFIX) else {
            break;
        };
        parse_options(options, &mut scan)?;
        scan.ranges.push(offset..offset + inclusive_line.len());
        offset += inclusive_line.len();
    }
    Ok(scan)
}

fn parse_options(source: &str, scan: &mut PreambleScan) -> Result<(), DirectiveError> {
    if !source.starts_with(' ') {
        return Err(DirectiveError::InvalidDirective);
    }
    let mut options = source.split_ascii_whitespace().peekable();
    if options.peek().is_none() {
        return Err(DirectiveError::InvalidDirective);
    }
    while let Some(option) = options.next() {
        match option {
            "--full-output" => {
                if scan.full_output {
                    return Err(DirectiveError::InvalidDirective);
                }
                scan.full_output = true;
            }
            "--wait" => {
                if scan.wait.is_some() {
                    return Err(DirectiveError::InvalidDirective);
                }
                let value = options.next().ok_or(DirectiveError::InvalidDirective)?;
                if value.starts_with("--") {
                    return Err(DirectiveError::InvalidDirective);
                }
                let seconds = value
                    .parse::<f64>()
                    .map_err(|_| DirectiveError::InvalidDirective)?;
                scan.wait =
                    Some(positive_duration(seconds).ok_or(DirectiveError::InvalidDirective)?);
            }
            _ => return Err(DirectiveError::InvalidDirective),
        }
    }
    Ok(())
}

fn delete_ranges(source: &str, ranges: &[Range<usize>]) -> String {
    let removed_bytes = ranges.iter().map(Range::len).sum::<usize>();
    let mut result = String::with_capacity(source.len() - removed_bytes);
    let mut copied_until = 0;
    for range in ranges {
        result.push_str(&source[copied_until..range.start]);
        copied_until = range.end;
    }
    result.push_str(&source[copied_until..]);
    result
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use proptest::prelude::*;

    use super::{DirectiveError, delete_ranges, prepare_run_shell, scan_preamble};

    #[test]
    fn consumes_multiple_directives_and_preserves_other_bytes() {
        let source =
            "\r\n# loommux: --wait 0.25\r\n\n# loommux: --full-output\n# ordinary\nprintf ok\r\n";
        let prepared = prepare_run_shell(source).unwrap();
        assert_eq!(prepared.script, "\r\n\n# ordinary\nprintf ok\r\n");
        assert_eq!(prepared.initial_wait, Duration::from_millis(250));
        assert!(prepared.full_output_requested);
    }

    #[test]
    fn first_source_line_stops_scanning_including_here_document_data() {
        for source in [
            "#!/bin/sh\n# loommux: --wait 1\necho ok\n",
            "cat <<'EOF'\n# loommux: --wait 1\nEOF\n",
            "# ordinary\n# loommux: --wait 1\n",
        ] {
            assert_eq!(prepare_run_shell(source).unwrap().script, source);
        }
    }

    #[test]
    fn final_line_without_newline_is_consumed_exactly() {
        assert_eq!(
            prepare_run_shell("# loommux: --full-output\nprintf ok")
                .unwrap()
                .script,
            "printf ok"
        );
    }

    #[test]
    fn invalid_directives_and_empty_clean_script_are_rejected() {
        for source in [
            "# loommux:\necho ok",
            "# loommux:--wait 1\necho ok",
            "# loommux: --wait\necho ok",
            "# loommux: --wait 0\necho ok",
            "# loommux: --wait -1\necho ok",
            "# loommux: --wait NaN\necho ok",
            "# loommux: --wait inf\necho ok",
            "# loommux: --wait 1e-300\necho ok",
            "# loommux: --wait 1e300\necho ok",
            "# loommux: --wait 1e999\necho ok",
            "# loommux: --wait 1 --wait 2\necho ok",
            "# loommux: --full-output --full-output\necho ok",
            "# loommux: --unknown\necho ok",
        ] {
            assert_eq!(
                prepare_run_shell(source).unwrap_err(),
                DirectiveError::InvalidDirective
            );
        }
        assert_eq!(
            prepare_run_shell("# loommux: --wait 1\n \n").unwrap_err(),
            DirectiveError::InvalidScript
        );
    }

    proptest! {
        #[test]
        fn deletion_is_exact_for_arbitrary_prefix_and_script(
            empty_lines in prop::collection::vec(prop_oneof![Just("\n"), Just("\r\n")], 0..8),
            wait in 1_u32..100_000,
            body in "[ -~]{1,80}",
        ) {
            let source = format!("{}# loommux: --wait {wait}\n{body}", empty_lines.concat());
            let scan = scan_preamble(&source).unwrap();
            let deleted = delete_ranges(&source, &scan.ranges);
            let expected = format!("{}{body}", empty_lines.concat());
            prop_assert_eq!(deleted, expected);
        }
    }
}

//! Streaming UTF-8 decoding and terminal-text normalization.

#[derive(Debug, Default)]
pub struct Utf8StreamDecoder {
    pending: Vec<u8>,
    replacements: u64,
}

impl Utf8StreamDecoder {
    pub fn push(&mut self, bytes: &[u8]) -> String {
        self.pending.extend_from_slice(bytes);
        self.decode(false)
    }

    pub fn finish(&mut self) -> String {
        self.decode(true)
    }

    #[must_use]
    pub const fn replacements(&self) -> u64 {
        self.replacements
    }

    fn decode(&mut self, finish: bool) -> String {
        let mut output = String::with_capacity(self.pending.len());
        let mut consumed = 0;
        while consumed < self.pending.len() {
            match std::str::from_utf8(&self.pending[consumed..]) {
                Ok(text) => {
                    output.push_str(text);
                    consumed = self.pending.len();
                    break;
                }
                Err(error) => {
                    let valid = error.valid_up_to();
                    output.push_str(
                        std::str::from_utf8(&self.pending[consumed..consumed + valid])
                            .expect("valid_up_to identifies a UTF-8 prefix"),
                    );
                    consumed += valid;
                    if let Some(length) = error.error_len() {
                        output.push('\u{fffd}');
                        self.replacements += 1;
                        consumed += length;
                    } else if finish {
                        output.push('\u{fffd}');
                        self.replacements += 1;
                        consumed = self.pending.len();
                        break;
                    } else {
                        break;
                    }
                }
            }
        }
        self.pending.drain(..consumed);
        output
    }
}

#[derive(Debug, Clone, Copy, Default)]
enum EscapeState {
    #[default]
    Ground,
    Escape,
    EscapeIntermediate,
    Csi,
    Osc,
    OscEscape,
    String,
    StringEscape,
}

#[derive(Debug, Default)]
pub struct TerminalTextNormalizer {
    state: EscapeState,
}

impl TerminalTextNormalizer {
    pub fn push(&mut self, text: &str) -> String {
        let mut output = String::with_capacity(text.len());
        for character in text.chars() {
            match self.state {
                EscapeState::Ground => match character {
                    '\u{1b}' => self.state = EscapeState::Escape,
                    '\u{9b}' => self.state = EscapeState::Csi,
                    '\u{9d}' => self.state = EscapeState::Osc,
                    '\u{90}' | '\u{98}' | '\u{9e}' | '\u{9f}' => {
                        self.state = EscapeState::String;
                    }
                    '\r' | '\u{8}' => {}
                    '\n' | '\t' => output.push(character),
                    control if control.is_control() => {}
                    _ => output.push(character),
                },
                EscapeState::Escape => {
                    self.state = match character {
                        '[' => EscapeState::Csi,
                        ']' => EscapeState::Osc,
                        'P' | 'X' | '^' | '_' => EscapeState::String,
                        ' '..='/' => EscapeState::EscapeIntermediate,
                        _ => EscapeState::Ground,
                    };
                }
                EscapeState::EscapeIntermediate => {
                    self.state = if character == '\u{1b}' {
                        EscapeState::Escape
                    } else if (' '..='/').contains(&character) {
                        EscapeState::EscapeIntermediate
                    } else {
                        EscapeState::Ground
                    };
                }
                EscapeState::Csi => {
                    if character == '\u{1b}' {
                        self.state = EscapeState::Escape;
                    } else if character == '\u{9c}' || ('@'..='~').contains(&character) {
                        self.state = EscapeState::Ground;
                    }
                }
                EscapeState::Osc => match character {
                    '\u{7}' | '\u{9c}' => self.state = EscapeState::Ground,
                    '\u{1b}' => self.state = EscapeState::OscEscape,
                    _ => {}
                },
                EscapeState::OscEscape => {
                    self.state = if character == '\\' {
                        EscapeState::Ground
                    } else {
                        EscapeState::Osc
                    };
                }
                EscapeState::String => {
                    if character == '\u{9c}' {
                        self.state = EscapeState::Ground;
                    } else if character == '\u{1b}' {
                        self.state = EscapeState::StringEscape;
                    }
                }
                EscapeState::StringEscape => {
                    self.state = if character == '\\' {
                        EscapeState::Ground
                    } else {
                        EscapeState::String
                    };
                }
            }
        }
        output
    }

    pub fn finish(&mut self) {
        self.state = EscapeState::Ground;
    }
}

#[cfg(test)]
mod tests {
    use serde::Deserialize;

    use super::{TerminalTextNormalizer, Utf8StreamDecoder};

    #[derive(Deserialize)]
    struct Fixture {
        normalization: Vec<NormalizationCase>,
    }

    #[derive(Deserialize)]
    struct NormalizationCase {
        name: String,
        input: String,
        expected: String,
    }

    fn normalize_byte_chunks(chunks: &[&[u8]]) -> (String, u64) {
        let mut decoder = Utf8StreamDecoder::default();
        let mut normalizer = TerminalTextNormalizer::default();
        let mut output = String::new();
        for chunk in chunks {
            output.push_str(&normalizer.push(&decoder.push(chunk)));
        }
        output.push_str(&normalizer.push(&decoder.finish()));
        normalizer.finish();
        (output, decoder.replacements())
    }

    #[test]
    fn utf8_decoder_handles_every_one_byte_chunk_and_invalid_tail() {
        let source = "你好 café".as_bytes();
        let mut decoder = Utf8StreamDecoder::default();
        let mut output = String::new();
        for byte in source {
            output.push_str(&decoder.push(&[*byte]));
        }
        output.push_str(&decoder.finish());
        assert_eq!(output, "你好 café");
        assert_eq!(decoder.replacements(), 0);

        let mut invalid = Utf8StreamDecoder::default();
        assert_eq!(invalid.push(&[0xf0, 0x9f]), "");
        assert_eq!(invalid.finish(), "\u{fffd}");
        assert_eq!(invalid.replacements(), 1);
    }

    #[test]
    fn normalizer_removes_split_terminal_sequences_and_cursor_controls() {
        let mut normalizer = TerminalTextNormalizer::default();
        let chunks = [
            "a\u{1b}[3",
            "1mred\u{1b}[0m",
            "\r\u{8}b\u{1b}]0;title",
            "\u{7}c",
        ];
        let output = chunks
            .into_iter()
            .map(|chunk| normalizer.push(chunk))
            .collect::<String>();
        normalizer.finish();
        assert_eq!(output, "aredbc");
        assert!(!output.contains('\u{1b}'));
    }

    #[test]
    fn shared_normalization_vectors_cover_every_byte_split_and_one_byte_chunks() {
        let fixture: Fixture = serde_json::from_str(include_str!(
            "../../../tests/fixtures/text_contract/cases.json"
        ))
        .unwrap();
        for case in fixture.normalization {
            let bytes = case.input.as_bytes();
            for split in 0..=bytes.len() {
                let (actual, replacements) =
                    normalize_byte_chunks(&[&bytes[..split], &bytes[split..]]);
                assert_eq!(actual, case.expected, "{} split at {split}", case.name);
                assert_eq!(replacements, 0, "{} split at {split}", case.name);
            }
            let chunks = bytes.iter().map(std::slice::from_ref).collect::<Vec<_>>();
            let (actual, replacements) = normalize_byte_chunks(&chunks);
            assert_eq!(actual, case.expected, "{} one-byte chunks", case.name);
            assert_eq!(replacements, 0, "{} one-byte chunks", case.name);
        }
    }

    #[test]
    fn invalid_utf8_in_the_middle_is_replaced_without_losing_following_bytes() {
        let bytes = b"a\xf0\x28\x8c\x28b";
        for split in 0..=bytes.len() {
            let (actual, replacements) = normalize_byte_chunks(&[&bytes[..split], &bytes[split..]]);
            assert_eq!(actual, "a\u{fffd}(\u{fffd}(b");
            assert_eq!(replacements, 2);
        }
    }
}

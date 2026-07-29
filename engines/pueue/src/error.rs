//! Internal error sources and their stable public classifications.

use thiserror::Error;

use crate::workspace::WorkspaceError;

#[derive(Debug, Error)]
pub enum StartupError {
    #[error("Pueue settings could not be loaded")]
    PueueSettings,
    #[error("Pueue shared secret could not be read")]
    PueueSecret,
    #[error("Pueue daemon is unavailable")]
    BackendUnavailable,
    #[error("Pueue daemon protocol is incompatible")]
    BackendProtocolIncompatible,
    #[error("Pueue daemon returned an unexpected handshake response")]
    UnexpectedBackendResponse,
    #[error("tracing initialization failed")]
    Tracing,
    #[error("MCP stdio service failed")]
    Mcp,
    #[error(transparent)]
    Workspace(#[from] WorkspaceError),
}

impl StartupError {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::PueueSettings => "pueue_settings_failed",
            Self::PueueSecret => "pueue_secret_failed",
            Self::BackendUnavailable => "backend_unavailable",
            Self::BackendProtocolIncompatible => "backend_protocol_incompatible",
            Self::UnexpectedBackendResponse => "unexpected_backend_response",
            Self::Tracing => "tracing_initialization_failed",
            Self::Mcp => "mcp_stdio_failed",
            Self::Workspace(error) => error.kind(),
        }
    }

    pub fn pueue_settings(_error: impl std::fmt::Debug) -> Self {
        Self::PueueSettings
    }

    pub fn pueue_secret(_error: impl std::fmt::Debug) -> Self {
        Self::PueueSecret
    }

    pub fn backend_unavailable(_error: impl std::fmt::Debug) -> Self {
        Self::BackendUnavailable
    }

    pub fn tracing(_error: impl std::fmt::Debug) -> Self {
        Self::Tracing
    }

    pub fn mcp(_error: impl std::fmt::Debug) -> Self {
        Self::Mcp
    }
}

#[cfg(test)]
mod tests {
    use super::StartupError;

    #[test]
    fn startup_error_kinds_are_stable() {
        let cases = [
            (StartupError::PueueSettings, "pueue_settings_failed"),
            (StartupError::PueueSecret, "pueue_secret_failed"),
            (StartupError::BackendUnavailable, "backend_unavailable"),
            (
                StartupError::BackendProtocolIncompatible,
                "backend_protocol_incompatible",
            ),
            (
                StartupError::UnexpectedBackendResponse,
                "unexpected_backend_response",
            ),
            (StartupError::Tracing, "tracing_initialization_failed"),
            (StartupError::Mcp, "mcp_stdio_failed"),
            (
                StartupError::Workspace(crate::workspace::WorkspaceError::ConfigInvalidRule),
                "workspace_config_invalid_rule",
            ),
        ];

        for (error, expected) in cases {
            assert_eq!(error.kind(), expected);
        }

        assert!(matches!(
            StartupError::pueue_settings("settings detail"),
            StartupError::PueueSettings
        ));
        assert!(matches!(
            StartupError::pueue_secret("secret detail"),
            StartupError::PueueSecret
        ));
        assert!(matches!(
            StartupError::backend_unavailable("transport detail"),
            StartupError::BackendUnavailable
        ));
        assert!(matches!(
            StartupError::tracing("subscriber detail"),
            StartupError::Tracing
        ));
        assert!(matches!(
            StartupError::mcp("stdio detail"),
            StartupError::Mcp
        ));
    }
}

//! Typed Pueue protocol boundary.

use std::{
    future::Future,
    path::{Path, PathBuf},
    pin::Pin,
    sync::Arc,
    time::Duration,
};

use pueue_lib::{
    Client, Error as PueueError, PROTOCOL_VERSION, Request, Response,
    message::{AddRequest, AddedTaskResponse, KillRequest},
    network::socket::ConnectionSettings,
    secret::read_shared_secret,
    settings::{Settings, Shared},
    state::State,
};
use thiserror::Error;
use tokio::{sync::Mutex, time::timeout};

use crate::error::StartupError;

const EXCHANGE_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum GatewayError {
    #[error("Pueue daemon is unavailable")]
    BackendUnavailable,
    #[error("Pueue exchange timed out")]
    BackendTimeout,
    #[error("Pueue protocol is incompatible")]
    BackendProtocolIncompatible,
    #[error("Pueue daemon returned an explicit failure")]
    BackendRequestRejected,
    #[error("Pueue daemon returned an unexpected response")]
    UnexpectedBackendResponse,
    #[error("task submission outcome is unknown")]
    AddOutcomeUnknown,
    #[error("task removal outcome is unknown")]
    RemoveOutcomeUnknown,
    #[error("task termination outcome is unknown")]
    KillOutcomeUnknown,
}

impl GatewayError {
    #[must_use]
    pub const fn kind(self) -> &'static str {
        match self {
            Self::BackendUnavailable => "backend_unavailable",
            Self::BackendTimeout => "backend_timeout",
            Self::BackendProtocolIncompatible => "backend_protocol_incompatible",
            Self::BackendRequestRejected => "backend_request_rejected",
            Self::UnexpectedBackendResponse => "unexpected_backend_response",
            Self::AddOutcomeUnknown => "add_outcome_unknown",
            Self::RemoveOutcomeUnknown => "remove_outcome_unknown",
            Self::KillOutcomeUnknown => "kill_outcome_unknown",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProtocolFailure {
    Unavailable,
    Incompatible,
}

type ConnectFuture<'a> =
    Pin<Box<dyn Future<Output = Result<Box<dyn ProtocolClient>, ProtocolFailure>> + Send + 'a>>;

trait ProtocolClient: std::fmt::Debug + Send {
    fn send_request(
        &mut self,
        request: Request,
    ) -> Pin<Box<dyn Future<Output = Result<(), ProtocolFailure>> + Send + '_>>;

    fn receive_response(
        &mut self,
    ) -> Pin<Box<dyn Future<Output = Result<Response, ProtocolFailure>> + Send + '_>>;
}

#[derive(Debug)]
struct TypedClient(Client);

impl ProtocolClient for TypedClient {
    fn send_request(
        &mut self,
        request: Request,
    ) -> Pin<Box<dyn Future<Output = Result<(), ProtocolFailure>> + Send + '_>> {
        Box::pin(async move {
            self.0
                .send_request(request)
                .await
                .map_err(|error| classify_protocol_error(&error))
        })
    }

    fn receive_response(
        &mut self,
    ) -> Pin<Box<dyn Future<Output = Result<Response, ProtocolFailure>> + Send + '_>> {
        Box::pin(async move {
            self.0
                .receive_response()
                .await
                .map_err(|error| classify_protocol_error(&error))
        })
    }
}

trait Connector: std::fmt::Debug + Send + Sync {
    fn connect(&self) -> ConnectFuture<'_>;
}

#[derive(Debug)]
struct TypedConnector {
    shared: Shared,
    secret: Vec<u8>,
}

impl Connector for TypedConnector {
    fn connect(&self) -> ConnectFuture<'_> {
        Box::pin(async move {
            let connection = ConnectionSettings::try_from(self.shared.clone())
                .map_err(|_| ProtocolFailure::Unavailable)?;
            let client = Client::new(connection, &self.secret, false)
                .await
                .map_err(|_| ProtocolFailure::Unavailable)?;
            if client.daemon_version() != PROTOCOL_VERSION {
                return Err(ProtocolFailure::Incompatible);
            }
            Ok(Box::new(TypedClient(client)) as Box<dyn ProtocolClient>)
        })
    }
}

#[derive(Debug)]
struct GatewayConnection {
    connector: Arc<dyn Connector>,
    client: Option<Box<dyn ProtocolClient>>,
}

#[derive(Debug, Clone)]
pub struct PueueGateway {
    connection: Arc<Mutex<GatewayConnection>>,
    pueue_directory: PathBuf,
}

pub trait Gateway: std::fmt::Debug + Send {
    fn pueue_directory(&self) -> &Path;
    fn status(&self) -> impl Future<Output = Result<Box<State>, GatewayError>> + Send;
    fn add(
        &self,
        request: AddRequest,
    ) -> impl Future<Output = Result<AddedTaskResponse, GatewayError>> + Send;
    fn remove(&self, task_ids: Vec<usize>)
    -> impl Future<Output = Result<(), GatewayError>> + Send;
    fn kill(&self, request: KillRequest) -> impl Future<Output = Result<(), GatewayError>> + Send;
}

impl Gateway for PueueGateway {
    fn pueue_directory(&self) -> &Path {
        self.pueue_directory()
    }

    fn status(&self) -> impl Future<Output = Result<Box<State>, GatewayError>> + Send {
        self.status()
    }

    fn add(
        &self,
        request: AddRequest,
    ) -> impl Future<Output = Result<AddedTaskResponse, GatewayError>> + Send {
        self.add(request)
    }

    fn remove(
        &self,
        task_ids: Vec<usize>,
    ) -> impl Future<Output = Result<(), GatewayError>> + Send {
        self.remove(task_ids)
    }

    fn kill(&self, request: KillRequest) -> impl Future<Output = Result<(), GatewayError>> + Send {
        self.kill(request)
    }
}

impl PueueGateway {
    /// Connect to the configured daemon and prove a typed status exchange.
    ///
    /// # Errors
    ///
    /// Returns a stable startup category when settings, credentials, connection,
    /// handshake, or the initial response cannot be trusted.
    pub async fn connect_default() -> Result<Self, StartupError> {
        Self::connect_from_settings_path(None).await
    }

    /// Connect using one explicit Pueue settings file, primarily for isolated
    /// host-owned daemon profiles.
    ///
    /// # Errors
    ///
    /// Returns the same stable startup categories as [`Self::connect_default`].
    pub async fn connect_from_path(path: &Path) -> Result<Self, StartupError> {
        Self::connect_from_settings_path(Some(path.to_path_buf())).await
    }

    async fn connect_from_settings_path(path: Option<PathBuf>) -> Result<Self, StartupError> {
        let (settings, _) = match Settings::read(&path) {
            Ok(settings) => settings,
            Err(error) => return Err(StartupError::pueue_settings(error)),
        };
        let shared = settings.shared;
        let pueue_directory = shared.pueue_directory();
        let secret = match read_shared_secret(&shared.shared_secret_path()) {
            Ok(secret) => secret,
            Err(error) => return Err(StartupError::pueue_secret(error)),
        };
        let connector: Arc<dyn Connector> = Arc::new(TypedConnector { shared, secret });
        Self::connect_with_connector(connector, pueue_directory).await
    }

    async fn connect_with_connector(
        connector: Arc<dyn Connector>,
        pueue_directory: PathBuf,
    ) -> Result<Self, StartupError> {
        let client = match connector.connect().await {
            Ok(client) => client,
            Err(ProtocolFailure::Unavailable) => return Err(StartupError::BackendUnavailable),
            Err(ProtocolFailure::Incompatible) => {
                return Err(StartupError::BackendProtocolIncompatible);
            }
        };
        let gateway = Self {
            connection: Arc::new(Mutex::new(GatewayConnection {
                connector,
                client: Some(client),
            })),
            pueue_directory,
        };
        match gateway.status().await {
            Ok(_) => {}
            Err(GatewayError::UnexpectedBackendResponse) => {
                return Err(StartupError::UnexpectedBackendResponse);
            }
            Err(GatewayError::BackendProtocolIncompatible) => {
                return Err(StartupError::BackendProtocolIncompatible);
            }
            Err(_) => return Err(StartupError::BackendUnavailable),
        }
        Ok(gateway)
    }

    #[must_use]
    pub fn pueue_directory(&self) -> &Path {
        &self.pueue_directory
    }

    /// Return one typed daemon state snapshot, reconnecting once only when no
    /// trustworthy connection remains from an earlier exchange.
    ///
    /// # Errors
    ///
    /// Returns a stable gateway category for connection, explicit backend, and
    /// response-variant failures.
    pub async fn status(&self) -> Result<Box<State>, GatewayError> {
        self.observation_exchange(Request::Status, |response| match response {
            Response::Status(state) => Ok(state),
            Response::Failure(_) => Err(GatewayError::BackendRequestRejected),
            _ => Err(GatewayError::UnexpectedBackendResponse),
        })
        .await
    }

    /// Submit one task without replaying an exchange whose response is lost.
    ///
    /// # Errors
    ///
    /// Returns `add_outcome_unknown` after any post-send transport failure.
    pub async fn add(&self, request: AddRequest) -> Result<AddedTaskResponse, GatewayError> {
        self.mutation_exchange(
            Request::Add(request),
            GatewayError::AddOutcomeUnknown,
            |response| match response {
                Response::AddedTask(response) => Ok(response),
                Response::Failure(_) => Err(GatewayError::BackendRequestRejected),
                _ => Err(GatewayError::UnexpectedBackendResponse),
            },
        )
        .await
    }

    /// Remove queued or stashed tasks without mutation replay.
    ///
    /// # Errors
    ///
    /// Returns a stable rejection, transport-outcome, or response error.
    pub async fn remove(&self, task_ids: Vec<usize>) -> Result<(), GatewayError> {
        self.expect_mutation_success(
            Request::Remove(task_ids),
            GatewayError::RemoveOutcomeUnknown,
        )
        .await
    }

    /// Request termination of running or paused tasks without mutation replay.
    ///
    /// # Errors
    ///
    /// Returns a stable rejection, transport-outcome, or response error.
    pub async fn kill(&self, request: KillRequest) -> Result<(), GatewayError> {
        self.expect_mutation_success(Request::Kill(request), GatewayError::KillOutcomeUnknown)
            .await
    }

    async fn expect_mutation_success(
        &self,
        request: Request,
        unknown: GatewayError,
    ) -> Result<(), GatewayError> {
        self.mutation_exchange(request, unknown, |response| match response {
            Response::Success(_) => Ok(()),
            Response::Failure(_) => Err(GatewayError::BackendRequestRejected),
            _ => Err(GatewayError::UnexpectedBackendResponse),
        })
        .await
    }

    async fn observation_exchange<T>(
        &self,
        request: Request,
        classify: impl FnOnce(Response) -> Result<T, GatewayError>,
    ) -> Result<T, GatewayError> {
        let mut connection = self.connection.lock().await;
        if connection.client.is_none() {
            let connector = Arc::clone(&connection.connector);
            connection.client = Some(connect_connector(&connector).await?);
        }
        let response = exchange_observation_locked(&mut connection, request).await?;
        let result = classify(response);
        if matches!(result, Err(GatewayError::UnexpectedBackendResponse)) {
            connection.client = None;
        }
        result
    }

    async fn mutation_exchange<T>(
        &self,
        request: Request,
        unknown: GatewayError,
        classify: impl FnOnce(Response) -> Result<T, GatewayError>,
    ) -> Result<T, GatewayError> {
        let mut connection = self.connection.lock().await;
        if connection.client.is_none() {
            let connector = Arc::clone(&connection.connector);
            connection.client = Some(connect_connector(&connector).await?);
        }
        let response = exchange_mutation_locked(&mut connection, request, unknown).await?;
        let result = classify(response);
        if matches!(result, Err(GatewayError::UnexpectedBackendResponse)) {
            connection.client = None;
        }
        result
    }
}

async fn connect_connector(
    connector: &Arc<dyn Connector>,
) -> Result<Box<dyn ProtocolClient>, GatewayError> {
    connector.connect().await.map_err(|error| match error {
        ProtocolFailure::Unavailable => GatewayError::BackendUnavailable,
        ProtocolFailure::Incompatible => GatewayError::BackendProtocolIncompatible,
    })
}

async fn exchange_observation_locked(
    connection: &mut GatewayConnection,
    request: Request,
) -> Result<Response, GatewayError> {
    // Pueue uses one ordered stream for requests and responses, so this lock owns
    // both halves and invalidates the stream whenever either half becomes uncertain.
    let Some(client) = connection.client.as_mut() else {
        return Err(GatewayError::BackendUnavailable);
    };
    let sent = timeout(EXCHANGE_TIMEOUT, client.send_request(request)).await;
    let result = match sent {
        Err(_) => Err(GatewayError::BackendTimeout),
        Ok(Err(ProtocolFailure::Unavailable)) => Err(GatewayError::BackendUnavailable),
        Ok(Err(ProtocolFailure::Incompatible)) => Err(GatewayError::BackendProtocolIncompatible),
        Ok(Ok(())) => match timeout(EXCHANGE_TIMEOUT, client.receive_response()).await {
            Err(_) => Err(GatewayError::BackendTimeout),
            Ok(Err(ProtocolFailure::Unavailable)) => Err(GatewayError::BackendUnavailable),
            Ok(Err(ProtocolFailure::Incompatible)) => {
                Err(GatewayError::BackendProtocolIncompatible)
            }
            Ok(Ok(response)) => return Ok(response),
        },
    };
    connection.client = None;
    result
}

async fn exchange_mutation_locked(
    connection: &mut GatewayConnection,
    request: Request,
    unknown: GatewayError,
) -> Result<Response, GatewayError> {
    let Some(client) = connection.client.as_mut() else {
        return Err(GatewayError::BackendUnavailable);
    };
    // Once mutation serialization starts, transport failure cannot prove that
    // the daemon did not apply the request, so every such branch is unknown.
    if !matches!(
        timeout(EXCHANGE_TIMEOUT, client.send_request(request)).await,
        Ok(Ok(()))
    ) {
        connection.client = None;
        return Err(unknown);
    }
    if let Ok(Ok(response)) = timeout(EXCHANGE_TIMEOUT, client.receive_response()).await {
        Ok(response)
    } else {
        connection.client = None;
        Err(unknown)
    }
}

fn classify_protocol_error(error: &PueueError) -> ProtocolFailure {
    match error {
        PueueError::MessageDeserialization(_) | PueueError::UnexpectedPayload(_) => {
            ProtocolFailure::Incompatible
        }
        _ => ProtocolFailure::Unavailable,
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::{HashMap, VecDeque},
        future::{Future, pending},
        path::PathBuf,
        pin::Pin,
        sync::{
            Arc, Mutex as StdMutex,
            atomic::{AtomicUsize, Ordering},
        },
    };

    use pueue_lib::{
        Request, Response,
        message::{AddRequest, AddedTaskResponse},
        state::State,
    };
    use tokio::sync::Mutex;

    use super::{
        ConnectFuture, Connector, GatewayConnection, GatewayError, ProtocolClient, ProtocolFailure,
        PueueGateway,
    };

    #[tokio::test]
    async fn protocol_mismatch_fails_startup_without_a_second_connection_attempt() {
        let connects = Arc::new(AtomicUsize::new(0));
        let connector: Arc<dyn Connector> = Arc::new(FakeConnector {
            clients: StdMutex::new([Err(ProtocolFailure::Incompatible)].into_iter().collect()),
            connect_count: Arc::clone(&connects),
        });

        let error = PueueGateway::connect_with_connector(connector, PathBuf::from("/unused"))
            .await
            .unwrap_err();

        assert_eq!(error.kind(), "backend_protocol_incompatible");
        assert_eq!(connects.load(Ordering::SeqCst), 1);
    }

    #[derive(Debug)]
    struct FakeClient {
        sends: VecDeque<Result<(), ProtocolFailure>>,
        responses: VecDeque<Result<Response, ProtocolFailure>>,
        send_count: Arc<AtomicUsize>,
    }

    impl FakeClient {
        fn new(
            sends: impl IntoIterator<Item = Result<(), ProtocolFailure>>,
            responses: impl IntoIterator<Item = Result<Response, ProtocolFailure>>,
            send_count: Arc<AtomicUsize>,
        ) -> Self {
            Self {
                sends: sends.into_iter().collect(),
                responses: responses.into_iter().collect(),
                send_count,
            }
        }
    }

    impl ProtocolClient for FakeClient {
        fn send_request(
            &mut self,
            _request: Request,
        ) -> Pin<Box<dyn Future<Output = Result<(), ProtocolFailure>> + Send + '_>> {
            self.send_count.fetch_add(1, Ordering::SeqCst);
            let result = self.sends.pop_front().unwrap_or(Ok(()));
            Box::pin(async move { result })
        }

        fn receive_response(
            &mut self,
        ) -> Pin<Box<dyn Future<Output = Result<Response, ProtocolFailure>> + Send + '_>> {
            let result = self
                .responses
                .pop_front()
                .unwrap_or(Err(ProtocolFailure::Unavailable));
            Box::pin(async move { result })
        }
    }

    #[derive(Debug)]
    struct FakeConnector {
        clients: StdMutex<VecDeque<Result<Box<dyn ProtocolClient>, ProtocolFailure>>>,
        connect_count: Arc<AtomicUsize>,
    }

    #[derive(Debug)]
    struct HangingClient;

    #[derive(Debug)]
    struct ReceiveHangingClient;

    impl ProtocolClient for HangingClient {
        fn send_request(
            &mut self,
            _request: Request,
        ) -> Pin<Box<dyn Future<Output = Result<(), ProtocolFailure>> + Send + '_>> {
            Box::pin(pending())
        }

        fn receive_response(
            &mut self,
        ) -> Pin<Box<dyn Future<Output = Result<Response, ProtocolFailure>> + Send + '_>> {
            Box::pin(pending())
        }
    }

    impl ProtocolClient for ReceiveHangingClient {
        fn send_request(
            &mut self,
            _request: Request,
        ) -> Pin<Box<dyn Future<Output = Result<(), ProtocolFailure>> + Send + '_>> {
            Box::pin(async { Ok(()) })
        }

        fn receive_response(
            &mut self,
        ) -> Pin<Box<dyn Future<Output = Result<Response, ProtocolFailure>> + Send + '_>> {
            Box::pin(pending())
        }
    }

    impl Connector for FakeConnector {
        fn connect(&self) -> ConnectFuture<'_> {
            self.connect_count.fetch_add(1, Ordering::SeqCst);
            let result = self
                .clients
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or(Err(ProtocolFailure::Unavailable));
            Box::pin(async move { result })
        }
    }

    fn gateway(
        client: Box<dyn ProtocolClient>,
        reconnects: impl IntoIterator<Item = Result<Box<dyn ProtocolClient>, ProtocolFailure>>,
        connect_count: Arc<AtomicUsize>,
    ) -> PueueGateway {
        let connector: Arc<dyn Connector> = Arc::new(FakeConnector {
            clients: StdMutex::new(reconnects.into_iter().collect()),
            connect_count,
        });
        PueueGateway {
            connection: Arc::new(Mutex::new(GatewayConnection {
                connector,
                client: Some(client),
            })),
            pueue_directory: PathBuf::from("/unused"),
        }
    }

    fn add_request() -> AddRequest {
        AddRequest {
            command: "true".into(),
            path: PathBuf::from("/workspace"),
            envs: HashMap::default(),
            start_immediately: false,
            stashed: false,
            group: "default".into(),
            enqueue_at: None,
            dependencies: Vec::new(),
            priority: None,
            label: None,
        }
    }

    #[test]
    fn gateway_error_kinds_do_not_embed_backend_messages() {
        let cases = [
            (GatewayError::BackendUnavailable, "backend_unavailable"),
            (GatewayError::BackendTimeout, "backend_timeout"),
            (
                GatewayError::BackendProtocolIncompatible,
                "backend_protocol_incompatible",
            ),
            (
                GatewayError::BackendRequestRejected,
                "backend_request_rejected",
            ),
            (
                GatewayError::UnexpectedBackendResponse,
                "unexpected_backend_response",
            ),
            (GatewayError::AddOutcomeUnknown, "add_outcome_unknown"),
            (GatewayError::RemoveOutcomeUnknown, "remove_outcome_unknown"),
            (GatewayError::KillOutcomeUnknown, "kill_outcome_unknown"),
        ];
        for (error, expected) in cases {
            assert_eq!(error.kind(), expected);
        }
    }

    #[tokio::test]
    async fn unexpected_response_invalidates_and_next_observation_reconnects() {
        let sends = Arc::new(AtomicUsize::new(0));
        let initial = Box::new(FakeClient::new(
            [Ok(())],
            [Ok(Response::Success("wrong variant".into()))],
            Arc::clone(&sends),
        ));
        let reconnect = Box::new(FakeClient::new(
            [Ok(())],
            [Ok(Response::Status(Box::<State>::default()))],
            Arc::clone(&sends),
        ));
        let connects = Arc::new(AtomicUsize::new(0));
        let gateway = gateway(
            initial,
            [Ok(reconnect as Box<dyn ProtocolClient>)],
            Arc::clone(&connects),
        );

        assert_eq!(
            gateway.status().await.unwrap_err(),
            GatewayError::UnexpectedBackendResponse
        );
        assert!(gateway.status().await.unwrap().tasks.is_empty());
        assert_eq!(connects.load(Ordering::SeqCst), 1);
        assert_eq!(sends.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn mutation_response_loss_is_unknown_and_never_replayed() {
        let sends = Arc::new(AtomicUsize::new(0));
        let client = Box::new(FakeClient::new(
            [Ok(())],
            [Err(ProtocolFailure::Unavailable)],
            Arc::clone(&sends),
        ));
        let connects = Arc::new(AtomicUsize::new(0));
        let gateway = gateway(client, [], Arc::clone(&connects));

        assert_eq!(
            gateway.add(add_request()).await.unwrap_err(),
            GatewayError::AddOutcomeUnknown
        );
        assert_eq!(sends.load(Ordering::SeqCst), 1);
        assert_eq!(connects.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn unexpected_mutation_response_is_invalidated_before_the_next_exchange() {
        let sends = Arc::new(AtomicUsize::new(0));
        let initial = Box::new(FakeClient::new(
            [Ok(())],
            [Ok(Response::Success("wrong add response".into()))],
            Arc::clone(&sends),
        ));
        let reconnect = Box::new(FakeClient::new(
            [Ok(())],
            [Ok(Response::Status(Box::<State>::default()))],
            Arc::clone(&sends),
        ));
        let connects = Arc::new(AtomicUsize::new(0));
        let gateway = gateway(
            initial,
            [Ok(reconnect as Box<dyn ProtocolClient>)],
            Arc::clone(&connects),
        );

        assert_eq!(
            gateway.add(add_request()).await.unwrap_err(),
            GatewayError::UnexpectedBackendResponse
        );
        assert!(gateway.status().await.unwrap().tasks.is_empty());
        assert_eq!(connects.load(Ordering::SeqCst), 1);
        assert_eq!(sends.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn protocol_and_explicit_failure_categories_are_stable() {
        let sends = Arc::new(AtomicUsize::new(0));
        let incompatible = gateway(
            Box::new(FakeClient::new(
                [Ok(())],
                [Err(ProtocolFailure::Incompatible)],
                Arc::clone(&sends),
            )),
            [],
            Arc::new(AtomicUsize::new(0)),
        );
        assert_eq!(
            incompatible.status().await.unwrap_err(),
            GatewayError::BackendProtocolIncompatible
        );

        let rejected = gateway(
            Box::new(FakeClient::new(
                [Ok(())],
                [Ok(Response::Failure("private backend detail".into()))],
                sends,
            )),
            [],
            Arc::new(AtomicUsize::new(0)),
        );
        assert_eq!(
            rejected.add(add_request()).await.unwrap_err(),
            GatewayError::BackendRequestRejected
        );

        let accepted = gateway(
            Box::new(FakeClient::new(
                [Ok(())],
                [Ok(Response::AddedTask(AddedTaskResponse {
                    task_id: 7,
                    enqueue_at: None,
                    group_is_paused: false,
                }))],
                Arc::new(AtomicUsize::new(0)),
            )),
            [],
            Arc::new(AtomicUsize::new(0)),
        );
        assert_eq!(accepted.add(add_request()).await.unwrap().task_id, 7);
    }

    #[tokio::test(start_paused = true)]
    async fn observation_timeout_has_its_own_category_and_invalidates() {
        let connects = Arc::new(AtomicUsize::new(0));
        let timing_gateway = gateway(Box::new(HangingClient), [], Arc::clone(&connects));

        assert_eq!(
            timing_gateway.status().await.unwrap_err(),
            GatewayError::BackendTimeout
        );
        assert_eq!(connects.load(Ordering::SeqCst), 0);
        assert_eq!(
            timing_gateway.status().await.unwrap_err(),
            GatewayError::BackendUnavailable
        );
        assert_eq!(connects.load(Ordering::SeqCst), 1);

        let mut send_hanging = HangingClient;
        assert!(
            tokio::time::timeout(super::EXCHANGE_TIMEOUT, send_hanging.receive_response())
                .await
                .is_err()
        );

        let receive_timeout = gateway(
            Box::new(ReceiveHangingClient),
            [],
            Arc::new(AtomicUsize::new(0)),
        );
        assert_eq!(
            receive_timeout.status().await.unwrap_err(),
            GatewayError::BackendTimeout
        );
    }

    #[tokio::test]
    async fn connector_mapping_and_directory_are_covered() {
        let connects = Arc::new(AtomicUsize::new(0));
        let gateway = gateway(
            Box::new(FakeClient::new([], [], Arc::new(AtomicUsize::new(0)))),
            [Err(ProtocolFailure::Incompatible)],
            Arc::clone(&connects),
        );
        assert_eq!(gateway.pueue_directory(), PathBuf::from("/unused"));
        assert_eq!(connects.load(Ordering::SeqCst), 0);
    }
}

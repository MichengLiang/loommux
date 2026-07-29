use clap::Parser;
use loommux_pueue::{
    error::StartupError, pueue_gateway::PueueGateway, server::PueueServer,
    workspace::resolve_workspace_launch,
};
use rmcp::{ServiceExt, transport::stdio};
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(name = "loommux-pueue", version, about)]
struct Cli {
    /// Select whether tools return model-readable content only or content plus structured data.
    #[arg(long, value_enum, default_value_t)]
    result_mode: loommux_pueue::result::ResultMode,
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("{}: {error}", error.kind());
        std::process::exit(1);
    }
}

async fn run() -> Result<(), StartupError> {
    let cli = Cli::parse();
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .with_writer(std::io::stderr)
        .try_init()
        .map_err(StartupError::tracing)?;

    let workspace = resolve_workspace_launch()?;
    let gateway = PueueGateway::connect_default().await?;
    let server = PueueServer::new(gateway, cli.result_mode, workspace);
    server
        .serve(stdio())
        .await
        .map_err(StartupError::mcp)?
        .waiting()
        .await
        .map(|_| ())
        .map_err(StartupError::mcp)
}

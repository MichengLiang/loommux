import { Activity, Clipboard, Eraser, Pause, Play, ScrollText } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MonitorHealth } from "../shared/types";
import type { MonitorClientEvent } from "./events";
import { parseSseMessage } from "./events";
import { buildMonitorState, type ExecutionView, type OutputStreamName, type ToolCallView } from "./state";

type ConnectionState = "disabled" | "connecting" | "open" | "closed" | "error";

type AppProps = {
	initialEvents?: MonitorClientEvent[];
	initialHealth?: MonitorHealth;
	connect?: boolean;
};

const STREAM_TABS: OutputStreamName[] = ["stdout", "stderr", "result", "traceback"];

export function App({ initialEvents = [], initialHealth, connect = true }: AppProps) {
	const [events, setEvents] = useState<MonitorClientEvent[]>(initialEvents);
	const [health, setHealth] = useState<MonitorHealth | undefined>(initialHealth);
	const [connection, setConnection] = useState<ConnectionState>(connect ? "connecting" : "disabled");
	const [selectedExecutionId, setSelectedExecutionId] = useState<string | undefined>();
	const [activeStream, setActiveStream] = useState<OutputStreamName | "combined">("combined");
	const [paused, setPaused] = useState(false);
	const [autoScroll, setAutoScroll] = useState(true);
	const outputRef = useRef<HTMLPreElement>(null);

	useEffect(() => {
		if (!connect) {
			return undefined;
		}
		const source = new EventSource("/api/events/stream");
		setConnection("connecting");
		source.addEventListener("open", () => setConnection("open"));
		source.addEventListener("error", () => setConnection(source.readyState === EventSource.CLOSED ? "closed" : "error"));
		source.addEventListener("snapshot", (message) => {
			const parsed = parseSseMessage("snapshot", message.data);
			if (parsed?.kind === "snapshot") {
				setHealth(parsed.snapshot.health);
				if (!paused) {
					setEvents(parsed.snapshot.events ?? []);
				}
			}
		});
		source.addEventListener("event", (message) => {
			const parsed = parseSseMessage("event", message.data);
			if (parsed?.kind === "event" && !paused) {
				setEvents((current) => [...current, parsed.event]);
			}
		});
		source.addEventListener("heartbeat", (message) => {
			parseSseMessage("heartbeat", message.data);
		});
		return () => {
			source.close();
			setConnection("closed");
		};
	}, [connect, paused]);

	const view = useMemo(() => buildMonitorState(events, health), [events, health]);
	const selectedExecution =
		view.executions.find((execution) => execution.id === selectedExecutionId) ?? view.executions[0];

	useEffect(() => {
		if (selectedExecution && selectedExecution.id !== selectedExecutionId) {
			setSelectedExecutionId(selectedExecution.id);
		}
	}, [selectedExecution, selectedExecutionId]);

	useEffect(() => {
		if (autoScroll && outputRef.current) {
			outputRef.current.scrollTop = outputRef.current.scrollHeight;
		}
	}, [autoScroll, selectedExecution?.combinedOutput, activeStream]);

	const clearView = () => {
		setEvents([]);
		setSelectedExecutionId(undefined);
	};

	return (
		<div className="appShell">
			<StatusBar
				connection={connection}
				eventCount={view.events.length}
				paused={paused}
				autoScroll={autoScroll}
				onTogglePaused={() => setPaused((value) => !value)}
				onToggleAutoScroll={() => setAutoScroll((value) => !value)}
				onClear={clearView}
				{...(view.health === undefined ? {} : { health: view.health })}
				{...(view.latestEventTime === undefined ? {} : { latestEventTime: view.latestEventTime })}
			/>
			<main className="monitorGrid">
				<section className="panel executionPanel" aria-label="Executions">
					<div className="panelHeader">
						<h1>loommux monitor</h1>
						<span>{view.executions.length} executions</span>
					</div>
					<ExecutionList
						executions={view.executions}
						onSelect={setSelectedExecutionId}
						{...(selectedExecution?.id === undefined ? {} : { selectedId: selectedExecution.id })}
					/>
				</section>
				<section className="panel detailPanel" aria-label="Execution detail">
					<ExecutionDetail
						activeStream={activeStream}
						onActiveStreamChange={setActiveStream}
						outputRef={outputRef}
						{...(selectedExecution === undefined ? {} : { execution: selectedExecution })}
					/>
				</section>
				<section className="panel timelinePanel" aria-label="Tool timeline">
					<div className="panelHeader">
						<h2>Tool Timeline</h2>
						<span>{view.toolCalls.length} calls</span>
					</div>
					<ToolTimeline toolCalls={view.toolCalls} />
				</section>
			</main>
		</div>
	);
}

type StatusBarProps = {
	connection: ConnectionState;
	health?: MonitorHealth;
	eventCount: number;
	latestEventTime?: number;
	paused: boolean;
	autoScroll: boolean;
	onTogglePaused: () => void;
	onToggleAutoScroll: () => void;
	onClear: () => void;
};

function StatusBar({
	connection,
	health,
	eventCount,
	latestEventTime,
	paused,
	autoScroll,
	onTogglePaused,
	onToggleAutoScroll,
	onClear,
}: StatusBarProps) {
	return (
		<header className="statusBar">
			<div className="statusGroup brandMark">
				<Activity size={18} aria-hidden="true" />
				<span>Monitor</span>
			</div>
			<StatusPill tone={connection === "open" ? "ok" : connection === "error" ? "bad" : "idle"} label={connection} />
			<span>clients {health?.clients ?? 0}</span>
			<span>buffer {health?.events_buffered ?? 0}</span>
			<span>received {health?.events_received ?? eventCount}</span>
			<span>dropped {health?.events_dropped ?? 0}</span>
			<span>latest {latestEventTime ? formatTime(latestEventTime) : "none"}</span>
			<div className="statusActions">
				<button type="button" className="iconButton" onClick={onTogglePaused} aria-label={paused ? "Resume live updates" : "Pause live updates"}>
					{paused ? <Play size={16} aria-hidden="true" /> : <Pause size={16} aria-hidden="true" />}
				</button>
				<button type="button" className="iconButton" onClick={onToggleAutoScroll} aria-label={autoScroll ? "Disable auto-scroll" : "Enable auto-scroll"}>
					<ScrollText size={16} aria-hidden="true" />
				</button>
				<button type="button" className="clearButton" onClick={onClear} aria-label="Clear browser view">
					<Eraser size={16} aria-hidden="true" />
					<span>Clear view</span>
				</button>
			</div>
			<span className="scopeNote">Browser view only; MCP execution history is not deleted.</span>
		</header>
	);
}

function ExecutionList({
	executions,
	selectedId,
	onSelect,
}: {
	executions: ExecutionView[];
	selectedId?: string;
	onSelect: (id: string) => void;
}) {
	if (executions.length === 0) {
		return <div className="emptyState">Waiting for Python execution events.</div>;
	}
	return (
		<div className="executionList">
			{executions.map((execution) => (
				<button
					type="button"
					key={execution.id}
					className={`executionRow ${execution.id === selectedId ? "selected" : ""}`}
					onClick={() => onSelect(execution.id)}
				>
					<div className="rowTop">
						<strong>{execution.id}</strong>
						<StatusPill tone={statusTone(execution.status)} label={execution.status} />
					</div>
					<div className="codeLine">{execution.codeFirstLine || "(code unavailable)"}</div>
					<div className="rowMeta">
						<span>{execution.submittedAt ? formatTime(execution.submittedAt) : "time pending"}</span>
						<span>{execution.durationMs === undefined ? "duration pending" : `${Math.round(execution.durationMs)} ms`}</span>
						<span>{execution.outputTotalLines} lines</span>
						{execution.errorSummary ? <span>{execution.errorSummary}</span> : null}
					</div>
				</button>
			))}
		</div>
	);
}

function ExecutionDetail({
	execution,
	activeStream,
	onActiveStreamChange,
	outputRef,
}: {
	execution?: ExecutionView;
	activeStream: OutputStreamName | "combined";
	onActiveStreamChange: (stream: OutputStreamName | "combined") => void;
	outputRef: React.RefObject<HTMLPreElement | null>;
}) {
	if (!execution) {
		return <div className="emptyState">No execution selected.</div>;
	}
	const outputText = activeStream === "combined" ? execution.combinedOutput : execution.outputs[activeStream];
	return (
		<div className="detailStack">
			<div className="detailHeader">
				<div>
					<h2>{execution.id}</h2>
					<p>
						{execution.status} · {execution.outputTotalLines} output lines
					</p>
				</div>
				<StatusPill tone={statusTone(execution.status)} label={execution.status} />
			</div>
			<div className="metaGrid">
				<span>workspace</span>
				<strong>{execution.workspace ?? "not reported"}</strong>
				<span>kernel</span>
				<strong>{execution.kernelPid ?? "unknown"}</strong>
				<span>output_log</span>
				<strong>{execution.outputLog ?? "none"}</strong>
				<span>error</span>
				<strong>{execution.errorSummary || "none"}</strong>
			</div>
			<div className="sectionHeader">
				<h3>Code</h3>
				<CopyButton text={execution.code} label="Copy code" />
			</div>
			<pre className="codeBlock">{execution.code || "(code unavailable)"}</pre>
			<div className="sectionHeader">
				<h3>Output</h3>
				<CopyButton text={outputText || execution.combinedOutput} label="Copy output" />
			</div>
			<div className="tabs" role="tablist" aria-label="Output streams">
				{(["combined", ...STREAM_TABS] as const).map((stream) => (
					<button
						type="button"
						key={stream}
						className={stream === activeStream ? "active" : ""}
						onClick={() => onActiveStreamChange(stream)}
					>
						{stream}
					</button>
				))}
			</div>
			<pre ref={outputRef} className={`outputBlock ${activeStream}`}>
				{outputText || "(no output)"}
			</pre>
		</div>
	);
}

function ToolTimeline({ toolCalls }: { toolCalls: ToolCallView[] }) {
	if (toolCalls.length === 0) {
		return <div className="emptyState">Waiting for MCP tool calls.</div>;
	}
	return (
		<div className="toolTimeline">
			{toolCalls.map((tool) => (
				<article key={tool.callId} className="toolRow">
					<div className="rowTop">
						<strong>{tool.toolName}</strong>
						<StatusPill tone={statusTone(tool.status)} label={tool.status} />
					</div>
					<div className="rowMeta">
						<span>{tool.callId}</span>
						<span>{tool.durationMs === undefined ? "duration pending" : `${Math.round(tool.durationMs)} ms`}</span>
						<span>{tool.ok === undefined ? "ok pending" : tool.ok ? "ok" : "failed"}</span>
					</div>
					{tool.argumentsSummary ? <p className="summaryText">{tool.argumentsSummary}</p> : null}
					{tool.resultSummary ? <p className="summaryText resultSummary">{tool.resultSummary}</p> : null}
					{tool.prettyTextSummary ? <pre className="prettySummary">{tool.prettyTextSummary}</pre> : null}
				</article>
			))}
		</div>
	);
}

function CopyButton({ text, label }: { text: string; label: string }) {
	const copy = () => {
		if (navigator.clipboard && text) {
			void navigator.clipboard.writeText(text);
		}
	};
	return (
		<button type="button" className="iconButton" onClick={copy} aria-label={label} title={label}>
			<Clipboard size={16} aria-hidden="true" />
		</button>
	);
}

function StatusPill({ tone, label }: { tone: "ok" | "bad" | "warn" | "idle"; label: string }) {
	return <span className={`statusPill ${tone}`}>{label}</span>;
}

function statusTone(status: string): "ok" | "bad" | "warn" | "idle" {
	if (status === "completed" || status === "open") {
		return "ok";
	}
	if (status === "error" || status === "exception" || status === "failed") {
		return "bad";
	}
	if (status === "running" || status === "interrupted" || status === "killed") {
		return "warn";
	}
	return "idle";
}

function formatTime(value: number): string {
	return new Intl.DateTimeFormat(undefined, {
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
	}).format(new Date(value));
}

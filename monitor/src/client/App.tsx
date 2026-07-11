import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import * as Slider from "@radix-ui/react-slider";
import { Activity, Clipboard, Eraser, PanelLeftClose, PanelLeftOpen, Pause, Play, ScrollText } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import type { MonitorHealth } from "../shared/types";
import type { MonitorClientEvent } from "./events";
import { parseSseMessage } from "./events";
import { buildMonitorState, type ExecutionView, type OutputStreamName } from "./state";

hljs.registerLanguage("python", python);

type ConnectionState = "disabled" | "connecting" | "open" | "closed" | "error";

type AppProps = {
	initialEvents?: MonitorClientEvent[];
	initialHealth?: MonitorHealth;
	connect?: boolean;
};

const STREAM_TABS: OutputStreamName[] = ["stdout", "stderr", "result", "traceback"];
const DEFAULT_CODE_FONT_SIZE = 16;

export function App({ initialEvents = [], initialHealth, connect = true }: AppProps) {
	const [events, setEvents] = useState<MonitorClientEvent[]>(initialEvents);
	const [health, setHealth] = useState<MonitorHealth | undefined>(initialHealth);
	const [connection, setConnection] = useState<ConnectionState>(connect ? "connecting" : "disabled");
	const [selectedExecutionId, setSelectedExecutionId] = useState<string | undefined>();
	const [activeStream, setActiveStream] = useState<OutputStreamName | "combined">("combined");
	const [paused, setPaused] = useState(false);
	const [autoScroll, setAutoScroll] = useState(true);
	const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
	const [codeFontSize, setCodeFontSize] = useState(DEFAULT_CODE_FONT_SIZE);
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
		<div className="appShell" style={{ "--code-font-size": `${codeFontSize}px` } as React.CSSProperties}>
			<StatusBar
				connection={connection}
				eventCount={view.events.length}
				codeFontSize={codeFontSize}
				paused={paused}
				autoScroll={autoScroll}
				onCodeFontSizeChange={setCodeFontSize}
				onTogglePaused={() => setPaused((value) => !value)}
				onToggleAutoScroll={() => setAutoScroll((value) => !value)}
				onClear={clearView}
				{...(view.health === undefined ? {} : { health: view.health })}
				{...(view.latestEventTime === undefined ? {} : { latestEventTime: view.latestEventTime })}
			/>
			<main className={`monitorGrid ${sidebarCollapsed ? "sidebarCollapsed" : ""}`}>
				<section className="panel executionPanel" aria-label="Executions">
					<div className="panelHeader sidebarHeader">
						{sidebarCollapsed ? null : (
							<>
								<h1>loommux monitor</h1>
								<span>{view.executions.length} executions</span>
							</>
						)}
						<button
							type="button"
							className="iconButton sidebarToggle"
							onClick={() => setSidebarCollapsed((value) => !value)}
							aria-label={sidebarCollapsed ? "Expand execution list" : "Collapse execution list"}
							title={sidebarCollapsed ? "Expand execution list" : "Collapse execution list"}
						>
							{sidebarCollapsed ? <PanelLeftOpen size={16} aria-hidden="true" /> : <PanelLeftClose size={16} aria-hidden="true" />}
						</button>
					</div>
					{sidebarCollapsed ? null : (
						<ExecutionList
							executions={view.executions}
							onSelect={setSelectedExecutionId}
							{...(selectedExecution?.id === undefined ? {} : { selectedId: selectedExecution.id })}
						/>
					)}
				</section>
				<div className="detailPanel">
					<ExecutionWorkspace
						activeStream={activeStream}
						onActiveStreamChange={setActiveStream}
						outputRef={outputRef}
						{...(selectedExecution === undefined ? {} : { execution: selectedExecution })}
					/>
				</div>
			</main>
		</div>
	);
}

type StatusBarProps = {
	connection: ConnectionState;
	health?: MonitorHealth;
	eventCount: number;
	latestEventTime?: number;
	codeFontSize: number;
	paused: boolean;
	autoScroll: boolean;
	onCodeFontSizeChange: (value: number) => void;
	onTogglePaused: () => void;
	onToggleAutoScroll: () => void;
	onClear: () => void;
};

function StatusBar({
	connection,
	health,
	eventCount,
	latestEventTime,
	codeFontSize,
	paused,
	autoScroll,
	onCodeFontSizeChange,
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
			<label className="fontControl">
				<span>Code {codeFontSize}px</span>
				<Slider.Root
					className="fontSlider"
					min={13}
					max={24}
					step={1}
					value={[codeFontSize]}
					onValueChange={([value]) => {
						if (value !== undefined) {
							onCodeFontSizeChange(value);
						}
					}}
				>
					<Slider.Track className="fontSliderTrack">
						<Slider.Range className="fontSliderRange" />
					</Slider.Track>
					<Slider.Thumb className="fontSliderThumb" aria-label="Code and output font size" />
				</Slider.Root>
			</label>
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

function ExecutionWorkspace({
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
	const panelOrientation = useNarrowViewport() ? "vertical" : "horizontal";
	return (
		<Group className="detailWorkspace" orientation={panelOrientation}>
			<Panel defaultSize={47} minSize={24}>
				<section className="codeSurface" aria-label="Python code">
					<div className="sectionHeader">
						<div className="surfaceMeta">
							<span>code</span>
							<span>{execution.workspace ?? "workspace not reported"}</span>
						</div>
						<CopyButton text={execution.code} label="Copy code" />
					</div>
					<HighlightedPythonCode code={execution.code || "# code unavailable"} />
				</section>
			</Panel>
			<Separator className="panelResizeHandle" aria-label="Resize code and output panels" />
			<Panel defaultSize={53} minSize={24}>
				<section className="outputSurface" aria-label="Python output">
					<div className="sectionHeader">
						<div className="surfaceMeta">
							<span>output</span>
							<span>{execution.outputLog ?? `${execution.outputTotalLines} lines`}</span>
							{execution.errorSummary ? <strong>{execution.errorSummary}</strong> : null}
						</div>
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
				</section>
			</Panel>
		</Group>
	);
}

function useNarrowViewport() {
	const [narrow, setNarrow] = useState(() =>
		typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 900px)").matches : false,
	);
	useEffect(() => {
		if (typeof window.matchMedia !== "function") {
			return undefined;
		}
		const query = window.matchMedia("(max-width: 900px)");
		const update = () => setNarrow(query.matches);
		update();
		query.addEventListener("change", update);
		return () => query.removeEventListener("change", update);
	}, []);
	return narrow;
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

function HighlightedPythonCode({ code }: { code: string }) {
	const highlighted = useMemo(() => hljs.highlight(code, { language: "python" }).value, [code]);
	return <pre className="codeBlock"><code dangerouslySetInnerHTML={{ __html: highlighted }} /></pre>;
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

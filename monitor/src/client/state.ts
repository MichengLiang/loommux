import type { MonitorHealth } from "../shared/types";
import type { MonitorClientEvent } from "./events";
import { summarizeValue } from "./events";

export type OutputStreamName = "stdout" | "stderr" | "result" | "traceback";

export type ExecutionView = {
	id: string;
	callId?: string | null;
	status: string;
	code: string;
	codeFirstLine: string;
	workspace?: string | null;
	kernelPid?: number | null;
	timeoutSeconds?: number;
	submittedAt?: number;
	finishedAt?: number;
	durationMs?: number;
	outputs: Record<OutputStreamName, string>;
	combinedOutput: string;
	outputLog?: string | null;
	outputTotalLines: number;
	executionCount?: number | null;
	error?: unknown;
	errorSummary: string;
	latestSequence: number;
};

export type ToolCallView = {
	callId: string;
	toolName: string;
	argumentsSummary: string;
	resultSummary: string;
	prettyTextSummary: string;
	status: string;
	ok?: boolean;
	durationMs?: number;
	startedAt?: number;
	finishedAt?: number;
	latestSequence: number;
};

export type MonitorViewState = {
	events: MonitorClientEvent[];
	health?: MonitorHealth;
	executions: ExecutionView[];
	toolCalls: ToolCallView[];
	latestEventTime?: number;
};

const EMPTY_OUTPUTS: Record<OutputStreamName, string> = {
	stdout: "",
	stderr: "",
	result: "",
	traceback: "",
};

export function buildMonitorState(events: MonitorClientEvent[], health?: MonitorHealth): MonitorViewState {
	const executions = new Map<string, ExecutionView>();
	const toolCalls = new Map<string, ToolCallView>();
	let latestEventTime: number | undefined;

	for (const event of events) {
		latestEventTime = Math.max(latestEventTime ?? 0, eventTimeMs(event) ?? event.received_at ?? 0);
		if (event.type.startsWith("execution_")) {
			applyExecutionEvent(executions, event);
		}
		if (event.type.startsWith("tool_call_")) {
			applyToolEvent(toolCalls, event);
		}
	}

	const state: MonitorViewState = {
		events,
		executions: [...executions.values()].sort((left, right) => right.latestSequence - left.latestSequence),
		toolCalls: [...toolCalls.values()].sort((left, right) => left.latestSequence - right.latestSequence),
	};
	if (health !== undefined) {
		state.health = health;
	}
	if (latestEventTime !== undefined) {
		state.latestEventTime = latestEventTime;
	}
	return state;
}

export function eventTimeMs(event: MonitorClientEvent): number | undefined {
	if (typeof event.timestamp === "number") {
		return event.timestamp < 10_000_000_000 ? event.timestamp * 1000 : event.timestamp;
	}
	if (typeof event.received_at === "number") {
		return event.received_at;
	}
	return undefined;
}

function applyExecutionEvent(executions: Map<string, ExecutionView>, event: MonitorClientEvent) {
	const id = event.execution_id;
	if (!id) {
		return;
	}
	const current = ensureExecution(executions, id, event.sequence);
	current.latestSequence = Math.max(current.latestSequence, event.sequence);

	if (event.type === "execution_submitted") {
		current.status = "running";
		current.code = event.code ?? current.code;
		current.codeFirstLine = firstLine(current.code);
		assignDefined(current, "callId", event.call_id);
		assignDefined(current, "workspace", event.workspace);
		assignDefined(current, "kernelPid", event.kernel_pid);
		assignDefined(current, "timeoutSeconds", event.timeout_seconds);
		assignDefined(current, "submittedAt", eventTimeMs(event));
		return;
	}

	if (event.type === "execution_output") {
		const stream = normalizeStream(event.stream);
		if (stream && event.text) {
			current.outputs[stream] += event.text;
			current.combinedOutput += event.text;
			current.outputTotalLines = Math.max(current.outputTotalLines, countLines(current.combinedOutput));
		}
		assignDefined(current, "executionCount", event.execution_count);
		return;
	}

	if (event.type === "execution_finished") {
		current.status = event.status ?? current.status;
		assignDefined(current, "finishedAt", eventTimeMs(event));
		assignDefined(current, "outputLog", event.output_log);
		current.outputTotalLines = event.output_total_lines ?? current.outputTotalLines;
		assignDefined(current, "error", event.error);
		current.errorSummary = summarizeError(current.error);
		if (current.submittedAt !== undefined && current.finishedAt !== undefined) {
			current.durationMs = Math.max(0, current.finishedAt - current.submittedAt);
		}
	}
}

function applyToolEvent(toolCalls: Map<string, ToolCallView>, event: MonitorClientEvent) {
	const callId = event.call_id;
	if (!callId) {
		return;
	}
	const current = ensureToolCall(toolCalls, callId, event.sequence);
	current.latestSequence = Math.max(current.latestSequence, event.sequence);
	current.toolName = event.tool_name ?? current.toolName;

	if (event.type === "tool_call_started") {
		current.status = "running";
		current.argumentsSummary = summarizeValue(event.arguments, 220);
		assignDefined(current, "startedAt", eventTimeMs(event));
		return;
	}

	if (event.type === "tool_call_finished") {
		current.status = event.status ?? current.status;
		assignDefined(current, "ok", event.ok);
		assignDefined(current, "durationMs", event.duration_ms);
		current.resultSummary = event.result_summary ?? current.resultSummary;
		current.prettyTextSummary = event.pretty_text_summary ?? current.prettyTextSummary;
		assignDefined(current, "finishedAt", eventTimeMs(event));
	}
}

function ensureExecution(executions: Map<string, ExecutionView>, id: string, sequence: number): ExecutionView {
	const existing = executions.get(id);
	if (existing) {
		return existing;
	}
	const created: ExecutionView = {
		id,
		status: "unknown",
		code: "",
		codeFirstLine: "",
		outputs: { ...EMPTY_OUTPUTS },
		combinedOutput: "",
		outputTotalLines: 0,
		errorSummary: "",
		latestSequence: sequence,
	};
	executions.set(id, created);
	return created;
}

function ensureToolCall(toolCalls: Map<string, ToolCallView>, callId: string, sequence: number): ToolCallView {
	const existing = toolCalls.get(callId);
	if (existing) {
		return existing;
	}
	const created: ToolCallView = {
		callId,
		toolName: "unknown_tool",
		argumentsSummary: "",
		resultSummary: "",
		prettyTextSummary: "",
		status: "pending",
		latestSequence: sequence,
	};
	toolCalls.set(callId, created);
	return created;
}

function normalizeStream(stream: string | undefined): OutputStreamName | undefined {
	if (stream === "stdout" || stream === "stderr" || stream === "result" || stream === "traceback") {
		return stream;
	}
	return undefined;
}

function firstLine(value: string): string {
	return value.split(/\r?\n/, 1)[0]?.trim() || "(empty code)";
}

function countLines(value: string): number {
	if (!value) {
		return 0;
	}
	return value.endsWith("\n") ? value.split(/\r?\n/).length - 1 : value.split(/\r?\n/).length;
}

function summarizeError(error: unknown): string {
	if (!error) {
		return "";
	}
	if (typeof error === "string") {
		return error;
	}
	if (typeof error === "object") {
		const record = error as Record<string, unknown>;
		const name = typeof record.ename === "string" ? record.ename : "Error";
		const value = typeof record.evalue === "string" ? record.evalue : "";
		return value ? `${name}: ${value}` : name;
	}
	return String(error);
}

function assignDefined<T extends object, K extends keyof T>(target: T, key: K, value: T[K] | undefined) {
	if (value !== undefined) {
		target[key] = value;
	}
}

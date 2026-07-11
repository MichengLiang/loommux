import type { BufferedMonitorEvent, MonitorHealth } from "../shared/types";

export type MonitorClientEvent = BufferedMonitorEvent & {
	type: string;
	call_id?: string | null;
	tool_name?: string;
	arguments?: unknown;
	duration_ms?: number;
	ok?: boolean;
	status?: string;
	result_summary?: string;
	pretty_text_summary?: string;
	execution?: number;
	workspace?: string | null;
	kernel_pid?: number | null;
	code?: string;
	timeout_seconds?: number;
	stream?: string;
	text?: string;
	kernel_execution_count?: number | null;
	output_total_lines?: number;
	error?: unknown;
};

export type SnapshotMessage = {
	health?: MonitorHealth;
	events?: MonitorClientEvent[];
};

export type StreamMessage =
	| { kind: "snapshot"; snapshot: SnapshotMessage }
	| { kind: "event"; event: MonitorClientEvent }
	| { kind: "heartbeat" };

export function parseSseMessage(kind: "snapshot" | "event" | "heartbeat", data: string): StreamMessage | null {
	if (kind === "heartbeat") {
		return { kind };
	}
	try {
		const parsed = JSON.parse(data) as unknown;
		if (kind === "snapshot" && isRecord(parsed)) {
			return { kind, snapshot: parsed as SnapshotMessage };
		}
		if (kind === "event" && isRecord(parsed) && typeof parsed.type === "string") {
			return { kind, event: parsed as MonitorClientEvent };
		}
	} catch {
		return null;
	}
	return null;
}

export function summarizeValue(value: unknown, maxLength = 160): string {
	if (value === undefined || value === null) {
		return "";
	}
	const text = typeof value === "string" ? value : JSON.stringify(value);
	if (!text) {
		return "";
	}
	return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

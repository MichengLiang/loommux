export type MonitorEventPayload = Record<string, unknown>;

export type MonitorEventInput = {
	type: string;
	timestamp?: number;
	[key: string]: unknown;
};

export type BufferedMonitorEvent = MonitorEventInput & {
	sequence: number;
	received_at: number;
};

export type MonitorHealth = {
	ok: true;
	started_at: string;
	uptime_ms: number;
	clients: number;
	events_buffered: number;
	events_received: number;
	events_dropped: number;
};

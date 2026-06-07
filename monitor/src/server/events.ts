import type { BufferedMonitorEvent, MonitorEventInput, MonitorHealth } from "../shared/types";

export type EventStoreOptions = {
	capacity?: number;
	now?: () => number;
};

export type EventListener = (event: BufferedMonitorEvent) => void;

const DEFAULT_RING_BUFFER_CAPACITY = 1000;

export class MonitorEventStore {
	private readonly capacity: number;
	private readonly now: () => number;
	private readonly startedAtMs: number;
	private readonly buffered: BufferedMonitorEvent[] = [];
	private readonly listeners = new Set<EventListener>();
	private nextSequence = 1;
	private received = 0;
	private dropped = 0;

	constructor(options: EventStoreOptions = {}) {
		if (options.capacity !== undefined && options.capacity <= 0) {
			throw new Error("capacity must be greater than 0");
		}
		this.capacity = options.capacity ?? DEFAULT_RING_BUFFER_CAPACITY;
		this.now = options.now ?? Date.now;
		this.startedAtMs = this.now();
	}

	append(input: MonitorEventInput): BufferedMonitorEvent {
		const event: BufferedMonitorEvent = {
			...input,
			sequence: this.nextSequence,
			received_at: this.now(),
		};
		this.nextSequence += 1;
		this.received += 1;
		if (this.buffered.length >= this.capacity) {
			this.buffered.shift();
			this.dropped += 1;
		}
		this.buffered.push(event);
		for (const listener of this.listeners) {
			listener(event);
		}
		return event;
	}

	subscribe(listener: EventListener): () => void {
		this.listeners.add(listener);
		return () => {
			this.listeners.delete(listener);
		};
	}

	snapshot(): BufferedMonitorEvent[] {
		return [...this.buffered];
	}

	health(): MonitorHealth {
		return {
			ok: true,
			started_at: new Date(this.startedAtMs).toISOString(),
			uptime_ms: Math.max(0, this.now() - this.startedAtMs),
			clients: this.listeners.size,
			events_buffered: this.buffered.length,
			events_received: this.received,
			events_dropped: this.dropped,
		};
	}
}

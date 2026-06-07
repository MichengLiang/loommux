import { Hono } from "hono";
import { streamSSE } from "hono/streaming";
import { MonitorEventSchema } from "../shared/schema";
import type { BufferedMonitorEvent, MonitorEventInput, MonitorHealth } from "../shared/types";
import { MonitorEventStore } from "./events";

export type CreateAppOptions = {
	eventStore?: MonitorEventStore;
	ringBufferCapacity?: number;
	heartbeatIntervalMs?: number;
};

type EventResponse = {
	ok: true;
	sequence: number;
};

type SnapshotMessage = {
	health: MonitorHealth;
	events: unknown[];
};

const DEFAULT_HEARTBEAT_INTERVAL_MS = 15_000;

export function createApp(options: CreateAppOptions = {}) {
	const store =
		options.eventStore ??
		new MonitorEventStore(
			options.ringBufferCapacity === undefined ? {} : { capacity: options.ringBufferCapacity },
		);
	const heartbeatIntervalMs = options.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS;
	const app = new Hono();

	app.get("/api/health", (c) => c.json(store.health()));

	app.post("/api/events", async (c) => {
		let body: unknown;
		try {
			body = await c.req.json();
		} catch {
			return c.json({ ok: false, error: "invalid_json" }, 400);
		}
		const parsed = MonitorEventSchema.safeParse(body);
		if (!parsed.success) {
			return c.json({ ok: false, error: "invalid_event" }, 400);
		}
		const event = store.append(parsed.data as MonitorEventInput);
		return c.json({ ok: true, sequence: event.sequence } satisfies EventResponse);
	});

	app.get("/api/events/stream", (c) =>
		streamSSE(c, async (stream) => {
			let closed = false;
			let snapshotSent = false;
			const pendingEvents: BufferedMonitorEvent[] = [];
			const writeEvent = async (name: string, data: unknown, id?: number) => {
				const message = {
					event: name,
					data: JSON.stringify(data),
					...(id === undefined ? {} : { id: String(id) }),
				};
				await stream.writeSSE(message);
			};
			const unsubscribe = store.subscribe((event) => {
				if (closed) {
					return;
				}
				if (!snapshotSent) {
					pendingEvents.push(event);
					return;
				}
				void writeEvent("event", event, event.sequence);
			});
			stream.onAbort(() => {
				closed = true;
				unsubscribe();
			});
			const snapshot: SnapshotMessage = {
				health: store.health(),
				events: store.snapshot(),
			};
			await writeEvent("snapshot", snapshot);
			snapshotSent = true;
			for (const event of pendingEvents) {
				if (closed) {
					break;
				}
				await writeEvent("event", event, event.sequence);
			}
			pendingEvents.length = 0;
			while (!closed) {
				await stream.sleep(heartbeatIntervalMs);
				if (!closed) {
					await writeEvent("heartbeat", { now: Date.now() });
				}
			}
		}),
	);

	app.all("/api/*", (c) => c.json({ ok: false, error: "not_found" }, 404));

	return app;
}

export const app = createApp();

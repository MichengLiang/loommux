import { describe, expect, test } from "vitest";
import { createApp } from "./app";
import { MonitorEventStore } from "./events";

async function readSseUntil(response: Response, pattern: string): Promise<string> {
	const reader = response.body?.getReader();
	if (!reader) {
		throw new Error("missing response body");
	}
	const decoder = new TextDecoder();
	let text = "";
	const deadline = Date.now() + 1_000;
	while (!text.includes(pattern) && Date.now() < deadline) {
		const { done, value } = await reader.read();
		if (done) {
			break;
		}
		text += decoder.decode(value, { stream: true });
	}
	await reader.cancel();
	return text;
}

describe("monitor backend", () => {
	test("health returns expected fields", async () => {
		const app = createApp({ eventStore: new MonitorEventStore({ now: () => 1_000 }) });

		const response = await app.request("/api/health");
		const health = await response.json();

		expect(response.status).toBe(200);
		expect(health).toMatchObject({
			ok: true,
			started_at: "1970-01-01T00:00:01.000Z",
			uptime_ms: 0,
			clients: 0,
			events_buffered: 0,
			events_received: 0,
			events_dropped: 0,
		});
	});

	test("legal event post returns sequence and increments received count", async () => {
		const app = createApp();

		const posted = await app.request("/api/events", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ type: "tool_call_started", tool_name: "python_status" }),
		});
		const result = await posted.json();
		const health = await (await app.request("/api/health")).json();

		expect(posted.status).toBe(200);
		expect(result).toEqual({ ok: true, sequence: 1 });
		expect(health.events_received).toBe(1);
		expect(health.events_buffered).toBe(1);
	});

	test("illegal event post returns 400", async () => {
		const app = createApp();

		const response = await app.request("/api/events", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ type: "" }),
		});

		expect(response.status).toBe(400);
	});

	test("ring buffer drops old events at configured capacity", async () => {
		const store = new MonitorEventStore({ capacity: 2 });

		store.append({ type: "event_1" });
		store.append({ type: "event_2" });
		store.append({ type: "event_3" });

		expect(store.snapshot().map((event) => event.type)).toEqual(["event_2", "event_3"]);
		expect(store.health()).toMatchObject({
			events_buffered: 2,
			events_received: 3,
			events_dropped: 1,
		});
	});

	test("sse stream receives a posted event", async () => {
		const app = createApp({ heartbeatIntervalMs: 10_000 });
		const streamResponse = await app.request("/api/events/stream");
		expect(streamResponse.status).toBe(200);

		const readPromise = readSseUntil(streamResponse, "tool_call_finished");
		await app.request("/api/events", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ type: "tool_call_finished", tool_name: "python_status" }),
		});
		const text = await readPromise;

		expect(text).toContain("event: snapshot");
		expect(text).toContain("event: event");
		expect(text).toContain("tool_call_finished");
	});
});

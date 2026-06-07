import { expect, test } from "@playwright/test";
import type { Server } from "node:http";
import { spawn, type ChildProcess } from "node:child_process";
import { serve } from "@hono/node-server";
import { createApp } from "../src/server/app";

const BACKEND_URL = "http://127.0.0.1:9765";
const FRONTEND_URL = "http://127.0.0.1:5175";
let backendServer: Server;
let viteServer: ChildProcess;

test.beforeAll(async () => {
	backendServer = serve({
		fetch: createApp().fetch,
		hostname: "127.0.0.1",
		port: 9765,
	});
	await waitForBackend();
	viteServer = spawn("pnpm", ["exec", "vite", "--host", "127.0.0.1", "--port", "5175", "--strictPort"], {
		stdio: "ignore",
	});
	await waitForFrontend();
});

test.afterAll(async () => {
	await new Promise<void>((resolve, reject) => {
		backendServer.close((error) => {
			if (error) {
				reject(error);
				return;
			}
			resolve();
		});
	});
	if (viteServer.pid !== undefined) {
		viteServer.kill();
	}
});

test.beforeEach(async ({ page }) => {
	await page.route("**/api/**", async (route) => {
		const requestUrl = new URL(route.request().url());
		await route.continue({ url: `${BACKEND_URL}${requestUrl.pathname}${requestUrl.search}` });
	});
});

test("monitor receives backend events and keeps primary UI stable", async ({ page, request }) => {
	await page.goto("/");

	await expect(page.getByRole("heading", { name: "loommux monitor" })).toBeVisible();
	await expect(page.getByText(/clients \d+/)).toBeVisible();
	await expect(page.getByText("open")).toBeVisible();

	const executionId = `exec-e2e-${test.info().project.name}`;
	await request.post(`${BACKEND_URL}/api/events`, {
		data: {
			type: "execution_submitted",
			execution_id: executionId,
			call_id: "call-e2e",
			code: "print('e2e monitor')",
			timeout_seconds: 5,
			workspace: "/tmp/loommux-e2e",
			kernel_pid: 4321,
			timestamp: Date.now() / 1000,
		},
	});
	await request.post(`${BACKEND_URL}/api/events`, {
		data: {
			type: "execution_output",
			execution_id: executionId,
			stream: "stdout",
			text: "e2e monitor output\n",
			execution_count: 1,
			timestamp: Date.now() / 1000,
		},
	});

	await expect(page.getByText(executionId).first()).toBeVisible();
	await expect(page.getByText("print('e2e monitor')").first()).toBeVisible();
	await expect(page.getByText(/e2e monitor output/)).toBeVisible();

	const hasHorizontalOverflow = await page.evaluate(
		() => document.documentElement.scrollWidth > window.innerWidth + 1,
	);
	expect(hasHorizontalOverflow).toBe(false);

	for (const label of ["Executions", "Execution detail", "Tool timeline"]) {
		const box = await page.getByLabel(label).boundingBox();
		expect(box, `${label} panel should render`).not.toBeNull();
		expect(box?.width ?? 0, `${label} width`).toBeGreaterThan(120);
		expect(box?.height ?? 0, `${label} height`).toBeGreaterThan(80);
	}
});

async function waitForBackend() {
	const deadline = Date.now() + 5_000;
	while (Date.now() < deadline) {
		try {
			const response = await fetch(`${BACKEND_URL}/api/health`);
			if (response.ok) {
				return;
			}
		} catch {
			await new Promise((resolve) => setTimeout(resolve, 100));
		}
	}
	throw new Error("monitor backend did not become ready");
}

async function waitForFrontend() {
	const deadline = Date.now() + 10_000;
	while (Date.now() < deadline) {
		try {
			const response = await fetch(FRONTEND_URL);
			if (response.ok) {
				return;
			}
		} catch {
			await new Promise((resolve) => setTimeout(resolve, 100));
		}
	}
	throw new Error("monitor frontend did not become ready");
}

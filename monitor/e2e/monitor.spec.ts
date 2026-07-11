import { expect, test } from "@playwright/test";
import type { Server } from "node:http";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { serve } from "@hono/node-server";
import { createApp } from "../src/server/app";

let backendServer: Server;
let viteServer: ChildProcess;
let backendUrl: string;
let frontendUrl: string;

test.beforeAll(async () => {
	const backendPort = await findOpenPort();
	const frontendPort = await findOpenPort();
	backendUrl = `http://127.0.0.1:${backendPort}`;
	frontendUrl = `http://127.0.0.1:${frontendPort}`;
	backendServer = serve({
		fetch: createApp().fetch,
		hostname: "127.0.0.1",
		port: backendPort,
	});
	await waitForBackend();
	viteServer = spawn("pnpm", ["exec", "vite", "--host", "127.0.0.1", "--port", String(frontendPort), "--strictPort"], {
		env: { ...process.env, MONITOR_BACKEND_URL: backendUrl },
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

test("monitor receives backend events and keeps primary UI stable", async ({ page, request }) => {
	await page.goto(frontendUrl);

	await expect(page.getByRole("heading", { name: "loommux monitor" })).toBeVisible();
	await expect(page.getByText(/clients \d+/)).toBeVisible();
	await expect(page.getByText("open")).toBeVisible();

	const execution = test.info().project.name === "chromium-desktop" ? 101 : 102;
	await request.post(`${backendUrl}/api/events`, {
		data: {
			type: "execution_submitted",
			execution,
			call_id: "call-e2e",
			code: "print('e2e monitor')",
			timeout_seconds: 5,
			workspace: "/tmp/loommux-e2e",
			kernel_pid: 4321,
			timestamp: Date.now() / 1000,
		},
	});
	await request.post(`${backendUrl}/api/events`, {
		data: {
			type: "execution_output",
			execution,
			stream: "stdout",
			text: "e2e monitor output\n",
			kernel_execution_count: 1,
			timestamp: Date.now() / 1000,
		},
	});
	await request.post(`${backendUrl}/api/events`, {
		data: {
			type: "execution_output",
			execution,
			stream: "result",
			text: "42",
			kernel_execution_count: 1,
			timestamp: Date.now() / 1000,
		},
	});

	await expect(page.getByText(`Execution ${execution}`).first()).toBeVisible();
	await expect(page.getByText("print('e2e monitor')").first()).toBeVisible();
	await expect(page.getByText(/e2e monitor output/)).toBeVisible();
	await expect(page.getByText(`Out[${execution}]: 42`)).toBeVisible();

	const hasHorizontalOverflow = await page.evaluate(
		() => document.documentElement.scrollWidth > window.innerWidth + 1,
	);
	expect(hasHorizontalOverflow).toBe(false);

	for (const label of ["Executions", "Python code", "Python output"]) {
		const box = await page.getByLabel(label).boundingBox();
		expect(box, `${label} panel should render`).not.toBeNull();
		expect(box?.width ?? 0, `${label} width`).toBeGreaterThan(120);
		expect(box?.height ?? 0, `${label} height`).toBeGreaterThan(80);
	}
	const codeStyle = await page.locator(".codeBlock").evaluate((element) => {
		const style = getComputedStyle(element);
		return { backgroundColor: style.backgroundColor, color: style.color, fontSize: style.fontSize, fontFamily: style.fontFamily };
	});
	const outputStyle = await page.locator(".outputBlock").evaluate((element) => {
		const style = getComputedStyle(element);
		return { backgroundColor: style.backgroundColor, color: style.color, fontSize: style.fontSize };
	});
	expect(codeStyle.backgroundColor).not.toBe("rgb(16, 35, 31)");
	expect(outputStyle.backgroundColor).not.toBe("rgb(17, 24, 32)");
	expect(Number.parseFloat(codeStyle.fontSize)).toBeGreaterThanOrEqual(15);
	expect(Number.parseFloat(outputStyle.fontSize)).toBeGreaterThanOrEqual(15);
	expect(codeStyle.fontFamily.toLowerCase()).toContain("mono");
	const fontSlider = page.getByRole("slider", { name: "Code and output font size" });
	await fontSlider.focus();
	await fontSlider.press("ArrowRight");
	await fontSlider.press("ArrowRight");
	const resizedCodeStyle = await page.locator(".codeBlock").evaluate((element) => {
		const style = getComputedStyle(element);
		return { fontSize: style.fontSize, fontFamily: style.fontFamily };
	});
	const resizedOutputStyle = await page.locator(".outputBlock").evaluate((element) => getComputedStyle(element).fontSize);
	expect(resizedCodeStyle.fontSize).toBe("18px");
	expect(resizedOutputStyle).toBe("18px");
	expect(resizedCodeStyle.fontFamily.toLowerCase()).toContain("mono");
	const resizeHandle = page.getByRole("separator", { name: "Resize code and output panels" });
	const handleBox = await resizeHandle.boundingBox();
	expect(handleBox).not.toBeNull();
	if (test.info().project.name === "chromium-desktop" && handleBox) {
		const codeBeforeResize = await page.getByLabel("Python code").boundingBox();
		expect(codeBeforeResize).not.toBeNull();
		if (!codeBeforeResize) {
			throw new Error("missing code panel before resize");
		}
		await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
		await page.mouse.down();
		await page.mouse.move(handleBox.x + handleBox.width / 2 + 160, handleBox.y + handleBox.height / 2);
		await page.mouse.up();
		const codeAfterResize = await page.getByLabel("Python code").boundingBox();
		expect((codeAfterResize?.width ?? 0) - codeBeforeResize.width).toBeGreaterThan(80);
	}
	await expect(page.getByLabel("Execution detail")).toHaveCount(0);
	await expect(page.getByLabel("Tool timeline")).toHaveCount(0);
	await page.getByRole("button", { name: "Collapse execution list" }).click();
	await expect(page.locator(".executionList").getByText(`Execution ${execution}`)).toHaveCount(0);
	await page.getByRole("button", { name: "Expand execution list" }).click();
	await expect(page.locator(".executionList").getByText(`Execution ${execution}`)).toBeVisible();
});

async function waitForBackend() {
	const deadline = Date.now() + 5_000;
	while (Date.now() < deadline) {
		try {
			const response = await fetch(`${backendUrl}/api/health`);
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
			const response = await fetch(frontendUrl);
			if (response.ok) {
				return;
			}
		} catch {
			await new Promise((resolve) => setTimeout(resolve, 100));
		}
	}
	throw new Error("monitor frontend did not become ready");
}

async function findOpenPort(): Promise<number> {
	return new Promise((resolve, reject) => {
		const server = createServer();
		server.once("error", reject);
		server.listen(0, "127.0.0.1", () => {
			const address = server.address();
			if (typeof address === "object" && address !== null) {
				const { port } = address;
				server.close(() => resolve(port));
				return;
			}
			server.close(() => reject(new Error("failed to allocate port")));
		});
	});
}

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
	testDir: "./e2e",
	timeout: 30_000,
	workers: 1,
	expect: {
		timeout: 5_000,
	},
	use: {
		baseURL: "http://127.0.0.1:5175",
		trace: "on-first-retry",
	},
	projects: [
		{
			name: "chromium-desktop",
			use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
		},
		{
			name: "chromium-mobile",
			use: { ...devices["Pixel 5"], viewport: { width: 390, height: 844 } },
		},
	],
});

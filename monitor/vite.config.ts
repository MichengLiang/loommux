import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
	plugins: [react()],
	server: {
		host: "127.0.0.1",
		port: 5175,
		proxy: {
			"/api": process.env.MONITOR_BACKEND_URL ?? "http://127.0.0.1:9765",
		},
	},
});

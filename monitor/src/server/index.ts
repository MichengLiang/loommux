import { resolve } from "node:path";
import { serve } from "@hono/node-server";
import { createApp } from "./app";

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 9765;

type ServerEnv = Partial<Pick<NodeJS.ProcessEnv, "HOST" | "PORT" | "STATIC_ROOT">>;

export function getServerConfig(env: ServerEnv = process.env) {
	return {
		hostname: env.HOST ?? DEFAULT_HOST,
		port: Number.parseInt(env.PORT ?? String(DEFAULT_PORT), 10),
		staticRoot: env.STATIC_ROOT ?? resolve(process.cwd(), "dist"),
	};
}

const config = getServerConfig();
const app = createApp({ staticRoot: config.staticRoot });

serve({ fetch: app.fetch, hostname: config.hostname, port: config.port }, (info) => {
	console.log(`loommux monitor listening on http://${info.address}:${info.port}`);
});

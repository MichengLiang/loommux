import { serve } from "@hono/node-server";
import { createApp } from "./app";

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 9765;

const hostname = process.env.HOST ?? DEFAULT_HOST;
const port = Number.parseInt(process.env.PORT ?? String(DEFAULT_PORT), 10);
const app = createApp();

serve({ fetch: app.fetch, hostname, port }, (info) => {
	console.log(`loommux monitor listening on http://${info.address}:${info.port}`);
});

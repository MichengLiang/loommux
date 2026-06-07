import { defineConfig } from "tsdown";

export default defineConfig({
	entry: ["src/server/index.ts"],
	format: ["esm"],
	outDir: "dist/server",
	dts: false,
	clean: false,
});

import { z } from "zod";

export const MonitorEventSchema = z
	.object({
		type: z.string().min(1),
		timestamp: z.number().finite().optional(),
	})
	.catchall(z.unknown());

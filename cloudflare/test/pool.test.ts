import { describe, expect, it } from "vitest";
import { containerInstanceName } from "../src/pool";

describe("containerInstanceName", () => {
	it("stays inside a fixed pool", () => {
		const names = new Set(Array.from({ length: 32 }, (_, byte) => containerInstanceName(byte, 3)));
		expect(names).toEqual(new Set(["pool-0", "pool-1", "pool-2"]));
	});

	it("treats a non-positive pool as a single instance", () => {
		expect(containerInstanceName(7, 0)).toBe("pool-0");
	});
});

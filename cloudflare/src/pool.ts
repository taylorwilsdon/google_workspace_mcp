/** How many interchangeable container instances the Worker spreads calls across. */
export const WORKSPACE_POOL_SIZE = 3;

/**
 * Pick a Durable Object name in a fixed pool.
 *
 * The Python server is stateless, so any instance can take any call. A fixed
 * set of names keeps `max_instances` honest: random names would open a new
 * container every time.
 */
export function containerInstanceName(randomByte: number, poolSize = WORKSPACE_POOL_SIZE): string {
	const size = poolSize > 0 ? poolSize : 1;
	const index = ((randomByte % size) + size) % size;
	return `pool-${index}`;
}

export function pickContainerInstance(poolSize = WORKSPACE_POOL_SIZE): string {
	const bytes = new Uint8Array(1);
	crypto.getRandomValues(bytes);
	return containerInstanceName(bytes[0] ?? 0, poolSize);
}

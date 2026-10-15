// How many flow particles may start: per edge per second, and in flight overall. Pure.

export function createBudget({ perEdgePerSec = 6, total = 60, clock = () => performance.now() } = {}) {
  const starts = new Map(); // edge key -> start times (ms) in the last second
  let inFlight = 0;
  return {
    take(edge) {
      const now = clock();
      const recent = (starts.get(edge) || []).filter((t) => now - t < 1000);
      if (recent.length >= perEdgePerSec || inFlight >= total) {
        starts.set(edge, recent);
        return false;
      }
      recent.push(now);
      starts.set(edge, recent);
      inFlight += 1;
      return true;
    },
    release() {
      inFlight = Math.max(0, inFlight - 1);
    },
    get inFlight() {
      return inFlight;
    },
  };
}

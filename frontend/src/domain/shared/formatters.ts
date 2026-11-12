/**
 * Pure Domain Formatting Helpers (Zero UI / Browser dependencies).
 */

/**
 * Formats a score represented either as a unit ratio in [0.0, 1.0]
 * or as a 0..100 percentage score in (1.0, 100.0], preventing double-scaling bugs.
 */
export function formatPercentage(value: number, decimals = 1): string {
  if (!Number.isFinite(value) || value <= 0) {
    return `${(0).toFixed(decimals)}%`;
  }
  const normalized = value <= 1.0 ? value * 100 : value;
  const bounded = Math.min(100, Math.max(0, normalized));
  return `${bounded.toFixed(decimals)}%`;
}

/**
 * Truncates long UUIDs for high-density executive dashboards while retaining head/tail entropy.
 */
export function formatShortUuid(id: string, head = 8, tail = 4): string {
  const trimmed = id.trim();
  if (trimmed.length <= head + tail + 3) {
    return trimmed;
  }
  return `${trimmed.slice(0, head)}…${trimmed.slice(-tail)}`;
}

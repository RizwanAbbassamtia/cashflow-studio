/** Short, readable numbers for the candidates table. */

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

/** 1234567 -> "1.2M"; 5400 -> "5.4K" */
export function formatViews(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return compact.format(value);
}

export function formatWhole(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return whole.format(value);
}

/** 0 -> "today", 3 -> "3 days", 20 -> "3 wk", 100 -> "3 mo", 500 -> "1.4 yr" */
export function formatAge(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(days)) return "-";
  const d = Math.max(0, Math.round(days));
  if (d === 0) return "today";
  if (d === 1) return "1 day";
  if (d < 14) return `${d} days`;
  if (d < 60) return `${Math.round(d / 7)} wk`;
  if (d < 365) return `${Math.round(d / 30)} mo`;
  const years = d / 365;
  return `${years < 10 ? years.toFixed(1).replace(/\.0$/, "") : Math.round(years)} yr`;
}

/** 754 -> "12:34"; 3725 -> "1:02:05" */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** 7.25 -> "7.3x"; 12 -> "12x" */
export function formatMultiple(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  if (value >= 10) return `${Math.round(value)}x`;
  return `${value.toFixed(1)}x`;
}

/** 0.83 -> "83%" */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

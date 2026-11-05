export const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

export const wholePercent = (value: number) => `${Math.round(value * 100)}%`;

export const count = (value: number) => value.toLocaleString("en-IN");

const pad = (value: number) => String(value).padStart(2, "0");

export function date(iso: string) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function stamp(iso: string) {
  const d = new Date(iso);
  return `${date(iso)} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export const rank = (value: number) => `#${pad(value)}`;

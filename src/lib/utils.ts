import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names, with Tailwind conflict resolution. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** "3 minutes ago" - short, human, and stable enough to render in a list without re-rendering. */
export function relativeTime(value: string | number | Date | undefined | null): string {
  if (!value) return "never";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const units: Array<[number, string]> = [
    [60, "second"],
    [3600, "minute"],
    [86400, "hour"],
    [604800, "day"],
    [2592000, "week"],
    [31536000, "month"],
  ];
  let previous = 1;
  for (const [limit, name] of units) {
    if (seconds < limit) {
      const count = Math.round(seconds / previous);
      return `${count} ${name}${count === 1 ? "" : "s"} ago`;
    }
    previous = limit;
  }
  const years = Math.round(seconds / 31536000);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}

/** "14:32" - the Live Terminal reads better with wall-clock times than with timestamps. */
export function clockTime(value: string | number | Date | undefined | null): string {
  if (!value) return "--:--";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatBytes(bytes: number | undefined | null): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

export function formatDuration(seconds: number | undefined | null): string {
  if (!seconds || seconds < 0) return "0s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/** Turn an agent or task id into something a person reads: "image_generator" -> "Image Generator". */
export function humanise(value: string | undefined | null): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/** A file name from a project-relative path, for screenshot and artifact lists. */
export function baseName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

export function percent(value: number | undefined | null, digits = 0): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "-";
  return `${value.toFixed(digits)}%`;
}

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

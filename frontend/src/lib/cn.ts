import { clsx, type ClassValue } from "clsx";

/** Join class names, dropping falsy values. */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}

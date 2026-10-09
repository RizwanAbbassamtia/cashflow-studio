/**
 * Typed fetch wrapper for the CashCow Studio backend (FastAPI, same origin, prefix /api).
 *
 * This is the only file that deals with untyped JSON: everything that leaves it is typed.
 * Errors are turned into ApiError with a plain-English message and, for 422 responses,
 * a list of field errors mapped from Pydantic `loc` paths to form paths ("channel.name").
 */

export interface FieldError {
  /** dotted path, e.g. "channel.name" or "competitors.0.url" */
  path: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: FieldError[];
  readonly detail: unknown;

  constructor(status: number, message: string, fieldErrors: FieldError[] = [], detail: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.detail = detail;
  }

  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

const NETWORK_MESSAGE = "Cannot reach the CashCow Studio server. Start it and try again.";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Turn a Pydantic `loc` (["body", "channel", "name"]) into a form path ("channel.name"). */
function locToPath(loc: unknown): string {
  if (!Array.isArray(loc)) return "";
  const parts = loc.filter((p): p is string | number => typeof p === "string" || typeof p === "number");
  if (parts[0] === "body" || parts[0] === "query" || parts[0] === "path") parts.shift();
  return parts.map(String).join(".");
}

function tidyMessage(message: string): string {
  // Pydantic prefixes some messages with "Value error, "; the user does not need that.
  return message.replace(/^Value error,\s*/i, "").replace(/^Assertion failed,\s*/i, "");
}

/** Build an ApiError from a FastAPI error body (`{detail: string | [{loc, msg}]}`). */
export function errorFromBody(status: number, body: unknown, fallback: string): ApiError {
  if (isRecord(body) && "detail" in body) {
    const detail = body.detail;
    if (typeof detail === "string") {
      return new ApiError(status, detail, [], detail);
    }
    if (Array.isArray(detail)) {
      const fieldErrors: FieldError[] = [];
      for (const item of detail) {
        if (!isRecord(item)) continue;
        const path = locToPath(item.loc);
        const msg = typeof item.msg === "string" ? tidyMessage(item.msg) : "Invalid value";
        fieldErrors.push({ path, message: msg });
      }
      const message =
        fieldErrors.length > 0
          ? fieldErrors.length === 1 && !fieldErrors[0]!.path
            ? fieldErrors[0]!.message
            : "Some fields need attention. Check the highlighted fields."
          : fallback;
      return new ApiError(status, message, fieldErrors, detail);
    }
    if (isRecord(detail) && typeof detail.message === "string") {
      return new ApiError(status, detail.message, [], detail);
    }
  }
  if (isRecord(body) && typeof body.message === "string") {
    return new ApiError(status, body.message, [], body);
  }
  return new ApiError(status, fallback, [], body);
}

function statusFallback(status: number): string {
  switch (status) {
    case 404:
      return "Not found.";
    case 409:
      return "Something with that name already exists.";
    case 422:
      return "Some fields need attention.";
    case 500:
      return "The server hit an unexpected problem.";
    default:
      return `The server answered with an error (${status}).`;
  }
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function run<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiError(0, NETWORK_MESSAGE);
  }
  if (!response.ok) {
    const body = await parseBody(response);
    throw errorFromBody(response.status, body, statusFallback(response.status));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const body = await parseBody(response);
  return body as T;
}

const jsonHeaders = { "Content-Type": "application/json", Accept: "application/json" };

export const api = {
  get<T>(path: string): Promise<T> {
    return run<T>(path, { method: "GET", headers: { Accept: "application/json" } });
  },
  post<T>(path: string, body: unknown): Promise<T> {
    return run<T>(path, { method: "POST", headers: jsonHeaders, body: JSON.stringify(body) });
  },
  put<T>(path: string, body: unknown): Promise<T> {
    return run<T>(path, { method: "PUT", headers: jsonHeaders, body: JSON.stringify(body) });
  },
  delete<T = void>(path: string): Promise<T> {
    return run<T>(path, { method: "DELETE", headers: { Accept: "application/json" } });
  },
  /** multipart/form-data upload; the browser sets the boundary header itself */
  upload<T>(path: string, form: FormData): Promise<T> {
    return run<T>(path, { method: "POST", headers: { Accept: "application/json" }, body: form });
  },
};

/** Plain-English message for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message || "Something went wrong.";
  return "Something went wrong.";
}

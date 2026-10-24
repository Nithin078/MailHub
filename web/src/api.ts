export type Token = string;

let token = localStorage.getItem("ia_token");
const listeners = new Set<() => void>();

export function authToken() {
  return token;
}

export function saveToken(value: string | null) {
  token = value;
  if (value) localStorage.setItem("ia_token", value);
  else localStorage.removeItem("ia_token");
  listeners.forEach((listener) => listener());
}

export function onAuth(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    if (response.status === 401) saveToken(null);
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : "Request failed";
    throw new ApiError(response.status, message);
  }
  return data as T;
}

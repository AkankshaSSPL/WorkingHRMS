import { useAuthStore } from "@/stores/authStore";

// No fallback here on purpose. If VITE_API_BASE_URL is unset, the app should
// fail loudly at startup instead of silently talking to the wrong backend
// port (previously defaulted to :8001, which doesn't match the documented
// backend port of :8000 and caused confusing "API request failed" errors).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

if (!API_BASE_URL) {
  throw new Error(
    "VITE_API_BASE_URL is not set. Copy Frontend/.env.example to Frontend/.env and set VITE_API_BASE_URL " +
      "to the backend's API base (e.g. http://127.0.0.1:8000/api/v1).",
  );
}

export async function apiGet<T>(path: string): Promise<T> {
  return apiRequest<T>(path, { method: "GET" });
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

export async function apiDelete<T>(path: string): Promise<T> {
  return apiRequest<T>(path, { method: "DELETE" });
}

async function apiRequest<T>(path: string, init: RequestInit): Promise<T> {
  const token = useAuthStore.getState().accessToken;
  const headers = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init.body ? { "Content-Type": "application/json" } : {}),
  };
  let response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });

  if (response.status === 401) {
    const refreshedToken = await useAuthStore.getState().refreshSession();
    if (refreshedToken) {
      response = await fetch(`${API_BASE_URL}${path}`, {
        ...init,
        headers: { ...headers, Authorization: `Bearer ${refreshedToken}` },
      });
    }
  }

  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}
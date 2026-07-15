import type { AuthUser, TokenResponse } from "@/types/auth";

// No fallback here on purpose — same reasoning as services/api.ts. This file
// has its own independent fetch logic and previously duplicated the same
// wrong :8001 default, so it's fixed the same way here.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

if (!API_BASE_URL) {
  throw new Error(
    "VITE_API_BASE_URL is not set. Copy Frontend/.env.example to Frontend/.env and set VITE_API_BASE_URL " +
      "to the backend's API base (e.g. http://127.0.0.1:8000/api/v1).",
  );
}

export async function loginRequest(email: string, password: string): Promise<TokenResponse> {
  const body = new URLSearchParams();
  body.set("username", email);
  body.set("password", password);

  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });

  if (!response.ok) {
    throw new Error("Invalid email or password");
  }

  return response.json() as Promise<TokenResponse>;
}

export async function refreshRequest(refreshToken: string): Promise<TokenResponse> {
  const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!response.ok) {
    throw new Error("Refresh token expired");
  }

  return response.json() as Promise<TokenResponse>;
}

export async function logoutRequest(refreshToken: string | null): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

export async function meRequest(accessToken: string): Promise<AuthUser> {
  const response = await fetch(`${API_BASE_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });

  if (!response.ok) {
    throw new Error("Unable to load current user");
  }

  return response.json() as Promise<AuthUser>;
}
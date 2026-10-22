// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { SESSION_KEY } from "./session";

vi.mock("./BackOfficeLogin", () => ({ default: () => <div>Connexion de test</div> }));
vi.mock("./FaceVerification", () => ({ default: () => null }));
const self = { id: "admin-1", email: "admin@example.com", first_name: "Reda", last_name: "Khouya", role: "admin" };
const other = { id: "user-2", email: "user@example.com", first_name: "Camille", last_name: "Martin", role: "user" };
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
let userList;
function savedSession() {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token: "stored-token", faceExpiresAt: Date.now() / 1000 + 1800, page: "dashboard", dashboardTab: "users" }));
}
beforeEach(() => {
  sessionStorage.clear();
  userList = [self, other];
  vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
    if (url.endsWith("/auth/me")) return response(self);
    if (options.method === "DELETE") { userList = [self]; return response({}, 204); }
    if (url.endsWith("/admin/users")) return response(userList);
    if (url.endsWith("/admin/stats")) return response({ total_transactions: 0, successful_transactions: 0, declined_transactions: 0, total_successful_amount: 0 });
    if (url.endsWith("/accounts/me")) return response({ balance: "500.00" });
    return response([]);
  }));
});
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("restores authenticated page and selected admin tab after a remount", async () => {
  savedSession();
  const view = render(<App />);
  await screen.findByRole("tab", { name: "Utilisateurs" });
  expect(screen.getByRole("tab", { name: "Utilisateurs" }).getAttribute("aria-selected")).toBe("true");
  view.unmount();
  render(<App />);
  await screen.findByText("user@example.com");
  expect(screen.queryByText("Connexion de test")).toBeNull();
  expect(screen.getByRole("tab", { name: "Utilisateurs" }).getAttribute("aria-selected")).toBe("true");
});

it("rejects expired and server-revoked sessions", async () => {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({token: "old", faceExpiresAt: 1}));
  const view = render(<App />);
  expect(screen.getByText("Connexion de test")).toBeTruthy();
  view.unmount();
  savedSession();
  fetch.mockResolvedValue(response({detail: "Invalid token"}, 401));
  render(<App />);
  await screen.findByText("Connexion de test");
  expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
});

it("keeps a valid saved session during a network failure instead of sending to login", async () => {
  savedSession();
  fetch.mockRejectedValue(new Error("offline"));
  render(<App />);
  await screen.findByRole("button", { name: "Reessayer" });
  expect(screen.queryByText("Connexion de test")).toBeNull();
  expect(JSON.parse(sessionStorage.getItem(SESSION_KEY)).token).toBe("stored-token");
});

it("confirms deletion and removes the user without exposing self-deletion", async () => {
  savedSession();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<App />);
  const button = await screen.findByRole("button", { name: "Supprimer" });
  await act(async () => fireEvent.click(button));
  expect(fetch.mock.calls.some(([,options]) => options?.method === "DELETE")).toBe(false);
  window.confirm.mockReturnValue(true);
  await act(async () => fireEvent.click(button));
  await waitFor(() => expect(screen.queryByText("user@example.com")).toBeNull());
  expect(fetch.mock.calls.some(([url,options]) => url.endsWith("/admin/users/user-2") && options?.method === "DELETE")).toBe(true);
  expect(screen.queryByRole("button", { name: "Supprimer" })).toBeNull();
});

it("clears the saved session on logout", async () => {
  savedSession();
  render(<App />);
  const button = await screen.findByRole("button", {name: "Se déconnecter"});
  fireEvent.click(button);
  expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
});

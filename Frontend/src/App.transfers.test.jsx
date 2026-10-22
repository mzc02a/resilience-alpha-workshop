// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";

vi.mock("./BackOfficeLogin", () => ({ default: ({ onAuthenticated }) => (
  <button onClick={() => onAuthenticated({
    access_token: "test-token", face_verified_until: Math.floor(Date.now() / 1000) + 1800,
  })}>Connexion de test</button>
) }));
vi.mock("./FaceVerification", () => ({ default: () => null }));

const SELF = "00000000-0000-0000-0000-000000000001";
const OTHER = "00000000-0000-0000-0000-000000000002";
const IBAN = "FR1420041010050500013M02606";
const RECIPIENT_IBAN = "FR7630006000011234567890189";
const response = (data, ok = true) => ({ ok, json: async () => data });
let balance, post;

beforeEach(() => {
  sessionStorage.clear();
  balance = "500.00";
  let next = 0;
  vi.stubGlobal("crypto", { randomUUID: () => "test-transfer-key-" + (++next) });
  post = vi.fn(async () => {
    balance = "474.50";
    return response({ id: "transfer-1", status: "SUCCESS", amount: "25.50", recipient_user_id: OTHER });
  });
  vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
    if (url.endsWith("/auth/me")) return response({ id: SELF, email: "self@example.com", role: "user", iban: IBAN });
    if (url.endsWith("/accounts/me")) return response({ user_id: SELF, iban: IBAN, balance });
    if (url.endsWith("/beneficiaries")) return response([{ user_id: OTHER, email: "recipient@example.com", first_name: "Camille", last_name: "Martin", iban: RECIPIENT_IBAN }]);
    if (url.endsWith("/payments") && options.method === "POST") return post(options);
    if (url.endsWith("/payments")) return response([]);
    throw new Error("Unexpected request: " + url);
  }));
});
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

async function login() {
  render(<App />);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Connexion de test" })));
  await screen.findByText(/500,00/);
}
function prepare() {
  fireEvent.change(screen.getByLabelText("Destinataire"), { target: { value: RECIPIENT_IBAN } });
  fireEvent.change(screen.getByLabelText("Montant (€)"), { target: { value: "25.50" } });
}

it("shows the real balance and requires a beneficiary before enabling a transfer", async () => {
  await login();
  expect(screen.getByText(/FR14 2004 1010/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Envoyer le virement" }).disabled).toBe(true);
  expect(screen.getByRole("option", {name: "Camille Martin"})).toBeTruthy();
  prepare();
  expect(screen.getByRole("button", { name: "Envoyer le virement" }).disabled).toBe(false);
});

it("sends the selected person's IBAN and refreshes the balance after success", async () => {
  await login();
  prepare();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Envoyer le virement" })));
  expect(JSON.parse(post.mock.calls[0][0].body)).toEqual({
    amount: "25.50", currency: "EUR", recipient_iban: RECIPIENT_IBAN,
  });
  expect(post.mock.calls[0][0].headers["Idempotency-Key"]).toBeTruthy();
  expect(screen.getByText(/474,50/)).toBeTruthy();
});

it("reuses the same idempotency key after an uncertain network failure", async () => {
  post.mockRejectedValueOnce(new TypeError("network lost"));
  await login();
  prepare();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Envoyer le virement" })));
  expect(screen.getByText(/500,00/)).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Envoyer le virement" })));
  expect(post.mock.calls[0][0].headers["Idempotency-Key"]).toBe(post.mock.calls[1][0].headers["Idempotency-Key"]);
});

it("blocks duplicate submissions while a transfer is pending", async () => {
  let finish;
  post.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await login();
  prepare();
  const form = screen.getByRole("button", { name: "Envoyer le virement" }).closest("form");
  fireEvent.submit(form);
  fireEvent.submit(form);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  await act(async () => finish(response({ id: "transfer-1", status: "SUCCESS", amount: "25.50" })));
});

it("keeps the balance unchanged and explains an insufficient-funds refusal", async () => {
  post.mockImplementationOnce(async () => response({ status: "DECLINED", amount: "25.50", failure_reason: "INSUFFICIENT_FUNDS" }));
  await login();
  prepare();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Envoyer le virement" })));
  expect(screen.getByText(/solde insuffisant/)).toBeTruthy();
  expect(screen.getByText(/500,00/)).toBeTruthy();
});

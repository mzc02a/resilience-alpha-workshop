// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BackOfficeLogin from "./BackOfficeLogin";

vi.mock("./FaceVerification", () => ({
  default: ({ onVerified, onRejected, captureEnabled }) => (
    <div aria-label="Caméra active">
      <button disabled={!captureEnabled} onClick={() => onVerified({
        face_challenge: "proof", expires_at: Date.now() / 1000 + 120,
      })}>Reconnaître le visage</button>
      <button disabled={!captureEnabled} onClick={() => onRejected({
        message: "Visage non reconnu", reset_identification: true,
        retry_at: Date.now() / 1000 + 30, camera_off_at: Date.now() / 1000 + 30,
        led_state: "red_solid", led_until: Date.now() / 1000 + 30,
      })}>Refuser le visage</button>
    </div>
  ),
}));

const response = (data, ok = true) => ({ ok, json: async () => data });
let onAuthenticated;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-21T10:00:00Z"));
  onAuthenticated = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    challenge_token: "proof", expires_at: Date.now() / 1000 + 120,
  })));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function show() {
  render(<BackOfficeLogin api="http://localhost:8001" onAuthenticated={onAuthenticated} />);
}

async function identify() {
  fireEvent.change(screen.getByLabelText("Identifiant (adresse e-mail)"), {
    target: { value: "admin@example.test" },
  });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Continuer avec la caméra" })));
}

describe("identifier -> face -> password", () => {
  it("does not display a password field until the face is recognized", async () => {
    show();
    expect(screen.queryByLabelText("Mot de passe")).toBeNull();
    await identify();
    expect(screen.getByLabelText("Caméra active")).toBeTruthy();
    expect(screen.queryByLabelText("Mot de passe")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reconnaître le visage" }));
    expect(screen.getByLabelText("Mot de passe")).toBeTruthy();
    expect(screen.queryByLabelText("Caméra active")).toBeNull();
  });

  it("clears identification, holds the camera for 30 seconds, then permits retry", async () => {
    show();
    await identify();
    fireEvent.click(screen.getByRole("button", { name: "Refuser le visage" }));
    expect(screen.queryByLabelText("Mot de passe")).toBeNull();
    expect(screen.getByLabelText("Caméra active")).toBeTruthy();
    await act(async () => vi.advanceTimersByTime(30000));
    expect(screen.queryByLabelText("Caméra active")).toBeNull();
    expect(screen.getByLabelText("Identifiant (adresse e-mail)").value).toBe("");
    expect(screen.getByRole("button", { name: "Continuer avec la caméra" }).disabled).toBe(false);
  });

  it("resets the entire identification after a wrong password and waits for ten red flashes", async () => {
    show();
    await identify();
    fireEvent.click(screen.getByRole("button", { name: "Reconnaître le visage" }));
    fetch.mockResolvedValueOnce(response({ detail: {
      message: "Mot de passe incorrect", reset_identification: true,
      retry_at: Date.now() / 1000 + 10, led_until: Date.now() / 1000 + 10,
      led_state: "red_blink",
    } }, false));
    fireEvent.change(screen.getByLabelText("Mot de passe"), { target: { value: "wrong" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Se connecter" })));
    expect(screen.queryByLabelText("Mot de passe")).toBeNull();
    expect(screen.getByLabelText("Identifiant (adresse e-mail)").value).toBe("");
    expect(screen.getByRole("button", { name: "Continuer avec la caméra" }).disabled).toBe(true);
    expect(onAuthenticated).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(10000));
    expect(screen.getByRole("button", { name: "Continuer avec la caméra" }).disabled).toBe(false);
  });

  it("passes the face proof with the password and opens the back-office only after 30 seconds", async () => {
    show();
    await identify();
    fireEvent.click(screen.getByRole("button", { name: "Reconnaître le visage" }));
    const session = { access_token: "session", face_verified_until: Date.now() / 1000 + 1830,
                      backoffice_ready_at: Date.now() / 1000 + 30 };
    fetch.mockResolvedValueOnce(response(session));
    fireEvent.change(screen.getByLabelText("Mot de passe"), { target: { value: "correct" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Se connecter" })));
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
      email: "admin@example.test", password: "correct", face_challenge: "proof",
    });
    expect(onAuthenticated).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(29999));
    expect(onAuthenticated).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(1));
    expect(onAuthenticated).toHaveBeenCalledTimes(1);
    expect(onAuthenticated).toHaveBeenCalledWith(session);
  });

  it("honors the five-minute server lockout even after mounting a new login screen", async () => {
    fetch.mockResolvedValueOnce(response({ detail: {
      message: "Identification bloquée", reset_identification: true,
      retry_at: Date.now() / 1000 + 300, led_state: "off",
    } }, false));
    show();
    await identify();
    expect(screen.getByRole("button", { name: "Continuer avec la caméra" }).disabled).toBe(true);
    await act(async () => vi.advanceTimersByTime(299000));
    expect(screen.getByRole("button", { name: "Continuer avec la caméra" }).disabled).toBe(true);
    await act(async () => vi.advanceTimersByTime(1000));
    expect(screen.getByRole("button", { name: "Continuer avec la caméra" }).disabled).toBe(false);
  });
});

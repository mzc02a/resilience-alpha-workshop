// @vitest-environment jsdom
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import App from "./App";

vi.mock("./BackOfficeLogin", () => ({ default: () => <div>Connexion avec visage</div> }));
vi.mock("./FaceVerification", () => ({
  default: ({ onCapture }) => (
    <button type="button" onClick={() => onCapture(new Blob(["frame"], { type: "image/jpeg" }))}>
      Capturer mon visage
    </button>
  ),
}));

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

function openRegistration() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Créer un compte" }));
  fireEvent.change(screen.getByLabelText("Prénom"), { target: { value: "Camille" } });
  fireEvent.change(screen.getByLabelText("Nom"), { target: { value: "Martin" } });
  fireEvent.change(screen.getByLabelText("Adresse e-mail"), { target: { value: "new@example.com" } });
  fireEvent.change(screen.getByLabelText("Mot de passe"), { target: { value: "correct-password" } });
  fireEvent.change(screen.getByLabelText("Confirmer le mot de passe"), { target: { value: "correct-password" } });
}

it("requires a camera capture before submitting registration and permits retaking", async () => {
  openRegistration();
  expect(screen.getByRole("button", { name: "S'inscrire" }).disabled).toBe(true);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Capturer mon visage" })));
  expect(screen.getByRole("button", { name: "S'inscrire" }).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Reprendre la photo" }));
  expect(screen.getByRole("button", { name: "S'inscrire" }).disabled).toBe(true);
});

it("submits the camera frame with credentials as multipart and returns to facial login", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ role: "user" }) }));
  openRegistration();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Capturer mon visage" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "S'inscrire" })));
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe("http://localhost:8001/auth/register");
  expect(options.body.get("first_name")).toBe("Camille");
  expect(options.body.get("last_name")).toBe("Martin");
  expect(options.body.get("email")).toBe("new@example.com");
  expect(options.body.get("password")).toBe("correct-password");
  expect(options.body.get("image").type).toBe("image/jpeg");
  expect(options.body.get("role")).toBeNull();
  expect(screen.getByText("Connexion avec visage")).toBeTruthy();
});

it("keeps the registration form available when the server refuses the face", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false, json: async () => ({ detail: "Un seul visage doit etre visible" }),
  }));
  openRegistration();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Capturer mon visage" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "S'inscrire" })));
  expect(screen.getByText("Un seul visage doit etre visible")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Reprendre la photo" })).toBeTruthy();
});

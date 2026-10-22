// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import FaceVerification from "./FaceVerification";

let stop, stream, getUserMedia, onVerified;
const response = (data, ok = true) => ({ ok, json: async () => data });
const recognized = { face_challenge: "proof", expires_at: 123, led_state: "green_blink" };

beforeEach(() => {
  stop = vi.fn();
  stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  const track = { stop, onended: null };
  getUserMedia = vi.fn().mockResolvedValue(stream);
  onVerified = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(recognized)));
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
    getUserMedia,
    enumerateDevices: vi.fn().mockResolvedValue([
      { kind: "videoinput", deviceId: "usb", label: "USB Camera" },
      { kind: "videoinput", deviceId: "builtin", label: "Built-in Camera" },
    ]),
  } });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", { configurable: true, get: () => 640 });
  Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", { configurable: true, get: () => 480 });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage: vi.fn() });
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback =>
    callback(new Blob(["jpeg-frame"], { type: "image/jpeg" })));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function show() {
  return render(<FaceVerification api="http://localhost:8001" challengeToken="test-challenge" onVerified={onVerified} onRejected={vi.fn()} />);
}

async function opened() {
  await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
  const video = screen.getByLabelText("Aperçu de la caméra");
  await waitFor(() => expect(video.srcObject).toBe(stream));
  fireEvent.loadedData(video);
}

describe("camera verification", () => {
  it("opens automatically without audio, offers no file input, and stops on close", async () => {
    const { container } = show();
    await opened();
    expect(getUserMedia.mock.calls[0][0].audio).toBe(false);
    expect(container.querySelector('input[type="file"]')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Fermer la caméra" }));
    await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Ouvrir la caméra" })).toBeTruthy();
  });

  it("does not open a camera without an identification challenge", async () => {
    render(<FaceVerification api="http://localhost:8001" onVerified={onVerified} />);
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("explains permission refusal and lets the user retry", async () => {
    getUserMedia.mockRejectedValueOnce(Object.assign(new Error(), { name: "NotAllowedError" }));
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("Accès caméra refusé");
    fireEvent.click(screen.getByRole("button", { name: "Ouvrir la caméra" }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
  });

  it("sends a JPEG capture and releases the camera after success and unmount", async () => {
    const { unmount } = show();
    await opened();
    fetch.mockResolvedValueOnce(response({ face_challenge: "proof", expires_at: 123 }));
    fireEvent.click(screen.getByRole("button", { name: "Vérifier mon visage" }));
    await waitFor(() => expect(onVerified).toHaveBeenCalledWith({
      face_challenge: "proof", expires_at: 123,
    }));
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("http://localhost:8001/auth/face/verify");
    expect(options.headers).toBeUndefined();
    expect(options.body.get("challenge_token")).toBe("test-challenge");
    expect(options.body.get("image").type).toBe("image/jpeg");
    expect(options.body.get("image").name).toBe("camera.jpg");
    unmount();
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it("keeps admin access locked when the server refuses the face", async () => {
    show();
    await opened();
    fetch.mockResolvedValueOnce(response({ detail: "Visage non reconnu" }, false));
    fireEvent.click(screen.getByRole("button", { name: "Vérifier mon visage" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Visage non reconnu");
    expect(onVerified).not.toHaveBeenCalled();
  });

  it("releases the previous camera before switching to USB", async () => {
    show();
    await opened();
    const select = await screen.findByLabelText("Caméra à utiliser");
    fireEvent.change(select, { target: { value: "usb" } });
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
    expect(stop).toHaveBeenCalledTimes(1);
    expect(getUserMedia.mock.calls[1][0].video.deviceId).toEqual({ exact: "usb" });
  });

  it("releases a stream if permission is granted after leaving the screen", async () => {
    let resolve;
    getUserMedia.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const { unmount } = show();
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
    unmount();
    resolve(stream);
    await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
  });
});


it("captures a registration photo locally without calling the verification API", async () => {
  const onCapture = vi.fn();
  render(<FaceVerification onCapture={onCapture} />);
  await opened();
  fireEvent.click(screen.getByRole("button", { name: "Capturer mon visage" }));
  await waitFor(() => expect(onCapture).toHaveBeenCalledTimes(1));
  expect(onCapture.mock.calls[0][0].type).toBe("image/jpeg");
  expect(fetch).not.toHaveBeenCalled();
});

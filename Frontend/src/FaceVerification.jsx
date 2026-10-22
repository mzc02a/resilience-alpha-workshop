import React, { useEffect, useRef, useState } from "react";

const cameraErrors = {
  NotAllowedError: "Accès caméra refusé. Autorise la caméra pour ce site dans ton navigateur, puis réessaie.",
  NotFoundError: "Aucune caméra détectée. Branche ta caméra USB puis réessaie.",
  NotReadableError: "Caméra indisponible. Ferme les autres applications qui l'utilisent puis réessaie.",
  OverconstrainedError: "Cette caméra n'est plus disponible. Choisis une autre caméra.",
};

export default function FaceVerification({ api, challengeToken, onVerified, onRejected, captureEnabled = true, onCapture }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [cameraRequested, setCameraRequested] = useState(true);
  const [ready, setReady] = useState(false);
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const videoRef = useRef(null);
  const mountedRef = useRef(true);
  const canVerify = Boolean(challengeToken || onCapture);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!cameraRequested || !canVerify) return;
    let cancelled = false;
    let stream = null;
    const video = videoRef.current;
    setReady(false);
    setMessage("");

    async function openCamera() {
      try {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
          throw new Error("Ouvre l'application sur http://localhost:5173 ou en HTTPS pour utiliser la caméra.");
        }
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: {
            ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: "user" }),
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
        });
        if (cancelled) {
          stream.getTracks().forEach(track => track.stop());
          return;
        }
        stream.getVideoTracks().forEach(track => {
          track.onended = () => {
            if (!cancelled) {
              setMessage("La caméra a été déconnectée. Rebranche-la puis réessaie.");
              setCameraRequested(false);
            }
          };
        });
        video.srcObject = stream;
        await video.play();
        if (cancelled) return;
        // Device labels become available once the user has granted camera permission.
        try {
          const available = await navigator.mediaDevices.enumerateDevices();
          if (!cancelled) setDevices(available.filter(device => device.kind === "videoinput"));
        } catch {
          // A working default camera remains usable if device enumeration is unavailable.
        }
      } catch (error) {
        if (!cancelled) {
          setMessage(cameraErrors[error.name] || error.message || "Impossible d'ouvrir la caméra.");
          setCameraRequested(false);
        }
      }
    }

    openCamera();
    return () => {
      cancelled = true;
      if (stream) stream.getTracks().forEach(track => {
        track.onended = null;
        track.stop();
      });
      if (video) video.srcObject = null;
    };
  }, [cameraRequested, canVerify, deviceId]);

  async function verify() {
    const video = videoRef.current;
    if (!captureEnabled || !ready || busy || !video?.videoWidth || !video?.videoHeight) return;
    setBusy(true);
    setMessage("");
    try {
      const canvas = document.createElement("canvas");
      const scale = Math.min(1, 1280 / Math.max(video.videoWidth, video.videoHeight));
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Impossible de capturer l'image.");
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const image = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.9));
      if (!image) throw new Error("La capture a échoué. Réessaie.");
      if (!mountedRef.current) return;
      if (onCapture) {
        await onCapture(image);
        return;
      }
      const body = new FormData();
      body.append("image", image, "camera.jpg");
      body.append("challenge_token", challengeToken);
      const response = await fetch(`${api}/auth/face/verify`, {
        method: "POST",
        body,
      });
      const data = await response.json();
      if (!response.ok) {
        if (data.detail?.reset_identification) {
          if (mountedRef.current) onRejected(data.detail);
          return;
        }
        throw new Error(typeof data.detail === "string" ? data.detail : "Vérification refusée");
      }
      if (mountedRef.current) await onVerified(data);
    } catch (error) {
      if (mountedRef.current) setMessage(error.message);
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }

  return (
    <section className="face-capture" aria-labelledby="face-title">
      <div className="panel-heading">{onCapture && <span className="step-number">02</span>}<h2 id="face-title">{onCapture ? "Votre visage" : "Reconnaissance faciale"}</h2></div>
      <p className="field-hint">Visage de face, dans le cadre.</p>
      {canVerify && (
        <>
          {devices.length > 1 && (
            <>
              <label htmlFor="face-camera">Caméra à utiliser</label>
              <select id="face-camera" value={deviceId} disabled={busy || !captureEnabled}
                onChange={event => {
                  setDeviceId(event.target.value);
                  setReady(false);
                  setCameraRequested(true);
                }}>
                <option value="">Caméra par défaut</option>
                {devices.map((device, index) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label || `Caméra ${index + 1}`}
                  </option>
                ))}
              </select>
            </>
          )}
          {cameraRequested && (
            <div className="face-camera-preview">
              <video ref={videoRef} autoPlay muted playsInline aria-label="Aperçu de la caméra"
                onLoadedData={() => setReady(true)} />
              {ready && <><div className="face-guide" aria-hidden="true" /><span className="preview-label">Camera active</span></>}
              {!ready && <p className="camera-loading" role="status"><span className="spinner" aria-hidden="true" />Ouverture de la camera...</p>}
            </div>
          )}
          <div className="face-camera-actions">
            {cameraRequested ? (
              <>
                <button type="button" disabled={!ready || busy || !captureEnabled} onClick={verify}>
                  {busy ? "Traitement..." : onCapture ? "Capturer mon visage" : "Vérifier mon visage"}
                </button>
                <button type="button" className="secondary" disabled={busy || !captureEnabled}
                  onClick={() => { setCameraRequested(false); setReady(false); }}>
                  Fermer la caméra
                </button>
              </>
            ) : (
              <button type="button" onClick={() => setCameraRequested(true)}>Ouvrir la caméra</button>
            )}
          </div>
        </>
      )}
      {message && <p className="inline-alert" role="alert">{message}</p>}
    </section>
  );
}

import React, { useEffect, useRef, useState } from "react";
import FaceVerification from "./FaceVerification";

async function request(url, data) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    const error = new Error(typeof detail === "string" ? detail : detail?.message || "Identification refusée");
    error.flow = detail && typeof detail === "object" ? detail : null;
    throw error;
  }
  return result;
}

export default function BackOfficeLogin({ api, onAuthenticated }) {
  const deliveredRef = useRef(false);
  const mountedRef = useRef(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [step, setStep] = useState("identify");
  const [challenge, setChallenge] = useState(null);
  const [proof, setProof] = useState(null);
  const [pendingSession, setPendingSession] = useState(null);
  const [retryAt, setRetryAt] = useState(0);
  const [cameraOffAt, setCameraOffAt] = useState(0);
  const [led, setLed] = useState("off");
  const [ledUntil, setLedUntil] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 250);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (step === "face-failed" && now >= cameraOffAt) {
      setChallenge(null);
      setStep("identify");
    }
    if (ledUntil && now >= ledUntil) {
      setLed("off");
      setLedUntil(0);
    }
    if ((step === "password" && proof?.expires_at <= now)
        || (step === "face" && challenge?.expires_at <= now)) {
      setEmail("");
      setPassword("");
      setChallenge(null);
      setProof(null);
      setLed("off");
      setStep("identify");
      setMessage("Identification expirée. Recommence avec ton identifiant.");
    }
  }, [now, step, cameraOffAt, ledUntil, proof, challenge]);

  useEffect(() => {
    if (!pendingSession || deliveredRef.current) return;
    const timer = setTimeout(async () => {
      deliveredRef.current = true;
      setLed("off");
      try {
        await onAuthenticated(pendingSession);
      } catch (error) {
        if (mountedRef.current) {
          setPendingSession(null);
          setStep("identify");
          setEmail("");
          setMessage(error.message || "Connexion indisponible. Recommence l'identification.");
        }
      }
    }, Math.max(0, pendingSession.backoffice_ready_at * 1000 - Date.now()));
    return () => clearTimeout(timer);
  }, [pendingSession, onAuthenticated]);

  function resetIdentification(detail) {
    setEmail("");
    setPassword("");
    setProof(null);
    setRetryAt(detail.retry_at || 0);
    setCameraOffAt(detail.camera_off_at || 0);
    setLed(detail.led_state || "off");
    setLedUntil(detail.led_until || 0);
    setMessage(detail.message || "Recommence l'identification.");
    if (detail.camera_off_at > Date.now() / 1000) {
      setStep("face-failed");
    } else {
      setChallenge(null);
      setStep("identify");
    }
  }

  async function identify(event) {
    event.preventDefault();
    if (busy || retryAt > Date.now() / 1000) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await request(`${api}/auth/backoffice/identify`, { email });
      setChallenge(result);
      setProof(null);
      setStep("face");
      setLed("off");
    } catch (error) {
      if (error.flow?.reset_identification) resetIdentification(error.flow);
      else setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function login(event) {
    event.preventDefault();
    if (busy || !proof) return;
    setBusy(true);
    setMessage("");
    try {
      const session = await request(`${api}/auth/login`, {
        email, password, face_challenge: proof.face_challenge,
      });
      setPassword("");
      setProof(null);
      setChallenge(null);
      setLed("green_solid");
      setLedUntil(session.backoffice_ready_at);
      deliveredRef.current = false;
      setPendingSession(session);
      setStep("success");
    } catch (error) {
      if (error.flow?.reset_identification) resetIdentification(error.flow);
      else setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  const wait = Math.max(0, Math.ceil(retryAt - now));
  const ledLabels = {
    off: "",
    green_blink: "Visage reconnu",
    green_solid: "Connexion validée",
    red_solid: "Visage non reconnu",
    red_blink: "Mot de passe incorrect",
  };

  return (
    <section className="login-card" aria-labelledby="backoffice-title">
      <h1 id="backoffice-title">Connexion</h1>
      <ol className="auth-steps" aria-label="Étapes de connexion">
        {["Identifiant", "Visage", "Mot de passe"].map((label, index) => {
          const current = step === "identify" ? 0 : step === "face" || step === "face-failed" ? 1 : 2;
          const done = step === "success" || index < current;
          return (
            <li key={label} className={done ? "is-done" : index === current ? "is-current" : ""}
              aria-current={!done && index === current ? "step" : undefined}>
              <span aria-hidden="true">{done ? "✓" : index + 1}</span>{label}
            </li>
          );
        })}
      </ol>
      {led !== "off" && (
        <div className="auth-signal" role="status" aria-live="polite">
          <span aria-hidden="true" className={`auth-led auth-led-${led}`} />
          <span>{ledLabels[led]}</span>
        </div>
      )}
      {step === "identify" && (
        <form onSubmit={identify}>
          <label htmlFor="backoffice-email">Identifiant (adresse e-mail)</label>
          <input id="backoffice-email" type="email" autoComplete="username" required
            placeholder="vous@exemple.fr" value={email} disabled={busy || wait > 0}
            onChange={event => setEmail(event.target.value)} />
          <button disabled={busy || wait > 0}>
            {busy ? "Identification..." : "Continuer avec la caméra"}
          </button>
        </form>
      )}
      {(step === "face" || step === "face-failed") && challenge && (
        <FaceVerification key={challenge.challenge_token} api={api}
          challengeToken={challenge.challenge_token}
          captureEnabled={step === "face"}
          onVerified={result => {
            setProof(result);
            setLed("green_blink");
            setStep("password");
          }}
          onRejected={resetIdentification} />
      )}
      {step === "password" && (
        <form onSubmit={login}>
          <p className="identity-pill">{email}</p>
          <label htmlFor="backoffice-password">Mot de passe</label>
          <input id="backoffice-password" type="password" autoComplete="current-password"
            placeholder="Votre mot de passe" required autoFocus value={password} disabled={busy}
            onChange={event => setPassword(event.target.value)} />
          <button disabled={busy}>{busy ? "Connexion..." : "Se connecter"}</button>
        </form>
      )}
      {step === "face-failed" && (
        <p className="camera-countdown">Fermeture de la caméra : {Math.max(0, Math.ceil(cameraOffAt - now))} s.</p>
      )}
      {wait > 0 && step !== "success" && (
        <div className="auth-timer" role="timer">
          Nouvelle tentative dans<span className="timer-value">{wait} <small>s</small></span>
        </div>
      )}
      {step === "success" && (
        <div className="auth-timer" role="timer">
          Ouverture de votre espace
          <span className="timer-value">{Math.max(0, Math.ceil(pendingSession.backoffice_ready_at - now))} <small>s</small></span>
        </div>
      )}
      {message && message !== ledLabels[led] && <p className="inline-alert" role="alert">{message}</p>}
    </section>
  );
}

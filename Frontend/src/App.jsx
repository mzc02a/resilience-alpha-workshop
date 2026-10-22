import React, { useEffect, useRef, useState } from "react";
import BackOfficeLogin from "./BackOfficeLogin";
import FaceVerification from "./FaceVerification";
import { readSession, saveSession, SESSION_KEY } from "./session";

const AUTH_API = "http://localhost:8001";
const PAYMENT_API = "http://localhost:8000";

export default function App() {
  // ============================
  // STATES
  // ============================

  const [initialSession] = useState(readSession);
  const [page, setPage] = useState(initialSession.page);
  const [restoring, setRestoring] = useState(Boolean(initialSession.token));
  const [restoreError, setRestoreError] = useState("");
  const [registrationImage, setRegistrationImage] = useState(null);
  const [dashboardTab, setDashboardTab] = useState(initialSession.dashboardTab);

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [savingNames, setSavingNames] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [token, setToken] = useState(initialSession.token || "");
  const [profile, setProfile] = useState(null);
  const [faceExpiresAt, setFaceExpiresAt] = useState(initialSession.faceExpiresAt || 0);
  const adminVerified = faceExpiresAt > Date.now() / 1000;

  useEffect(() => {
    if (!faceExpiresAt) return;
    const timer = setTimeout(() => {
      logout();
      setMessage("Session expiree : recommence la connexion.");
    }, Math.max(0, faceExpiresAt * 1000 - Date.now()));
    return () => clearTimeout(timer);
  }, [faceExpiresAt]);

  const [wallet, setWallet] = useState(null);
  const [beneficiaries, setBeneficiaries] = useState([]);
  const [recipientIban, setRecipientIban] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const transferAttempt = useRef(null);
  const transferPending = useRef(false);
  const activeToken = useRef("");
  const [amount, setAmount] = useState("100");
  const [payments, setPayments] = useState([]);

  const [allPayments, setAllPayments] = useState([]);
  const [users, setUsers] = useState([]);
  const [stats, setStats] = useState(null);

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [adminLoading, setAdminLoading] = useState(false);
  const [roleUpdatingId, setRoleUpdatingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  useEffect(() => {
    if (!restoring) saveSession({ token, faceExpiresAt, page, dashboardTab });
  }, [token, faceExpiresAt, page, dashboardTab, restoring]);

  useEffect(() => {
    if (!initialSession.token) return;
    let cancelled = false;
    const accessToken = initialSession.token;
    activeToken.current = accessToken;
    async function restore() {
      try {
        const user = await request(`${AUTH_API}/auth/me`, { headers: authHeaders(accessToken) });
        if (cancelled) return;
        setProfile(user);
        setFirstName(user.first_name || "");
        setLastName(user.last_name || "");
        setPage("dashboard");
        if (user.role !== "admin") setDashboardTab("overview");
        try { await loadAccountData(accessToken); }
        catch { if (!cancelled) setMessage("Session restauree. Le compte est momentanement indisponible."); }
        if (!cancelled && user.role === "admin") await loadAdminDashboard(accessToken);
      } catch (error) {
        if (cancelled) return;
        if (error.status === 401 || error.status === 403) {
          logout();
          setMessage("Session expiree ou revoquee : reconnectez-vous.");
        } else {
          setRestoreError("Impossible de restaurer la session. Verifiez la connexion puis reessayez.");
        }
      } finally {
        if (!cancelled) setRestoring(false);
      }
    }
    restore();
    return () => { cancelled = true; };
  }, [initialSession]);

  // ============================
  // API HELPERS
  // ============================

  async function request(url, options = {}) {
    let response;

    try {
      response = await fetch(url, options);
    } catch {
      throw new Error(
        "Serveur indisponible. Réessayez dans un instant."
      );
    }

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      const error = new Error(
        typeof data.detail === "string"
          ? data.detail
          : `Erreur HTTP ${response.status}`
      );
      error.status = response.status;
      throw error;
    }

    return data;
  }

  function authHeaders(accessToken = token) {
    return {
      Authorization: `Bearer ${accessToken}`,
    };
  }

  function navigate(destination) {
    setPage(destination);
    if (destination === "register") { setFirstName(""); setLastName(""); }
    setRegistrationImage(null);
    setMessage("");
    setPassword("");
    setConfirmPassword("");
  }

  // ============================
  // REGISTER
  // ============================

  async function register(event) {
    event.preventDefault();
    setMessage("");

    if (password !== confirmPassword) {
      setMessage("Les mots de passe ne correspondent pas.");
      return;
    }

    if (password.length < 12) {
      setMessage(
        "Le mot de passe doit contenir au moins 12 caractères."
      );
      return;
    }

    setLoading(true);

    try {
      if (!registrationImage) {
        throw new Error("Capture ton visage avant de valider l'inscription.");
      }
      const body = new FormData();
      body.append("first_name", firstName.trim());
      body.append("last_name", lastName.trim());
      body.append("email", email);
      body.append("password", password);
      body.append("image", registrationImage, "enrollment.jpg");
      const created = await request(`${AUTH_API}/auth/register`, { method: "POST", body });
      setRegistrationImage(null);

      setPassword("");
      setConfirmPassword("");
      setPage("login");
      setMessage(created.iban ? `Compte créé avec 500 €. Votre IBAN : ${formatIban(created.iban)}` : "Compte créé. Vous pouvez vous connecter.");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  // ============================
  // LOGIN
  // ============================

  async function completeLogin(data) {
    setLoading(true);
    try {
      const user = await request(`${AUTH_API}/auth/me`, {
        headers: authHeaders(data.access_token),
      });
      setToken(data.access_token);
      activeToken.current = data.access_token;
      setWallet(null);
      setBeneficiaries([]);
      setRecipientIban("");
      transferAttempt.current = null;
      setFaceExpiresAt(data.face_verified_until || 0);
      setProfile(user);
      setFirstName(user.first_name || "");
      setLastName(user.last_name || "");
      setDashboardTab("overview");
      setPayments([]);
      setAllPayments([]);
      setUsers([]);
      setStats(null);
      setPassword("");
      setMessage("");
      setPage("dashboard");
      try {
        await loadAccountData(data.access_token);
      } catch {
        setMessage("Connexion reussie. Le compte est momentanement indisponible.");
      }
      if (user.role === "admin") await loadAdminDashboard(data.access_token);
    } catch (error) {
      setMessage(error.message);
      throw error;
    } finally {
      setLoading(false);
    }
  }

  // ============================
  // PAYMENT
  // ============================

  async function loadAccountData(accessToken = token) {
    const headers = authHeaders(accessToken);
    const [account, history, recipients] = await Promise.all([
      request(`${PAYMENT_API}/accounts/me`, { headers }),
      request(`${PAYMENT_API}/payments`, { headers }),
      request(`${PAYMENT_API}/beneficiaries`, { headers }),
    ]);
    if (activeToken.current !== accessToken) return;
    setWallet(account);
    setPayments(history);
    setBeneficiaries(recipients);
  }

  async function refreshAccount() {
    setRefreshing(true);
    try {
      await loadAccountData();
      if (profile?.role === "admin") await loadAdminDashboard();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setRefreshing(false);
    }
  }

  async function pay(event) {
    event.preventDefault();
    if (transferPending.current || !wallet || !recipientIban) return;
    transferPending.current = true;
    const accessToken = token;
    const body = { amount, currency: "EUR", recipient_iban: recipientIban };
    const signature = JSON.stringify(body);
    if (!transferAttempt.current || transferAttempt.current.signature !== signature) {
      transferAttempt.current = { signature, key: crypto.randomUUID() };
    }
    const key = transferAttempt.current.key;
    setLoading(true);
    setMessage("");
    try {
      const result = await request(`${PAYMENT_API}/payments`, {
        method: "POST",
        headers: { ...authHeaders(accessToken), "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify(body),
      });
      if (activeToken.current !== accessToken) return;
      transferAttempt.current = null;
      setMessage(result.status === "SUCCESS"
        ? `Virement envoyé : ${formatAmount(result.amount)}.`
        : "Virement refusé : solde insuffisant.");
      // Refresh failures must not turn a committed transfer into an apparent failure.
      try {
        await loadAccountData(accessToken);
        if (profile?.role === "admin" && adminVerified) await loadAdminDashboard(accessToken);
      } catch {
        setMessage(result.status === "SUCCESS"
          ? "Virement effectué. Actualisez pour mettre à jour le solde."
          : "Virement refusé. Actualisez pour mettre à jour le compte.");
      }
    } catch (error) {
      if (activeToken.current === accessToken) setMessage(error.message);
      // Retain the same key after a network/response failure: retry cannot debit twice.
    } finally {
      transferPending.current = false;
      setLoading(false);
    }
  }

  // ============================
  // ADMIN DASHBOARD
  // ============================

  async function loadAdminDashboard(accessToken = token) {
    setAdminLoading(true);

    try {
      const headers = authHeaders(accessToken);

      const [transactions, statistics, userList] =
        await Promise.all([
          request(`${PAYMENT_API}/admin/payments`, {
            headers,
          }),
          request(`${PAYMENT_API}/admin/stats`, {
            headers,
          }),
          request(`${AUTH_API}/admin/users`, {
            headers,
          }),
        ]);

      if (activeToken.current !== accessToken) return;
      setAllPayments(transactions);
      setStats(statistics);
      setUsers(userList);
    } catch (error) {
      setMessage(
        `Impossible de charger l'administration : ${error.message}`
      );
    } finally {
      setAdminLoading(false);
    }
  }

  // ============================
  // ADMIN: CHANGE USER ROLE
  // ============================

  async function changeUserRole(userId, newRole) {
    if (userId === profile?.id && newRole !== "admin") {
      setMessage(
        "Vous ne pouvez pas supprimer votre propre rôle administrateur."
      );
      return;
    }

    const confirmed = window.confirm(
      `Confirmer le changement de rôle vers "${newRole}" ?`
    );

    if (!confirmed) return;

    setRoleUpdatingId(userId);
    setMessage("");

    try {
      await request(
        `${AUTH_API}/admin/users/${userId}/role`,
        {
          method: "PATCH",
          headers: {
            ...authHeaders(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            role: newRole,
          }),
        }
      );

      setMessage("Le rôle a été modifié avec succès.");

      await loadAdminDashboard(token);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setRoleUpdatingId(null);
    }
  }

  async function deleteUser(user) {
    if (user.id === profile?.id || deletingId) return;
    if (!window.confirm(`Supprimer definitivement le compte de ${personName(user) || user.email} (${user.email}) ? Son acces et son compte seront supprimes. L'historique des virements sera conserve.`)) return;
    setDeletingId(user.id);
    setMessage("");
    try {
      await request(`${AUTH_API}/admin/users/${user.id}`, { method: "DELETE", headers: authHeaders() });
      setUsers(current => current.filter(item => item.id !== user.id));
      setBeneficiaries(current => current.filter(item => item.user_id !== user.id));
      setMessage("Utilisateur supprime. L'historique des virements est conserve.");
      await loadAdminDashboard(token);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setDeletingId(null);
    }
  }

  // ============================
  // LOGOUT
  // ============================

  function logout() {
    try { sessionStorage.removeItem(SESSION_KEY); } catch { /* unavailable storage */ }
    setRestoreError("");
    setToken("");
    activeToken.current = "";
    setWallet(null);
    setBeneficiaries([]);
    setRecipientIban("");
    transferAttempt.current = null;
    setFaceExpiresAt(0);
    setProfile(null);
    setDashboardTab("overview");
    setPayments([]);
    setAllPayments([]);
    setUsers([]);
    setStats(null);
    setPassword("");
    setConfirmPassword("");
    setPage("login");
    setMessage("Vous êtes déconnecté.");
  }

  // ============================
  // FORMAT HELPERS
  // ============================

  function formatAmount(value) {
    return new Intl.NumberFormat("fr-FR", {
      style: "currency",
      currency: "EUR",
    }).format(Number(value));
  }

  function formatIban(value) {
    return value ? value.replace(/(.{4})/g, "$1 ").trim() : "Indisponible";
  }

  function personName(person) {
    return [person?.first_name, person?.last_name].filter(Boolean).join(" ") || person?.email || "";
  }

  async function saveNames(event) {
    event.preventDefault();
    setSavingNames(true);
    try {
      const updated = await request(`${AUTH_API}/auth/me`, {method: "PATCH", headers: {...authHeaders(), "Content-Type": "application/json"}, body: JSON.stringify({first_name: firstName, last_name: lastName})});
      setProfile(updated);
      setMessage("Nom et pr\u00e9nom enregistr\u00e9s.");
    } catch (error) { setMessage(error.message); }
    finally { setSavingNames(false); }
  }

  function counterpart(payment) {
    const incoming = payment.recipient_user_id === profile?.id;
    const iban = incoming ? payment.sender_iban : payment.recipient_iban;
    if (!iban) return "Paiement historique";
    return personName(beneficiaries.find(person => person.iban === iban)) || formatIban(iban);
  }

  function transferAmount(payment) {
    if (payment.status !== "SUCCESS" || !payment.recipient_user_id) return formatAmount(payment.amount);
    return (payment.recipient_user_id === profile?.id ? "+ " : "− ") + formatAmount(payment.amount);
  }

  function formatDate(value) {
    return new Date(value).toLocaleString("fr-FR");
  }

  // ============================
  // UI
  // ============================

  if (restoring || restoreError) return (
    <main className="container" aria-live="polite">
      <p>{restoreError || "Restauration de votre session..."}</p>
      {restoreError && <button type="button" onClick={() => window.location.reload()}>Reessayer</button>}
    </main>
  );

  return (
    <main className={page === "dashboard" ? "app-shell" : "app-shell auth-shell"}>
      <header className="app-header">
        <div className="brand" aria-label="Resilience Alpha">
          <span className="brand-mark" aria-hidden="true">R</span>
          <span className="brand-name">RESILIENCE <span>ALPHA</span></span>
        </div>
        {page === "dashboard" && profile ? (
          <div className="account-menu">
            <div className="account-identity">
              <span className="account-email">{personName(profile)}</span>
              <span className={`role-badge ${profile.role === "admin" ? "role-admin" : ""}`}>
                {profile.role === "admin" ? "Administrateur" : "Utilisateur"}
              </span>
            </div>
            <button type="button" className="button-quiet" onClick={logout}>Se déconnecter</button>
          </div>
        ) : (
          <span className="secure-label"><span aria-hidden="true" />Espace sécurisé</span>
        )}
      </header>

      {message && <div className="notice" role="status">{message}</div>}

      {page === "login" && (
        <div className="auth-stage">
          <BackOfficeLogin api={AUTH_API} onAuthenticated={completeLogin} />
          <div className="auth-switch">
            <span>Pas encore de compte ?</span>
            <button type="button" className="text-button" onClick={() => navigate("register")}>
              Créer un compte
            </button>
          </div>
        </div>
      )}

      {page === "register" && (
        <div className="register-stage">
          <div className="page-heading">
            <h1>Créer un compte</h1>
          </div>
          <section className="surface register-panel" aria-label="Inscription">
            <form onSubmit={register} className="registration-form">
              <div className="registration-fields">
                <div className="panel-heading"><span className="step-number">01</span><h2>Votre compte</h2></div>
                <div className="field">
                  <label htmlFor="register-first-name">Prénom</label>
                  <input id="register-first-name" autoComplete="given-name" required maxLength={80} value={firstName} onChange={e => setFirstName(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="register-last-name">Nom</label>
                  <input id="register-last-name" autoComplete="family-name" required maxLength={80} value={lastName} onChange={e => setLastName(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="register-email">Adresse e-mail</label>
                  <input id="register-email" type="email" autoComplete="email" required
                    placeholder="vous@exemple.fr" value={email} onChange={e => setEmail(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="register-password">Mot de passe</label>
                  <input id="register-password" type="password" autoComplete="new-password" required
                    minLength={12} placeholder="12 caractères minimum" value={password}
                    onChange={e => setPassword(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="confirm-password">Confirmer le mot de passe</label>
                  <input id="confirm-password" type="password" autoComplete="new-password" required
                    placeholder="Saisissez à nouveau le mot de passe" value={confirmPassword}
                    onChange={e => setConfirmPassword(e.target.value)} />
                </div>
              </div>
              <div className="registration-face">
                {registrationImage ? (
                  <div className="enrollment-complete">
                    <span className="confirmation-icon" aria-hidden="true">✓</span>
                    <h2>Visage capturé</h2>
                    <button type="button" className="button-outline" disabled={loading}
                      onClick={() => setRegistrationImage(null)}>Reprendre la photo</button>
                  </div>
                ) : (
                  <FaceVerification onCapture={async image => setRegistrationImage(image)}
                    captureEnabled={!loading} />
                )}
              </div>
              <div className="registration-footer">
                <span className="field-hint">Photo non conservée.</span>
                <button type="submit" disabled={loading || !registrationImage}>
                  {loading ? "Inscription..." : "S'inscrire"}
                </button>
              </div>
            </form>
          </section>
          <div className="auth-switch">
            <span>Déjà un compte ?</span>
            <button type="button" className="text-button" onClick={() => navigate("login")}>Se connecter</button>
          </div>
        </div>
      )}

      {page === "dashboard" && profile && (
        <div className="dashboard">
          <div className="page-heading">
            <div>
              <p className="eyebrow">{profile.role === "admin" ? "Administration" : "Mon espace"}</p>
              <h1>{profile.role === "admin" ? "Vue d'ensemble" : "Mes paiements"}</h1>

            </div>
            <button type="button" className="button-outline" disabled={refreshing || loading}
              onClick={refreshAccount}>{refreshing ? "Actualisation..." : "Actualiser"}</button>
          </div>

          {profile.role === "admin" && adminVerified && stats && (
            <div className="stats-grid">
              <div className="stat-card"><span>Transactions</span><strong>{stats.total_transactions}</strong></div>
              <div className="stat-card"><span>Réussies</span><strong className="text-success">{stats.successful_transactions}</strong></div>
              <div className="stat-card"><span>Refusées</span><strong>{stats.declined_transactions}</strong></div>
              <div className="stat-card"><span>Volume encaissé</span><strong>{formatAmount(stats.total_successful_amount)}</strong></div>
            </div>
          )}

          {profile.role === "admin" && adminVerified && (
            <div className="dashboard-tabs" role="tablist" aria-label="Sections du tableau de bord">
              {[["overview", "Mon espace"], ["transactions", "Transactions"], ["users", "Utilisateurs"]].map(([id, label]) => (
                <button key={id} type="button" role="tab" id={`tab-${id}`}
                  aria-selected={dashboardTab === id} aria-controls="dashboard-content"
                  onClick={() => setDashboardTab(id)}>{label}</button>
              ))}
            </div>
          )}

          <div id="dashboard-content" role={profile.role === "admin" ? "tabpanel" : undefined}
            aria-labelledby={profile.role === "admin" ? `tab-${dashboardTab}` : undefined}>
            {(profile.role !== "admin" || dashboardTab === "overview") && (
              <>
                {(!profile.first_name || !profile.last_name) && (
                  <section className="surface" style={{padding: 24, marginBottom: 24}}>
                    <h2>Compléter mon profil</h2>
                    <form onSubmit={saveNames}>
                      <div className="field"><label htmlFor="profile-first-name">Prénom</label><input id="profile-first-name" required maxLength={80} value={firstName} onChange={e => setFirstName(e.target.value)} /></div>
                      <div className="field"><label htmlFor="profile-last-name">Nom</label><input id="profile-last-name" required maxLength={80} value={lastName} onChange={e => setLastName(e.target.value)} /></div>
                      <button disabled={savingNames}>Enregistrer</button>
                    </form>
                  </section>
                )}
                <section className="wallet-summary" aria-label="Mon compte">
                  <div><span className="wallet-label">Solde disponible</span>
                    <strong className="wallet-balance">{wallet ? formatAmount(wallet.balance) : "Indisponible"}</strong>
                  </div>
                  <div className="wallet-iban"><span className="wallet-label">IBAN de démonstration</span>
                    <code>{formatIban(wallet?.iban || profile.iban)}</code>
                  </div>
                </section>
              <div className="dashboard-grid">
                <section className="surface payment-panel">
                  <div className="section-heading"><h2>Envoyer de l'argent</h2></div>
                  <form onSubmit={pay}>
                    <div className="field">
                      <label htmlFor="recipient">Destinataire</label>
                      <select id="recipient" required value={recipientIban} disabled={loading || !wallet}
                        onChange={event => setRecipientIban(event.target.value)}>
                        <option value="">{beneficiaries.length ? "Choisir une personne" : "Aucun destinataire"}</option>
                        {beneficiaries.map(person => <option key={person.user_id} value={person.iban}>{personName(person)}</option>)}
                      </select>
                      {recipientIban && <code className="recipient-iban">{formatIban(recipientIban)}</code>}
                    </div>
                    <div className="field">
                      <label htmlFor="amount">Montant (€)</label>
                      <div className="amount-input">
                        <input id="amount" type="number" min="0.01" step="0.01" required
                          value={amount} disabled={loading || !wallet} onChange={e => setAmount(e.target.value)} />
                        <span aria-hidden="true">EUR</span>
                      </div>
                    </div>
                    <button className="full-width" type="submit" disabled={loading || !wallet || !recipientIban}>
                      {loading ? "Traitement..." : "Envoyer le virement"}
                    </button>
                  </form>
                </section>

                <section className="surface history-panel">
                  <div className="section-heading"><h2>Mes virements</h2></div>
                  <div className="table-wrapper">
                    <table>
                      <thead><tr><th>Avec</th><th className="numeric">Montant</th><th>Statut</th><th>Date</th></tr></thead>
                      <tbody>
                        {payments.map(payment => (
                          <tr key={payment.id}>
                            <td className="counterpart-cell" title={payment.id}>{counterpart(payment)}</td>
                            <td className={`numeric ${payment.recipient_user_id === profile.id ? "text-success" : ""}`}>{transferAmount(payment)}</td>
                            <td><span className={`status-badge ${payment.status === "SUCCESS" ? "status-success" : payment.status === "DECLINED" ? "status-declined" : ""}`}>{payment.status === "SUCCESS" ? "Réussi" : payment.status === "DECLINED" ? "Refusé" : payment.status}</span></td>
                            <td className="cell-date">{formatDate(payment.created_at)}</td>
                          </tr>
                        ))}
                        {!payments.length && <tr><td className="empty-cell" colSpan="4">Aucune transaction.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>
              </>
            )}

            {profile.role === "admin" && adminVerified && dashboardTab === "transactions" && (
              <section className="surface">
                <div className="section-heading"><h2>Toutes les transactions</h2></div>
                <div className="table-wrapper">
                  <table>
                    <thead><tr><th>Référence</th><th>De</th><th>Vers</th><th className="numeric">Montant</th><th>Statut</th><th>Date</th></tr></thead>
                    <tbody>
                      {allPayments.map(payment => (
                        <tr key={payment.id}>
                          <td className="cell-id" title={payment.id}>{payment.id.slice(0, 8)}</td>
                          <td><code className="account-code">{payment.sender_iban ? formatIban(payment.sender_iban) : "—"}</code></td>
                          <td><code className="account-code">{payment.recipient_iban ? formatIban(payment.recipient_iban) : "—"}</code></td>
                          <td className="numeric">{formatAmount(payment.amount)}</td>
                          <td><span className={`status-badge ${payment.status === "SUCCESS" ? "status-success" : payment.status === "DECLINED" ? "status-declined" : ""}`}>{payment.status === "SUCCESS" ? "Réussi" : payment.status === "DECLINED" ? "Refusé" : payment.status}</span></td>
                          <td className="cell-date">{formatDate(payment.created_at)}</td>
                        </tr>
                      ))}
                      {!allPayments.length && <tr><td className="empty-cell" colSpan="6">Aucune transaction.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {profile.role === "admin" && adminVerified && dashboardTab === "users" && (
              <section className="surface">
                <div className="section-heading"><h2>Utilisateurs</h2></div>
                <div className="table-wrapper">
                  <table>
                    <thead><tr><th>IBAN de démonstration</th><th>Nom et prénom</th><th>Adresse e-mail</th><th>Rôle</th><th className="numeric">Action</th></tr></thead>
                    <tbody>
                      {users.map(user => (
                        <tr key={user.id}>
                          <td><code className="account-code">{formatIban(user.iban)}</code></td>
                          <td>{personName(user)}</td>
                          <td>{user.email}{user.id === profile.id && <span className="self-label">Vous</span>}</td>
                          <td><span className={`role-badge ${user.role === "admin" ? "role-admin" : ""}`}>{user.role === "admin" ? "Administrateur" : "Utilisateur"}</span></td>
                          <td className="numeric">
                            <button type="button" className="button-outline button-small"
                              disabled={roleUpdatingId === user.id || Boolean(deletingId) || user.id === profile.id}
                              onClick={() => changeUserRole(user.id, user.role === "admin" ? "user" : "admin")}>
                              {roleUpdatingId === user.id ? "Modification..." : user.role === "admin" ? "Passer utilisateur" : "Passer administrateur"}
                            </button>
                            {user.id !== profile.id && <button type="button" className="button-outline button-small"
                              style={{ marginLeft: 8, color: "#b42318" }}
                              disabled={Boolean(deletingId) || Boolean(roleUpdatingId)}
                              onClick={() => deleteUser(user)}>
                              {deletingId === user.id ? "Suppression..." : "Supprimer"}
                            </button>}
                          </td>
                        </tr>
                      ))}
                      {!users.length && <tr><td className="empty-cell" colSpan="5">Aucun utilisateur.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </div>
        </div>
      )}
    </main>
  );
}

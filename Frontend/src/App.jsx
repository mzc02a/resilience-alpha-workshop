import React, { useState } from "react";

const AUTH_API = "http://localhost:8001";
const PAYMENT_API = "http://localhost:8000";

export default function App() {
  // ============================
  // STATES
  // ============================

  const [page, setPage] = useState("login");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [token, setToken] = useState("");
  const [profile, setProfile] = useState(null);

  const [amount, setAmount] = useState("100");
  const [payments, setPayments] = useState([]);

  const [allPayments, setAllPayments] = useState([]);
  const [users, setUsers] = useState([]);
  const [stats, setStats] = useState(null);

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [adminLoading, setAdminLoading] = useState(false);
  const [roleUpdatingId, setRoleUpdatingId] = useState(null);

  // ============================
  // API HELPERS
  // ============================

  async function request(url, options = {}) {
    let response;

    try {
      response = await fetch(url, options);
    } catch {
      throw new Error(
        "Impossible de contacter le serveur. Vérifiez les services Docker."
      );
    }

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : `Erreur HTTP ${response.status}`
      );
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
      await request(`${AUTH_API}/auth/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          password,
        }),
      });

      setPassword("");
      setConfirmPassword("");
      setPage("login");
      setMessage("Compte créé avec succès ! Connectez-vous.");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  // ============================
  // LOGIN
  // ============================

  async function login(event) {
    event.preventDefault();
    setMessage("");
    setLoading(true);

    try {
      const data = await request(`${AUTH_API}/auth/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          password,
        }),
      });

      const user = await request(`${AUTH_API}/auth/me`, {
        headers: authHeaders(data.access_token),
      });

      setToken(data.access_token);
      setProfile(user);
      setPayments([]);
      setAllPayments([]);
      setUsers([]);
      setStats(null);
      setPassword("");
      setPage("dashboard");

      // Login succeeds even if payment service is unavailable.
      try {
        const history = await request(`${PAYMENT_API}/payments`, {
          headers: authHeaders(data.access_token),
        });
        setPayments(history);
      } catch {
        setMessage(
          "Connexion réussie, mais l'historique des paiements est indisponible."
        );
      }

      if (user.role === "admin") {
        await loadAdminDashboard(data.access_token);
      }
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  // ============================
  // PAYMENT
  // ============================

  async function pay(event) {
    event.preventDefault();
    setLoading(true);
    setMessage("");

    try {
      const result = await request(`${PAYMENT_API}/payments`, {
        method: "POST",
        headers: {
          ...authHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          amount: Number(amount),
          currency: "EUR",
        }),
      });

      setMessage(
        result.status === "SUCCESS"
          ? `Paiement réussi : ${result.amount} €`
          : `Paiement refusé : ${result.amount} €`
      );

      const history = await request(`${PAYMENT_API}/payments`, {
        headers: authHeaders(),
      });

      setPayments(history);

      if (profile?.role === "admin") {
        await loadAdminDashboard(token);
      }
    } catch (error) {
      setMessage(error.message);
    } finally {
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

  // ============================
  // LOGOUT
  // ============================

  function logout() {
    setToken("");
    setProfile(null);
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

  function formatDate(value) {
    return new Date(value).toLocaleString("fr-FR");
  }

  // ============================
  // UI
  // ============================

  return (
    <main className="container">
      <header>
        <h1>RESILIENCE-ALPHA</h1>
        <p>
          Plateforme de paiement sécurisée — Démonstration
        </p>
      </header>

      {/* LOGIN */}

      {page === "login" && (
        <section className="card">
          <h2>Connexion</h2>

          <form onSubmit={login}>
            <label htmlFor="login-email">
              Adresse e-mail
            </label>
            <input
              id="login-email"
              type="email"
              required
              placeholder="exemple@gmail.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />

            <label htmlFor="login-password">
              Mot de passe
            </label>
            <input
              id="login-password"
              type="password"
              required
              placeholder="Votre mot de passe"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            <button type="submit" disabled={loading}>
              {loading ? "Connexion..." : "Se connecter"}
            </button>
          </form>

          <p>
            Vous n'avez pas de compte ?{" "}
            <button
              type="button"
              className="secondary"
              onClick={() => navigate("register")}
            >
              Créer un compte
            </button>
          </p>
        </section>
      )}

      {/* REGISTER */}

      {page === "register" && (
        <section className="card">
          <h2>Créer un compte</h2>

          <form onSubmit={register}>
            <label htmlFor="register-email">
              Adresse e-mail
            </label>
            <input
              id="register-email"
              type="email"
              required
              placeholder="exemple@gmail.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />

            <label htmlFor="register-password">
              Mot de passe
            </label>
            <input
              id="register-password"
              type="password"
              required
              minLength={12}
              placeholder="12 caractères minimum"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            <label htmlFor="confirm-password">
              Confirmer le mot de passe
            </label>
            <input
              id="confirm-password"
              type="password"
              required
              placeholder="Confirmez votre mot de passe"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />

            <button type="submit" disabled={loading}>
              {loading ? "Inscription..." : "S'inscrire"}
            </button>
          </form>

          <p>
            Vous avez déjà un compte ?{" "}
            <button
              type="button"
              className="secondary"
              onClick={() => navigate("login")}
            >
              Se connecter
            </button>
          </p>
        </section>
      )}

      {/* DASHBOARD */}

      {page === "dashboard" && profile && (
        <>
          <section className="card">
            <h2>
              {profile.role === "admin"
                ? "Tableau de bord administrateur"
                : "Tableau de bord utilisateur"}
            </h2>

            <p>
              <strong>Email :</strong> {profile.email}
            </p>

            <p>
              <strong>Rôle :</strong>{" "}
              {profile.role === "admin"
                ? "ADMIN"
                : "USER"}
            </p>

            <button
              type="button"
              className="secondary"
              onClick={logout}
            >
              Se déconnecter
            </button>
          </section>

          {/* PAYMENT FORM */}

          <section className="card">
            <h2>Effectuer un paiement</h2>

            <form onSubmit={pay}>
              <label htmlFor="amount">
                Montant (€)
              </label>

              <input
                id="amount"
                type="number"
                min="0.01"
                step="0.01"
                required
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />

              <button
                type="submit"
                disabled={loading}
              >
                {loading
                  ? "Traitement..."
                  : "Payer maintenant"}
              </button>
            </form>
          </section>

          {/* PERSONAL TRANSACTIONS */}

          <section className="card">
            <h2>Mes transactions</h2>

            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Montant</th>
                    <th>Statut</th>
                    <th>Date</th>
                  </tr>
                </thead>

                <tbody>
                  {payments.map((payment) => (
                    <tr key={payment.id}>
                      <td>
                        {payment.id.slice(0, 8)}
                      </td>
                      <td>
                        {formatAmount(payment.amount)}
                      </td>
                      <td>{payment.status}</td>
                      <td>
                        {formatDate(payment.created_at)}
                      </td>
                    </tr>
                  ))}

                  {payments.length === 0 && (
                    <tr>
                      <td colSpan="4">
                        Aucune transaction.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {/* ADMIN ONLY */}

          {profile.role === "admin" && (
            <>
              <section className="card">
                <h2>Administration</h2>

                <button
                  type="button"
                  onClick={() => loadAdminDashboard()}
                  disabled={adminLoading}
                >
                  {adminLoading
                    ? "Chargement..."
                    : "Actualiser les données"}
                </button>
              </section>

              {/* STATISTICS */}

              <section className="card">
                <h2>Statistiques globales</h2>

                {stats ? (
                  <>
                    <p>
                      <strong>
                        Total des transactions :
                      </strong>{" "}
                      {stats.total_transactions}
                    </p>

                    <p>
                      <strong>
                        Paiements réussis :
                      </strong>{" "}
                      {stats.successful_transactions}
                    </p>

                    <p>
                      <strong>
                        Paiements refusés :
                      </strong>{" "}
                      {stats.declined_transactions}
                    </p>

                    <p>
                      <strong>
                        Montant total réussi :
                      </strong>{" "}
                      {formatAmount(
                        stats.total_successful_amount
                      )}
                    </p>
                  </>
                ) : (
                  <p>
                    Aucune statistique chargée.
                  </p>
                )}
              </section>

              {/* USERS */}

              <section className="card">
                <h2>Gestion des utilisateurs</h2>

                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>Email</th>
                        <th>Rôle</th>
                        <th>Action</th>
                      </tr>
                    </thead>

                    <tbody>
                      {users.map((user) => (
                        <tr key={user.id}>
                          <td>{user.email}</td>
                          <td>{user.role}</td>

                          <td>
                            <button
                              type="button"
                              className="secondary"
                              disabled={
                                roleUpdatingId === user.id ||
                                user.id === profile.id
                              }
                              onClick={() =>
                                changeUserRole(
                                  user.id,
                                  user.role === "admin"
                                    ? "user"
                                    : "admin"
                                )
                              }
                            >
                              {roleUpdatingId === user.id
                                ? "Modification..."
                                : user.role === "admin"
                                ? "Passer USER"
                                : "Passer ADMIN"}
                            </button>
                          </td>
                        </tr>
                      ))}

                      {users.length === 0 && (
                        <tr>
                          <td colSpan="3">
                            Aucun utilisateur chargé.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* ALL TRANSACTIONS */}

              <section className="card">
                <h2>Toutes les transactions</h2>

                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>ID</th>
                        <th>Utilisateur</th>
                        <th>Montant</th>
                        <th>Statut</th>
                        <th>Date</th>
                      </tr>
                    </thead>

                    <tbody>
                      {allPayments.map((payment) => (
                        <tr key={payment.id}>
                          <td>
                            {payment.id.slice(0, 8)}
                          </td>
                          <td>
                            {payment.user_id.slice(0, 8)}
                          </td>
                          <td>
                            {formatAmount(payment.amount)}
                          </td>
                          <td>{payment.status}</td>
                          <td>
                            {formatDate(
                              payment.created_at
                            )}
                          </td>
                        </tr>
                      ))}

                      {allPayments.length === 0 && (
                        <tr>
                          <td colSpan="5">
                            Aucune transaction chargée.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </>
      )}

      {/* NOTIFICATIONS */}

      {message && (
        <section className="card">
          <p>{message}</p>
        </section>
      )}
    </main>
  );
}
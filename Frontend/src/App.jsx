import React, { useState } from "react";

const AUTH_API = "http://localhost:8001";
const PAYMENT_API = "http://localhost:8000";

export default function App() {
  const [page, setPage] = useState("login");

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [token, setToken] = useState("");
  const [profile, setProfile] = useState(null);

  const [amount, setAmount] = useState("100");
  const [payments, setPayments] = useState([]);

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Une erreur est survenue"
      );
    }

    return data;
  }

  function navigate(destination) {
    setPage(destination);
    setMessage("");
    setPassword("");
    setConfirmPassword("");
  }

  // INSCRIPTION
  async function register(event) {
    event.preventDefault();
    setMessage("");

    if (password !== confirmPassword) {
      setMessage("Les mots de passe ne correspondent pas.");
      return;
    }

    if (password.length < 12) {
      setMessage("Le mot de passe doit contenir au moins 12 caractères.");
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

  // CONNEXION
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

      const headers = {
        Authorization: `Bearer ${data.access_token}`,
      };

      const user = await request(`${AUTH_API}/auth/me`, {
        headers,
      });

      const history = await request(`${PAYMENT_API}/payments`, {
        headers,
      });

      setToken(data.access_token);
      setProfile(user);
      setPayments(history);
      setPassword("");
      setPage("dashboard");
      setMessage("");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  // PAIEMENT
  async function pay(event) {
    event.preventDefault();
    setLoading(true);
    setMessage("");

    try {
      const headers = {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      };

      const result = await request(`${PAYMENT_API}/payments`, {
        method: "POST",
        headers,
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
        headers,
      });

      setPayments(history);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    setToken("");
    setProfile(null);
    setPayments([]);
    setPassword("");
    setPage("login");
    setMessage("Vous êtes déconnecté.");
  }

  return (
    <main className="container">
      <header>
        <h1>RESILIENCE-ALPHA</h1>
        <p>Plateforme de paiement sécurisée — Démonstration</p>
      </header>

      {page === "login" && (
        <section className="card">
          <h2>Connexion</h2>

          <form onSubmit={login}>
            <label htmlFor="login-email">Adresse e-mail</label>
            <input
              id="login-email"
              type="email"
              required
              placeholder="exemple@gmail.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />

            <label htmlFor="login-password">Mot de passe</label>
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

      {page === "register" && (
        <section className="card">
          <h2>Créer un compte</h2>

          <form onSubmit={register}>
            <label htmlFor="register-name">Nom complet</label>
            <input
              id="register-name"
              type="text"
              required
              placeholder="Votre nom complet"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />

            <label htmlFor="register-email">Adresse e-mail</label>
            <input
              id="register-email"
              type="email"
              required
              placeholder="exemple@gmail.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />

            <label htmlFor="register-password">Mot de passe</label>
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

      {page === "dashboard" && profile && (
        <>
          <section className="card">
            <h2>Tableau de bord</h2>
            <p><strong>Email :</strong> {profile.email}</p>
            <p><strong>Rôle :</strong> {profile.role}</p>
            <button className="secondary" onClick={logout}>
              Se déconnecter
            </button>
          </section>

          <section className="card">
            <h2>Effectuer un paiement</h2>
            <form onSubmit={pay}>
              <label htmlFor="amount">Montant (€)</label>
              <input
                id="amount"
                type="number"
                min="0.01"
                step="0.01"
                required
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
              <button type="submit" disabled={loading}>
                {loading ? "Traitement..." : "Payer maintenant"}
              </button>
            </form>
          </section>

          <section className="card">
            <h2>Historique des transactions</h2>
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Montant</th>
                    <th>Statut</th>
                  </tr>
                </thead>
                <tbody>
                  {payments.map((payment) => (
                    <tr key={payment.id}>
                      <td>{payment.id.slice(0, 8)}</td>
                      <td>{payment.amount} €</td>
                      <td>{payment.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {message && (
        <section className="card">
          <p>{message}</p>
        </section>
      )}
    </main>
  );
}
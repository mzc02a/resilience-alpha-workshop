export const SESSION_KEY = "resilience-alpha.session";
const pages = new Set(["login", "register", "dashboard"]);
const tabs = new Set(["overview", "transactions", "users"]);

export function readSession() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    if (!saved) return { page: "login", dashboardTab: "overview" };
    if (saved.token && (!Number.isFinite(saved.faceExpiresAt) || saved.faceExpiresAt <= Date.now() / 1000)) {
      sessionStorage.removeItem(SESSION_KEY);
      return { page: "login", dashboardTab: "overview" };
    }
    return {
      token: typeof saved.token === "string" ? saved.token : "",
      faceExpiresAt: saved.faceExpiresAt || 0,
      page: saved.token ? "dashboard" : pages.has(saved.page) && saved.page !== "dashboard" ? saved.page : "login",
      dashboardTab: tabs.has(saved.dashboardTab) ? saved.dashboardTab : "overview",
    };
  } catch {
    return { page: "login", dashboardTab: "overview" };
  }
}

export function saveSession(session) {
  try {
    if (!session.token && session.page !== "register") sessionStorage.removeItem(SESSION_KEY);
    else sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch { /* Storage can be unavailable; the current in-memory session still works. */ }
}

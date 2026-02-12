import * as backend from "./api/backend.js";

export async function requireAuth({ redirectTo = "login.html" } = {}) {
  try {
    const user = await backend.me();
    return user;
  } catch {
    window.location.href = redirectTo;
    return null;
  }
}

export async function requireAdmin({ redirectTo = "index.html" } = {}) {
  const user = await requireAuth({ redirectTo: "login.html" });
  if (!user) return null;
  if (user.role !== "admin") {
    window.location.href = redirectTo;
    return null;
  }
  return user;
}

/**
 * BUYWISE AI — Frontend Configuration & Utilities
 * Client-side session management, formatting, and comparison state.
 * Strictly zero backend secrets or API keys.
 */

export const CONFIG = {
  APP_NAME: "BUYWISE AI",
  TAGLINE: "Discover smarter. Compare deeper. Spend responsibly.",
  _apiBase: null,
  get API_BASE() {
    if (this._apiBase !== null) return this._apiBase;
    if (typeof window !== "undefined" && window.location) {
      // When served on port 8000, use same-origin relative path
      if (window.location.port === "8000") {
        return "";
      }
      // When served on port 5500 (Live Server) or others, match hostname (localhost or 127.0.0.1)
      if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
        return `${window.location.protocol}//${window.location.hostname}:8000`;
      }
    }
    return "http://127.0.0.1:8000";
  },
  set API_BASE(val) {
    this._apiBase = val;
  },
  DEFAULT_CURRENCY: "USD",
  MAX_COMPARE_ITEMS: 4,
  STORAGE_KEYS: {
    SESSION_ID: "buywise_guest_session_id",
    SESSION_IDENTIFIER: "buywise_guest_session_identifier",
    SESSION_TOKEN: "buywise_guest_session_token",
    AUTH_TOKEN: "buywise_auth_token",
    AUTH_USER: "buywise_auth_user",
    COMPARE_IDS: "buywise_compare_ids",
    GUEST_ACTIVE: "buywise_guest_active",
    PENDING_CHECKOUT: "buywise_pending_checkout",
  },
  CATEGORIES: [
    "All",
    "Audio",
    "Wearables",
    "Computing",
    "Electronics",
    "Smart Home",
    "Gaming",
  ],
};

/**
 * Generate a random UUID v4 string for guest identification.
 */
export function generateUUID() {
  if (crypto && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Retrieve or generate the persistent guest session identifier.
 */
export function getSessionIdentifier() {
  let identifier = localStorage.getItem(CONFIG.STORAGE_KEYS.SESSION_IDENTIFIER);
  if (!identifier) {
    identifier = `guest_${generateUUID().replace(/-/g, "").substring(0, 16)}`;
    localStorage.setItem(CONFIG.STORAGE_KEYS.SESSION_IDENTIFIER, identifier);
  }
  return identifier;
}

/**
 * Retrieve the canonical database session ID if resolved, otherwise fallback to identifier.
 */
export function getSessionId() {
  return localStorage.getItem(CONFIG.STORAGE_KEYS.SESSION_ID) || getSessionIdentifier();
}

/**
 * Store canonical backend-assigned ShoppingSession UUID.
 */
export function setSessionId(sessionId) {
  if (sessionId) {
    localStorage.setItem(CONFIG.STORAGE_KEYS.SESSION_ID, sessionId);
  }
}

/**
 * Retrieve the guest session proof-of-possession token.
 */
export function getGuestSessionToken() {
  return localStorage.getItem(CONFIG.STORAGE_KEYS.SESSION_TOKEN) || null;
}

/**
 * Persist the guest session proof-of-possession token.
 */
export function setGuestSessionToken(token) {
  if (token) {
    localStorage.setItem(CONFIG.STORAGE_KEYS.SESSION_TOKEN, token);
  } else {
    localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_TOKEN);
  }
}

/**
 * Retrieve the authenticated user's JWT access token.
 */
export function getAuthToken() {
  return localStorage.getItem(CONFIG.STORAGE_KEYS.AUTH_TOKEN) || null;
}

/**
 * Persist the authenticated user's JWT access token.
 */
export function setAuthToken(token) {
  if (token) {
    localStorage.setItem(CONFIG.STORAGE_KEYS.AUTH_TOKEN, token);
  } else {
    localStorage.removeItem(CONFIG.STORAGE_KEYS.AUTH_TOKEN);
  }
}

/**
 * Retrieve the cached authenticated user profile.
 */
export function getAuthUser() {
  try {
    const raw = localStorage.getItem(CONFIG.STORAGE_KEYS.AUTH_USER);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Persist the authenticated user profile.
 */
export function setAuthUser(user) {
  if (user) {
    localStorage.setItem(CONFIG.STORAGE_KEYS.AUTH_USER, JSON.stringify(user));
  } else {
    localStorage.removeItem(CONFIG.STORAGE_KEYS.AUTH_USER);
  }
}

/**
 * Clear all authentication tokens and cached user profiles upon logout or expiration.
 */
export function clearAuth() {
  localStorage.removeItem(CONFIG.STORAGE_KEYS.AUTH_TOKEN);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.AUTH_USER);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_TOKEN);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.GUEST_ACTIVE);
  if (!getPendingCheckout()) {
    localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_ID);
    localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_IDENTIFIER);
  }
}

/**
 * Clear guest session state and tokens.
 */
export function clearGuestSession() {
  localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_ID);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_IDENTIFIER);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_TOKEN);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.GUEST_ACTIVE);
}

/**
 * Check whether a user is currently authenticated with a local token.
 */
/**
 * Check whether guest browsing mode is currently active.
 */
export function isGuestActive() {
  return localStorage.getItem(CONFIG.STORAGE_KEYS.GUEST_ACTIVE) === "true";
}

/**
 * Set or clear active guest browsing flag.
 */
export function setGuestActive(active) {
  if (active) {
    localStorage.setItem(CONFIG.STORAGE_KEYS.GUEST_ACTIVE, "true");
  } else {
    localStorage.removeItem(CONFIG.STORAGE_KEYS.GUEST_ACTIVE);
  }
}

/**
 * Retrieve pending checkout context saved before authentication.
 */
export function getPendingCheckout() {
  try {
    const raw = sessionStorage.getItem(CONFIG.STORAGE_KEYS.PENDING_CHECKOUT);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Persist pending checkout context across authentication redirects.
 */
export function setPendingCheckout(data) {
  if (data) {
    sessionStorage.setItem(CONFIG.STORAGE_KEYS.PENDING_CHECKOUT, JSON.stringify(data));
  } else {
    sessionStorage.removeItem(CONFIG.STORAGE_KEYS.PENDING_CHECKOUT);
  }
}

/**
 * Clear pending checkout context after successful checkout resumption or cancellation.
 */
export function clearPendingCheckout() {
  sessionStorage.removeItem(CONFIG.STORAGE_KEYS.PENDING_CHECKOUT);
}

export function isAuthenticated() {
  return Boolean(getAuthToken());
}

/**
 * Reset the guest session and clear session-specific local state.
 */
export function resetSession() {
  localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_ID);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_IDENTIFIER);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.SESSION_TOKEN);
  localStorage.removeItem(CONFIG.STORAGE_KEYS.COMPARE_IDS);
  return getSessionIdentifier();
}

/**
 * Format numerical prices with localized currency formatting.
 */
export function formatPrice(amount, currency = "USD") {
  if (amount === null || amount === undefined || isNaN(amount)) {
    return "N/A";
  }
  const curr = (currency || "USD").toUpperCase();
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: curr,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${curr} ${Number(amount).toFixed(2)}`;
  }
}

/**
 * Format ISO datetime strings to human-readable format.
 */
export function formatDate(isoString) {
  if (!isoString) return "N/A";
  try {
    const d = new Date(isoString);
    return d.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoString;
  }
}

/**
 * Get list of product IDs selected for comparison.
 */
export function getCompareList() {
  try {
    const raw = localStorage.getItem(CONFIG.STORAGE_KEYS.COMPARE_IDS);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

/**
 * Toggle a product ID in the comparison list (maximum 4).
 * Returns { added: boolean, list: string[], error: string | null }
 */
export function toggleCompareItem(productId) {
  let list = getCompareList();
  const exists = list.includes(productId);

  if (exists) {
    list = list.filter((id) => id !== productId);
    localStorage.setItem(CONFIG.STORAGE_KEYS.COMPARE_IDS, JSON.stringify(list));
    return { added: false, list, error: null };
  }

  if (list.length >= CONFIG.MAX_COMPARE_ITEMS) {
    return {
      added: false,
      list,
      error: `Comparison limit reached (max ${CONFIG.MAX_COMPARE_ITEMS} products). Remove one to add another.`,
    };
  }

  list.push(productId);
  localStorage.setItem(CONFIG.STORAGE_KEYS.COMPARE_IDS, JSON.stringify(list));
  return { added: true, list, error: null };
}

/**
 * Clear all items from comparison list.
 */
export function clearCompareList() {
  localStorage.removeItem(CONFIG.STORAGE_KEYS.COMPARE_IDS);
  return [];
}

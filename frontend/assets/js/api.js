/**

 * BUYWISE AI — Centralized API Client

 * Manages same-origin communication with the FastAPI backend.

 * Full RFC 9457 Problem Details support, typed errors, and clean error messages.

 */



import {
  CONFIG,
  setSessionId,
  getSessionId,
  getGuestSessionToken,
  setGuestSessionToken,
  getAuthToken,
  setAuthToken,
  setAuthUser,
  clearAuth,
  isGuestActive,
  getPendingCheckout,
  clearGuestSession,
} from "./config.js";



export class APIError extends Error {

  constructor(message, status = 500, detail = null, code = null) {

    super(message);

    this.name = "APIError";

    this.status = status;

    this.detail = detail;

    this.code = code;

  }

}



/**

 * Low-level request dispatcher with RFC 9457 error normalization.

 */

async function request(endpoint, options = {}) {
  const url = `${CONFIG.API_BASE}${endpoint}`;
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };

  const authToken = getAuthToken();
  if (authToken && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const guestToken = getGuestSessionToken();
  if (guestToken && !headers["X-Guest-Session-Token"]) {
    headers["X-Guest-Session-Token"] = guestToken;
  }

  if (options.body && typeof options.body === "object") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }

  try {
    const response = await fetch(url, { ...options, headers });

    // Handle 204 No Content
    if (response.status === 204) {
      return null;
    }

    const contentType = response.headers.get("content-type") || "";
    const isJson = contentType.includes("application/json") || contentType.includes("application/problem+json");
    const data = isJson ? await response.json() : await response.text();

    if (!response.ok) {
      if (response.status === 401 && authToken) {
        clearAuth();
        window.dispatchEvent(
          new CustomEvent("buywise:auth-expired", {
            detail: { endpoint, status: response.status },
          })
        );
      }

      // RFC 9457 Problem Details extraction

      let errorMessage = "An unexpected error occurred.";

      let detailData = null;

      let errorCode = null;



      if (typeof data === "object" && data !== null) {

        errorCode = data.code || null;

        if (typeof data.detail === "string") {

          errorMessage = data.detail;

          detailData = data.detail;

        } else if (typeof data.detail === "object" && data.detail !== null) {

          errorMessage = data.detail.message || data.title || JSON.stringify(data.detail);

          detailData = data.detail;

        } else if (data.title) {

          errorMessage = data.title;

        }

      } else if (typeof data === "string" && data.trim()) {

        errorMessage = data;

      }



      throw new APIError(errorMessage, response.status, detailData, errorCode);

    }



    return data;

  } catch (err) {

    if (err instanceof APIError) {

      throw err;

    }

    // Network failures / fetch connection errors

    throw new APIError(

      `Network connection error: ${err.message || "Failed to communicate with backend."}`,

      0,

      null,

      "NETWORK_ERROR"

    );

  }

}



export const api = {

  /**

   * System & Database Health Probe

   */

  async getHealth() {

    return request("/health?check_timeout=5");

  },



  /**

   * Product Catalog Search & Filtering

   */

  async getProducts(params = {}) {

    const query = new URLSearchParams();

    if (params.q) query.append("q", params.q);

    if (params.category && params.category !== "All") query.append("category", params.category);

    if (params.brand) query.append("brand", params.brand);

    if (params.min_price !== undefined && params.min_price !== "") query.append("min_price", params.min_price);

    if (params.max_price !== undefined && params.max_price !== "") query.append("max_price", params.max_price);

    if (params.source && params.source !== "All") query.append("source", params.source);

    if (params.order_by) query.append("order_by", params.order_by);

    if (params.seed !== undefined && params.seed !== null && params.seed !== "") query.append("seed", String(params.seed));

    if (params.expand_discovery) query.append("expand_discovery", "true");

    if (params.page) query.append("page", params.page);

    if (params.page_size) query.append("page_size", params.page_size);



    const qs = query.toString();

    return request(`/api/products${qs ? `?${qs}` : ""}`);

  },



  /**

   * Controlled Live Catalog Discovery via Channel3

   */

  async triggerDiscovery(category = null) {

    const params = new URLSearchParams();

    if (category && category !== "All") params.append("category", category);

    const qs = params.toString();

    return request(`/api/products/discover${qs ? `?${qs}` : ""}`, { method: "POST" });

  },

  

  /**

   * Live Product Search via Channel3

   */

  async searchLiveProducts(query, limit = 10) {

    const params = new URLSearchParams({

      q: query,

      limit: String(limit),

    });



    return request(`/api/products/search?${params.toString()}`);

  },



  /**

   * AI Shopping Assistant — Conversational Query & Recommendations

   * Dispatches natural language shopping query to the backend Gemini AI orchestrator.

   * Backend endpoint: POST /api/assistant/query

   *

   * @param {string} query - Natural-language query from the user

   * @param {Array<Object>} [conversationContext=[]] - Bounded previous dialogue [{'role': 'user'|'assistant', 'content': '...'}]

   * @param {string|null} [sessionId=null] - Optional explicit session ID; defaults to current session from getSessionId()

   * @returns {Promise<Object>} AssistantQueryResponse matching backend schema

   */

  async queryAssistant(query, conversationContext = [], sessionId = null) {

    if (!query || !query.trim()) {

      throw new Error("Assistant query text is required.");

    }



    const sid = sessionId || getSessionId();



    const res = await request("/api/assistant/query", {

      method: "POST",

      body: {

        session_id: sid,

        query: query.trim(),

        conversation_context: Array.isArray(conversationContext) ? conversationContext : [],

      },

    });



    if (res && res.session_id) {

      setSessionId(res.session_id);

    }



    return res;

  },





  /**

   * Single Product Specifications & Offers Detail

   */

  async getProductDetail(productId) {

    if (!productId) throw new Error("Product ID is required.");

    return request(`/api/products/${encodeURIComponent(productId)}`);

  },



  /**

   * Product Intelligence & Historical Price Analytics

   * Backend endpoint: GET /api/products/{productId}/intelligence

   */

  async getProductIntelligence(productId) {

    if (!productId) throw new Error("Product ID is required.");

    return request(`/api/products/${encodeURIComponent(productId)}/intelligence`);

  },



  /**

   * Multi-Product Specifications & Price Matrix Comparison

   */

  async compareProducts(productIds) {

    if (!productIds || productIds.length === 0) {

      return { products: [], compared_count: 0, comparison_matrix: {}, price_summary: {} };

    }

    const query = new URLSearchParams();

    for (const id of productIds) {

      query.append("ids", id);

    }

    return request(`/api/products/compare?${query.toString()}`);

  },



  /**

   * Spending Mandate - Retrieve Active Mandate by Session ID

   */

  async getMandate(sessionId) {

    if (!sessionId) return null;

    try {

      const res = await request(`/api/mandates/${encodeURIComponent(sessionId)}`);

      if (res && res.session_id) {

        setSessionId(res.session_id);

      }

      return res;

    } catch (err) {

      if (err.status === 404) {

        return null; // No mandate exists yet for this session

      }

      throw err;

    }

  },



  /**

   * Spending Mandate - Create or Configure

   */

  async createMandate(data) {

    const res = await request("/api/mandates", {

      method: "POST",

      body: data,

    });

    if (res && res.session_id) {

      setSessionId(res.session_id);

    }

    return res;

  },



  /**

   * Spending Mandate - Update Existing Budget Ceilings

   */

  async updateMandate(sessionId, data) {

    const res = await request(`/api/mandates/${encodeURIComponent(sessionId)}`, {

      method: "PUT",

      body: data,

    });

    if (res && res.session_id) {

      setSessionId(res.session_id);

    }

    return res;

  },



  /**

   * Spending Mandate - Deterministic Purchase Verification Check

   */

  async validatePurchase(sessionId, itemData) {

    return request(`/api/mandates/${encodeURIComponent(sessionId)}/validate`, {

      method: "POST",

      body: itemData,

    });

  },



  /**

   * Checkout & Payment Order History

   */

  async getOrderHistory(sessionId, page = 1, pageSize = 20) {

    if (!sessionId) return { orders: [], total: 0, session_id: "" };

    try {

      return await request(

        `/api/checkout/history/${encodeURIComponent(sessionId)}?page=${page}&page_size=${pageSize}`

      );

    } catch (err) {

      if (err.status === 404) {

        return { orders: [], total: 0, session_id: sessionId };

      }

      throw err;

    }

  },



  /**

   * Payment Order Details & Verified Settlement Records

   */

  async getOrderDetail(orderId, sessionId) {

    return request(

      `/api/checkout/orders/${encodeURIComponent(orderId)}?session_id=${encodeURIComponent(sessionId)}`

    );

  },

/**

   * Personal Shopping Analytics

   * Authoritative settled spending metrics, monthly trends, breakdowns, and mandate utilization.

   *

   * @param {string|null} [sessionId=null] - Optional explicit session ID; defaults to getSessionId()

   * @returns {Promise<Object>} PersonalAnalyticsResponse matching backend schema

   */

  async getAnalytics(sessionId = null, isRetry = false) {
    const sid = sessionId || getSessionId();
    if (!sid) {
      return null;
    }
    try {
      const res = await request(`/api/checkout/analytics/${encodeURIComponent(sid)}`);
      if (res && res.session_id) {
        setSessionId(res.session_id);
      }
      return res;
    } catch (err) {
      // If an unauthenticated guest attempts to access a session already claimed by an account
      if (
        !isRetry &&
        !getAuthToken() &&
        err.status === 403 &&
        (err.message?.includes("belongs to another account") || err.message?.includes("Shopping session belongs"))
      ) {
        console.warn("Stored guest session belongs to another account. Bootstrapping fresh guest session...");
        clearGuestSession();
        const fresh = await this.bootstrapGuestSession(true);
        if (fresh && fresh.session_id) {
          return this.getAnalytics(fresh.session_id, true);
        }
      }
      throw err;
    }
  },



  /**

   * Create Checkout Order

   * Payload: CheckoutOrderCreate (supports budget_override_confirmed & acknowledged_budget_excess)

   */

  async createCheckout(orderData) {

    const res = await request("/api/checkout/orders", {

      method: "POST",

      body: orderData,

    });

    if (res && res.session_id) {

      setSessionId(res.session_id);

    }

    return res;

  },



  /**

   * Capture Checkout Order

   */

  async captureOrder(orderId, sessionId = null) {
    const sid = sessionId || getSessionId();
    return request(
      `/api/checkout/orders/${encodeURIComponent(orderId)}/capture?session_id=${encodeURIComponent(sid)}`,
      {
        method: "POST",
      }
    );
  },

  /**
   * Cancel Checkout Order
   */
  async cancelOrder(orderId, sessionId = null) {
    const sid = sessionId || getSessionId();
    return request(
      `/api/checkout/orders/${encodeURIComponent(orderId)}/cancel?session_id=${encodeURIComponent(sid)}`,
      {
        method: "POST",
      }
    );
  },

  /**
   * Lookup Order by PayPal Order ID (token)
   */
  async lookupOrderByPayPalId(paypalOrderId, sessionId = null) {
    const sid = sessionId || getSessionId();
    return request(
      `/api/checkout/orders/by-paypal-id/${encodeURIComponent(paypalOrderId)}?session_id=${encodeURIComponent(sid)}`
    );
  },

  /**
   * User Authentication & Session Linking
   */

  async register({ name, email, password, guest_session_id = null, guest_session_token = null }) {
    const payload = {
      name,
      email,
      password,
      guest_session_id: guest_session_id || getSessionId(),
      guest_session_token: guest_session_token || getGuestSessionToken(),
    };
    const res = await request("/api/auth/register", {
      method: "POST",
      body: payload,
    });
    if (res && res.access_token) {
      setAuthToken(res.access_token);
      setAuthUser(res.user);
      if (res.linked_session_id) {
        setSessionId(res.linked_session_id);
      }
      window.dispatchEvent(new CustomEvent("buywise:auth-changed", { detail: { user: res.user } }));
    }
    return res;
  },

  async login({ email, password, guest_session_id = null, guest_session_token = null }) {
    // Only supply guest session credentials if explicitly provided or if an active guest session exists with valid proof
    let sid = guest_session_id;
    let stoken = guest_session_token;

    if (!sid && (isGuestActive() || getPendingCheckout())) {
      const activeToken = getGuestSessionToken();
      if (activeToken) {
        sid = getSessionId();
        stoken = activeToken;
      }
    }

    const payload = {
      email,
      password,
      guest_session_id: sid || null,
      guest_session_token: stoken || null,
    };
    const res = await request("/api/auth/login", {
      method: "POST",
      body: payload,
    });
    if (res && res.access_token) {
      setAuthToken(res.access_token);
      setAuthUser(res.user);
      if (res.linked_session_id) {
        setSessionId(res.linked_session_id);
      }
      window.dispatchEvent(new CustomEvent("buywise:auth-changed", { detail: { user: res.user } }));
    }
    return res;
  },

  async getMe() {
    const res = await request("/api/auth/me");
    if (res) {
      setAuthUser(res);
    }
    return res;
  },

  async logout() {
    try {
      await request("/api/auth/logout", {
        method: "POST",
      });
    } catch (e) {
      console.warn("Logout error from server:", e);
    } finally {
      clearAuth();
      window.dispatchEvent(new CustomEvent("buywise:auth-changed", { detail: { user: null } }));
    }
    return true;
  },

  async linkSession({ guest_session_id, guest_session_token, session_identifier = null }) {
    return request("/api/auth/link-session", {
      method: "POST",
      body: {
        guest_session_id,
        guest_session_token,
        session_identifier,
      },
    });
  },

  async bootstrapGuestSession(force = false) {
    if (!force) {
      const existingToken = getGuestSessionToken();
      if (existingToken) {
        return { session_token: existingToken, session_id: getSessionId() };
      }
    }
    try {
      const res = await request("/api/auth/session-token", {
        method: "POST",
        body: {},
      });
      if (res && res.session_token) {
        setSessionId(res.session_id);
        if (res.session_identifier) {
          localStorage.setItem(CONFIG.STORAGE_KEYS.SESSION_IDENTIFIER, res.session_identifier);
        }
        setGuestSessionToken(res.session_token);
      }
      return res;
    } catch (err) {
      console.warn("Could not bootstrap guest session token:", err);
      return null;
    }
  },
};


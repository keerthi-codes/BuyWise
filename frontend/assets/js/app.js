/**

 * BUYWISE AI — Application Orchestrator & Client-Side Navigation

 * Coordinates view transitions, API calls, event delegation, and reactive UI state.

 */



import {
  CONFIG,
  getSessionId,
  getSessionIdentifier,
  resetSession,
  formatPrice,
  formatDate,
  getCompareList,
  toggleCompareItem,
  clearCompareList,
  getGuestSessionToken,
  setGuestSessionToken,
  getAuthToken,
  setAuthToken,
  getAuthUser,
  setAuthUser,
  clearAuth,
  isAuthenticated,
  isGuestActive,
  setGuestActive,
  getPendingCheckout,
  setPendingCheckout,
  clearPendingCheckout,
  clearGuestSession,
} from "./config.js";



import { api, APIError } from "./api.js?v=1.1";



import {

  renderProductCard,

  renderProductDetailModal,

  renderStatusBadge,

  renderSkeletonCard,

  renderEmptyState,

  showToast,

  PLACEHOLDER_IMAGE,

  getSafeImageUrl,

  IMG_FALLBACK_ATTR,

  escapeHtml,

  renderAssistantWelcome,

  renderAssistantUserMessage,

  renderAssistantMessage,

  renderAssistantLoading,

  renderAssistantError,

  renderAnalyticsSkeleton,

  renderAnalyticsError,

  renderAnalyticsEmptyState,

  renderAnalyticsDashboard,

  renderProductIntelligenceSkeleton,

  renderProductIntelligenceError,

  renderProductIntelligenceContent,

  renderComparisonAIInsights,
  renderBudgetOverrideModal,
  renderPayPalApprovalModal,
  renderBuywiseLogo,
  renderLoginPage,
  renderRegisterPage,
  renderUserNavWidget,
  renderAuthRequiredModal,
  renderOrderReviewModal,
} from "./components.js";

/**
 * Prompt user with explicit budget override confirmation dialogue if item exceeds preferred budget.
 * Returns Promise<boolean>: true if confirmed, false if cancelled.
 */
export async function promptBudgetOverrideConfirmation({ productName, price, currency, preferredBudget, excessAmount }) {
  return new Promise((resolve) => {
    const modalHtml = renderBudgetOverrideModal({
      productName,
      price,
      currency,
      preferredBudget,
      excessAmount,
    });
    openModal(modalHtml);

    const modalEl = document.getElementById("modal-content") || document.getElementById("app-modal");
    if (!modalEl) {
      resolve(false);
      return;
    }

    const confirmBtn = modalEl.querySelector(".js-btn-override-confirm");
    const cancelBtn = modalEl.querySelector(".js-btn-override-cancel");

    const cleanup = () => {
      closeModal();
    };

    if (confirmBtn) {
      confirmBtn.addEventListener("click", () => {
        cleanup();
        resolve(true);
      }, { once: true });
    }

    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => {
        cleanup();
        resolve(false);
      }, { once: true });
    }
  });
}

/**
 * Prompt guest with an Authentication Required modal when attempting to place an order.
 * Saves checkout context and allows user to Sign In, Register, or Cancel.
 */
export function promptAuthRequiredModal(checkoutParams) {
  const modalHtml = renderAuthRequiredModal(checkoutParams);
  openModal(modalHtml);

  const modalEl = document.getElementById("modal-content") || document.getElementById("app-modal");
  if (!modalEl) return;

  modalEl.querySelectorAll(".js-btn-close-auth-modal").forEach((b) => {
    b.addEventListener("click", () => {
      closeModal();
    });
  });

  const loginBtn = modalEl.querySelector(".js-btn-auth-modal-login");
  if (loginBtn) {
    loginBtn.addEventListener("click", () => {
      closeModal();
      window.location.hash = "#login";
    });
  }

  const regBtn = modalEl.querySelector(".js-btn-auth-modal-register");
  if (regBtn) {
    regBtn.addEventListener("click", () => {
      closeModal();
      window.location.hash = "#register";
    });
  }
}

/**
 * Prompt user with Order Review Modal when returning from successful authentication.
 * Confirms order details before launching PayPal Sandbox approval without creating duplicates.
 */
export function promptOrderReviewModal(checkoutParams) {
  const modalHtml = renderOrderReviewModal(checkoutParams);
  openModal(modalHtml);

  const modalEl = document.getElementById("modal-content") || document.getElementById("app-modal");
  if (!modalEl) return;

  const cancelBtns = modalEl.querySelectorAll(".js-btn-cancel-resumed-order");
  cancelBtns.forEach((b) => {
    b.addEventListener("click", () => {
      closeModal();
      showToast("Order cancelled. You can continue browsing.", "info");
      window.location.hash = "#home";
    });
  });

  const confirmBtn = modalEl.querySelector(".js-btn-confirm-resumed-order");
  if (confirmBtn) {
    confirmBtn.addEventListener("click", async () => {
      closeModal();
      await executeCheckoutFlow(null, checkoutParams);
    });
  }
}

/**
 * Prompt user to complete PayPal approval in external window and explicitly confirm settlement capture.
 * Returns Promise<{ success: boolean, cancelled?: boolean, order?: object }>
 */
export async function promptPayPalApprovalAndCapture({ order, productName, retailerName, price, currency }) {
  return new Promise((resolve) => {
    const modalHtml = renderPayPalApprovalModal({
      order,
      productName,
      retailerName,
      price,
      currency,
    });
    openModal(modalHtml);

    // Auto-attempt to open the PayPal approval window
    let popup = null;
    if (order.approval_url) {
      try {
        popup = window.open(order.approval_url, "paypal_approval_window", "width=600,height=750,menubar=no,toolbar=no");
        if (!popup || popup.closed || typeof popup.closed === "undefined") {
          console.warn("Popup blocked by browser. User can click 'Launch PayPal Window'.");
        }
      } catch (e) {
        console.warn("Could not auto-open PayPal approval window:", e);
      }
    }

    const modalEl = document.getElementById("modal-content") || document.getElementById("app-modal");
    if (!modalEl) {
      resolve({ success: false, cancelled: true });
      return;
    }

    const confirmBtn = modalEl.querySelector(".js-btn-confirm-capture");
    const cancelBtn = modalEl.querySelector(".js-btn-cancel-checkout");

    const cleanupListeners = () => {
      window.removeEventListener("message", handleMessage);
      window.removeEventListener("storage", handleStorage);
    };

    const performCapture = async (payerId = null) => {
      if (confirmBtn.disabled && confirmBtn.getAttribute("data-capturing") === "true") return;
      confirmBtn.disabled = true;
      confirmBtn.setAttribute("data-capturing", "true");
      if (cancelBtn) cancelBtn.disabled = true;
      const originalText = confirmBtn.innerHTML;
      confirmBtn.innerHTML = `
        <svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white inline-block" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
        </svg>
        <span>Verifying PayPal Approval &amp; Settling...</span>
      `;

      try {
        const capturedOrder = await api.captureOrder(order.id, order.session_id || getSessionId());

        cleanupListeners();
        closeModal();
        openModal(renderOrderReceiptModal(capturedOrder));
        showToast("Payment successfully settled via PayPal Sandbox!", "success");

        // Invalidate cached analytics & refresh views
        state.analyticsData = null;
        state.analyticsLastFetched = 0;

        if (state.currentRoute === "orders") {
          loadOrdersSection();
        }

        const analyticsContainer = document.getElementById("home-analytics-container");
        if (analyticsContainer) {
          loadHomeAnalytics(true);
        }

        resolve({ success: true, order: capturedOrder });
      } catch (captureErr) {
        confirmBtn.removeAttribute("data-capturing");
        const detail = captureErr.detail || {};
        if (captureErr.status === 422 || detail.error === "order_not_approved") {
          showToast(detail.message || captureErr.message || "PayPal order has not yet been approved. Please approve the payment in the PayPal window first.", "warning");
        } else {
          showToast(`Payment capture failed: ${captureErr.message}`, "error");
        }
        confirmBtn.disabled = false;
        if (cancelBtn) cancelBtn.disabled = false;
        confirmBtn.innerHTML = originalText;
      }
    };

    const handleMessage = async (event) => {
      if (!event.data || typeof event.data !== "object") return;
      if (event.data.type === "BUYWISE_PAYPAL_APPROVED") {
        if (event.data.token === order.paypal_order_id) {
          console.log("Cross-window approval notification received:", event.data);
          showToast("PayPal approval verified! Capturing settlement...", "info");
          await performCapture(event.data.payerId);
        }
      } else if (event.data.type === "BUYWISE_PAYPAL_CANCELLED") {
        if (event.data.token === order.paypal_order_id) {
          cleanupListeners();
          closeModal();
          showToast("PayPal checkout was cancelled. No payment was captured.", "info");
          resolve({ success: false, cancelled: true });
        }
      }
    };

    const handleStorage = async (event) => {
      if (event.key === "buywise_paypal_return_event" && event.newValue) {
        try {
          const payload = JSON.parse(event.newValue);
          if (payload.token === order.paypal_order_id) {
            if (payload.type === "BUYWISE_PAYPAL_APPROVED") {
              console.log("Cross-tab storage approval event received:", payload);
              showToast("PayPal approval verified! Capturing settlement...", "info");
              await performCapture(payload.payerId);
            } else if (payload.type === "BUYWISE_PAYPAL_CANCELLED") {
              cleanupListeners();
              closeModal();
              showToast("PayPal checkout was cancelled. No payment was captured.", "info");
              resolve({ success: false, cancelled: true });
            }
          }
        } catch (e) {
          console.warn("Could not parse storage return event:", e);
        }
      }
    };

    window.addEventListener("message", handleMessage);
    window.addEventListener("storage", handleStorage);

    if (cancelBtn) {
      cancelBtn.addEventListener("click", async () => {
        cancelBtn.disabled = true;
        if (confirmBtn) confirmBtn.disabled = true;
        cancelBtn.textContent = "Cancelling...";

        try {
          await api.cancelOrder(order.id, order.session_id || getSessionId());
        } catch (cancelErr) {
          console.warn("Cancel order request failed:", cancelErr);
        } finally {
          cleanupListeners();
          closeModal();
          showToast("PayPal checkout was cancelled. No payment was captured.", "info");
          resolve({ success: false, cancelled: true });
        }
      });
    }

    if (confirmBtn) {
      confirmBtn.addEventListener("click", async () => {
        await performCapture();
      });
    }
  });
}

// Global In-Memory State

const state = {

  currentRoute: "home",

  activeMandate: null,

  exploreProducts: [],

  exploreFilters: {

    q: "",

    category: "All",

    brand: "",

    min_price: "",

    max_price: "",

    source: "All",

    order_by: "random",

    seed: Math.floor(Math.random() * 1000000),

    page: 1,

    page_size: 12,

  },

  exploreTotalPages: 1,

  selectedProductForModal: null,

  assistantConversation: [],

  assistantIsLoading: false,

  analyticsData: null,

  analyticsLastFetched: 0,

  analyticsSessionId: null,

  currentModalIntelligence: null,

};



/**
 * Handle PayPal return or cancellation callback parameters in current URL.
 * Supports cross-window message to opener, localStorage broadcast, or direct same-window settlement.
 */
export async function handlePayPalCallback() {
  if (typeof window === "undefined" || !window.location || !window.location.search) return;

  const urlParams = new URLSearchParams(window.location.search);
  const isReturn = urlParams.get("paypal_return") === "true" || (urlParams.has("token") && urlParams.has("PayerID"));
  const isCancel = urlParams.get("paypal_cancel") === "true";
  const token = urlParams.get("token");
  const payerId = urlParams.get("PayerID");

  if (!isReturn && !isCancel) return;

  // Case 1: Child popup or dedicated approval window
  if (window.opener && !window.opener.closed) {
    try {
      if (isReturn && token) {
        window.opener.postMessage({
          type: "BUYWISE_PAYPAL_APPROVED",
          token: token,
          payerId: payerId,
        }, window.location.origin);
      } else if (isCancel) {
        window.opener.postMessage({
          type: "BUYWISE_PAYPAL_CANCELLED",
          token: token,
        }, window.location.origin);
      }
    } catch (e) {
      console.warn("Could not postMessage to opener:", e);
    }

    try {
      localStorage.setItem("buywise_paypal_return_event", JSON.stringify({
        type: isReturn ? "BUYWISE_PAYPAL_APPROVED" : "BUYWISE_PAYPAL_CANCELLED",
        token: token,
        payerId: payerId,
        timestamp: Date.now(),
      }));
    } catch (e) {}

    // Present clear confirmation message inside popup window and close automatically
    if (document.body) {
      document.body.innerHTML = `
        <div style="background:#0f172a;color:#fff;font-family:ui-sans-serif,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;padding:24px;text-align:center;">
          <div style="font-size:36px;margin-bottom:12px;">${isReturn ? "✅" : "ℹï¸"}</div>
          <h2 style="margin:0 0 8px 0;font-size:18px;font-weight:700;">${isReturn ? "PayPal Approval Received" : "Checkout Cancelled"}</h2>
          <p style="color:#94a3b8;font-size:13px;max-width:320px;margin:0 0 16px 0;line-height:1.5;">${isReturn ? "Settlement capture is being verified in BUYWISE AI. You can close this window." : "PayPal checkout was cancelled. You can close this window."}</p>
          <button onclick="window.close()" style="background:#334155;color:#fff;border:1px solid #475569;padding:8px 18px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;">Close Window</button>
        </div>
      `;
    }

    setTimeout(() => {
      try { window.close(); } catch (e) {}
    }, 1500);
    return;
  }

  // Case 2: Main browser window itself returned from PayPal (e.g. mobile or same-tab redirect)
  if (isReturn && token) {
    const cleanUrl = window.location.pathname + (window.location.hash || "");
    window.history.replaceState({}, document.title, cleanUrl);

    try {
      showToast("Verifying PayPal order approval & settling...", "info");
      const order = await api.lookupOrderByPayPalId(token, getSessionId());
      if (order && order.id) {
        const capturedOrder = await api.captureOrder(order.id, order.session_id || getSessionId());
        openModal(renderOrderReceiptModal(capturedOrder));
        showToast("Payment successfully settled via PayPal Sandbox!", "success");
        state.analyticsData = null;
        state.analyticsLastFetched = 0;
        if (state.currentRoute === "orders") {
          loadOrdersSection();
        }
        const analyticsContainer = document.getElementById("home-analytics-container");
        if (analyticsContainer) {
          loadHomeAnalytics(true);
        }
      }
    } catch (err) {
      console.error("Direct return capture failed:", err);
      showToast(`Settlement verification failed: ${err.message}`, "error");
    }
  } else if (isCancel) {
    const cleanUrl = window.location.pathname + (window.location.hash || "");
    window.history.replaceState({}, document.title, cleanUrl);
    showToast("PayPal checkout was cancelled. No payment was captured.", "info");
  }
}

/**
 * Initialize Application on DOM Ready
 */
document.addEventListener("DOMContentLoaded", () => {
  initNavigation();
  initHealthProbe();
  initSessionBadge();
  initModalListeners();
  initComparisonTray();
  initAuthUI();

  // Ensure fresh guests have an established proof-of-possession token in background
  api.bootstrapGuestSession().then(() => {
    initSessionBadge();
  });

  // Handle incoming PayPal approval or cancellation callback
  handlePayPalCallback();

  // Router listener
  window.addEventListener("hashchange", handleRouteChange);
  handleRouteChange(); // Initial route dispatch
});

/**
 * Client-Side Hash Router
 */
function handleRouteChange() {
  let hash = window.location.hash.replace("#", "");

  // Hybrid Authentication Guard:
  // First-time visitors without active guest browsing or authentication see Welcome / Login
  if (!isAuthenticated() && !isGuestActive()) {
    if (hash !== "login" && hash !== "register") {
      hash = "login";
      window.location.hash = "#login";
    }
  } else if (!hash) {
    hash = "home";
  }

  const validRoutes = ["home", "assistant", "explore", "compare", "mandate", "orders", "login", "register"];
  const route = validRoutes.includes(hash) ? hash : "home";

  state.currentRoute = route;

  // Update navigation visual indicators
  document.querySelectorAll(".nav-link").forEach((link) => {
    const target = link.getAttribute("href").replace("#", "");
    if (target === route) {
      link.classList.add("text-white", "bg-slate-800/90", "border-slate-700");
      link.classList.remove("text-slate-400", "border-transparent");
    } else {
      link.classList.remove("text-white", "bg-slate-800/90", "border-slate-700");
      link.classList.add("text-slate-400", "border-transparent");
    }
  });

  // Switch visible section
  document.querySelectorAll(".page-section").forEach((sec) => {
    sec.classList.add("hidden");
  });

  const activeSec = document.getElementById(`section-${route}`);
  if (activeSec) {
    activeSec.classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // Load section-specific data
  switch (route) {
    case "home":
      loadHomeSection();
      break;

    case "assistant":
      loadAssistantSection();
      break;

    case "explore":
      loadExploreSection();
      break;

    case "compare":
      loadCompareSection();
      break;

    case "mandate":
      loadMandateSection();
      break;

    case "orders":
      loadOrdersSection();
      break;

    case "login":
      loadLoginSection();
      break;

    case "register":
      loadRegisterSection();
      break;
  }

  updateComparisonTray();
}

/**
 * Initialize Authentication UI and state synchronization listeners
 */
function initAuthUI() {
  updateAuthNav();

  // Listen for login/logout/profile changes
  window.addEventListener("buywise:auth-changed", () => {
    updateAuthNav();
    if (state.currentRoute === "orders") {
      loadOrdersSection();
    } else if (state.currentRoute === "mandate") {
      loadMandateSection();
    }
  });

  // Listen for JWT expiration
  window.addEventListener("buywise:auth-expired", () => {
    showToast("Your session has expired. Please sign in again.", "warning");
    updateAuthNav();
    if (["orders", "mandate"].includes(state.currentRoute)) {
      window.location.hash = "#login";
    }
  });

  // If user has token, verify profile in background
  if (isAuthenticated()) {
    api.getMe().catch((err) => {
      console.warn("Background auth verification:", err.message);
    });
  }
}

/**
 * Update desktop and mobile navigation auth widgets
 */
function updateAuthNav() {
  const user = getAuthUser();
  const authNavSlot = document.getElementById("auth-nav-slot");
  const mobileAuthSlot = document.getElementById("mobile-auth-nav-slot");

  if (authNavSlot) {
    authNavSlot.innerHTML = renderUserNavWidget(user);
    const logoutBtn = authNavSlot.querySelector("#btn-header-logout");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", handleLogout);
    }
  }

  if (mobileAuthSlot) {
    if (user) {
      mobileAuthSlot.innerHTML = `
        <div class="flex items-center justify-between px-3 py-2">
          <div class="flex items-center gap-2">
            <span class="w-6 h-6 rounded-lg bg-burnt-orange/20 text-burnt-orange flex items-center justify-center font-bold text-xs border border-burnt-orange/40">
              ${(user.name || user.email || "U").substring(0, 1).toUpperCase()}
            </span>
            <span class="text-xs text-slate-200 font-semibold truncate max-w-[150px]">${escapeHtml(user.name || user.email)}</span>
          </div>
          <button type="button" class="js-mobile-logout text-xs text-rose-400 hover:text-rose-300 font-semibold">Sign Out</button>
        </div>
      `;
      const mobileLogout = mobileAuthSlot.querySelector(".js-mobile-logout");
      if (mobileLogout) {
        mobileLogout.addEventListener("click", handleLogout);
      }
    } else {
      mobileAuthSlot.innerHTML = `
        <div class="grid grid-cols-2 gap-2 p-1">
          <a href="#login" class="text-center py-2 text-xs font-semibold rounded-lg bg-slate-900 text-slate-200 hover:bg-slate-800">Sign In</a>
          <a href="#register" class="text-center py-2 text-xs font-bold rounded-lg bg-burnt-orange text-white hover:bg-amber-600">Create Account</a>
        </div>
      `;
    }
  }
}

/**
 * Handle user logout
 */
async function handleLogout() {
  try {
    await api.logout();
    setGuestActive(false);
    if (!getPendingCheckout()) {
      clearGuestSession();
    }
    clearPendingCheckout();
    state.analyticsData = null;
    state.analyticsSessionId = null;
    state.analyticsLastFetched = 0;
    showToast("Signed out successfully.", "info");
    window.location.hash = "#login";
  } catch (err) {
    showToast(err.message || "Logout failed.", "error");
  }
}

/**
 * Render and bind the Login Section
 */
function loadLoginSection(error = null) {
  const container = document.getElementById("section-login");
  if (!container) return;

  const pendingCheckout = getPendingCheckout();
  container.innerHTML = renderLoginPage({ error, pendingCheckout });

  // Bind Browse as Guest buttons
  container.querySelectorAll(".js-btn-browse-guest").forEach((btn) => {
    btn.addEventListener("click", async () => {
      setGuestActive(true);
      await api.bootstrapGuestSession(true);
      window.location.hash = "#home";
    });
  });

  const form = container.querySelector("#login-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const emailInput = form.querySelector("#login-email");
      const passInput = form.querySelector("#login-password");
      const submitBtn = form.querySelector("#btn-login-submit");

      const email = emailInput ? emailInput.value.trim() : "";
      const password = passInput ? passInput.value : "";

      if (!email || !password) {
        loadLoginSection("Please provide both your email address and password.");
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `
          <svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white inline-block" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          Signing In...
        `;
      }

      try {
        const res = await api.login({ email, password });
        showToast(`Welcome back, ${res.user?.name || "Shopper"}!`, "success");
        const pending = getPendingCheckout();
        if (pending) {
          clearPendingCheckout();
          promptOrderReviewModal(pending);
        } else {
          window.location.hash = "#home";
        }
      } catch (err) {
        loadLoginSection(err.message || "Invalid email or password.");
      }
    });
  }

  // Bind password visibility toggles
  container.querySelectorAll(".js-toggle-password").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-target");
      const input = document.getElementById(targetId);
      if (input) {
        const isPass = input.type === "password";
        input.type = isPass ? "text" : "password";
        btn.textContent = isPass ? "Hide" : "Show";
      }
    });
  });
}

/**
 * Render and bind the Registration Section
 */
function loadRegisterSection(error = null) {
  const container = document.getElementById("section-register");
  if (!container) return;

  const pendingCheckout = getPendingCheckout();
  container.innerHTML = renderRegisterPage({ error, pendingCheckout });

  // Bind Browse as Guest buttons
  container.querySelectorAll(".js-btn-browse-guest").forEach((btn) => {
    btn.addEventListener("click", async () => {
      setGuestActive(true);
      await api.bootstrapGuestSession(true);
      window.location.hash = "#home";
    });
  });

  const form = container.querySelector("#register-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const nameInput = form.querySelector("#register-name");
      const emailInput = form.querySelector("#register-email");
      const passInput = form.querySelector("#register-password");
      const confirmInput = form.querySelector("#register-confirm-password");
      const submitBtn = form.querySelector("#btn-register-submit");

      const name = nameInput ? nameInput.value.trim() : "";
      const email = emailInput ? emailInput.value.trim() : "";
      const password = passInput ? passInput.value : "";
      const confirmPassword = confirmInput ? confirmInput.value : "";

      if (!name || !email || !password || !confirmPassword) {
        loadRegisterSection("Please fill in all required fields.");
        return;
      }

      if (password.length < 8) {
        loadRegisterSection("Password must be at least 8 characters long.");
        return;
      }

      if (new TextEncoder().encode(password).length > 72) {
        loadRegisterSection("Password cannot exceed 72 bytes.");
        return;
      }

      if (password !== confirmPassword) {
        loadRegisterSection("Passwords do not match. Please re-enter.");
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `
          <svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white inline-block" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          Creating Account...
        `;
      }

      try {
        const res = await api.register({ name, email, password });
        showToast(`Account created! Welcome to BUYWISE AI, ${res.user?.name || "Shopper"}.`, "success");
        const pending = getPendingCheckout();
        if (pending) {
          clearPendingCheckout();
          promptOrderReviewModal(pending);
        } else {
          window.location.hash = "#home";
        }
      } catch (err) {
        loadRegisterSection(err.message || "Failed to create account.");
      }
    });
  }

  // Bind password visibility toggles
  container.querySelectorAll(".js-toggle-password").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-target");
      const input = document.getElementById(targetId);
      if (input) {
        const isPass = input.type === "password";
        input.type = isPass ? "text" : "password";
        btn.textContent = isPass ? "Hide" : "Show";
      }
    });
  });
}



/**

 * Initialize Desktop & Mobile Navigation

 */

function initNavigation() {

  const mobileToggle = document.getElementById("mobile-menu-toggle");

  const mobileMenu = document.getElementById("mobile-menu");



  if (mobileToggle && mobileMenu) {

    mobileToggle.addEventListener("click", () => {

      mobileMenu.classList.toggle("hidden");

    });



    mobileMenu.querySelectorAll("a").forEach((link) => {

      link.addEventListener("click", () => {

        mobileMenu.classList.add("hidden");

      });

    });

  }

}



/**

 * System Health Probe

 */

async function initHealthProbe() {
  const pill = document.getElementById("system-health-pill");
  if (!pill) return;

  const probe = async () => {
    try {
      const health = await api.getHealth();
      if (health && health.status === "healthy" && health.database === "connected") {
        pill.innerHTML = `
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span class="text-xs font-semibold text-emerald-400">System Online</span>
        `;
        pill.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20";
      } else {
        pill.innerHTML = `
          <span class="w-2 h-2 rounded-full bg-amber-400"></span>
          <span class="text-xs font-semibold text-amber-400">Degraded</span>
        `;
        pill.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/10 border border-amber-500/20";
      }
    } catch (err) {
      console.warn("Backend health probe failed:", err);
      pill.innerHTML = `
        <span class="w-2 h-2 rounded-full bg-rose-400"></span>
        <span class="text-xs font-semibold text-rose-400">Offline</span>
      `;
      pill.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-500/10 border border-rose-500/20";
    }
  };

  await probe();

  if (!window._healthProbeInterval) {
    window._healthProbeInterval = setInterval(probe, 30000);
  }
}



/**

 * Guest Session Header Display

 */

function initSessionBadge() {

  const badge = document.getElementById("session-badge");

  const identifier = getSessionIdentifier();



  if (badge) {

    badge.title = `Guest Session: ${identifier} (Click to reset)`;

    badge.innerHTML = `

      <span class="w-2 h-2 rounded-full bg-slate-400"></span>

      <span class="text-xs text-slate-300 font-mono">${identifier.substring(0, 14)}...</span>

    `;

    badge.addEventListener("click", () => {

      if (confirm("Reset current guest session? This generates a fresh guest identifier.")) {

        const newId = resetSession();

        showToast("Guest session reset successfully.", "info");

        state.analyticsData = null;

        state.analyticsLastFetched = 0;

        state.analyticsSessionId = null;

        initSessionBadge();

        handleRouteChange();

      }

    });

  }

}



/**

 * Floating Comparison Tray

 */

function initComparisonTray() {

  const clearBtn = document.getElementById("tray-clear-compare");

  if (clearBtn) {

    clearBtn.addEventListener("click", () => {

      clearCompareList();

      updateComparisonTray();

      // Re-render explore cards if in explore view

      if (state.currentRoute === "explore") {

        renderExploreGrid();

      }

      showToast("Comparison list cleared.", "info");

    });

  }

}



function updateComparisonTray() {

  const tray = document.getElementById("comparison-floating-tray");

  const countBadge = document.getElementById("tray-compare-count");

  if (!tray || !countBadge) return;



  const list = getCompareList();

  if (list.length > 0) {

    countBadge.textContent = list.length;

    tray.classList.remove("translate-y-24", "opacity-0");

  } else {

    tray.classList.add("translate-y-24", "opacity-0");

  }

}



/**

 * Global Modal Event Handlers

 */

function initModalListeners() {

  const modal = document.getElementById("app-modal");

  if (!modal) return;



  // Close when clicking backdrop or close buttons

  modal.addEventListener("click", (e) => {

    if (e.target === modal || e.target.closest(".js-close-modal")) {

      closeModal();

    }

  });



  // ESC key listener

  document.addEventListener("keydown", (e) => {

    if (e.key === "Escape" && !modal.classList.contains("hidden")) {

      closeModal();

    }

  });

}



function openModal(htmlContent) {

  const modal = document.getElementById("app-modal");

  const content = document.getElementById("modal-content");

  if (!modal || !content) return;



  content.innerHTML = htmlContent;

  modal.classList.remove("hidden");

  document.body.classList.add("overflow-hidden");

}



function closeModal() {

  const modal = document.getElementById("app-modal");

  if (!modal) return;

  modal.classList.add("hidden");

  document.body.classList.remove("overflow-hidden");

  state.currentModalIntelligence = null;

}



// -------------------------------------------------------------------------

// SECTION: HOME

// -------------------------------------------------------------------------



let homeInitialized = false;



function loadHomeSection() {

  if (!homeInitialized) {

    homeInitialized = true;



    // Search input & button

    const searchInput = document.getElementById("home-search-input");

    const searchBtn = document.getElementById("home-search-btn");



    const executeSearch = () => {

      const q = (searchInput?.value || "").trim();

      if (q) {

        state.exploreFilters.q = q;

        state.exploreFilters.page = 1;

        window.location.hash = "#explore";

      }

    };



    if (searchBtn) searchBtn.addEventListener("click", executeSearch);

    if (searchInput) {

      searchInput.addEventListener("keydown", (e) => {

        if (e.key === "Enter") executeSearch();

      });

    }



    // Quick query chips

    document.querySelectorAll(".js-query-chip").forEach((chip) => {

      chip.addEventListener("click", () => {

        const query = chip.getAttribute("data-query");

        if (query) {

          state.exploreFilters.q = query;

          state.exploreFilters.page = 1;

          window.location.hash = "#explore";

        }

      });

    });



    // Category browse chips

    document.querySelectorAll(".js-category-card").forEach((card) => {

      card.addEventListener("click", () => {

        const cat = card.getAttribute("data-category");

        if (cat) {

          state.exploreFilters.category = cat;

          state.exploreFilters.q = "";

          state.exploreFilters.page = 1;

          window.location.hash = "#explore";

        }

      });

    });



    // Analytics refresh button listener

    const refreshAnalyticsBtn = document.getElementById("btn-refresh-analytics");

    if (refreshAnalyticsBtn) {

      refreshAnalyticsBtn.addEventListener("click", () => {

        loadHomeAnalytics(true);

      });

    }



    // Fetch real featured products from catalog

    fetchHomeFeaturedProducts();

  }



  // Load / refresh personal analytics on every Home visit

  loadHomeAnalytics(false);

}



/**

 * Fetch and render Personal Shopping Analytics.

 * Authoritative settled spending metrics and mandate utilization.

 * Throttles rapid repeated calls unless forced.

 */

async function loadHomeAnalytics(force = false) {

  const container = document.getElementById("home-analytics-container");

  if (!container) return;



  const currentSessionId = getSessionId();

  const now = Date.now();

  const CACHE_TTL_MS = 15000; // 15 seconds throttle



  // Reuse cached data if valid, session unchanged, and not explicitly forced

  if (

    !force &&

    state.analyticsData &&

    state.analyticsSessionId === currentSessionId &&

    now - state.analyticsLastFetched < CACHE_TTL_MS

  ) {

    container.innerHTML = renderAnalyticsDashboard(state.analyticsData);

    bindAnalyticsEvents(container);

    return;

  }



  // Visual feedback on refresh button

  const refreshBtn = document.getElementById("btn-refresh-analytics");

  if (refreshBtn) {

    refreshBtn.classList.add("opacity-75");

    const icon = refreshBtn.querySelector("svg");

    if (icon) icon.classList.add("animate-spin");

  }



  if (!state.analyticsData || state.analyticsSessionId !== currentSessionId) {

    container.innerHTML = renderAnalyticsSkeleton();

  }



  try {

    const data = await api.getAnalytics(currentSessionId);

    state.analyticsData = data;

    state.analyticsSessionId = currentSessionId;

    state.analyticsLastFetched = Date.now();



    container.innerHTML = renderAnalyticsDashboard(data);

    bindAnalyticsEvents(container);

  } catch (err) {

    console.error("Failed to load personal shopping analytics:", err);

    container.innerHTML = renderAnalyticsError(err.message);

    bindAnalyticsEvents(container);

  } finally {

    if (refreshBtn) {

      refreshBtn.classList.remove("opacity-75");

      const icon = refreshBtn.querySelector("svg");

      if (icon) icon.classList.remove("animate-spin");

    }

  }

}



function bindAnalyticsEvents(container) {

  if (!container) return;

  const retryBtn = container.querySelector("#btn-analytics-retry");

  if (retryBtn) {

    retryBtn.addEventListener("click", () => {

      loadHomeAnalytics(true);

    });

  }

}



async function fetchHomeFeaturedProducts() {

  const container = document.getElementById("home-featured-grid");

  if (!container) return;



  container.innerHTML = Array(4).fill(0).map(() => renderSkeletonCard()).join("");



  try {

    const res = await api.getProducts({ page: 1, page_size: 4 });

    if (res.items && res.items.length > 0) {

      const compareList = getCompareList();

      container.innerHTML = res.items

        .map((p) => renderProductCard(p, compareList.includes(p.id), state.activeMandate))

        .join("");



      bindProductCardEvents(container);

    } else {

      container.innerHTML = `<div class="col-span-full py-8 text-center text-slate-500 text-sm">No featured products available in the catalog.</div>`;

    }

  } catch (err) {

    container.innerHTML = `<div class="col-span-full py-8 text-center text-rose-400 text-sm">Failed to load catalog products: ${err.message}</div>`;

  }

}



// -------------------------------------------------------------------------

// SECTION: AI SHOPPING ASSISTANT

// -------------------------------------------------------------------------



let assistantInitialized = false;



function loadAssistantSection() {

  const container = document.getElementById("assistant-messages-container");

  const input = document.getElementById("assistant-query-input");

  const sendBtn = document.getElementById("assistant-send-btn");

  const clearBtn = document.getElementById("assistant-clear-btn");



  if (!container) return;



  // Render initial welcome if conversation is empty

  if (state.assistantConversation.length === 0) {

    container.innerHTML = renderAssistantWelcome();

    bindAssistantPromptChips();

  }



  // Initialize event listeners once

  if (!assistantInitialized) {

    assistantInitialized = true;



    // Send button click

    if (sendBtn && input) {

      sendBtn.addEventListener("click", () => {

        const query = input.value.trim();

        if (query) sendAssistantQuery(query);

      });

    }



    // Enter to send (Shift+Enter for new line)

    if (input) {

      input.addEventListener("keydown", (e) => {

        if (e.key === "Enter" && !e.shiftKey) {

          e.preventDefault();

          const query = input.value.trim();

          if (query) sendAssistantQuery(query);

        }

      });

    }



    // Clear chat button

    if (clearBtn) {

      clearBtn.addEventListener("click", () => {

        if (state.assistantConversation.length === 0) return;

        state.assistantConversation = [];

        container.innerHTML = renderAssistantWelcome();

        bindAssistantPromptChips();

        if (input) input.value = "";

        showToast("Assistant conversation cleared.", "info");

      });

    }

    // Toggle Show More / Show Fewer Products in Assistant responses (Phase 4A.4C)
    container.addEventListener("click", (e) => {
      const toggleBtn = e.target.closest(".js-btn-toggle-assistant-products");
      if (!toggleBtn) return;

      const targetId = toggleBtn.getAttribute("data-target");
      const targetEl = document.getElementById(targetId);
      if (!targetEl) return;

      const isExpanded = toggleBtn.getAttribute("data-expanded") === "true";
      const count = toggleBtn.getAttribute("data-count") || "more";
      const btnText = toggleBtn.querySelector(".btn-text");
      const icon = toggleBtn.querySelector("svg");

      if (isExpanded) {
        targetEl.classList.add("hidden");
        toggleBtn.setAttribute("data-expanded", "false");
        if (btnText) btnText.textContent = `Show More Products (+${count} more)`;
        if (icon) icon.classList.remove("rotate-180");
      } else {
        targetEl.classList.remove("hidden");
        toggleBtn.setAttribute("data-expanded", "true");
        if (btnText) btnText.textContent = "Show Fewer Products";
        if (icon) icon.classList.add("rotate-180");
        bindProductCardEvents(targetEl);
      }
    });

  }



  // Focus input

  if (input) {

    setTimeout(() => input.focus(), 100);

  }

}



function bindAssistantPromptChips() {

  const container = document.getElementById("assistant-messages-container");

  if (!container) return;



  container.querySelectorAll(".js-assistant-prompt-chip").forEach((chip) => {

    chip.addEventListener("click", () => {

      const prompt = chip.getAttribute("data-prompt");

      if (prompt) {

        const input = document.getElementById("assistant-query-input");

        if (input) input.value = prompt;

        sendAssistantQuery(prompt);

      }

    });

  });

}



async function sendAssistantQuery(queryText) {

  const cleanQuery = (queryText || "").trim();

  if (!cleanQuery) return;



  if (state.assistantIsLoading) return; // Prevent duplicate submission



  const container = document.getElementById("assistant-messages-container");

  const input = document.getElementById("assistant-query-input");

  const sendBtn = document.getElementById("assistant-send-btn");

  if (!container) return;



  // Clear welcome screen on first interaction

  const welcomeScreen = document.getElementById("assistant-welcome-screen");

  if (welcomeScreen) {

    container.innerHTML = "";

  }



  // Append user message

  state.assistantConversation.push({ role: "user", content: cleanQuery });

  const userMsgDiv = document.createElement("div");

  userMsgDiv.innerHTML = renderAssistantUserMessage(cleanQuery);

  container.appendChild(userMsgDiv);



  // Clear input & disable controls

  if (input) input.value = "";

  state.assistantIsLoading = true;

  if (sendBtn) sendBtn.disabled = true;

  if (input) input.disabled = true;



  // Append loading indicator

  const loadingDiv = document.createElement("div");

  loadingDiv.id = "assistant-loading-wrapper";

  loadingDiv.innerHTML = renderAssistantLoading();

  container.appendChild(loadingDiv);

  container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });



  try {

    // Extract bounded recent context (last 6 dialogue messages)

    const context = state.assistantConversation

      .filter((m) => m.role === "user" || m.role === "assistant")

      .slice(-6)

      .map((m) => ({ role: m.role, content: m.content }));



    const res = await api.queryAssistant(cleanQuery, context);



    // Remove loading indicator

    document.getElementById("assistant-loading-wrapper")?.remove();



    // Append assistant response

    state.assistantConversation.push({

      role: "assistant",

      content: res.assistant_message || "",

      data: res,

    });



    const assistantMsgDiv = document.createElement("div");

    assistantMsgDiv.innerHTML = renderAssistantMessage(res, getCompareList(), state.activeMandate);

    container.appendChild(assistantMsgDiv);



    // Bind "+ Compare" and "View Offers" card handlers on the newly appended response

    bindProductCardEvents(assistantMsgDiv);



    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });

  } catch (err) {

    document.getElementById("assistant-loading-wrapper")?.remove();



    const errorDiv = document.createElement("div");

    errorDiv.innerHTML = renderAssistantError(err.message, cleanQuery);

    container.appendChild(errorDiv);



    // Bind retry button

    errorDiv.querySelector(".js-btn-retry-assistant")?.addEventListener("click", (e) => {

      const retryQuery = e.currentTarget.getAttribute("data-query");

      errorDiv.remove();

      if (retryQuery) sendAssistantQuery(retryQuery);

    });



    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });

  } finally {

    state.assistantIsLoading = false;

    if (sendBtn) sendBtn.disabled = false;

    if (input) {

      input.disabled = false;

      input.focus();

    }

  }

}



// -------------------------------------------------------------------------

// SECTION: EXPLORE

// -------------------------------------------------------------------------



let exploreInitialized = false;



function loadExploreSection() {

  if (!exploreInitialized) {

    exploreInitialized = true;

    initExploreFilters();

  } else {

    // Generate fresh discovery seed on revisit if in dynamic discovery mode and without active search

    if (state.exploreFilters.order_by === "random" && !state.exploreFilters.q) {

      state.exploreFilters.seed = Math.floor(Math.random() * 1000000);

      state.exploreFilters.page = 1;

    }

  }



  // Synchronize search input if set from Home

  const searchInput = document.getElementById("explore-search-input");

  if (searchInput && state.exploreFilters.q) {

    searchInput.value = state.exploreFilters.q;

  }



  syncCategoryPills();

  fetchExploreProducts();

}



function syncCategoryPills() {
  const catContainer = document.getElementById("explore-category-pills");
  if (!catContainer) return;
  const currentCat = state.exploreFilters.category || "All";
  catContainer.querySelectorAll(".js-cat-pill").forEach((p) => {
    const isCurrent = p.getAttribute("data-category") === currentCat;
    p.className = `js-cat-pill px-3.5 py-1.5 rounded-full text-xs font-semibold transition-colors border ${
      isCurrent
        ? "bg-burnt-orange text-white border-burnt-orange"
        : "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-700 hover:text-white"
    }`;
  });
}



function initExploreFilters() {

  const searchInput = document.getElementById("explore-search-input");

  const searchBtn = document.getElementById("explore-search-btn");

  const clearBtn = document.getElementById("explore-clear-filters");

  const brandInput = document.getElementById("explore-brand-filter");

  const minPriceInput = document.getElementById("explore-min-price");

  const maxPriceInput = document.getElementById("explore-max-price");

  const sourceSelect = document.getElementById("explore-source-filter");

  const orderSelect = document.getElementById("explore-order-filter");

  const refreshDiscoveryBtn = document.getElementById("btn-refresh-discovery");



  // Refresh Discovery Button Handler

  if (refreshDiscoveryBtn) {

    refreshDiscoveryBtn.addEventListener("click", async () => {

      const icon = refreshDiscoveryBtn.querySelector("svg");

      if (icon) icon.classList.add("animate-spin");

      refreshDiscoveryBtn.classList.add("opacity-75");



      state.exploreFilters.order_by = "random";

      state.exploreFilters.seed = Math.floor(Math.random() * 1000000);

      state.exploreFilters.page = 1;



      if (orderSelect) orderSelect.value = "random";



      try {

        await fetchExploreProducts();

        showToast("Refreshed discovery catalogue with varied recommendations.", "info");

      } catch (err) {

        showToast(`Failed to refresh discovery: ${err.message}`, "error");

      } finally {

        if (icon) icon.classList.remove("animate-spin");

        refreshDiscoveryBtn.classList.remove("opacity-75");

      }

    });

  }



  // Order/Sort filter change handler

  if (orderSelect) {

    orderSelect.addEventListener("change", () => {

      state.exploreFilters.order_by = orderSelect.value || "random";

      if (state.exploreFilters.order_by === "random") {

        state.exploreFilters.seed = Math.floor(Math.random() * 1000000);

      }

      state.exploreFilters.page = 1;

      fetchExploreProducts();

    });

  }



  // Category filter buttons

  const catContainer = document.getElementById("explore-category-pills");

  if (catContainer) {

    catContainer.innerHTML = CONFIG.CATEGORIES.map(

      (cat) => `

      <button

        type="button"

        class="js-cat-pill px-3.5 py-1.5 rounded-full text-xs font-semibold transition-colors border ${

          state.exploreFilters.category === cat

            ? "bg-burnt-orange text-white border-burnt-orange"

            : "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-700 hover:text-white"

        }"

        data-category="${cat}"

      >

        ${cat}

      </button>

    `

    ).join("");



    catContainer.addEventListener("click", (e) => {

      const btn = e.target.closest(".js-cat-pill");

      if (!btn) return;

      const cat = btn.getAttribute("data-category");

      state.exploreFilters.category = cat;

      state.exploreFilters.page = 1;



      // Update pill classes

      catContainer.querySelectorAll(".js-cat-pill").forEach((p) => {

        const isCurrent = p.getAttribute("data-category") === cat;

        p.className = `js-cat-pill px-3.5 py-1.5 rounded-full text-xs font-semibold transition-colors border ${

          isCurrent

            ? "bg-burnt-orange text-white border-burnt-orange"

            : "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-700 hover:text-white"

        }`;

      });



      fetchExploreProducts();

    });

  }



  const triggerSearch = () => {

    state.exploreFilters.q = (searchInput?.value || "").trim();

    state.exploreFilters.brand = (brandInput?.value || "").trim();

    state.exploreFilters.min_price = minPriceInput?.value || "";

    state.exploreFilters.max_price = maxPriceInput?.value || "";

    state.exploreFilters.source = sourceSelect?.value || "All";

    state.exploreFilters.order_by = orderSelect?.value || state.exploreFilters.order_by || "random";

    state.exploreFilters.page = 1;

    fetchExploreProducts();

  };



  if (searchBtn) searchBtn.addEventListener("click", triggerSearch);

  if (searchInput) {

    searchInput.addEventListener("keydown", (e) => {

      if (e.key === "Enter") triggerSearch();

    });

  }

  if (brandInput) brandInput.addEventListener("change", triggerSearch);

  if (minPriceInput) minPriceInput.addEventListener("change", triggerSearch);

  if (maxPriceInput) maxPriceInput.addEventListener("change", triggerSearch);

  if (sourceSelect) sourceSelect.addEventListener("change", triggerSearch);



  if (clearBtn) {

    clearBtn.addEventListener("click", () => {

      state.exploreFilters = {

        q: "",

        category: "All",

        brand: "",

        min_price: "",

        max_price: "",

        source: "All",

        order_by: "random",

        seed: Math.floor(Math.random() * 1000000),

        page: 1,

        page_size: 12,

      };

      if (searchInput) searchInput.value = "";

      if (brandInput) brandInput.value = "";

      if (minPriceInput) minPriceInput.value = "";

      if (maxPriceInput) maxPriceInput.value = "";

      if (sourceSelect) sourceSelect.value = "All";

      if (orderSelect) orderSelect.value = "random";



      // Reset category pills

      if (catContainer) {

        catContainer.querySelectorAll(".js-cat-pill").forEach((p) => {

          const isAll = p.getAttribute("data-category") === "All";

          p.className = `js-cat-pill px-3.5 py-1.5 rounded-full text-xs font-semibold transition-colors border ${

            isAll

              ? "bg-burnt-orange text-white border-burnt-orange"

              : "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-700 hover:text-white"

          }`;

        });

      }



      fetchExploreProducts();

    });

  }

}



async function fetchExploreProducts() {

  const grid = document.getElementById("explore-product-grid");

  const countLabel = document.getElementById("explore-results-count");

  if (!grid) return;



  grid.innerHTML = Array(6).fill(0).map(() => renderSkeletonCard()).join("");

  if (countLabel) countLabel.textContent = "Loading catalog...";



  try {

    const filters = state.exploreFilters;



    const res = filters.q && filters.q.trim()

      ? await api.searchLiveProducts(filters.q.trim(), 10)

     : await api.getProducts(filters);

    

    state.exploreProducts = res.items || [];

    state.exploreTotalPages = res.total_pages || 1;



    const isLive = Boolean(filters.q && filters.q.trim());



    if (countLabel) {

      if (isLive) {

        countLabel.textContent = `Showing ${res.items.length} live results via Channel3 for "${filters.q.trim()}"`;

      } else if (filters.order_by === "random") {

        countLabel.textContent = `Showing ${res.items.length} of ${res.total} catalog products (Page ${res.page} of ${res.total_pages}) • Dynamic Discovery`;

      } else {

        countLabel.textContent = `Showing ${res.items.length} of ${res.total} catalog products (Page ${res.page} of ${res.total_pages})`;

      }

    }



    renderExploreGrid();

    renderPaginationControls(res.page, res.total_pages);

  } catch (err) {

    grid.innerHTML = `

      <div class="col-span-full py-12">

        ${renderEmptyState("Search Error", `Failed to retrieve products from backend: ${err.message}`, "Retry", "btn-retry-explore")}

      </div>

    `;

    const retryBtn = document.getElementById("btn-retry-explore");

    if (retryBtn) retryBtn.addEventListener("click", fetchExploreProducts);

  }

}



function renderExploreGrid() {

  const grid = document.getElementById("explore-product-grid");

  if (!grid) return;



  if (state.exploreProducts.length === 0) {

    grid.innerHTML = `

      <div class="col-span-full py-12">

        ${renderEmptyState(

          "No Products Found",

          "No products match your current keyword or filter criteria. Try broadening your price range or clearing filters.",

          "Clear Filters",

          "btn-empty-clear-filters"

        )}

      </div>

    `;

    const clearBtn = document.getElementById("btn-empty-clear-filters");

    if (clearBtn) {

      clearBtn.addEventListener("click", () => {

        document.getElementById("explore-clear-filters")?.click();

      });

    }

    return;

  }



  const compareList = getCompareList();

  grid.innerHTML = state.exploreProducts

    .map((p) => renderProductCard(p, compareList.includes(p.id), state.activeMandate))

    .join("");



  bindProductCardEvents(grid);

}



function renderPaginationControls(currentPage, totalPages) {

  const container = document.getElementById("explore-pagination");

  if (!container) return;



  if (totalPages <= 1) {

    container.innerHTML = "";

    return;

  }



  container.innerHTML = `

    <div class="flex items-center gap-3">

      <button

        type="button"

        id="pag-prev"

        class="px-3.5 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-xs font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"

        ${currentPage <= 1 ? "disabled" : ""}

      >

        ← Previous

      </button>

      <span class="text-xs text-slate-400 font-medium">Page ${currentPage} of ${totalPages}</span>

      <button

        type="button"

        id="pag-next"

        class="px-3.5 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-xs font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"

        ${currentPage >= totalPages ? "disabled" : ""}

      >

        Next →

      </button>

    </div>

  `;



  document.getElementById("pag-prev")?.addEventListener("click", () => {

    if (state.exploreFilters.page > 1) {

      state.exploreFilters.page -= 1;

      fetchExploreProducts();

    }

  });



  document.getElementById("pag-next")?.addEventListener("click", () => {

    if (state.exploreFilters.page < totalPages) {

      state.exploreFilters.page += 1;

      fetchExploreProducts();

    }

  });

}



/**
 * Bind checkout button event listeners on product cards and modals.
 */
export function bindCheckoutEvents(container) {
  if (!container) return;
  const buttons = container.querySelectorAll(".js-btn-checkout");
  buttons.forEach((btn) => {
    if (btn.hasAttribute("data-checkout-bound")) return;
    btn.setAttribute("data-checkout-bound", "true");

    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();

      const productId = btn.getAttribute("data-product-id") || null;
      const offerId = btn.getAttribute("data-offer-id") || null;
      const productName = btn.getAttribute("data-product-name") || "Demo Product";
      const retailerName = btn.getAttribute("data-retailer-name") || "Catalog";
      const rawPrice = btn.getAttribute("data-price");
      const currency = (btn.getAttribute("data-currency") || "USD").toUpperCase();

      if (!rawPrice || isNaN(parseFloat(rawPrice))) {
        showToast("Price unavailable for checkout.", "error");
        return;
      }

      const price = parseFloat(rawPrice);

      // HYBRID AUTH: Guest clicks purchase -> require authentication before order creation
      if (!isAuthenticated()) {
        const pendingData = {
          productId,
          offerId,
          productName,
          retailerName,
          price,
          currency,
          isSynthetic: btn.getAttribute("data-is-synthetic") !== "false",
        };
        setPendingCheckout(pendingData);
        promptAuthRequiredModal(pendingData);
        return;
      }

      await executeCheckoutFlow(btn, {
        productId,
        offerId,
        productName,
        retailerName,
        price,
        currency,
      });
    });
  });
}

/**
 * Orchestrate complete PayPal checkout and capture flow for a synthetic demo product/offer.
 * Handles duplicate click prevention, spending mandate soft-limit override confirmations,
 * PayPal approval, and settlement capture.
 */
export async function executeCheckoutFlow(triggerBtn, checkoutParams) {
  const { productId, offerId, productName, retailerName, price, currency } = checkoutParams;

  // HYBRID AUTH GUARD: Unauthenticated users cannot create payment orders
  if (!isAuthenticated()) {
    setPendingCheckout(checkoutParams);
    promptAuthRequiredModal(checkoutParams);
    return null;
  }

  // 1. Prevent duplicate clicks & show loading spinner
  if (triggerBtn) {
    if (triggerBtn.disabled) return;
    triggerBtn.disabled = true;
    triggerBtn.setAttribute("data-original-html", triggerBtn.innerHTML);
    triggerBtn.innerHTML = `
      <svg class="animate-spin w-3.5 h-3.5 inline-block mr-1 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
      </svg>
      Processing...
    `;
  }

  const restoreBtn = () => {
    if (triggerBtn) {
      triggerBtn.disabled = false;
      const originalHtml = triggerBtn.getAttribute("data-original-html");
      if (originalHtml) {
        triggerBtn.innerHTML = originalHtml;
      }
    }
  };

  try {
    let returnUrl = "http://localhost:5500/?paypal_return=true";
    let cancelUrl = "http://localhost:5500/?paypal_cancel=true";
    if (typeof window !== "undefined" && window.location) {
      try {
        const origin = window.location.origin || "http://localhost:5500";
        const pathname = window.location.pathname || "/";
        returnUrl = `${origin}${pathname}?paypal_return=true`;
        cancelUrl = `${origin}${pathname}?paypal_cancel=true`;
      } catch (e) {
        console.warn("Could not compute dynamic return URL:", e);
      }
    }

    const payload = {
      session_id: getSessionId() || null,
      session_identifier: getSessionIdentifier() || null,
      currency: currency || "USD",
      order_type: "SANDBOX_DEMO",
      items: [
        {
          product_name: productName,
          quantity: 1,
          unit_price: price,
          currency: currency || "USD",
          product_id: productId || null,
          retailer_offer_id: offerId || null,
          retailer_name: retailerName || null,
        },
      ],
      budget_override_confirmed: false,
      return_url: returnUrl,
      cancel_url: cancelUrl,
    };

    let order = null;

    try {
      order = await api.createCheckout(payload);
    } catch (err) {
      const detail = err.detail || {};
      const isOverrideReq =
        err.status === 400 &&
        (detail.error === "budget_override_required" || detail.requires_override_confirmation === true);

      if (isOverrideReq) {
        // Trigger modal prompt
        const prefBudget = detail.preferred_budget ? parseFloat(detail.preferred_budget) : 0;
        const excess = detail.exceeded_amount ? parseFloat(detail.exceeded_amount) : Math.max(0, price - prefBudget);

        const confirmed = await promptBudgetOverrideConfirmation({
          productName: productName,
          price: price,
          currency: detail.currency || currency || "USD",
          preferredBudget: prefBudget,
          excessAmount: excess,
        });

        if (!confirmed) {
          showToast("Purchase cancelled. Mandate remains active.", "info");
          restoreBtn();
          return;
        }

        // Retry with explicit user override
        payload.budget_override_confirmed = true;
        payload.acknowledged_budget_excess = detail.exceeded_amount != null ? detail.exceeded_amount : excess;

        try {
          order = await api.createCheckout(payload);
        } catch (retryErr) {
          showToast(`Checkout failed: ${retryErr.message}`, "error");
          restoreBtn();
          return;
        }
      } else if (err.status === 400 && detail.requires_external_checkout) {
        showToast("Live retailer products must be purchased directly on the merchant's store.", "warning");
        if (detail.product_url) {
          window.open(detail.product_url, "_blank", "noopener,noreferrer");
        }
        restoreBtn();
        return;
      } else if (err.status === 400 && detail.error === "mandate_policy_violation") {
        const reason = detail.reasons?.[0] || detail.message || "Purchase violates active spending mandate policy.";
        showToast(reason, "warning");
        restoreBtn();
        return;
      } else {
        showToast(`Checkout failed: ${err.message}`, "error");
        restoreBtn();
        return;
      }
    }

    if (!order || !order.id) {
      showToast("Failed to initialize checkout order.", "error");
      restoreBtn();
      return;
    }

    // Step 2: Prompt user to approve in PayPal Sandbox and explicitly confirm capture settlement
    const approvalResult = await promptPayPalApprovalAndCapture({
      order,
      productName,
      retailerName,
      price,
      currency,
    });
    restoreBtn();
    return approvalResult;
  } catch (outerErr) {
    showToast(`Unexpected checkout error: ${outerErr.message}`, "error");
    restoreBtn();
  }
}

function bindProductCardEvents(container) {
  // Bind Buy with PayPal action buttons
  bindCheckoutEvents(container);

  // View Details Modal button
  container.querySelectorAll(".js-btn-details").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const productId = btn.getAttribute("data-id");
      if (!productId) return;

      btn.disabled = true;
      btn.textContent = "Loading...";

      try {
        const product = await api.getProductDetail(productId);
        openModal(renderProductDetailModal(product));
        const modalContent = document.getElementById("modal-content");
        if (modalContent) {
          bindCheckoutEvents(modalContent);
        }
        // Asynchronously load intelligence without blocking initial modal render
        loadProductIntelligence(productId);
      } catch (err) {
        showToast(`Failed to load product details: ${err.message}`, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "View Offers";
      }
    });
  });

  // Toggle Compare button

  container.querySelectorAll(".js-btn-compare").forEach((btn) => {

    btn.addEventListener("click", () => {

      const productId = btn.getAttribute("data-id");

      if (!productId) return;



      const res = toggleCompareItem(productId);

      if (res.error) {

        showToast(res.error, "warning");

        return;

      }



      if (res.added) {

        btn.textContent = "✓ Compared";

        btn.className = "js-btn-compare w-full py-2 px-3 text-xs font-semibold rounded-lg border transition-colors bg-burnt-orange text-white border-burnt-orange shadow-sm text-center";

        showToast("Product added to comparison.", "success");

      } else {

        btn.textContent = "+ Compare";

        btn.className = "js-btn-compare w-full py-2 px-3 text-xs font-semibold rounded-lg border transition-colors bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700 hover:text-white text-center";

        showToast("Product removed from comparison.", "info");

      }



      updateComparisonTray();

    });

  });

}



// -------------------------------------------------------------------------

// SECTION: COMPARE

// -------------------------------------------------------------------------



async function loadCompareSection() {

  const container = document.getElementById("compare-content-container");

  if (!container) return;



  const compareIds = getCompareList();



  if (compareIds.length === 0) {

    container.innerHTML = renderEmptyState(

      "No Products Selected for Comparison",

      "Select up to 4 products from Explore or Home to view their technical specifications, lowest prices, and retailer offer matrices side by side.",

      "Explore Products",

      "btn-compare-goto-explore"

    );

    document.getElementById("btn-compare-goto-explore")?.addEventListener("click", () => {

      window.location.hash = "#explore";

    });

    return;

  }



  container.innerHTML = `

    <div class="flex items-center justify-center p-12 text-slate-400 text-sm">

      <div class="flex items-center gap-2">

        <svg class="animate-spin h-5 w-5 text-burnt-orange" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg>

        <span>Retrieving comparison matrix...</span>

      </div>

    </div>

  `;



  try {

    const data = await api.compareProducts(compareIds);

    renderComparisonMatrix(container, data);

  } catch (err) {

    container.innerHTML = renderEmptyState(

      "Comparison Error",

      `Unable to generate comparison matrix: ${err.message}`,

      "Clear Selection",

      "btn-clear-broken-compare"

    );

    document.getElementById("btn-clear-broken-compare")?.addEventListener("click", () => {

      clearCompareList();

      loadCompareSection();

    });

  }

}



function renderComparisonMatrix(container, data) {

  const products = data.products || [];

  if (products.length === 0) {

    container.innerHTML = renderEmptyState(

      "Products Unavailable",

      "The selected products could not be retrieved from the catalog.",

      "Explore Products",

      "btn-compare-goto-explore"

    );

    return;

  }



  // Detect mixed currencies

  const currencies = new Set(products.map((p) => p.currency));

  const hasCurrencyMismatch = currencies.size > 1;



  // Header cards

  const productHeaders = products.map((p) => `

    <th class="py-4 px-4 text-left border-l border-slate-800 bg-slate-900/90 w-64 min-w-[16rem] align-top">

      <div class="relative flex flex-col h-full">

        <button

          type="button"

          class="js-remove-compare absolute -top-1 -right-1 text-slate-500 hover:text-rose-400 p-1"

          data-id="${p.id}"

          title="Remove from comparison"

        >

          ✖

        </button>

        <div class="w-full h-32 bg-slate-950/60 rounded-xl p-3 flex items-center justify-center mb-3 border border-slate-800 overflow-hidden">

          <img src="${getSafeImageUrl(p.image_url)}" alt="${escapeHtml(p.name)}" class="max-h-full object-contain" ${IMG_FALLBACK_ATTR}/>

        </div>

        <div class="text-[10px] font-bold uppercase tracking-wider text-burnt-orange">${escapeHtml(p.brand || "")}</div>

        <div class="text-sm font-bold text-white line-clamp-2 leading-tight mb-2">${escapeHtml(p.name)}</div>

        <div class="mt-auto">

          <div class="text-[11px] text-slate-400">Lowest Price (${escapeHtml(p.best_offer_retailer || "Catalog")})</div>

          <div class="text-lg font-extrabold text-white">${formatPrice(p.lowest_price, p.currency)}</div>

        </div>

      </div>

    </th>

  `).join("");



  // Specification Matrix rows

  const matrix = data.comparison_matrix || {};

  const specRows = Object.entries(matrix).map(([specKey, valMap]) => `

    <tr class="border-b border-slate-800/60 hover:bg-slate-800/30">

      <td class="py-3 px-4 text-xs font-semibold text-slate-400 capitalize bg-slate-950/40 w-48">${specKey.replace(/[-_]/g, " ")}</td>

      ${products.map((p) => {

        let val = valMap?.[p.id];

        if (val === undefined || val === null || val === "Not Specified") {

          const nameVal = valMap?.[p.name];

          if (nameVal !== undefined && nameVal !== null && nameVal !== "Not Specified") {

            val = nameVal;

          }

        }

        if (val === undefined || val === null || val === "Not Specified") {

          const specVal = p.specifications?.[specKey];

          if (specVal !== undefined && specVal !== null) {

            val = specVal;

          }

        }

        const displayVal = val !== undefined && val !== null && String(val).trim() !== "" ? String(val) : "Not Specified";

        const isMuted = displayVal === "Not Specified";

        return `<td class="py-3 px-4 text-xs ${isMuted ? 'text-slate-500 italic' : 'text-slate-200 font-medium'} border-l border-slate-800">${displayVal}</td>`;

      }).join("")}

    </tr>

  `).join("");



  container.innerHTML = `

    <!-- Grounded AI Comparison Insights (Phase 4A.4A) -->

    ${data.ai_explanation ? renderComparisonAIInsights(data.ai_explanation, false) : ""}



    <!-- Top Price Summary & Currency Warnings -->

    ${

      hasCurrencyMismatch

        ? `<div class="mb-6 p-4 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300 flex items-center gap-3">

             <span class="text-lg">⚠</span>

             <div>

               <strong>Multi-Currency Notice:</strong> The compared products quote prices in different currencies (${Array.from(currencies).join(", ")}). Direct numerical price comparison is withheld to prevent misleading FX valuation without live conversion rates.

             </div>

           </div>`

        : ""

    }



    <!-- Table Container -->

    <div class="overflow-x-auto rounded-2xl border border-slate-800 bg-slate-900 shadow-xl">

      <table class="w-full text-left border-collapse">

        <thead>

          <tr class="border-b border-slate-800">

            <th class="py-4 px-4 bg-slate-950/80 text-xs font-bold text-slate-400 uppercase tracking-wider w-48">Feature / Metric</th>

            ${productHeaders}

          </tr>

        </thead>

        <tbody>

          <!-- Availability Row -->

          <tr class="border-b border-slate-800/80 bg-slate-950/20">

            <td class="py-3 px-4 text-xs font-bold text-slate-300">Availability</td>

            ${products.map((p) => `

              <td class="py-3 px-4 text-xs border-l border-slate-800">

                <span class="inline-flex px-2 py-0.5 rounded text-[11px] font-semibold border ${

                  p.overall_availability === "IN_STOCK"

                    ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"

                    : "bg-rose-500/10 text-rose-400 border-rose-500/20"

                }">

                  ${p.overall_availability.replace(/_/g, " ")}

                </span>

              </td>

            `).join("")}

          </tr>



          <!-- Data Source Row -->

          <tr class="border-b border-slate-800/80 bg-slate-950/20">

            <td class="py-3 px-4 text-xs font-bold text-slate-300">Catalog Source</td>

            ${products.map((p) => `

              <td class="py-3 px-4 text-xs border-l border-slate-800">

                ${

                  p.is_synthetic

                    ? `<span class="text-indigo-400 text-xs font-medium">Synthetic Demo</span>`

                    : `<span class="text-emerald-400 text-xs font-medium">Live Channel3</span>`

                }

              </td>

            `).join("")}

          </tr>



          <!-- Matrix Spec Rows -->

          ${specRows || `<tr><td colspan="${products.length + 1}" class="p-4 text-xs text-slate-500 text-center">No common specifications recorded for matrix comparison.</td></tr>`}

        </tbody>

      </table>

    </div>

  `;



  // Remove buttons

  container.querySelectorAll(".js-remove-compare").forEach((btn) => {

    btn.addEventListener("click", () => {

      const pid = btn.getAttribute("data-id");

      toggleCompareItem(pid);

      updateComparisonTray();

      loadCompareSection();

      showToast("Removed product from comparison.", "info");

    });

  });

}



// -------------------------------------------------------------------------

// SECTION: SPENDING MANDATE

// -------------------------------------------------------------------------



let mandateInitialized = false;



async function loadMandateSection() {

  const sessionId = getSessionId();



  if (!mandateInitialized) {

    mandateInitialized = true;

    initMandateForms(sessionId);

  }



  fetchMandateDetails(sessionId);

}



async function fetchMandateDetails(sessionId) {

  const overviewCard = document.getElementById("mandate-overview-card");

  if (!overviewCard) return;



  try {

    const mandate = await api.getMandate(sessionId);

    state.activeMandate = mandate;



    if (!mandate) {

      renderNoMandateState(overviewCard);

      return;

    }



    // Fetch order history to compute consumed budget safely (strictly same currency)

    const ordersRes = await api.getOrderHistory(sessionId);

    const completedOrders = (ordersRes.orders || []).filter((o) => o.status === "COMPLETED");

    const mandateCurrency = (mandate.currency || "USD").toUpperCase();

    const sameCurrencyCompletedOrders = completedOrders.filter(

      (o) => (o.currency || "USD").toUpperCase() === mandateCurrency

    );

    const mismatchedOrders = completedOrders.filter(

      (o) => (o.currency || "USD").toUpperCase() !== mandateCurrency

    );

    const spentAmount = sameCurrencyCompletedOrders.reduce((sum, o) => sum + Number(o.total_amount), 0);

    const remainingBudget = Math.max(0, mandate.total_budget - spentAmount);

    const pctUsed = Math.min(100, Math.round((spentAmount / mandate.total_budget) * 100));



    const mismatchedNoticeHtml = mismatchedOrders.length > 0 ? `

      <div class="mb-4 p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs flex items-start gap-2.5">

        <span class="text-base leading-none">⚠</span>

        <div>

          <span class="font-bold">${mismatchedOrders.length} completed order(s) placed in other currencies (${[...new Set(mismatchedOrders.map((o) => o.currency))].join(", ")}).</span>

          <span class="text-slate-300 block mt-0.5">To prevent currency conversion distortion, only orders matching your active mandate currency (${mandateCurrency}) are deducted from this budget ceiling.</span>

        </div>

      </div>

    ` : "";



    overviewCard.innerHTML = `

      ${mismatchedNoticeHtml}

      <div class="grid grid-cols-1 md:grid-cols-4 gap-4 p-6 bg-slate-900 border border-slate-800 rounded-2xl mb-8">

        <div class="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80">

          <div class="text-xs text-slate-400 font-medium mb-1">Total Spending Budget</div>

          <div class="text-2xl font-black text-white">${formatPrice(mandate.total_budget, mandate.currency)}</div>

          <div class="text-[11px] text-slate-500 mt-1">Configured Ceiling</div>

        </div>

        

        <div class="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80">

          <div class="text-xs text-slate-400 font-medium mb-1">Consumed / Spent</div>

          <div class="text-2xl font-black text-burnt-orange">${formatPrice(spentAmount, mandate.currency)}</div>

          <div class="text-[11px] text-slate-500 mt-1">${sameCurrencyCompletedOrders.length} completed ${mandateCurrency} purchases</div>

        </div>



        <div class="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80">

          <div class="text-xs text-slate-400 font-medium mb-1">Remaining Available</div>

          <div class="text-2xl font-black text-emerald-400">${formatPrice(remainingBudget, mandate.currency)}</div>

          <div class="text-[11px] text-slate-500 mt-1">${100 - pctUsed}% budget remaining</div>

        </div>



        <div class="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80">

          <div class="text-xs text-slate-400 font-medium mb-1">Item Constraints</div>

          <div class="text-sm font-bold text-white">Max Item: ${formatPrice(mandate.maximum_item_price, mandate.currency)}</div>

          <div class="text-xs text-slate-400 mt-0.5">Max Qty: ${mandate.maximum_quantity} units/order</div>

          <div class="mt-2 inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">

            ● Policy Active

          </div>

        </div>

      </div>

    `;



    // Populate form fields with current values

    populateMandateForm(mandate);

  } catch (err) {

    overviewCard.innerHTML = `<div class="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">Failed to load mandate: ${err.message}</div>`;

  }

}



function renderNoMandateState(container) {

  container.innerHTML = `

    <div class="p-8 bg-slate-900 border border-slate-800 rounded-2xl text-center mb-8">

      <div class="w-12 h-12 rounded-xl bg-slate-800 flex items-center justify-center text-burnt-orange mx-auto mb-3">

        🛡️

      </div>

      <h3 class="text-base font-bold text-white mb-1">No Spending Mandate Configured</h3>

      <p class="text-xs text-slate-400 max-w-md mx-auto mb-4">

        Spending mandates act as deterministic financial guardrails. Configure your maximum budget and item ceilings below to enforce responsible spending.

      </p>

    </div>

  `;

}



function populateMandateForm(mandate) {

  const bInput = document.getElementById("mandate-form-budget");

  const pInput = document.getElementById("mandate-form-item-price");

  const qInput = document.getElementById("mandate-form-quantity");

  const cSelect = document.getElementById("mandate-form-currency");



  if (bInput) bInput.value = mandate.total_budget;

  if (pInput) pInput.value = mandate.maximum_item_price;

  if (qInput) qInput.value = mandate.maximum_quantity;

  if (cSelect) cSelect.value = mandate.currency;

}



function initMandateForms(sessionId) {

  const form = document.getElementById("mandate-config-form");

  const testForm = document.getElementById("mandate-test-form");



  if (form) {

    form.addEventListener("submit", async (e) => {

      e.preventDefault();

      const bVal = parseFloat(document.getElementById("mandate-form-budget").value);

      const pVal = parseFloat(document.getElementById("mandate-form-item-price").value);

      const qVal = parseInt(document.getElementById("mandate-form-quantity").value, 10);

      const cVal = document.getElementById("mandate-form-currency").value;



      if (pVal > bVal) {

        showToast("Maximum item price cannot exceed total budget.", "error");

        return;

      }



      const submitBtn = form.querySelector("button[type='submit']");

      if (submitBtn) {

        submitBtn.disabled = true;

        submitBtn.textContent = "Saving...";

      }



      try {

        const payload = {

          session_identifier: getSessionIdentifier(),

          total_budget: bVal,

          maximum_item_price: pVal,

          maximum_quantity: qVal,

          currency: cVal,

        };



        if (state.activeMandate && state.activeMandate.session_id) {

          await api.updateMandate(state.activeMandate.session_id, payload);

          showToast("Spending mandate updated successfully.", "success");

        } else {

          await api.createMandate(payload);

          showToast("Spending mandate created successfully.", "success");

        }



        fetchMandateDetails(getSessionId());

      } catch (err) {

        showToast(`Failed to save mandate: ${err.message}`, "error");

      } finally {

        if (submitBtn) {

          submitBtn.disabled = false;

          submitBtn.textContent = "Save Mandate Policy";

        }

      }

    });

  }



  // Interactive Policy Verification Sandbox

  if (testForm) {

    testForm.addEventListener("submit", async (e) => {

      e.preventDefault();

      const currentSessionId = getSessionId();

      const itemName = document.getElementById("test-item-name").value || "Candidate Purchase";

      const unitPrice = parseFloat(document.getElementById("test-unit-price").value);

      const quantity = parseInt(document.getElementById("test-quantity").value, 10);

      const currency = document.getElementById("test-currency").value;

      const resultBox = document.getElementById("test-policy-result");



      if (!resultBox) return;



      resultBox.innerHTML = `<div class="text-xs text-slate-400 animate-pulse">Evaluating spending rules against backend policy engine...</div>`;



      try {

        const res = await api.validatePurchase(currentSessionId, {

          item_name: itemName,

          unit_price: unitPrice,

          quantity: quantity,

          currency: currency,

        });



        const isAllowed = res.decision === "ALLOWED";

        resultBox.innerHTML = `

          <div class="p-4 rounded-xl border ${

            isAllowed

              ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300"

              : "bg-rose-500/10 border-rose-500/20 text-rose-300"

          }">

            <div class="flex items-center justify-between mb-2">

              <span class="text-xs font-bold uppercase tracking-wider">${res.decision}: ${isAllowed ? "Policy Compliant" : "Policy Violation"}</span>

              <span class="text-sm font-extrabold">${formatPrice(res.purchase_amount, res.currency)}</span>

            </div>

            <ul class="text-xs space-y-1 list-disc list-inside">

              ${(res.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("")}

            </ul>

            ${

              res.remaining_budget_after_purchase !== null

                ? `<div class="mt-2 pt-2 border-t border-slate-700/40 text-[11px] text-slate-400">

                     Projected Remaining Budget: <strong class="text-white">${formatPrice(res.remaining_budget_after_purchase, res.currency)}</strong>

                   </div>`

                : ""

            }

          </div>

        `;

      } catch (err) {

        resultBox.innerHTML = `

          <div class="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">

            Evaluation failed: ${err.message}. Ensure a mandate is configured first.

          </div>

        `;

      }

    });

  }

}



// -------------------------------------------------------------------------

// SECTION: ORDERS (PURCHASE HISTORY)

// -------------------------------------------------------------------------



async function loadOrdersSection() {

  const container = document.getElementById("orders-content-container");

  if (!container) return;



  const sessionId = getSessionId();



  container.innerHTML = `

    <div class="flex items-center justify-center p-12 text-slate-400 text-sm">

      <div class="flex items-center gap-2">

        <svg class="animate-spin h-5 w-5 text-burnt-orange" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg>

        <span>Loading purchase history...</span>

      </div>

    </div>

  `;



  try {

    const res = await api.getOrderHistory(sessionId);

    const orders = res.orders || [];



    if (orders.length === 0) {

      container.innerHTML = renderEmptyState(

        "No Orders Found",

        "You have not completed any demonstration purchases in this guest session. Orders placed through the sandbox checkout will appear here with full capture records.",

        "Browse Catalog",

        "btn-orders-goto-explore"

      );

      document.getElementById("btn-orders-goto-explore")?.addEventListener("click", () => {

        window.location.hash = "#explore";

      });

      return;

    }



    const rows = orders.map((o) => `

      <tr class="border-b border-slate-800/60 hover:bg-slate-800/30">

        <td class="py-3.5 px-4 font-mono text-xs font-bold text-white">${escapeHtml(o.internal_order_number)}</td>

        <td class="py-3.5 px-4 text-xs text-slate-400">${formatDate(o.created_at)}</td>

        <td class="py-3.5 px-4 text-xs font-medium text-slate-300">${o.item_count} ${o.item_count === 1 ? 'item' : 'items'}</td>

        <td class="py-3.5 px-4 text-sm font-extrabold text-white">${formatPrice(o.total_amount, o.currency)}</td>

        <td class="py-3.5 px-4">${renderStatusBadge(o.status)}</td>

        <td class="py-3.5 px-4 text-right">

          <button

            type="button"

            class="js-btn-view-receipt px-3 py-1 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"

            data-id="${o.id}"

          >

            Receipt

          </button>

        </td>

      </tr>

    `).join("");



    container.innerHTML = `

      <div class="overflow-x-auto rounded-2xl border border-slate-800 bg-slate-900 shadow-xl">

        <table class="w-full text-left border-collapse">

          <thead>

            <tr class="border-b border-slate-800 bg-slate-950/80 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">

              <th class="py-3 px-4">Order Reference</th>

              <th class="py-3 px-4">Date</th>

              <th class="py-3 px-4">Items</th>

              <th class="py-3 px-4">Total Amount</th>

              <th class="py-3 px-4">Status</th>

              <th class="py-3 px-4 text-right">Action</th>

            </tr>

          </thead>

          <tbody>

            ${rows}

          </tbody>

        </table>

      </div>

    `;



    // Bind Receipt View buttons

    container.querySelectorAll(".js-btn-view-receipt").forEach((btn) => {

      btn.addEventListener("click", async () => {

        const orderId = btn.getAttribute("data-id");

        btn.disabled = true;

        btn.textContent = "...";



        try {

          const order = await api.getOrderDetail(orderId, sessionId);

          openModal(renderOrderReceiptModal(order));

        } catch (err) {

          showToast(`Failed to load order receipt: ${err.message}`, "error");

        } finally {

          btn.disabled = false;

          btn.textContent = "Receipt";

        }

      });

    });

  } catch (err) {

    container.innerHTML = renderEmptyState(

      "History Error",

      `Unable to load order history: ${err.message}`,

      "Retry",

      "btn-retry-orders"

    );

    document.getElementById("btn-retry-orders")?.addEventListener("click", loadOrdersSection);

  }

}



export function renderOrderReceiptModal(order) {
  const items = order.items || [];
  const captures = order.captures || [];
  const overrideNotice = order.budget_override_applied
    ? `
      <div class="mb-4 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span>⚠ï¸</span>
          <span><strong>Budget Override Confirmed:</strong> Order exceeded original preferred budget.</span>
        </div>
        ${order.budget_exceeded_amount ? `<span class="font-mono font-bold text-amber-400">+${formatPrice(order.budget_exceeded_amount, order.currency)}</span>` : ""}
      </div>
    `
    : "";



  const itemRows = items.map((it) => `

    <tr class="border-b border-slate-800/60 last:border-b-0">

      <td class="py-2.5 px-3 text-xs text-white font-medium">

        <div>${escapeHtml(it.product_name)}</div>

        <div class="text-[10px] text-slate-400">${escapeHtml(it.retailer_name || "Catalog")}</div>

      </td>

      <td class="py-2.5 px-3 text-xs text-slate-400">${it.quantity}</td>

      <td class="py-2.5 px-3 text-xs text-slate-400">${formatPrice(it.unit_price, it.currency)}</td>

      <td class="py-2.5 px-3 text-xs font-bold text-white text-right">${formatPrice(it.subtotal, it.currency)}</td>

    </tr>

  `).join("");



  const captureRows = captures.map((c) => `

    <div class="p-3 rounded-lg bg-slate-950/60 border border-slate-800 text-xs text-slate-300 mb-2">

      <div class="flex items-center justify-between mb-1">

        <span class="font-mono text-emerald-400 font-bold">${escapeHtml(c.paypal_capture_id)}</span>

        <span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Verified Capture</span>

      </div>

      <div class="text-[11px] text-slate-400">Settled: ${formatPrice(c.captured_amount, c.currency)} • ${formatDate(c.captured_at)}</div>

      ${c.paypal_payer_email_masked ? `<div class="text-[11px] text-slate-500">Payer: ${escapeHtml(c.paypal_payer_email_masked)}</div>` : ""}

    </div>

  `).join("");



  return `

    <div class="p-6">

      <div class="flex items-start justify-between pb-4 border-b border-slate-800 mb-4">
        <div>
          <div class="text-xs text-slate-400 font-medium">Order Receipt</div>
          <div class="text-lg font-mono font-bold text-white">${escapeHtml(order.internal_order_number)}</div>
          <div class="text-xs text-slate-400">${formatDate(order.created_at)}</div>
        </div>
        <div>
          ${renderStatusBadge(order.status)}
        </div>
      </div>

      ${overrideNotice}



      <!-- Item Snapshot Table -->

      <div class="mb-6">

        <h4 class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Purchased Items</h4>

        <div class="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/40">

          <table class="w-full text-left border-collapse">

            <thead>

              <tr class="border-b border-slate-800 bg-slate-900/80 text-[10px] font-semibold text-slate-400 uppercase">

                <th class="py-2 px-3">Product</th>

                <th class="py-2 px-3">Qty</th>

                <th class="py-2 px-3">Unit Price</th>

                <th class="py-2 px-3 text-right">Subtotal</th>

              </tr>

            </thead>

            <tbody>

              ${itemRows || `<tr><td colspan="4" class="p-3 text-xs text-slate-500 text-center">No item snapshots recorded.</td></tr>`}

            </tbody>

            <tfoot>

              <tr class="border-t border-slate-800 bg-slate-900/40">

                <td colspan="3" class="py-2.5 px-3 text-xs font-bold text-slate-300">Total Settled Amount</td>

                <td class="py-2.5 px-3 text-sm font-black text-white text-right">${formatPrice(order.total_amount, order.currency)}</td>

              </tr>

            </tfoot>

          </table>

        </div>

      </div>



      <!-- Verified Settlement Record -->

      <div>

        <h4 class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">PayPal Settlement Proof</h4>

        ${captureRows || `<div class="text-xs text-slate-500 italic p-3 bg-slate-950/40 rounded-lg border border-slate-800">No settled captures associated with this order.</div>`}

      </div>



      <div class="mt-6 pt-4 border-t border-slate-800 text-right">

        <button type="button" class="js-close-modal px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors">

          Close Receipt

        </button>

      </div>

    </div>

  `;

}





/**

 * Asynchronously fetch and render individual product intelligence & price history.

 * Invoked immediately upon opening the product modal so that basic product details

 * render instantly while analytics/history are loaded in the background.

 */

async function loadProductIntelligence(productId, activeCurrency = null) {

  const container = document.getElementById("product-intelligence-container");

  if (!container) return;



  // Use cached modal intelligence if user is simply toggling currency tabs

  if (

    state.currentModalIntelligence &&

    state.currentModalIntelligence.product_id === productId &&

    activeCurrency

  ) {

    container.innerHTML = renderProductIntelligenceContent(state.currentModalIntelligence, activeCurrency);

    bindIntelligenceEvents(productId);

    return;

  }



  container.innerHTML = renderProductIntelligenceSkeleton();



  try {

    const intelligence = await api.getProductIntelligence(productId);

    state.currentModalIntelligence = intelligence;



    // Verify modal is still open for this specific product

    const liveContainer = document.getElementById("product-intelligence-container");

    if (!liveContainer || liveContainer.getAttribute("data-product-id") !== productId) {

      return;

    }



    liveContainer.innerHTML = renderProductIntelligenceContent(intelligence, activeCurrency);

    bindIntelligenceEvents(productId);

  } catch (err) {

    const liveContainer = document.getElementById("product-intelligence-container");

    if (liveContainer && liveContainer.getAttribute("data-product-id") === productId) {

      liveContainer.innerHTML = renderProductIntelligenceError(err.message, productId);

      bindIntelligenceEvents(productId);

    }

  }

}



/**

 * Bind currency tab toggle and retry button events within the Product Intelligence section.

 */

function bindIntelligenceEvents(productId) {

  const container = document.getElementById("product-intelligence-container");

  if (!container) return;



  // Currency tabs

  container.querySelectorAll(".js-intel-currency-tab").forEach((tab) => {

    tab.addEventListener("click", () => {

      const targetCurr = tab.getAttribute("data-currency");

      loadProductIntelligence(productId, targetCurr);

    });

  });



  // Retry on error button

  container.querySelectorAll(".js-btn-retry-intel").forEach((btn) => {

    btn.addEventListener("click", () => {

      loadProductIntelligence(productId);

    });

  });

}


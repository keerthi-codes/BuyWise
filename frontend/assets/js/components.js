/**
 * BUYWISE AI — Reusable UI Components
 * Accessible, semantic HTML renderers for products, badges, modals, and toasts.
 */

import { formatPrice, formatDate } from "./config.js";

/**
 * Fallback SVG image placeholder when product image is missing or invalid.
 * Uses a pure base64-encoded SVG data URI to completely eliminate HTML attribute
 * escaping bugs, syntax errors, and literal text leaks in img onerror handlers.
 */
export const PLACEHOLDER_IMAGE = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMDAiIGhlaWdodD0iMjAwIiB2aWV3Qm94PSIwIDAgMzAwIDIwMCIgZmlsbD0ibm9uZSI+PHJlY3Qgd2lkdGg9IjMwMCIgaGVpZ2h0PSIyMDAiIGZpbGw9IiMxZTI5M2IiLz48cGF0aCBkPSJNMTAwIDYwaDEwMHY4MEgxMDB6IiBzdHJva2U9IiM0NzU1NjkiIHN0cm9rZS13aWR0aD0iMiIvPjxwYXRoIGQ9Ik0xMTAgMTIwbDIwLTI1IDIwIDE1IDI1LTMwIDI1IDQwSDEwMHoiIGZpbGw9IiM0NzU1NjkiLz48Y2lyY2xlIGN4PSIxMjUiIGN5PSI4NSIgcj0iOSIgZmlsbD0iIzQ3NTU2OSIvPjwvc3ZnPg==";

/**
 * Validates an image URL and returns either the clean URL or the safe placeholder.
 */
export function getSafeImageUrl(url) {
  if (!url || typeof url !== "string") {
    return PLACEHOLDER_IMAGE;
  }
  const clean = url.trim();
  if (clean.startsWith("https://") || clean.startsWith("http://") || clean.startsWith("data:image/")) {
    return clean;
  }
  return PLACEHOLDER_IMAGE;
}

/**
 * Standardized, safe onerror attribute string for <img> tags.
 */
export const IMG_FALLBACK_ATTR = `onerror="this.onerror=null;this.src='${PLACEHOLDER_IMAGE}';this.classList.add('opacity-60');"`;

/**
 * Render a product card for grids.
 */
export function renderProductCard(product, isCompared = false, activeMandate = null) {
  const offerCurrency = (product.offers?.[0]?.currency || "USD").toUpperCase();
  const lowestPrice = product.lowest_price !== null ? formatPrice(product.lowest_price, offerCurrency) : "Check Offers";
  const offersCount = product.offers ? product.offers.length : 0;
  const bestRetailer = product.lowest_price_retailer || product.offers?.[0]?.retailer_name || "Catalog";
  const isSynthetic = product.is_synthetic !== false;
  const warnings = product.data_quality_warnings || [];
  const hasWarnings = warnings.length > 0;

  // Check currency alignment with active mandate if configured
  const mandateCurrency = activeMandate?.currency ? activeMandate.currency.toUpperCase() : null;
  const isCurrencyMismatch = mandateCurrency && mandateCurrency !== offerCurrency;

  // Star rating representation
  let ratingHtml = "";
  if (product.rating) {
    const stars = Math.min(5, Math.max(0, Math.round(product.rating)));
    const starsStr = "★".repeat(stars) + "☆".repeat(5 - stars);
    ratingHtml = `
      <div class="flex items-center gap-1.5 text-xs text-amber-400">
        <span class="tracking-widest">${starsStr}</span>
        <span class="text-slate-400 font-medium">(${product.rating.toFixed(1)}${product.rating_count ? ` / ${product.rating_count}` : ""})</span>
      </div>
    `;
  }

  const compareBtnClass = isCompared
    ? "bg-burnt-orange text-white border-burnt-orange shadow-sm"
    : "bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700 hover:text-white";

  return `
    <div class="group flex flex-col bg-slate-900 border border-slate-800/80 rounded-2xl overflow-hidden hover:border-slate-700 transition-all duration-300 hover:shadow-xl hover:shadow-black/40" data-product-id="${product.id}">
      <!-- Image & Badges Container -->
      <div class="relative w-full h-48 bg-slate-950/60 flex items-center justify-center overflow-hidden p-4 border-b border-slate-800/60">
        <img
          src="${getSafeImageUrl(product.image_url)}"
          alt="${escapeHtml(product.name)}"
          class="max-h-full max-w-full object-contain transition-transform duration-300 group-hover:scale-105"
          loading="lazy"
          ${IMG_FALLBACK_ATTR}
        />
        
        <!-- Provenance & Quality Badges -->
        <div class="absolute top-3 left-3 flex flex-col gap-1.5 max-w-[70%]">
          ${
            isSynthetic
              ? `<span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 backdrop-blur-sm">
                   <span class="w-1.5 h-1.5 rounded-full bg-indigo-400"></span> Synthetic Demo
                 </span>`
              : `<span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 backdrop-blur-sm">
                   <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Live Channel3
                 </span>`
          }
          ${
            hasWarnings
              ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/10 text-amber-300 border border-amber-500/30 truncate" title="${escapeHtml(warnings.join(' | '))}">
                   ⚠ Data Notice
                 </span>`
              : ""
          }
          ${
            isCurrencyMismatch
              ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-rose-500/10 text-rose-300 border border-rose-500/30" title="Item currency is ${offerCurrency}, but your active mandate is in ${mandateCurrency}. Cross-currency purchases are blocked by policy.">
                   ⚠ ${offerCurrency} vs ${mandateCurrency}
                 </span>`
              : ""
          }
        </div>

        <!-- Category Badge -->
        <div class="absolute top-3 right-3">
          <span class="px-2.5 py-1 rounded-full text-[11px] font-medium bg-slate-800/80 text-slate-300 border border-slate-700/60 backdrop-blur-sm">
            ${escapeHtml(product.category)}
          </span>
        </div>
      </div>

      <!-- Card Body -->
      <div class="flex-1 flex flex-col p-5">
        <div class="mb-1 flex items-center justify-between">
          <span class="text-xs font-semibold uppercase tracking-wider text-burnt-orange">
            ${escapeHtml(product.brand)}
          </span>
        </div>
        <h3 class="text-base font-bold text-white line-clamp-2 leading-snug mb-2" title="${escapeHtml(product.name)}">
          ${escapeHtml(product.name)}
        </h3>

        ${ratingHtml}

        <p class="text-xs text-slate-400 line-clamp-2 mt-2 mb-4 leading-relaxed">
          ${escapeHtml(product.description || "Specifications and multi-retailer price comparison available.")}
        </p>

        <!-- Pricing & Best Offer -->
        <div class="mt-auto pt-3 border-t border-slate-800/80 flex items-end justify-between">
          <div>
            <div class="text-[11px] text-slate-400 font-medium">Best Offer (${escapeHtml(bestRetailer)})</div>
            <div class="text-xl font-extrabold text-white tracking-tight">${lowestPrice}</div>
          </div>
          <div class="text-right">
            <span class="inline-block text-[11px] font-medium text-slate-400 bg-slate-800/60 px-2 py-1 rounded-md border border-slate-700/40">
              ${offersCount} ${offersCount === 1 ? "offer" : "offers"}
            </span>
          </div>
        </div>

        <!-- Action Controls -->
        <div class="mt-4 flex flex-col gap-2 pt-2">
          ${
            isSynthetic && product.lowest_price !== null
              ? `<button
                  type="button"
                  class="js-btn-checkout w-full py-2 px-3 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors flex items-center justify-center gap-1.5 shadow-sm focus:ring-2 focus:ring-emerald-400 focus:outline-none"
                  data-product-id="${product.id}"
                  data-product-name="${escapeHtml(product.name)}"
                  data-retailer-name="${escapeHtml(bestRetailer)}"
                  data-price="${product.lowest_price}"
                  data-currency="${offerCurrency}"
                  data-is-synthetic="true"
                >
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M7.076 21.337H2.47a.641.641 0 0 1-.633-.74L4.944 3.72a.76.76 0 0 1 .752-.64h6.14c3.488 0 5.86 1.48 5.405 5.034-.33 2.585-2.072 4.183-4.52 4.673l-.42.062a.64.64 0 0 0-.53.535l-.83 4.275a.64.64 0 0 1-.63.518H7.433l-.357 3.155z"/></svg>
                  Buy with PayPal (Demo)
                </button>`
              : ""
          }
          <div class="grid grid-cols-2 gap-2">
            <button
              type="button"
              class="js-btn-details w-full py-2 px-3 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors focus:ring-2 focus:ring-burnt-orange/50 focus:outline-none text-center"
              data-id="${product.id}"
            >
              View Offers
            </button>
            <button
              type="button"
              class="js-btn-compare w-full py-2 px-3 text-xs font-semibold rounded-lg border transition-colors focus:ring-2 focus:ring-burnt-orange/50 focus:outline-none text-center ${compareBtnClass}"
              data-id="${product.id}"
            >
              ${isCompared ? "✓ Compared" : "+ Compare"}
            </button>
          </div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Render complete Product Detail modal content.
 */
export function renderProductDetailModal(product) {
  const isSynthetic = product.is_synthetic !== false;
  const lowestPrice = product.lowest_price !== null ? formatPrice(product.lowest_price, product.offers?.[0]?.currency || "USD") : "N/A";
  const bestRetailer = product.lowest_price_retailer || "Catalog Best";
  const offers = product.offers || [];

  // Specifications rows
  const specs = product.specifications || {};
  const specRows = Object.entries(specs)
    .filter(([key]) => !key.startsWith("_") && key !== "rating" && key !== "rating_count")
    .map(([key, val]) => `
      <tr class="border-b border-slate-800/60 last:border-b-0 hover:bg-slate-800/30">
        <td class="py-2.5 px-3 text-xs font-semibold text-slate-400 capitalize w-1/3">${escapeHtml(key.replace(/_/g, " "))}</td>
        <td class="py-2.5 px-3 text-xs text-white font-medium">${escapeHtml(typeof val === "object" ? JSON.stringify(val) : String(val))}</td>
      </tr>
    `)
    .join("");

  // Competing Retailer Offers rows
  const offerRows = offers.map((offer) => {
    const isOfferSynthetic = offer.is_synthetic !== false;
    const isStock = ["IN_STOCK", "LIMITED_STOCK", "LOW_STOCK"].includes(offer.availability.toUpperCase());
    const stockClass = isStock ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20" : "text-rose-400 bg-rose-500/10 border-rose-500/20";
    const strikePrice = offer.compare_at_price && offer.compare_at_price > offer.price
      ? `<span class="line-through text-slate-500 text-xs mr-1.5">${formatPrice(offer.compare_at_price, offer.currency)}</span>`
      : "";

    return `
      <tr class="border-b border-slate-800/60 last:border-b-0 hover:bg-slate-800/30">
        <td class="py-3 px-3">
          <div class="font-bold text-white text-xs">${escapeHtml(offer.retailer_name)}</div>
          <div class="text-[10px] text-slate-400">${isOfferSynthetic ? "Simulated Demo Store" : "Verified Merchant"}</div>
        </td>
        <td class="py-3 px-3">
          <div class="text-sm font-extrabold text-white">${strikePrice}${formatPrice(offer.price, offer.currency)}</div>
        </td>
        <td class="py-3 px-3">
          <span class="inline-flex px-2 py-0.5 rounded text-[10px] font-semibold border ${stockClass}">
            ${escapeHtml(offer.availability.replace(/_/g, " "))}
          </span>
        </td>
        <td class="py-3 px-3 text-right">
          ${
            isOfferSynthetic
              ? `<button
                  type="button"
                  class="js-btn-checkout inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors shadow-sm focus:ring-2 focus:ring-emerald-400 focus:outline-none"
                  data-product-id="${product.id}"
                  data-offer-id="${offer.id}"
                  data-product-name="${escapeHtml(product.name)}"
                  data-retailer-name="${escapeHtml(offer.retailer_name)}"
                  data-price="${offer.price}"
                  data-currency="${offer.currency}"
                  data-is-synthetic="true"
                >
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M7.076 21.337H2.47a.641.641 0 0 1-.633-.74L4.944 3.72a.76.76 0 0 1 .752-.64h6.14c3.488 0 5.86 1.48 5.405 5.034-.33 2.585-2.072 4.183-4.52 4.673l-.42.062a.64.64 0 0 0-.53.535l-.83 4.275a.64.64 0 0 1-.63.518H7.433l-.357 3.155z"/></svg>
                  Buy with PayPal
                </button>`
              : `<a
                  href="${offer.product_url}"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-burnt-orange hover:bg-burnt-orange/90 text-white transition-colors"
                >
                  Visit Merchant ↗
                </a>`
          }
        </td>
      </tr>
    `;
  }).join("");

  return `
    <div class="p-6">
      <!-- Modal Header -->
      <div class="flex items-start justify-between gap-4 pb-4 border-b border-slate-800">
        <div>
          <div class="flex items-center gap-2 mb-1">
            <span class="text-xs font-bold uppercase tracking-wider text-burnt-orange">${escapeHtml(product.brand)}</span>
            <span class="text-slate-500">•</span>
            <span class="text-xs font-medium text-slate-400">${escapeHtml(product.category)}</span>
            ${
              isSynthetic
                ? `<span class="ml-2 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">Synthetic Demo Item</span>`
                : `<span class="ml-2 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Live Channel3 Item</span>`
            }
          </div>
          <h2 class="text-xl font-extrabold text-white">${escapeHtml(product.name)}</h2>
        </div>
        <button type="button" class="js-close-modal p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- Main Overview Grid -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6 py-6 border-b border-slate-800">
        <div class="bg-slate-950/60 rounded-xl p-4 flex items-center justify-center border border-slate-800/80">
          <img
            src="${getSafeImageUrl(product.image_url)}"
            alt="${escapeHtml(product.name)}"
            class="max-h-48 object-contain"
            ${IMG_FALLBACK_ATTR}
          />
        </div>
        <div class="md:col-span-2 flex flex-col justify-between">
          <div>
            <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Description</h4>
            <p class="text-sm text-slate-300 leading-relaxed">${escapeHtml(product.description || "No marketing description provided.")}</p>
            
            ${
              product.data_quality_warnings && product.data_quality_warnings.length > 0
                ? `<div class="mt-3 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300">
                     <strong class="font-semibold block mb-0.5">Data Quality Notices:</strong>
                     <ul class="list-disc list-inside space-y-0.5 text-[11px] text-amber-400/90">
                       ${product.data_quality_warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}
                     </ul>
                   </div>`
                : ""
            }
          </div>

          <!-- Lowest Price Summary Box -->
          <div class="mt-4 p-4 rounded-xl bg-slate-800/60 border border-slate-700/60 flex items-center justify-between">
            <div>
              <span class="text-xs text-slate-400 font-medium">Lowest Available Price</span>
              <div class="text-2xl font-black text-white">${lowestPrice}</div>
              <div class="text-xs text-slate-400">Sold by <strong class="text-white">${escapeHtml(bestRetailer)}</strong></div>
            </div>
            <div class="text-right">
              <span class="inline-flex items-center gap-1 px-3 py-1 rounded-md text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                Verified Lowest
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Competing Retailer Offers Section -->
      <div class="py-6 border-b border-slate-800">
        <h3 class="text-sm font-bold text-white uppercase tracking-wider mb-3">All Retailer Offers (${offers.length})</h3>
        <div class="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/40">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-slate-800 bg-slate-900/80 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                <th class="py-2.5 px-3">Merchant</th>
                <th class="py-2.5 px-3">Offer Price</th>
                <th class="py-2.5 px-3">Availability</th>
                <th class="py-2.5 px-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              ${offerRows || `<tr><td colspan="4" class="py-4 text-center text-xs text-slate-500">No retailer offers found for this product.</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>

      <!-- Specifications Matrix Section -->
      <div class="pt-6">
        <h3 class="text-sm font-bold text-white uppercase tracking-wider mb-3">Technical Specifications</h3>
        <div class="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/40 max-h-60 overflow-y-auto">
          <table class="w-full text-left border-collapse">
            <tbody>
              ${specRows || `<tr><td class="py-4 text-center text-xs text-slate-500">No technical specifications recorded.</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>

      <!-- Product Intelligence & Price History Section -->
      <div id="product-intelligence-section" class="pt-6 border-t border-slate-800 mt-6">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-4">
          <div>
            <div class="flex items-center gap-2 mb-0.5">
              <span class="w-2 h-2 rounded-full bg-burnt-orange animate-pulse"></span>
              <h3 class="text-sm font-bold text-white uppercase tracking-wider">Product Intelligence &amp; Price History</h3>
            </div>
            <p class="text-xs text-slate-400">Authentic historical price observations, price stability, and cross-retailer spread analysis.</p>
          </div>
          <div id="product-intel-header-badges" class="flex items-center gap-2"></div>
        </div>
        <div id="product-intelligence-container" data-product-id="${escapeHtml(product.id)}">
          ${renderProductIntelligenceSkeleton()}
        </div>
      </div>
    </div>
  `;
}

/**
 * Render order status badge.
 */
export function renderStatusBadge(status) {
  const norm = (status || "").toUpperCase();
  const styles = {
    COMPLETED: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    CREATED: "bg-sky-500/10 text-sky-400 border-sky-500/20",
    APPROVED: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
    CAPTURE_PENDING: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    FAILED: "bg-rose-500/10 text-rose-400 border-rose-500/20",
    CANCELLED: "bg-slate-500/10 text-slate-400 border-slate-500/20",
    EXPIRED: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
  };
  const activeClass = styles[norm] || "bg-slate-700/20 text-slate-300 border-slate-700";

  return `
    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${activeClass}">
      <span class="w-1.5 h-1.5 rounded-full bg-current"></span>
      ${escapeHtml(norm)}
    </span>
  `;
}

/**
 * Render loading skeleton placeholder for cards.
 */
export function renderSkeletonCard() {
  return `
    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 flex flex-col gap-4 animate-pulse">
      <div class="w-full h-44 bg-slate-800/80 rounded-xl"></div>
      <div class="space-y-2">
        <div class="h-3 bg-slate-800 rounded w-1/4"></div>
        <div class="h-4 bg-slate-800 rounded w-3/4"></div>
        <div class="h-3 bg-slate-800/60 rounded w-full"></div>
      </div>
      <div class="mt-auto pt-3 border-t border-slate-800 flex justify-between items-center">
        <div class="h-5 bg-slate-800 rounded w-1/3"></div>
        <div class="h-8 bg-slate-800 rounded w-1/4"></div>
      </div>
    </div>
  `;
}

/**
 * Render empty state card with optional action button.
 */
export function renderEmptyState(title, description, actionText = null, actionId = null) {
  return `
    <div class="flex flex-col items-center justify-center text-center p-12 bg-slate-900/60 border border-slate-800 rounded-2xl max-w-lg mx-auto">
      <div class="w-14 h-14 rounded-2xl bg-slate-800/80 border border-slate-700 flex items-center justify-center text-slate-400 mb-4">
        <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
      </div>
      <h3 class="text-base font-bold text-white mb-1.5">${escapeHtml(title)}</h3>
      <p class="text-xs text-slate-400 max-w-sm mb-6 leading-relaxed">${escapeHtml(description)}</p>
      ${
        actionText && actionId
          ? `<button type="button" id="${actionId}" class="px-4 py-2 text-xs font-semibold rounded-lg bg-burnt-orange hover:bg-burnt-orange/90 text-white transition-colors">
               ${escapeHtml(actionText)}
             </button>`
          : ""
      }
    </div>
  `;
}

/**
 * Render floating toast notifications.
 */
export function showToast(message, type = "info", duration = 4000) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  const colors = {
    success: "bg-emerald-950 border-emerald-500/40 text-emerald-200",
    error: "bg-rose-950 border-rose-500/40 text-rose-200",
    warning: "bg-amber-950 border-amber-500/40 text-amber-200",
    info: "bg-slate-900 border-slate-700 text-slate-200",
  };
  const colorClass = colors[type] || colors.info;

  toast.className = `flex items-center gap-2.5 px-4 py-3 rounded-xl border shadow-xl text-xs font-medium transition-all duration-300 transform translate-y-2 opacity-0 ${colorClass}`;
  toast.innerHTML = `
    <span class="text-sm font-bold">${type === "success" ? "✓" : type === "error" ? "✖" : "ℹ"}</span>
    <span class="flex-1">${escapeHtml(message)}</span>
  `;

  container.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.remove("translate-y-2", "opacity-0");
  });

  setTimeout(() => {
    toast.classList.add("opacity-0", "translate-y-2");
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

/**
 * Basic HTML escaping helper.
 */
export function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/**
 * Lightweight inline markdown formatter (bold, italic, inline code).
 * Text MUST be HTML-escaped before passing into this function.
 */
function formatInlineMarkdown(text) {
  if (!text) return "";
  return text
    .replace(/`([^`\n]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-slate-800 text-amber-300 font-mono text-[11px] sm:text-xs border border-slate-700/60">$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-white">$1</strong>')
    .replace(/__(.+?)__/g, '<strong class="font-semibold text-white">$1</strong>')
    .replace(/\*([^\*\n]+)\*/g, '<em class="italic text-slate-200">$1</em>')
    .replace(/(?:^|\s)_([^_]+)_(?=\s|$|[.,!?:;])/g, (match, p1) => {
      const leadingSpace = match.startsWith(" ") ? " " : "";
      return `${leadingSpace}<em class="italic text-slate-200">${p1}</em>`;
    });
}

/**
 * Lightweight, safe Markdown renderer for assistant messages.
 * Escapes HTML first to strictly prevent arbitrary script/HTML execution,
 * then formats headings (###, ##, #), lists (ordered, unordered),
 * horizontal rules (---), bold, italic, and inline code.
 */
export function renderMarkdown(raw) {
  if (!raw) return "";
  const lines = String(raw).split(/\r?\n/);
  const out = [];
  let inList = null; // "ul" or "ol"

  function closeList() {
    if (inList) {
      out.push(inList === "ul" ? "</ul>" : "</ol>");
      inList = null;
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const trimmed = rawLine.trim();

    if (!trimmed) {
      closeList();
      continue;
    }

    // Horizontal rule: ---, ***, ___
    if (/^(?:---|\*\*\*|___)\s*$/.test(trimmed)) {
      closeList();
      out.push('<hr class="my-3 border-slate-700/80" />');
      continue;
    }

    // Headings: ###, ##, #
    const h3Match = trimmed.match(/^###\s+(.+)$/);
    if (h3Match) {
      closeList();
      const escaped = escapeHtml(h3Match[1]);
      out.push(`<h4 class="text-sm font-bold text-white mt-3 mb-1.5">${formatInlineMarkdown(escaped)}</h4>`);
      continue;
    }

    const h2Match = trimmed.match(/^##\s+(.+)$/);
    if (h2Match) {
      closeList();
      const escaped = escapeHtml(h2Match[1]);
      out.push(`<h3 class="text-base font-bold text-white mt-4 mb-2">${formatInlineMarkdown(escaped)}</h3>`);
      continue;
    }

    const h1Match = trimmed.match(/^#\s+(.+)$/);
    if (h1Match) {
      closeList();
      const escaped = escapeHtml(h1Match[1]);
      out.push(`<h2 class="text-lg font-bold text-white mt-4 mb-2">${formatInlineMarkdown(escaped)}</h2>`);
      continue;
    }

    // Unordered list item: - item, * item, ? item (\u2022)
    const ulMatch = trimmed.match(/^[-*?\u2022]\s+(.+)$/);
    if (ulMatch) {
      if (inList !== "ul") {
        closeList();
        out.push('<ul class="list-disc list-inside space-y-1 my-2 pl-1 text-slate-300">');
        inList = "ul";
      }
      const escaped = escapeHtml(ulMatch[1]);
      out.push(`<li class="leading-relaxed">${formatInlineMarkdown(escaped)}</li>`);
      continue;
    }

    // Ordered list item: 1. item, 2. item
    const olMatch = trimmed.match(/^\d+\.\s+(.+)$/);
    if (olMatch) {
      if (inList !== "ol") {
        closeList();
        out.push('<ol class="list-decimal list-inside space-y-1 my-2 pl-1 text-slate-300">');
        inList = "ol";
      }
      const escaped = escapeHtml(olMatch[1]);
      out.push(`<li class="leading-relaxed">${formatInlineMarkdown(escaped)}</li>`);
      continue;
    }

    // Normal paragraph
    closeList();
    const escaped = escapeHtml(trimmed);
    out.push(`<p class="mb-2 last:mb-0">${formatInlineMarkdown(escaped)}</p>`);
  }

  closeList();
  return out.join("");
}


/**
 * =========================================================================
 * AI SHOPPING ASSISTANT UI COMPONENTS
 * =========================================================================
 */

/**
 * Welcome screen rendered when the assistant conversation is empty.
 */
export function renderAssistantWelcome() {
  const examplePrompts = [
    "Find a laptop for coding with at least 16GB RAM under $1500",
    "Recommend wireless noise-cancelling headphones for travel under $250",
    "Find Nike running shoes for training under $120",
    "Finde Laufschuhe unter 150 Dollar",
  ];

  return `
    <div id="assistant-welcome-screen" class="py-6 sm:py-10 flex flex-col items-center text-center max-w-2xl mx-auto space-y-6">
      <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-burnt-orange to-amber-600 flex items-center justify-center shadow-xl shadow-burnt-orange/20">
        <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
      </div>

      <div class="space-y-2">
        <h2 class="text-xl sm:text-2xl font-heading font-extrabold text-white">How can I assist your shopping today?</h2>
        <p class="text-xs sm:text-sm text-slate-400 leading-relaxed">
          I extract your criteria, aggregate live offers across retailers via Channel3, assess technical trade-offs with Gemini 3.6 Flash, and verify your spending mandate.
        </p>
      </div>

      <!-- Quick Suggestion Prompt Chips -->
      <div class="w-full space-y-2 pt-2">
        <span class="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">Try one of these queries:</span>
        <div class="flex flex-wrap justify-center gap-2">
          ${examplePrompts
            .map(
              (prompt) => `
            <button
              type="button"
              class="js-assistant-prompt-chip text-left px-3.5 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 hover:border-burnt-orange/60 text-xs text-slate-300 hover:text-white transition-all duration-200"
              data-prompt="${escapeHtml(prompt)}"
            >
              💬 ${escapeHtml(prompt)}
            </button>
          `
            )
            .join("")}
        </div>
      </div>
    </div>
  `;
}

/**
 * User speech bubble.
 */
export function renderAssistantUserMessage(content) {
  return `
    <div class="flex justify-end items-start gap-3">
      <div class="max-w-[85%] sm:max-w-[75%] rounded-2xl rounded-tr-none px-4 py-3 bg-burnt-orange text-white text-xs sm:text-sm leading-relaxed shadow-lg">
        <p class="whitespace-pre-wrap">${escapeHtml(content)}</p>
      </div>
      <div class="w-8 h-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center shrink-0 text-[11px] font-bold text-slate-300">
        You
      </div>
    </div>
  `;
}

/**
 * Assistant speech bubble with conversational guidance, notices, and recommended products.
 */
export function renderAssistantMessage(data, isComparedList = [], activeMandate = null) {
  const isDegraded = Boolean(data.is_ai_degraded);
  const degradedReason = data.degraded_reason || "";
  const mandateEval = data.mandate_evaluation || null;
  const products = data.products || [];
  const eligibilityList = data.eligibility_details || [];
  const intelligenceList = data.ai_analyses || [];

  // Build maps for fast O(1) lookup
  const eligibilityMap = {};
  for (const item of eligibilityList) {
    if (item && item.product_id) eligibilityMap[item.product_id] = item;
  }

  const intelligenceMap = {};
  for (const item of intelligenceList) {
    if (item && item.product_id) intelligenceMap[item.product_id] = item;
  }

  // Format assistant message text with safe markdown rendering
  const rawMessage = data.assistant_message || "Here are the top options matching your search.";
  const formattedMessage = renderMarkdown(rawMessage);

  return `
    <div class="flex items-start gap-3">
      <div class="w-8 h-8 rounded-full bg-burnt-orange/20 border border-burnt-orange/40 flex items-center justify-center shrink-0 text-burnt-orange text-xs font-bold shadow-md">
        AI
      </div>
      <div class="flex-1 max-w-[95%] sm:max-w-[85%] rounded-2xl rounded-tl-none p-4 sm:p-5 bg-slate-900 border border-slate-800 text-xs sm:text-sm text-slate-200 space-y-4 shadow-xl">
        <!-- AI Degradation / High Traffic Notice -->
        ${
          isDegraded
            ? `
          <div class="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs flex items-start gap-2">
            <span class="text-amber-400 mt-0.5 text-sm">⚠</span>
            <div>
              <span class="font-semibold block">High Demand Grounded Notice</span>
              <span class="text-[11px] text-amber-200/90 leading-relaxed">
                Live Gemini reasoning experienced high traffic. Switched to deterministic rule-based analysis and verified live Channel3 results.
                ${degradedReason ? `(${escapeHtml(degradedReason)})` : ""}
              </span>
            </div>
          </div>
        `
            : ""
        }

        <!-- Spending Mandate Policy Banner -->
        ${
          mandateEval
            ? `
          <div class="p-3 rounded-xl text-xs flex items-start gap-2 ${
            mandateEval.decision === "ALLOWED"
              ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-300"
              : mandateEval.decision === "WARNING_OVERRIDE_REQUIRED"
              ? "bg-amber-500/10 border border-amber-500/30 text-amber-300"
              : "bg-rose-500/10 border border-rose-500/30 text-rose-300"
          }">
            <span class="mt-0.5">${mandateEval.decision === "ALLOWED" ? "✅" : mandateEval.decision === "WARNING_OVERRIDE_REQUIRED" ? "⚠ï¸" : "❌"}</span>
            <div>
              <span class="font-semibold block">Spending Mandate Evaluation: ${escapeHtml(mandateEval.decision)}</span>
              <span class="text-[11px] opacity-90 leading-relaxed">
                ${
                  mandateEval.decision === "ALLOWED"
                    ? "Purchase of top recommended item satisfies your active session spending ceilings."
                    : mandateEval.decision === "WARNING_OVERRIDE_REQUIRED"
                    ? "Item exceeds your preferred budget. You can proceed with checkout by confirming the budget override."
                    : escapeHtml((mandateEval.reasons || []).join(" ") || "Blocked by active mandate policy restrictions.")
                }
              </span>
            </div>
          </div>
        `
            : ""
        }

        <!-- Conversational Message Body -->
        <div class="text-slate-300 leading-relaxed font-normal">
          ${formattedMessage}
        </div>

        <!-- Grounded Technical Comparison & Trade-Offs (Phase 4A.4A) -->
        ${
          data.comparison_explanation
            ? renderComparisonAIInsights(data.comparison_explanation, true)
            : ""
        }

        <!-- Recommended Product Cards Grid (Phase 4A.4C Progressive Disclosure) -->
        ${
          products.length > 0
            ? (() => {
                const totalCount = products.length;
                const initialProducts = products.slice(0, 6);
                const remainingProducts = products.slice(6);
                const hasOverflow = remainingProducts.length > 0;
                const overflowId = "assistant-overflow-" + Math.random().toString(36).slice(2, 9);

                return `
          <div class="pt-2 border-t border-slate-800/80">
            <div class="mb-3 flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-burnt-orange">
                Recommended Live Products (${totalCount})
              </span>
              <span class="text-[11px] text-slate-500">Grounded &amp; Policy Evaluated</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              ${initialProducts
                .map((product) =>
                  renderAssistantProductCard(
                    product,
                    eligibilityMap[product.id] || null,
                    intelligenceMap[product.id] || null,
                    isComparedList.includes(product.id),
                    activeMandate
                  )
                )
                .join("")}
            </div>
            ${
              hasOverflow
                ? `
            <div id="${overflowId}" class="hidden grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 transition-all">
              ${remainingProducts
                .map((product) =>
                  renderAssistantProductCard(
                    product,
                    eligibilityMap[product.id] || null,
                    intelligenceMap[product.id] || null,
                    isComparedList.includes(product.id),
                    activeMandate
                  )
                )
                .join("")}
            </div>
            <div class="mt-4 flex justify-center">
              <button
                type="button"
                class="js-btn-toggle-assistant-products px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 hover:text-white transition-all flex items-center gap-2 border border-slate-700/80 shadow-sm"
                data-target="${overflowId}"
                data-expanded="false"
                data-count="${remainingProducts.length}"
              >
                <span class="btn-text">Show More Products (+${remainingProducts.length} more)</span>
                <svg class="w-3.5 h-3.5 text-burnt-orange transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                </svg>
              </button>
            </div>
            `
                : ""
            }
          </div>
        `;
              })()
            : ""
        }
      </div>
    </div>
  `;
}

/**
 * Render Grounded AI Technical Comparison & Trade-Offs Insights.
 * Used in both the Dedicated Compare section (#compare) and conversational stream.
 */
export function renderComparisonAIInsights(explanation, isCompact = false) {
  if (!explanation) return "";

  const summary = explanation.summary || "";
  const bestChoice = explanation.best_choice_for_use_case || explanation.overall_winner || "";
  const bestVal = explanation.best_value_pick || "";
  const perfDiffs = explanation.performance_differences && explanation.performance_differences.length > 0
    ? explanation.performance_differences
    : (explanation.key_differences || []);
  const valueTradeoffs = explanation.value_tradeoffs || "";
  const currencyWarning = explanation.currency_consistency_warning || null;
  const missingNotes = explanation.missing_specs_notes || [];

  return `
    <div class="rounded-xl border border-burnt-orange/30 bg-slate-900/90 overflow-hidden shadow-lg ${isCompact ? 'my-3 p-3.5' : 'mb-6 p-5'}">
      <!-- Header -->
      <div class="flex items-center justify-between gap-2 border-b border-slate-800 pb-2.5 mb-3">
        <div class="flex items-center gap-2">
          <span class="w-2.5 h-2.5 rounded-full bg-burnt-orange animate-pulse"></span>
          <h4 class="text-xs font-bold text-white uppercase tracking-wider">AI Technical Comparison &amp; Trade-Offs</h4>
        </div>
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-burnt-orange/15 text-burnt-orange border border-burnt-orange/30">
          Grounded Analysis
        </span>
      </div>

      <!-- Currency Warning if Cross-Currency -->
      ${currencyWarning ? `
        <div class="mb-3 p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-300 text-xs flex items-start gap-2">
          <span class="text-sm">⚠ï¸</span>
          <div class="leading-relaxed">
            <span class="font-semibold block text-[11px] uppercase tracking-wider text-amber-400">Multi-Currency Caution:</span>
            <span>${escapeHtml(currencyWarning)}</span>
          </div>
        </div>
      ` : ""}

      <!-- Summary & Winner Pill -->
      <div class="space-y-2 mb-3">
        ${summary ? `<p class="text-xs text-slate-300 leading-relaxed">${escapeHtml(summary)}</p>` : ""}
        
        <div class="flex flex-wrap items-center gap-2 pt-1">
          ${bestChoice ? `
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
              <span class="text-emerald-400">🏆</span>
              <span><strong>Top Pick:</strong> ${escapeHtml(bestChoice)}</span>
            </span>
          ` : ""}
          ${bestVal ? `
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30">
              <span class="text-sky-400">💡</span>
              <span><strong>Best Value:</strong> ${escapeHtml(bestVal)}</span>
            </span>
          ` : ""}
        </div>
      </div>

      <!-- Performance Differences -->
      ${perfDiffs.length > 0 ? `
        <div class="mt-3 pt-3 border-t border-slate-800/80">
          <span class="text-[11px] font-bold text-slate-400 uppercase tracking-wider block mb-1.5">Key Technical Differences:</span>
          <ul class="space-y-1">
            ${perfDiffs.slice(0, 4).map(d => `
              <li class="text-xs text-slate-300 flex items-start gap-1.5 leading-snug">
                <span class="text-burnt-orange shrink-0">•</span>
                <span>${escapeHtml(d)}</span>
              </li>
            `).join("")}
          </ul>
        </div>
      ` : ""}

      <!-- Value Trade-offs -->
      ${valueTradeoffs ? `
        <div class="mt-3 pt-2.5 border-t border-slate-800/80">
          <span class="text-[11px] font-bold text-slate-400 uppercase tracking-wider block mb-1">Price Spread &amp; Value Trade-Off:</span>
          <p class="text-xs text-slate-300 leading-relaxed">${escapeHtml(valueTradeoffs)}</p>
        </div>
      ` : ""}

      <!-- Missing Specs Notice -->
      ${missingNotes.length > 0 ? `
        <div class="mt-3 pt-2 text-[11px] text-slate-400 flex items-center gap-1.5">
          <span class="text-slate-500">ℹï¸</span>
          <span>${escapeHtml(missingNotes[0])}</span>
        </div>
      ` : ""}
    </div>
  `;
}

/**
 * Compact Product Recommendation Card tailored for the Assistant stream.
 */
export function renderAssistantProductCard(product, eligibility = null, intelligence = null, isCompared = false, activeMandate = null) {
  const offerCurrency = (product.offers?.[0]?.currency || "USD").toUpperCase();
  const lowestPrice = product.lowest_price !== null ? formatPrice(product.lowest_price, offerCurrency) : "Check Offers";
  const bestRetailer = product.lowest_price_retailer || product.offers?.[0]?.retailer_name || "Catalog";
  const offersCount = product.offers ? product.offers.length : 0;
  const isSynthetic = product.is_synthetic !== false;

  // Eligibility Status Pill
  let eligibilityPill = "";
  if (eligibility) {
    if (eligibility.status === "QUALIFIED") {
      eligibilityPill = `
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> ✓ Qualified
        </span>
      `;
    } else if (eligibility.status === "CURRENCY_MISMATCH") {
      eligibilityPill = `
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30" title="Cross-currency comparison caution">
          <span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span> ⚠ Currency Notice
        </span>
      `;
    } else if (eligibility.status === "UNVERIFIED_SPECS") {
      eligibilityPill = `
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
          <span class="w-1.5 h-1.5 rounded-full bg-indigo-400"></span> ℹ Unverified Specs
        </span>
      `;
    } else {
      eligibilityPill = `
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/15 text-rose-300 border border-rose-500/30">
          <span class="w-1.5 h-1.5 rounded-full bg-rose-400"></span> ✖ Excluded
        </span>
      `;
    }
  }

  // Eligibility Reasons
  let reasonsHtml = "";
  if (eligibility && eligibility.reasons && eligibility.reasons.length > 0) {
    reasonsHtml = `
      <ul class="space-y-0.5 my-2">
        ${eligibility.reasons
          .slice(0, 2)
          .map(
            (r) => `
          <li class="text-[11px] text-slate-400 flex items-start gap-1 leading-snug">
            <span class="text-slate-600 shrink-0">•</span>
            <span>${escapeHtml(r)}</span>
          </li>
        `
          )
          .join("")}
      </ul>
    `;
  }

  // Mandate Compliance Status Pill (Phase 4A.2 & User-Controlled Budget Override)
  let mandatePill = "";
  const mandateStatus = eligibility ? eligibility.mandate_status : (activeMandate ? null : "NOT_APPLICABLE");
  if (mandateStatus === "ALLOWED") {
    mandatePill = `
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30" title="Mandate Approved">
        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> ✓ Mandate Approved
      </span>
    `;
  } else if (mandateStatus === "WARNING_OVERRIDE_REQUIRED" || (eligibility && eligibility.is_budget_exceeded && mandateStatus !== "BLOCKED")) {
    const excessStr = (eligibility && eligibility.budget_exceeded_amount != null && eligibility.budget_exceeded_amount > 0)
      ? ` (+${formatPrice(eligibility.budget_exceeded_amount, offerCurrency)})`
      : "";
    mandatePill = `
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30" title="Exceeds Preferred Budget${excessStr}">
        <span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span> ⚠ï¸ Exceeds Budget${excessStr}
      </span>
    `;
  } else if (mandateStatus === "BLOCKED") {
    mandatePill = `
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/15 text-rose-300 border border-rose-500/30" title="Exceeds Mandate Restrictions">
        <span class="w-1.5 h-1.5 rounded-full bg-rose-400"></span> ✖ Exceeds Mandate Restrictions
      </span>
    `;
  } else {
    mandatePill = `
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-400 border border-slate-700/60" title="No Active Mandate">
        <span class="w-1.5 h-1.5 rounded-full bg-slate-500"></span> ⚪ No Active Mandate
      </span>
    `;
  }

  // Preference Match Score Pill (Phase 4A.3)
  let scorePill = "";
  if (eligibility && typeof eligibility.recommendation_score === "number" && eligibility.recommendation_score > 0) {
    const scoreVal = Math.round(eligibility.recommendation_score);
    scorePill = `
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-burnt-orange/15 text-burnt-orange border border-burnt-orange/30" title="Preference Match Score: ${scoreVal}/100">
        ⭐ ${scoreVal}% Match
      </span>
    `;
  }

  // Preference Scoring Highlights (Phase 4A.3)
  let scoreReasonsHtml = "";
  if (eligibility && eligibility.score_reasons && eligibility.score_reasons.length > 0 && eligibility.recommendation_score > 0) {
    const prefHighlights = eligibility.score_reasons.filter(r => r.startsWith("+") || r.startsWith("0.0"));
    if (prefHighlights.length > 0) {
      scoreReasonsHtml = `
        <div class="mt-1.5 p-2 rounded-lg bg-slate-900/90 border border-slate-800 text-[11px] text-slate-300">
          <span class="font-semibold block text-[10px] uppercase tracking-wider text-burnt-orange mb-0.5">Preference Breakdown:</span>
          <ul class="space-y-0.5">
            ${prefHighlights.slice(0, 2).map(r => `
              <li class="flex items-start gap-1 text-[10.5px] leading-tight ${r.startsWith('+') ? 'text-slate-300' : 'text-slate-400'}">
                <span class="${r.startsWith('+') ? 'text-emerald-400 font-bold' : 'text-slate-500'} shrink-0">${r.startsWith('+') ? '✓' : '•'}</span>
                <span>${escapeHtml(r)}</span>
              </li>
            `).join("")}
          </ul>
        </div>
      `;
    }
  }

  // Mandate Restriction Notice (if product is blocked by mandate or exceeds preferred budget)
  let mandateNoticeHtml = "";
  if (eligibility && (eligibility.mandate_status === "WARNING_OVERRIDE_REQUIRED" || (eligibility.is_budget_exceeded && eligibility.mandate_status !== "BLOCKED"))) {
    const excessStr = (eligibility.budget_exceeded_amount != null && eligibility.budget_exceeded_amount > 0)
      ? formatPrice(eligibility.budget_exceeded_amount, offerCurrency)
      : "";
    const noticeText = eligibility.mandate_reasons?.[0]
      || (excessStr ? `Item price exceeds your preferred budget by ${excessStr}. You can proceed with purchase after explicit confirmation.` : "Exceeds preferred budget.");
    mandateNoticeHtml = `
      <div class="my-1.5 p-2 rounded-lg bg-amber-950/40 border border-amber-800/40 text-[11px] text-amber-300 flex items-start gap-1.5">
        <span class="text-amber-400 shrink-0 font-bold">⚠ï¸</span>
        <div>
          <span class="font-semibold block text-[10px] uppercase tracking-wider text-amber-400">Exceeds Preferred Budget:</span>
          <span>${escapeHtml(noticeText)}</span>
        </div>
      </div>
    `;
  } else if (eligibility && eligibility.mandate_status === "BLOCKED" && eligibility.mandate_reasons && eligibility.mandate_reasons.length > 0) {
    mandateNoticeHtml = `
      <div class="my-1.5 p-2 rounded-lg bg-rose-950/40 border border-rose-800/40 text-[11px] text-rose-300 flex items-start gap-1.5">
        <span class="text-rose-400 shrink-0 font-bold">⚠ï¸</span>
        <div>
          <span class="font-semibold block text-[10px] uppercase tracking-wider text-rose-400">Exceeds Mandate Restrictions:</span>
          <span>${escapeHtml(eligibility.mandate_reasons[0])}</span>
        </div>
      </div>
    `;
  }

  // AI Intelligence highlights
  let aiIntelHtml = "";
  if (intelligence && (intelligence.suitability || (intelligence.strengths && intelligence.strengths.length > 0))) {
    aiIntelHtml = `
      <div class="mt-2.5 p-2 rounded-lg bg-slate-950/80 border border-slate-800/80 space-y-1">
        ${
          intelligence.suitability
            ? `<p class="text-[11px] text-slate-300 leading-snug line-clamp-2"><strong class="text-burnt-orange font-semibold">Suitability:</strong> ${escapeHtml(
                intelligence.suitability
              )}</p>`
            : ""
        }
        ${
          intelligence.strengths && intelligence.strengths.length > 0
            ? `
          <div class="pt-0.5">
            <span class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Strengths:</span>
            <ul class="space-y-0.5 mt-0.5">
              ${intelligence.strengths
                .slice(0, 2)
                .map(
                  (s) => `
                <li class="text-[11px] text-slate-400 flex items-start gap-1 leading-snug">
                  <span class="text-emerald-400 shrink-0">✓</span>
                  <span>${escapeHtml(s)}</span>
                </li>
              `
                )
                .join("")}
            </ul>
          </div>
        `
            : ""
        }
      </div>
    `;
  }

  const compareBtnClass = isCompared
    ? "bg-burnt-orange text-white border-burnt-orange shadow-sm"
    : "bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700 hover:text-white";

  return `
    <div class="flex flex-col bg-slate-950 border border-slate-800/90 rounded-xl overflow-hidden hover:border-slate-700 transition-all shadow-md" data-product-id="${product.id}">
      <!-- Header row with Image and Info -->
      <div class="p-3 flex gap-3 border-b border-slate-800/60">
        <div class="w-16 h-16 bg-slate-900 rounded-lg p-1.5 flex items-center justify-center shrink-0 border border-slate-800/80 overflow-hidden">
          <img
            src="${getSafeImageUrl(product.image_url)}"
            alt="${escapeHtml(product.name)}"
            class="max-h-full max-w-full object-contain"
            loading="lazy"
            ${IMG_FALLBACK_ATTR}
          />
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-1.5 flex-wrap mb-0.5">
            <span class="text-[10px] font-bold uppercase tracking-wider text-burnt-orange">${escapeHtml(product.brand)}</span>
            ${
              isSynthetic
                ? `<span class="px-1.5 py-0.2 rounded text-[9px] font-medium bg-indigo-500/10 text-indigo-400">Demo</span>`
                : `<span class="px-1.5 py-0.2 rounded text-[9px] font-medium bg-emerald-500/10 text-emerald-400">Live</span>`
            }
            ${eligibilityPill}
            ${mandatePill}
            ${scorePill}
          </div>
          <h4 class="text-xs font-bold text-white line-clamp-1 leading-snug" title="${escapeHtml(product.name)}">
            ${escapeHtml(product.name)}
          </h4>
          <div class="mt-1 flex items-baseline gap-2">
            <span class="text-sm font-extrabold text-white">${lowestPrice}</span>
            <span class="text-[10px] text-slate-400">via ${escapeHtml(bestRetailer)} (${offersCount} ${offersCount === 1 ? "offer" : "offers"})</span>
          </div>
        </div>
      </div>

      <!-- Card Body: Eligibility reasons and AI suitability -->
      <div class="p-3 flex-1 flex flex-col justify-between">
        <div>
          ${reasonsHtml}
          ${mandateNoticeHtml}
          ${scoreReasonsHtml}
          ${aiIntelHtml}
        </div>

        <!-- Action Buttons -->
        <div class="mt-3 flex flex-col gap-2 pt-2 border-t border-slate-800/60">
          ${
            isSynthetic && product.lowest_price !== null
              ? `<button
                  type="button"
                  class="js-btn-checkout w-full py-1.5 px-2 text-[11px] font-bold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors flex items-center justify-center gap-1.5 shadow-sm focus:ring-2 focus:ring-emerald-400 focus:outline-none"
                  data-product-id="${product.id}"
                  data-product-name="${escapeHtml(product.name)}"
                  data-retailer-name="${escapeHtml(bestRetailer)}"
                  data-price="${product.lowest_price}"
                  data-currency="${offerCurrency}"
                  data-is-synthetic="true"
                >
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M7.076 21.337H2.47a.641.641 0 0 1-.633-.74L4.944 3.72a.76.76 0 0 1 .752-.64h6.14c3.488 0 5.86 1.48 5.405 5.034-.33 2.585-2.072 4.183-4.52 4.673l-.42.062a.64.64 0 0 0-.53.535l-.83 4.275a.64.64 0 0 1-.63.518H7.433l-.357 3.155z"/></svg>
                  Buy with PayPal (Demo)
                </button>`
              : ""
          }
          <div class="grid grid-cols-2 gap-2">
            <button
              type="button"
              class="js-btn-details w-full py-1.5 px-2 text-[11px] font-semibold rounded-lg bg-burnt-orange hover:bg-burnt-orange/90 text-white transition-colors text-center"
              data-id="${product.id}"
            >
              View Offers
            </button>
            <button
              type="button"
              class="js-btn-compare w-full py-1.5 px-2 text-[11px] font-semibold rounded-lg border transition-colors text-center ${compareBtnClass}"
              data-id="${product.id}"
            >
              ${isCompared ? "✓ Compared" : "+ Compare"}
            </button>
          </div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Animated Loading Indicator for Assistant queries.
 */
export function renderAssistantLoading() {
  return `
    <div id="assistant-loading-indicator" class="flex items-start gap-3">
      <div class="w-8 h-8 rounded-full bg-burnt-orange/20 border border-burnt-orange/40 flex items-center justify-center shrink-0 text-burnt-orange text-xs font-bold shadow-md">
        AI
      </div>
      <div class="px-4 py-3 rounded-2xl rounded-tl-none bg-slate-900 border border-slate-800 text-xs text-slate-300 flex items-center gap-3 shadow-xl">
        <span class="flex gap-1.5">
          <span class="w-2 h-2 rounded-full bg-burnt-orange animate-bounce"></span>
          <span class="w-2 h-2 rounded-full bg-burnt-orange animate-bounce [animation-delay:0.2s]"></span>
          <span class="w-2 h-2 rounded-full bg-burnt-orange animate-bounce [animation-delay:0.4s]"></span>
        </span>
        <span class="font-medium">Extracting criteria &amp; retrieving live offers...</span>
      </div>
    </div>
  `;
}

/**
 * Assistant Error Bubble with Retry option.
 */
export function renderAssistantError(errorMessage, retryQuery = "") {
  return `
    <div class="flex items-start gap-3">
      <div class="w-8 h-8 rounded-full bg-rose-500/20 border border-rose-500/40 flex items-center justify-center shrink-0 text-rose-400 text-xs font-bold shadow-md">
        !
      </div>
      <div class="px-4 py-3 rounded-2xl rounded-tl-none bg-rose-950/40 border border-rose-500/30 text-xs text-rose-300 space-y-2 shadow-xl max-w-[85%]">
        <p class="font-medium">Failed to process assistant query: ${escapeHtml(errorMessage)}</p>
        ${
          retryQuery
            ? `
          <button
            type="button"
            class="js-btn-retry-assistant px-3 py-1.5 rounded-lg bg-rose-900/60 hover:bg-rose-900 border border-rose-700/60 text-xs font-semibold text-rose-200 transition-colors"
            data-query="${escapeHtml(retryQuery)}"
          >
            ↻ Retry Query
          </button>
        `
            : ""
        }
      </div>
    </div>
  `;
}

/**
 * =========================================================================
 * PERSONAL SHOPPING ANALYTICS DASHBOARD COMPONENTS
 * =========================================================================
 */

/**
 * Format Year-Month (e.g. '2026-11' -> 'Nov 2026').
 */
function formatYearMonth(ymStr) {
  if (!ymStr) return "N/A";
  const parts = String(ymStr).split("-");
  if (parts.length === 2) {
    const year = parts[0];
    const monthIdx = parseInt(parts[1], 10) - 1;
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    if (monthIdx >= 0 && monthIdx < 12) {
      return `${months[monthIdx]} ${year}`;
    }
  }
  return ymStr;
}

/**
 * Loading skeleton placeholder for analytics dashboard.
 */
export function renderAnalyticsSkeleton() {
  return `
    <div class="space-y-6 animate-pulse">
      <!-- Metric Cards Skeleton -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        ${Array(4).fill(0).map(() => `
          <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
            <div class="h-3 bg-slate-800 rounded w-1/2"></div>
            <div class="h-7 bg-slate-800 rounded w-3/4"></div>
            <div class="h-3 bg-slate-800/60 rounded w-2/3"></div>
          </div>
        `).join('')}
      </div>

      <!-- Breakdown Grid Skeleton -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        ${Array(3).fill(0).map(() => `
          <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-4">
            <div class="h-4 bg-slate-800 rounded w-1/3"></div>
            <div class="space-y-3">
              <div class="h-12 bg-slate-950/60 rounded-xl"></div>
              <div class="h-12 bg-slate-950/60 rounded-xl"></div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

/**
 * Error notice with retry button for analytics dashboard.
 */
export function renderAnalyticsError(errorMessage) {
  return `
    <div class="p-6 rounded-2xl bg-rose-950/40 border border-rose-500/30 text-rose-300 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xl">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400 font-bold shrink-0">
          ⚠
        </div>
        <div>
          <div class="text-sm font-bold text-white">Unable to Load Personal Analytics</div>
          <div class="text-xs text-rose-300/90 mt-0.5">${escapeHtml(errorMessage || "An unexpected error occurred while fetching analytics.")}</div>
        </div>
      </div>
      <button
        type="button"
        id="btn-analytics-retry"
        class="px-4 py-2 rounded-xl bg-rose-900/60 hover:bg-rose-900 border border-rose-700/60 text-xs font-semibold text-rose-100 transition-colors shrink-0"
      >
        ↻ Retry
      </button>
    </div>
  `;
}

/**
 * Empty state rendered when the guest session has 0 completed orders.
 */
export function renderAnalyticsEmptyState(data = null) {
  const mandate = data?.active_mandate_utilization;
  const hasMandate = Boolean(mandate);

  return `
    <div class="space-y-6">
      ${
        hasMandate
          ? `
        <!-- Active Mandate Overview Banner even before first purchase -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800/80">
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
              <span class="text-xs font-bold uppercase tracking-wider text-white">Active Spending Mandate Guardrail</span>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">0.0% Utilized</span>
            </div>
            <a href="#mandate" class="text-xs font-semibold text-burnt-orange hover:underline">
              Adjust Budget Ceiling →
            </a>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4">
            <div>
              <span class="text-[11px] text-slate-400 font-medium">Total Budget Ceiling</span>
              <div class="text-xl font-extrabold text-white mt-0.5">${formatPrice(mandate.total_budget, mandate.currency)}</div>
              <span class="text-[10px] text-slate-500 font-mono">${escapeHtml(mandate.currency)} limit</span>
            </div>
            <div>
              <span class="text-[11px] text-slate-400 font-medium">Remaining Available</span>
              <div class="text-xl font-extrabold text-emerald-400 mt-0.5">${formatPrice(mandate.remaining_budget, mandate.currency)}</div>
              <span class="text-[10px] text-emerald-500 font-mono">100% available</span>
            </div>
            <div>
              <span class="text-[11px] text-slate-400 font-medium">Item Guardrails</span>
              <div class="text-xs font-bold text-white mt-1">Max Item: ${formatPrice(mandate.maximum_item_price, mandate.currency)}</div>
              <div class="text-[11px] text-slate-400">Max Qty: ${mandate.maximum_quantity} units/order</div>
            </div>
          </div>
        </div>
      `
          : ""
      }

      <!-- Welcoming Empty State Card -->
      <div class="p-8 sm:p-10 rounded-2xl bg-slate-900/60 border border-slate-800 text-center max-w-2xl mx-auto shadow-xl">
        <div class="w-14 h-14 rounded-2xl bg-gradient-to-br from-burnt-orange/20 to-amber-500/10 border border-burnt-orange/30 flex items-center justify-center text-burnt-orange mx-auto mb-4 shadow-lg shadow-burnt-orange/10">
          <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
        </div>
        <h3 class="text-base sm:text-lg font-heading font-extrabold text-white mb-2">No Completed Purchases Recorded</h3>
        <p class="text-xs sm:text-sm text-slate-400 max-w-md mx-auto mb-6 leading-relaxed">
          Your personal shopping intelligence will update here as you complete purchases through PayPal Sandbox checkout. Settled capture totals, monthly trends, category shares, and retailer distributions will track automatically.
        </p>
        <div class="flex flex-wrap items-center justify-center gap-3">
          <a
            href="#explore"
            class="px-5 py-2.5 text-xs font-semibold rounded-xl bg-burnt-orange hover:bg-burnt-orange/90 text-white transition-colors shadow-md shadow-burnt-orange/20"
          >
            Explore Product Catalog →
          </a>
          <a
            href="#mandate"
            class="px-5 py-2.5 text-xs font-semibold rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"
          >
            Configure Spending Mandate
          </a>
        </div>
      </div>
    </div>
  `;
}

/**
 * Main Personal Shopping Analytics Dashboard Renderer.
 * Strictly preserves multi-currency segregation and displays only backend-computed values.
 */
export function renderAnalyticsDashboard(data) {
  if (!data) return renderAnalyticsSkeleton();

  if (data.completed_order_count === 0) {
    return renderAnalyticsEmptyState(data);
  }

  const spendingByCurrency = data.total_spending_by_currency || [];
  const monthlyTrends = data.monthly_spending_trends || [];
  const categorySpending = data.category_spending || [];
  const retailerSpending = data.retailer_spending || [];
  const mandate = data.active_mandate_utilization;

  // 1. Total Spending Metric Card Content
  let totalSpendingHtml = "";
  if (spendingByCurrency.length === 0) {
    totalSpendingHtml = `
      <div class="text-2xl font-black text-white">$0.00</div>
      <div class="text-[11px] text-slate-500 mt-1">No settled purchases</div>
    `;
  } else if (spendingByCurrency.length === 1) {
    const c = spendingByCurrency[0];
    totalSpendingHtml = `
      <div class="text-2xl font-black text-white">${formatPrice(c.total_spent, c.currency)}</div>
      <div class="text-[11px] text-slate-400 mt-1">${escapeHtml(c.currency)} verified captures (${c.order_count} ${c.order_count === 1 ? 'order' : 'orders'})</div>
      ${
        c.discrepancy_explanation
          ? `<div class="text-[10px] text-amber-400/90 mt-1 truncate" title="${escapeHtml(c.discrepancy_explanation)}">ℹ ${escapeHtml(c.discrepancy_explanation)}</div>`
          : ""
      }
    `;
  } else {
    totalSpendingHtml = `
      <div class="space-y-1.5 max-h-24 overflow-y-auto pr-1">
        ${spendingByCurrency
          .map(
            (c) => `
          <div class="flex items-baseline justify-between gap-2 border-b border-slate-800/60 pb-1 last:border-b-0 last:pb-0">
            <span class="text-base font-black text-white">${formatPrice(c.total_spent, c.currency)}</span>
            <span class="text-[11px] font-mono font-bold text-burnt-orange">${escapeHtml(c.currency)} <span class="text-slate-400 font-normal">(${c.order_count})</span></span>
          </div>
        `
          )
          .join("")}
      </div>
      <div class="text-[10px] text-slate-500 mt-1.5 font-medium">Segregated by currency</div>
    `;
  }

  // 2. Average Order Value Metric Card Content
  let aovHtml = "";
  if (spendingByCurrency.length === 0) {
    aovHtml = `<div class="text-2xl font-black text-slate-500">N/A</div>`;
  } else if (spendingByCurrency.length === 1) {
    const c = spendingByCurrency[0];
    aovHtml = `
      <div class="text-2xl font-black text-emerald-400">${formatPrice(c.average_order_value, c.currency)}</div>
      <div class="text-[11px] text-slate-500 mt-1">Per completed order (${escapeHtml(c.currency)})</div>
    `;
  } else {
    aovHtml = `
      <div class="space-y-1.5 max-h-24 overflow-y-auto pr-1">
        ${spendingByCurrency
          .map(
            (c) => `
          <div class="flex items-baseline justify-between gap-2 border-b border-slate-800/60 pb-1 last:border-b-0 last:pb-0">
            <span class="text-base font-black text-emerald-400">${formatPrice(c.average_order_value, c.currency)}</span>
            <span class="text-[11px] font-mono font-bold text-slate-300">${escapeHtml(c.currency)} AOV</span>
          </div>
        `
          )
          .join("")}
      </div>
      <div class="text-[10px] text-slate-500 mt-1.5 font-medium">Computed per currency</div>
    `;
  }

  // 3. Mandate Utilization Metric Card Content
  let mandateHtml = "";
  if (mandate) {
    const pct = Math.min(100, Math.max(0, Number(mandate.utilization_percentage) || 0));
    const colorClass = pct > 90 ? "text-rose-400" : pct > 70 ? "text-amber-400" : "text-emerald-400";
    const barClass = pct > 90 ? "bg-rose-500" : pct > 70 ? "bg-amber-500" : "bg-emerald-500";
    const badgeClass =
      pct > 90
        ? "bg-rose-500/10 text-rose-300 border-rose-500/20"
        : pct > 70
        ? "bg-amber-500/10 text-amber-300 border-amber-500/20"
        : "bg-emerald-500/10 text-emerald-300 border-emerald-500/20";

    mandateHtml = `
      <div class="flex items-baseline justify-between">
        <span class="text-2xl font-black ${colorClass}">${pct.toFixed(1)}%</span>
        <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full border ${badgeClass}">Active Policy</span>
      </div>
      <div class="w-full bg-slate-800 rounded-full h-2 mt-2 overflow-hidden">
        <div class="${barClass} h-2 rounded-full transition-all duration-500" style="width: ${pct}%"></div>
      </div>
      <div class="text-[11px] text-slate-400 mt-2 flex justify-between">
        <span>Spent: <strong class="text-white">${formatPrice(mandate.spent_amount, mandate.currency)}</strong></span>
        <span>Left: <strong class="text-emerald-400">${formatPrice(mandate.remaining_budget, mandate.currency)}</strong></span>
      </div>
    `;
  } else {
    mandateHtml = `
      <div class="text-xl font-bold text-slate-300">No Active Policy</div>
      <div class="text-xs text-slate-500 mt-1 mb-2">No budget ceiling enforced for this session.</div>
      <a href="#mandate" class="inline-flex items-center gap-1 text-xs font-semibold text-burnt-orange hover:underline">
        Configure Mandate Guardrail →
      </a>
    `;
  }

  // Monthly trends scaling factor
  const maxMonthlyAmount = Math.max(...monthlyTrends.map((t) => Number(t.total_amount) || 0), 1);

  return `
    <div class="space-y-6">
      <!-- Top 4 Summary Metric Cards -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <!-- Metric Card 1: Total Spending -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between text-xs text-slate-400 font-medium mb-1.5">
              <span>Total Settled Spending</span>
              <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 font-mono text-slate-300">💳 Settled</span>
            </div>
            ${totalSpendingHtml}
          </div>
        </div>

        <!-- Metric Card 2: Completed Orders -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between text-xs text-slate-400 font-medium mb-1.5">
              <span>Completed Orders</span>
              <span class="text-[11px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">📦 Verified</span>
            </div>
            <div class="text-2xl font-black text-white">${data.completed_order_count}</div>
            <div class="text-[11px] text-slate-400 mt-1">${data.completed_order_count === 1 ? '1 completed order' : `${data.completed_order_count} completed orders`}</div>
          </div>
          <div class="text-[10px] text-slate-500 mt-2 font-mono">Status: COMPLETED</div>
        </div>

        <!-- Metric Card 3: Average Order Value -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between text-xs text-slate-400 font-medium mb-1.5">
              <span>Average Order Value</span>
              <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 font-mono text-slate-300">📊 AOV</span>
            </div>
            ${aovHtml}
          </div>
        </div>

        <!-- Metric Card 4: Mandate Budget Utilization -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between text-xs text-slate-400 font-medium mb-1.5">
              <span>Budget Utilization</span>
              <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 font-mono text-slate-300">🛡️ Policy</span>
            </div>
            ${mandateHtml}
          </div>
        </div>
      </div>

      <!-- 3-Column Breakdown Visualizations (Monthly, Category, Retailer) -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        <!-- Column 1: Monthly Trends -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg flex flex-col">
          <div class="flex items-center justify-between mb-4 pb-2 border-b border-slate-800/80">
            <h3 class="text-xs font-bold uppercase tracking-wider text-white flex items-center gap-1.5">
              <span>📅</span> Monthly Spending Trends
            </h3>
            <span class="text-[10px] text-slate-500 font-mono">${monthlyTrends.length} ${monthlyTrends.length === 1 ? 'month' : 'months'}</span>
          </div>

          <div class="space-y-2.5 flex-1 max-h-72 overflow-y-auto pr-1">
            ${
              monthlyTrends.length === 0
                ? `<div class="p-6 text-center text-xs text-slate-500">No monthly spending records.</div>`
                : monthlyTrends
                    .map((t) => {
                      const barWidth = Math.min(
                        100,
                        Math.max(8, Math.round(((Number(t.total_amount) || 0) / maxMonthlyAmount) * 100))
                      );
                      return `
                  <div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                    <div class="flex items-center justify-between text-xs">
                      <div class="font-bold text-white flex items-center gap-1.5">
                        <span>${formatYearMonth(t.month)}</span>
                        <span class="text-[10px] font-mono px-1.5 py-0.2 rounded bg-slate-800 text-slate-300">${escapeHtml(t.currency)}</span>
                      </div>
                      <div class="font-extrabold text-burnt-orange">${formatPrice(t.total_amount, t.currency)}</div>
                    </div>
                    <div class="w-full bg-slate-800/80 rounded-full h-1.5 overflow-hidden">
                      <div class="bg-burnt-orange h-1.5 rounded-full transition-all duration-300" style="width: ${barWidth}%"></div>
                    </div>
                    <div class="text-[10px] text-slate-400 flex justify-between">
                      <span>${t.order_count} ${t.order_count === 1 ? 'order' : 'orders'}</span>
                      <span class="text-slate-500 font-mono">Settled</span>
                    </div>
                  </div>
                `;
                    })
                    .join("")
            }
          </div>
        </div>

        <!-- Column 2: Category Breakdown -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg flex flex-col">
          <div class="flex items-center justify-between mb-4 pb-2 border-b border-slate-800/80">
            <h3 class="text-xs font-bold uppercase tracking-wider text-white flex items-center gap-1.5">
              <span>🏷️</span> Category Distribution
            </h3>
            <span class="text-[10px] text-slate-500 font-mono">${categorySpending.length} ${categorySpending.length === 1 ? 'category' : 'categories'}</span>
          </div>

          <div class="space-y-2.5 flex-1 max-h-72 overflow-y-auto pr-1">
            ${
              categorySpending.length === 0
                ? `<div class="p-6 text-center text-xs text-slate-500">No category breakdown available.</div>`
                : categorySpending
                    .map((cat) => `
                  <div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                    <div class="flex items-center justify-between text-xs">
                      <div class="font-bold text-white flex items-center gap-1.5">
                        <span class="truncate max-w-[130px] sm:max-w-[150px]" title="${escapeHtml(cat.category)}">${escapeHtml(cat.category)}</span>
                        <span class="text-[10px] font-mono px-1.5 py-0.2 rounded bg-slate-800 text-slate-300">${escapeHtml(cat.currency)}</span>
                      </div>
                      <div class="font-extrabold text-white">${formatPrice(cat.total_amount, cat.currency)}</div>
                    </div>
                    <div class="w-full bg-slate-800/80 rounded-full h-1.5 overflow-hidden">
                      <div class="bg-amber-500 h-1.5 rounded-full transition-all duration-300" style="width: ${Math.min(100, Math.max(3, cat.percentage_of_currency_total))}%"></div>
                    </div>
                    <div class="text-[10px] text-slate-400 flex justify-between">
                      <span>${cat.item_count} ${cat.item_count === 1 ? 'item' : 'items'}</span>
                      <span class="font-semibold text-amber-400 font-mono">${cat.percentage_of_currency_total.toFixed(1)}%</span>
                    </div>
                  </div>
                `)
                    .join("")
            }
          </div>
        </div>

        <!-- Column 3: Retailer Breakdown -->
        <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800 shadow-lg flex flex-col">
          <div class="flex items-center justify-between mb-4 pb-2 border-b border-slate-800/80">
            <h3 class="text-xs font-bold uppercase tracking-wider text-white flex items-center gap-1.5">
              <span>🏪</span> Merchant Breakdown
            </h3>
            <span class="text-[10px] text-slate-500 font-mono">${retailerSpending.length} ${retailerSpending.length === 1 ? 'merchant' : 'merchants'}</span>
          </div>

          <div class="space-y-2.5 flex-1 max-h-72 overflow-y-auto pr-1">
            ${
              retailerSpending.length === 0
                ? `<div class="p-6 text-center text-xs text-slate-500">No merchant breakdown available.</div>`
                : retailerSpending
                    .map((ret) => `
                  <div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                    <div class="flex items-center justify-between text-xs">
                      <div class="font-bold text-white flex items-center gap-1.5">
                        <span class="truncate max-w-[130px] sm:max-w-[150px]" title="${escapeHtml(ret.retailer_name)}">${escapeHtml(ret.retailer_name)}</span>
                        <span class="text-[10px] font-mono px-1.5 py-0.2 rounded bg-slate-800 text-slate-300">${escapeHtml(ret.currency)}</span>
                      </div>
                      <div class="font-extrabold text-white">${formatPrice(ret.total_amount, ret.currency)}</div>
                    </div>
                    <div class="w-full bg-slate-800/80 rounded-full h-1.5 overflow-hidden">
                      <div class="bg-emerald-500 h-1.5 rounded-full transition-all duration-300" style="width: ${Math.min(100, Math.max(3, ret.percentage_of_currency_total))}%"></div>
                    </div>
                    <div class="text-[10px] text-slate-400 flex justify-between">
                      <span>${ret.item_count} ${ret.item_count === 1 ? 'item' : 'items'}</span>
                      <span class="font-semibold text-emerald-400 font-mono">${ret.percentage_of_currency_total.toFixed(1)}%</span>
                    </div>
                  </div>
                `)
                    .join("")
            }
          </div>
        </div>
      </div>

      <!-- Active Mandate Constraints Footnote (if mandate active) -->
      ${
        mandate
          ? `
        <div class="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 rounded-lg bg-burnt-orange/10 border border-burnt-orange/20 flex items-center justify-center text-burnt-orange font-bold text-xs shrink-0">
              🛡️
            </div>
            <div>
              <div class="text-xs font-bold text-white flex items-center gap-2">
                <span>Active Spending Mandate Policy</span>
                <span class="text-[10px] font-mono px-1.5 py-0.2 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Enforced</span>
              </div>
              <div class="text-[11px] text-slate-400 mt-0.5">
                Ceiling: <strong class="text-slate-200">${formatPrice(mandate.total_budget, mandate.currency)}</strong> • Max Item: <strong class="text-slate-200">${formatPrice(mandate.maximum_item_price, mandate.currency)}</strong> • Max Qty: <strong class="text-slate-200">${mandate.maximum_quantity} units</strong> • Currency: <strong class="text-slate-200 font-mono">${escapeHtml(mandate.currency)}</strong>
              </div>
            </div>
          </div>
          <a
            href="#mandate"
            class="px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs font-semibold text-slate-300 hover:text-white transition-colors shrink-0"
          >
            Edit Policy →
          </a>
        </div>
      `
          : ""
      }
    </div>
  `;
}



// -------------------------------------------------------------------------
// SECTION: PRODUCT INTELLIGENCE & PRICE HISTORY (PHASE 3C)
// -------------------------------------------------------------------------

/**
 * Render loading skeleton for Product Intelligence & Price History section.
 */
export function renderProductIntelligenceSkeleton() {
  return `
    <div class="space-y-4 animate-pulse">
      <!-- 4 Summary Cards Skeleton -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        ${[1, 2, 3, 4].map(() => `
          <div class="p-3 rounded-xl bg-slate-900/60 border border-slate-800/80">
            <div class="h-3 w-16 bg-slate-800 rounded mb-2"></div>
            <div class="h-5 w-20 bg-slate-700 rounded mb-1"></div>
            <div class="h-2.5 w-24 bg-slate-800/60 rounded"></div>
          </div>
        `).join("")}
      </div>
      <!-- Chart Skeleton -->
      <div class="h-44 rounded-xl bg-slate-900/60 border border-slate-800/80 p-4 flex flex-col justify-between">
        <div class="h-3 w-40 bg-slate-800 rounded"></div>
        <div class="space-y-2">
          <div class="h-2 w-full bg-slate-800/40 rounded"></div>
          <div class="h-2 w-4/5 bg-slate-800/40 rounded"></div>
          <div class="h-2 w-3/4 bg-slate-800/40 rounded"></div>
        </div>
        <div class="flex justify-between">
          <div class="h-2.5 w-16 bg-slate-800/60 rounded"></div>
          <div class="h-2.5 w-16 bg-slate-800/60 rounded"></div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Render error state for Product Intelligence section.
 */
export function renderProductIntelligenceError(errorMessage, productId) {
  return `
    <div class="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300">
      <div class="flex items-start justify-between gap-3">
        <div class="flex items-start gap-2.5">
          <svg class="w-5 h-5 text-rose-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
          <div>
            <div class="text-xs font-bold text-white mb-0.5">Failed to load price intelligence</div>
            <div class="text-xs text-rose-300/90 leading-relaxed">${escapeHtml(errorMessage || "Network or provider error.")}</div>
          </div>
        </div>
        <button
          type="button"
          class="js-btn-retry-intel px-3 py-1 text-xs font-semibold rounded-lg bg-rose-500/20 hover:bg-rose-500/30 text-rose-200 border border-rose-500/30 transition-colors shrink-0"
          data-id="${escapeHtml(productId)}"
        >
          Retry
        </button>
      </div>
    </div>
  `;
}

/**
 * Render native SVG line chart for authentic chronological price snapshots.
 * Segregates currencies, distinguishes retailer histories with colored plots,
 * and displays an informative baseline state when only 1 snapshot exists.
 */
export function renderPriceHistoryChartSVG(snapshots, currency = "USD") {
  if (!snapshots || snapshots.length === 0) {
    return `
      <div class="h-44 rounded-xl bg-slate-950/40 border border-slate-800/80 flex flex-col items-center justify-center text-center p-4">
        <svg class="w-8 h-8 text-slate-600 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a2 2 0 01-2 2H6a2 2 0 01-2-2V4z"/></svg>
        <span class="text-xs font-semibold text-slate-400">No Historical Observations Yet</span>
        <span class="text-[11px] text-slate-500 mt-0.5">Historical price points will appear here as periodic snapshots are recorded.</span>
      </div>
    `;
  }

  // 1 snapshot: Baseline state with explanatory guidance
  if (snapshots.length === 1) {
    const s = snapshots[0];
    return `
      <div class="rounded-xl bg-slate-950/40 border border-slate-800/80 p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-burnt-orange"></span>
            <span class="text-xs font-bold text-white">${escapeHtml(s.retailer_name)}</span>
            <span class="text-[11px] text-slate-400 font-mono">${formatPrice(s.price, currency)}</span>
          </div>
          <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-burnt-orange/15 text-burnt-orange border border-burnt-orange/30">
            Baseline Observation
          </span>
        </div>
        <div class="h-32 rounded-lg bg-slate-900/60 border border-slate-800/60 flex flex-col items-center justify-center p-4 text-center">
          <div class="text-base font-extrabold text-white mb-1">${formatPrice(s.price, currency)}</div>
          <div class="text-xs text-slate-400">First recorded on <strong class="text-slate-300">${formatDate(s.recorded_at)}</strong></div>
          <p class="text-[11px] text-slate-500 mt-2 max-w-md">
            Only 1 authentic snapshot recorded so far. A chronological trend line will activate automatically when subsequent price observations are captured.
          </p>
        </div>
      </div>
    `;
  }

  // Multi-snapshot chronological chart
  const W = 620;
  const H = 220;
  const pad = { top: 25, right: 30, bottom: 35, left: 65 };
  const pw = W - pad.left - pad.right;
  const ph = H - pad.top - pad.bottom;

  const prices = snapshots.map((s) => s.price);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const priceMargin = maxP === minP ? maxP * 0.05 : (maxP - minP) * 0.12;
  const yMin = Math.max(0, minP - priceMargin);
  const yMax = maxP + priceMargin;

  const times = snapshots.map((s) => new Date(s.recorded_at).getTime());
  const tMin = Math.min(...times);
  const tMax = Math.max(...times);
  const timeSpan = tMax === tMin ? 86400000 : (tMax - tMin);

  // Group by retailer
  const retailers = [...new Set(snapshots.map((s) => s.retailer_name))];
  const retailerColors = [
    "#f97316", // Burnt Orange
    "#38bdf8", // Sky Blue
    "#34d399", // Emerald
    "#a855f7", // Purple
    "#fbbf24", // Amber
    "#ec4899", // Pink
  ];

  const retailerColorMap = {};
  retailers.forEach((r, idx) => {
    retailerColorMap[r] = retailerColors[idx % retailerColors.length];
  });

  // Calculate coordinates
  const getX = (t) => pad.left + ((t - tMin) / timeSpan) * pw;
  const getY = (p) => pad.top + ph - ((p - yMin) / (yMax - yMin)) * ph;

  // Gridlines & Y-axis labels (4 levels)
  const yTicks = 4;
  let gridLinesSvg = "";
  for (let i = 0; i <= yTicks; i++) {
    const frac = i / yTicks;
    const yVal = yMin + frac * (yMax - yMin);
    const yPos = pad.top + ph - frac * ph;
    gridLinesSvg += `
      <line x1="${pad.left}" y1="${yPos}" x2="${pad.left + pw}" y2="${yPos}" stroke="#1e293b" stroke-dasharray="3 3" stroke-width="1" />
      <text x="${pad.left - 8}" y="${yPos + 3}" text-anchor="end" fill="#64748b" font-size="9" font-family="monospace">${formatPrice(yVal, currency)}</text>
    `;
  }

  // X-axis date labels (earliest and latest)
  let dateLabelsSvg = `
    <text x="${pad.left}" y="${H - 12}" text-anchor="start" fill="#64748b" font-size="9" font-family="monospace">${formatDate(new Date(tMin).toISOString()).split(",")[0]}</text>
    <text x="${pad.left + pw}" y="${H - 12}" text-anchor="end" fill="#64748b" font-size="9" font-family="monospace">${formatDate(new Date(tMax).toISOString()).split(",")[0]}</text>
  `;

  // Draw lines and circles per retailer
  let pathsSvg = "";
  let pointsSvg = "";

  retailers.forEach((ret) => {
    const retSnaps = snapshots.filter((s) => s.retailer_name === ret);
    const color = retailerColorMap[ret];

    if (retSnaps.length > 1) {
      let d = "";
      retSnaps.forEach((s, idx) => {
        const x = getX(new Date(s.recorded_at).getTime());
        const y = getY(s.price);
        d += `${idx === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)} `;
      });
      pathsSvg += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />`;
    }

    retSnaps.forEach((s) => {
      const x = getX(new Date(s.recorded_at).getTime());
      const y = getY(s.price);
      pointsSvg += `
        <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.5" fill="${color}" stroke="#090d16" stroke-width="2">
          <title>${escapeHtml(s.retailer_name)}: ${formatPrice(s.price, currency)} (${formatDate(s.recorded_at)})</title>
        </circle>
      `;
    });
  });

  // Legend
  const legendHtml = retailers.map((ret) => `
    <div class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-slate-900 border border-slate-800 text-[11px] font-medium text-slate-300">
      <span class="w-2 h-2 rounded-full" style="background-color: ${retailerColorMap[ret]}"></span>
      <span>${escapeHtml(ret)}</span>
    </div>
  `).join("");

  return `
    <div class="rounded-xl bg-slate-950/40 border border-slate-800/80 p-4">
      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-semibold text-slate-400">Authentic Price History (${snapshots.length} Observations)</span>
        <div class="flex flex-wrap gap-2">${legendHtml}</div>
      </div>
      <div class="overflow-hidden">
        <svg viewBox="0 0 ${W} ${H}" class="w-full h-auto max-h-56 select-none" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Chronological price history line chart">
          ${gridLinesSvg}
          ${pathsSvg}
          ${pointsSvg}
          ${dateLabelsSvg}
        </svg>
      </div>
    </div>
  `;
}

/**
 * Render complete interactive Product Intelligence UI inside product modal.
 */
export function renderProductIntelligenceContent(intel, activeCurrency = null) {
  if (!intel) {
    return `<div class="p-4 text-xs text-slate-500 text-center">No intelligence data available.</div>`;
  }

  // 1. Identify Currencies
  const availableCurrencies = [...new Set([
    ...intel.best_current_prices.map((p) => p.currency),
    ...intel.historical_lowest_prices.map((p) => p.currency),
    ...intel.price_changes.map((c) => c.currency),
    ...intel.retailer_comparisons.map((c) => c.currency),
    ...intel.price_history.map((s) => s.currency),
    ...intel.price_stability.map((s) => s.currency),
  ])].filter(Boolean);

  const curr = (activeCurrency && availableCurrencies.includes(activeCurrency))
    ? activeCurrency
    : (availableCurrencies[0] || "USD");

  // Currency Selector (if multiple currencies exist)
  const currencyTabs = availableCurrencies.length > 1 ? `
    <div class="flex items-center gap-2 mb-4 pb-3 border-b border-slate-800/80">
      <span class="text-xs text-slate-400 font-semibold">Select Currency:</span>
      <div class="flex items-center gap-1.5 p-1 rounded-xl bg-slate-950/80 border border-slate-800">
        ${availableCurrencies.map((c) => `
          <button
            type="button"
            class="js-intel-currency-tab px-3 py-1 text-xs font-bold rounded-lg transition-all ${c === curr ? 'bg-burnt-orange text-white shadow-sm' : 'text-slate-400 hover:text-white'}"
            data-currency="${c}"
          >
            ${c}
          </button>
        `).join("")}
      </div>
    </div>
  ` : "";

  // 2. Metrics for the active currency
  const bestPrice = intel.best_current_prices.find((p) => p.currency === curr);
  const lowPrice = intel.historical_lowest_prices.find((p) => p.currency === curr);
  const highPrice = intel.historical_highest_prices.find((p) => p.currency === curr);
  const primaryChange = intel.price_changes.find((c) => c.currency === curr);
  const comparison = intel.retailer_comparisons.find((c) => c.currency === curr);
  const stability = intel.price_stability.find((s) => s.currency === curr);
  const snapshots = intel.price_history.filter((s) => (s.currency || "USD").toUpperCase() === curr);

  // Price Movement representation
  let movementHtml = "";
  if (primaryChange) {
    if (primaryChange.status === "DECREASED") {
      movementHtml = `
        <div class="text-lg font-extrabold text-emerald-400">${primaryChange.percentage_change}%</div>
        <div class="text-[10px] text-slate-400 mt-0.5 truncate">${formatPrice(primaryChange.previous_price, curr)} → ${formatPrice(primaryChange.current_price, curr)}</div>
        <span class="mt-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
          📉 Price Dropped (${formatPrice(primaryChange.absolute_change, curr)})
        </span>
      `;
    } else if (primaryChange.status === "INCREASED") {
      movementHtml = `
        <div class="text-lg font-extrabold text-rose-400">+${primaryChange.percentage_change}%</div>
        <div class="text-[10px] text-slate-400 mt-0.5 truncate">${formatPrice(primaryChange.previous_price, curr)} → ${formatPrice(primaryChange.current_price, curr)}</div>
        <span class="mt-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
          📈 Price Rose (+${formatPrice(primaryChange.absolute_change, curr)})
        </span>
      `;
    } else if (primaryChange.status === "UNCHANGED") {
      movementHtml = `
        <div class="text-lg font-extrabold text-slate-300">0.0%</div>
        <div class="text-[10px] text-slate-400 mt-0.5">Holding at ${formatPrice(primaryChange.current_price, curr)}</div>
        <span class="mt-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-400 border border-slate-700">
          Unchanged
        </span>
      `;
    } else {
      movementHtml = `
        <div class="text-lg font-extrabold text-slate-400">Baseline</div>
        <div class="text-[10px] text-slate-400 mt-0.5">1 observation captured</div>
        <span class="mt-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
          Collecting Trend Data
        </span>
      `;
    }
  } else {
    movementHtml = `
      <div class="text-lg font-extrabold text-slate-500">N/A</div>
      <div class="text-[10px] text-slate-500 mt-0.5">No historical observations</div>
      <span class="mt-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-800/40 text-slate-500 border border-slate-800">
        No History
      </span>
    `;
  }

  // 3. Price Summary 4-card Grid
  const summaryCardsHtml = `
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
      <!-- Best Current Price -->
      <div class="p-3.5 rounded-xl bg-slate-900/70 border border-slate-800/80 flex flex-col justify-between">
        <div>
          <span class="text-[10px] font-bold uppercase tracking-wider text-slate-400">Current Best</span>
          <div class="text-lg font-black text-white mt-0.5">
            ${bestPrice ? formatPrice(bestPrice.price, curr) : "N/A"}
          </div>
        </div>
        <div class="mt-2">
          <div class="text-[10px] text-slate-400 truncate">${bestPrice ? escapeHtml(bestPrice.retailer_name) : "No active offers"}</div>
          <span class="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold ${bestPrice ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-slate-800 text-slate-500 border border-slate-700'}">
            ${bestPrice ? escapeHtml(bestPrice.availability.replace(/_/g, " ")) : "Unavailable"}
          </span>
        </div>
      </div>

      <!-- Historical Lowest -->
      <div class="p-3.5 rounded-xl bg-slate-900/70 border border-slate-800/80 flex flex-col justify-between">
        <div>
          <span class="text-[10px] font-bold uppercase tracking-wider text-slate-400">All-Time Low</span>
          <div class="text-lg font-black text-emerald-400 mt-0.5">
            ${lowPrice ? formatPrice(lowPrice.price, curr) : "N/A"}
          </div>
        </div>
        <div class="mt-2">
          <div class="text-[10px] text-slate-400 truncate">${lowPrice ? escapeHtml(lowPrice.retailer_name) : "No record"}</div>
          <span class="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            ${lowPrice ? formatDate(lowPrice.recorded_at).split(",")[0] : "N/A"}
          </span>
        </div>
      </div>

      <!-- Historical Highest -->
      <div class="p-3.5 rounded-xl bg-slate-900/70 border border-slate-800/80 flex flex-col justify-between">
        <div>
          <span class="text-[10px] font-bold uppercase tracking-wider text-slate-400">All-Time High</span>
          <div class="text-lg font-black text-rose-400 mt-0.5">
            ${highPrice ? formatPrice(highPrice.price, curr) : "N/A"}
          </div>
        </div>
        <div class="mt-2">
          <div class="text-[10px] text-slate-400 truncate">${highPrice ? escapeHtml(highPrice.retailer_name) : "No record"}</div>
          <span class="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
            ${highPrice ? formatDate(highPrice.recorded_at).split(",")[0] : "N/A"}
          </span>
        </div>
      </div>

      <!-- Price Movement -->
      <div class="p-3.5 rounded-xl bg-slate-900/70 border border-slate-800/80 flex flex-col justify-between">
        <div>
          <span class="text-[10px] font-bold uppercase tracking-wider text-slate-400">Price Movement</span>
          ${movementHtml}
        </div>
      </div>
    </div>
  `;

  // 4. SVG Price History Chart
  const chartHtml = renderPriceHistoryChartSVG(snapshots, curr);

  // 5. Retailer Comparison & Price Stability Two-Column Grid
  const compHtml = comparison ? `
    <div class="p-4 rounded-xl bg-slate-950/40 border border-slate-800/80 flex flex-col justify-between">
      <div>
        <div class="flex items-center justify-between mb-2">
          <h4 class="text-xs font-bold text-white uppercase tracking-wider">Active Retailer Spread</h4>
          <span class="text-[11px] text-slate-400 font-medium">${comparison.available_offers_count} available</span>
        </div>

        ${
          comparison.available_offers_count > 1
            ? (comparison.price_difference !== null && comparison.price_difference > 0
                ? `
                  <div class="flex items-baseline gap-2 mb-1.5">
                    <span class="text-xl font-black text-white">${formatPrice(comparison.price_difference, curr)}</span>
                    <span class="text-xs font-semibold text-emerald-400">Spread</span>
                  </div>
                  <p class="text-xs text-slate-300 leading-relaxed mb-3">
                    Save up to <strong class="text-emerald-400 font-bold">${comparison.potential_savings_percentage}%</strong> by selecting
                    <strong class="text-white">${escapeHtml(comparison.cheapest_retailer)}</strong> (${formatPrice(comparison.cheapest_price, curr)})
                    over <strong class="text-slate-400">${escapeHtml(comparison.highest_retailer)}</strong> (${formatPrice(comparison.highest_price, curr)}).
                  </p>
                `
                : `
                  <div class="flex items-baseline gap-2 mb-1.5">
                    <span class="text-base font-bold text-white">Identical Pricing Across Stores</span>
                    <span class="text-xs font-semibold text-slate-400">($0.00 Spread)</span>
                  </div>
                  <p class="text-xs text-slate-300 leading-relaxed mb-3">
                    All <strong class="text-white">${comparison.available_offers_count}</strong> in-stock merchants currently offer this product at the identical price of <strong class="text-emerald-400 font-bold">${formatPrice(comparison.cheapest_price, curr)}</strong>.
                  </p>
                `
              )
            : `
              <div class="text-xs text-slate-400 py-2 mb-2">
                ${comparison.available_offers_count === 1 ? "Single in-stock merchant offer — no alternative retailer spread currently." : "No active in-stock merchant offers for cross-comparison."}
              </div>
            `
        }
      </div>

      <!-- Retailers stock breakdown -->
      <div class="pt-3 border-t border-slate-800/60 text-[11px] space-y-1">
        ${
          comparison.available_retailers.length > 0
            ? `<div class="flex items-center gap-1.5 flex-wrap"><span class="text-slate-500">In Stock:</span> ${comparison.available_retailers.map((r) => `<span class="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px] font-medium">${escapeHtml(r)}</span>`).join("")}</div>`
            : ""
        }
        ${
          comparison.unavailable_retailers.length > 0
            ? `<div class="flex items-center gap-1.5 flex-wrap mt-1"><span class="text-slate-500">Out of Stock:</span> ${comparison.unavailable_retailers.map((r) => `<span class="px-2 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 text-[10px] font-medium">${escapeHtml(r)}</span>`).join("")}</div>`
            : ""
        }
      </div>
    </div>
  ` : "";

  // Stability styles
  const stabilityBadgeClass = {
    STABLE: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    MODERATE: "bg-sky-500/15 text-sky-400 border-sky-500/30",
    VOLATILE: "bg-rose-500/15 text-rose-400 border-rose-500/30",
    INSUFFICIENT_DATA: "bg-slate-800 text-slate-400 border-slate-700",
  }[stability?.status || "INSUFFICIENT_DATA"];

  const stabilityIcon = {
    STABLE: "🛡️",
    MODERATE: "⚖️",
    VOLATILE: "⚡",
    INSUFFICIENT_DATA: "📊",
  }[stability?.status || "INSUFFICIENT_DATA"];

  const stabilityHtml = stability ? `
    <div class="p-4 rounded-xl bg-slate-950/40 border border-slate-800/80 flex flex-col justify-between">
      <div>
        <div class="flex items-center justify-between mb-2">
          <h4 class="text-xs font-bold text-white uppercase tracking-wider">Price Stability</h4>
          <span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold border ${stabilityBadgeClass}">
            <span>${stabilityIcon}</span>
            <span>${escapeHtml(stability.status.replace(/_/g, " "))}</span>
          </span>
        </div>

        <div class="grid grid-cols-2 gap-2 my-2 py-2 border-y border-slate-800/60 text-xs">
          <div>
            <span class="text-[10px] text-slate-500 block uppercase">Recorded Range</span>
            <span class="text-white font-bold">${stability.price_range !== null ? formatPrice(stability.price_range, curr) : "N/A"}</span>
          </div>
          <div>
            <span class="text-[10px] text-slate-500 block uppercase">Volatility (CV)</span>
            <span class="text-white font-bold">${stability.volatility_score !== null ? `${stability.volatility_score}%` : "Insufficient Data"}</span>
          </div>
        </div>

        <p class="text-xs text-slate-300 leading-relaxed mt-2">
          ${escapeHtml(stability.explanation)}
        </p>
      </div>

      <div class="pt-3 border-t border-slate-800/60 text-[11px] text-slate-500">
        Based on <strong class="text-slate-300">${stability.observation_count}</strong> authentic historical snapshot${stability.observation_count === 1 ? "" : "s"}.
      </div>
    </div>
  ` : "";

  // 6. Tracking Metadata Footer
  const lastSnap = snapshots.length > 0 ? snapshots[snapshots.length - 1] : null;
  const trackingFooterHtml = `
    <div class="mt-4 p-3 rounded-xl bg-slate-900/60 border border-slate-800 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-400">
      <div class="flex items-center gap-3">
        <span>Tracking Started: <strong class="text-white">${intel.tracking_start_date ? formatDate(intel.tracking_start_date) : "First Ingestion"}</strong></span>
        <span>•</span>
        <span>Verified Observations: <strong class="text-white">${intel.total_genuine_observations}</strong></span>
        ${lastSnap ? `<span>•</span><span>Last Recorded: <strong class="text-white">${formatDate(lastSnap.recorded_at)}</strong></span>` : ""}
      </div>
      <div>
        ${
          intel.is_tracking_active
            ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Active 6h Cooldown</span>`
            : `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-400 border border-slate-700">Passive Ingestion</span>`
        }
      </div>
    </div>
  `;

  return `
    <div class="space-y-4">
      ${currencyTabs}
      ${summaryCardsHtml}
      ${chartHtml}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        ${compHtml}
        ${stabilityHtml}
      </div>
      ${trackingFooterHtml}
    </div>
  `;
}


/**
 * Render explicit User-Controlled Budget Override confirmation modal dialogue.
 */
export function renderBudgetOverrideModal(data) {
  const { productName, price, currency, preferredBudget, excessAmount } = data;
  return `
    <div class="p-6 max-w-lg mx-auto bg-slate-900 border border-amber-500/40 rounded-2xl shadow-2xl text-left">
      <div class="flex items-center gap-3 pb-4 border-b border-slate-800">
        <div class="w-10 h-10 rounded-xl bg-amber-500/15 border border-amber-500/30 flex items-center justify-center text-amber-400 text-xl font-bold shrink-0">
          ⚠ï¸
        </div>
        <div>
          <h3 class="text-base font-bold text-white">Confirm Budget Override</h3>
          <p class="text-xs text-slate-400">This item exceeds your preferred spending guardrail</p>
        </div>
      </div>
      <div class="py-4 space-y-3">
        <div class="p-3 rounded-xl bg-slate-950/70 border border-slate-800 text-xs space-y-1.5">
          <div class="flex justify-between text-slate-400">
            <span>Product:</span>
            <strong class="text-white">${escapeHtml(productName || "Product")}</strong>
          </div>
          <div class="flex justify-between text-slate-400">
            <span>Item Price:</span>
            <strong class="text-white font-mono">${formatPrice(price, currency)}</strong>
          </div>
          <div class="flex justify-between text-slate-400">
            <span>Preferred Budget:</span>
            <strong class="text-slate-300 font-mono">${formatPrice(preferredBudget, currency)}</strong>
          </div>
          <div class="flex justify-between text-amber-400 pt-1 border-t border-slate-800">
            <span class="font-semibold">Budget Exceeded By:</span>
            <strong class="font-bold font-mono">+${formatPrice(excessAmount, currency)}</strong>
          </div>
        </div>
        <p class="text-xs text-slate-300 leading-relaxed">
          Your spending mandate is a personal preference. You may choose to knowingly exceed it for this purchase. Your original mandate budget will remain untouched.
        </p>
      </div>
      <div class="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
        <button
          type="button"
          class="js-btn-override-cancel px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 text-slate-300 hover:bg-slate-700 transition-colors"
        >
          Cancel
        </button>
        <button
          type="button"
          class="js-btn-override-confirm px-4 py-2 text-xs font-semibold rounded-lg bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold transition-colors"
        >
          Continue Anyway / Confirm Override
        </button>
      </div>
    </div>
  `;
}


/**
 * Render explicit PayPal Sandbox Approval and Settlement modal dialog.
 * Guides the buyer through approving on PayPal and explicitly confirming settlement capture.
 */
export function renderPayPalApprovalModal({ order, productName, retailerName, price, currency }) {
  const displayPrice = formatPrice(price || order.total_amount, currency || order.currency);
  const approvalUrl = order.approval_url || "#";

  return `
    <div class="p-6 max-w-lg mx-auto bg-slate-900 border border-emerald-500/40 rounded-2xl shadow-2xl text-left" id="paypal-approval-modal-box">
      <!-- Header -->
      <div class="flex items-center justify-between pb-4 border-b border-slate-800">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0">
            <svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
              <path d="M7.076 21.337H2.47a.641.641 0 0 1-.633-.74L4.944 3.72a.76.76 0 0 1 .752-.64h6.14c3.488 0 5.86 1.48 5.405 5.034-.33 2.585-2.072 4.183-4.52 4.673l-.42.062a.64.64 0 0 0-.53.535l-.83 4.275a.64.64 0 0 1-.63.518H7.433l-.357 3.155z"/>
            </svg>
          </div>
          <div>
            <h3 class="text-base font-bold text-white">PayPal Sandbox Approval</h3>
            <p class="text-xs text-slate-400">Order Reference: <span class="font-mono text-emerald-400 font-semibold">${escapeHtml(order.internal_order_number)}</span></p>
          </div>
        </div>
        <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
          Sandbox Demo
        </span>
      </div>

      <!-- Order Summary Card -->
      <div class="py-4 space-y-4">
        <div class="p-3.5 rounded-xl bg-slate-950/70 border border-slate-800 text-xs space-y-2">
          <div class="flex justify-between text-slate-400">
            <span>Item:</span>
            <strong class="text-white">${escapeHtml(productName || "Product")}</strong>
          </div>
          <div class="flex justify-between text-slate-400">
            <span>Merchant:</span>
            <strong class="text-slate-300">${escapeHtml(retailerName || "Simulated Demo Store")}</strong>
          </div>
          <div class="flex justify-between text-slate-400 pt-2 border-t border-slate-800/80">
            <span class="text-sm font-semibold text-white">Total Amount Due:</span>
            <strong class="text-base font-extrabold text-white font-mono">${displayPrice}</strong>
          </div>
        </div>

        <!-- Approval Steps Guidance -->
        <div class="p-3.5 rounded-xl bg-slate-800/40 border border-slate-700/60 text-xs space-y-2">
          <div class="font-bold text-slate-200 flex items-center gap-2">
            <span class="w-5 h-5 rounded-full bg-burnt-orange text-white text-[11px] flex items-center justify-center font-bold">1</span>
            <span>Approve Payment in PayPal</span>
          </div>
          <p class="text-[11px] text-slate-400 pl-7 leading-relaxed">
            Click the button below to launch the PayPal Sandbox approval window. Sign in with sandbox credentials or approve the test transaction.
          </p>
          <div class="pl-7 pt-1">
            <a
              href="${escapeHtml(approvalUrl)}"
              target="paypal_approval_window"
              class="js-btn-open-paypal-window inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-slate-700 hover:bg-slate-600 text-white transition-colors border border-slate-600 shadow-sm"
            >
              <span>Launch PayPal Window ↗</span>
            </a>
          </div>

          <div class="font-bold text-slate-200 flex items-center gap-2 pt-2 border-t border-slate-700/40">
            <span class="w-5 h-5 rounded-full bg-burnt-orange text-white text-[11px] flex items-center justify-center font-bold">2</span>
            <span>Confirm &amp; Capture Settlement</span>
          </div>
          <p class="text-[11px] text-slate-400 pl-7 leading-relaxed">
            Once approved on PayPal, click Confirm &amp; Capture Settlement to finalize the purchase.
          </p>
        </div>
      </div>

      <!-- Actions -->
      <div class="flex items-center justify-between pt-3 border-t border-slate-800">
        <button
          type="button"
          class="js-btn-cancel-checkout px-4 py-2 text-xs font-semibold rounded-lg bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
          data-order-id="${order.id}"
        >
          Cancel Order
        </button>
        <button
          type="button"
          class="js-btn-confirm-capture inline-flex items-center gap-1.5 px-4 py-2 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors shadow-sm focus:ring-2 focus:ring-emerald-400 focus:outline-none"
          data-order-id="${order.id}"
          data-session-id="${order.session_id || ""}"
        >
          Confirm &amp; Capture Settlement
        </button>
      </div>
    </div>
  `;
}


/**
 * Render the official BUYWISE AI stylized orange "B" emblem SVG.
 * Clean, high-precision vector graphic with rich amber-orange gradient and tech cuts.
 */
export function renderBuywiseLogo(size = 36, className = "") {
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg" class="${className} inline-block shrink-0">
      <defs>
        <linearGradient id="bw-orange-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#F59E0B" />
          <stop offset="45%" stop-color="#E88A38" />
          <stop offset="100%" stop-color="#D97706" />
        </linearGradient>
        <linearGradient id="bw-inner-glow" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.35" />
          <stop offset="100%" stop-color="#FFFFFF" stop-opacity="0" />
        </linearGradient>
        <filter id="bw-shadow" x="-10%" y="-10%" width="130%" height="130%">
          <feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#E88A38" flood-opacity="0.35"/>
        </filter>
      </defs>
      <!-- Squircle Base -->
      <rect x="6" y="6" width="88" height="88" rx="24" fill="url(#bw-orange-grad)" filter="url(#bw-shadow)"/>
      <rect x="7" y="7" width="86" height="86" rx="23" stroke="url(#bw-inner-glow)" stroke-width="2" fill="none"/>
      <!-- Stylized Geometric B Monogram (Two bold dynamic loops + vertical spine with precision tech cut) -->
      <path d="M30 26 H54 C63 26 70 31.5 70 39.5 C70 45 66 48.5 61 50 C67.5 51.5 72 56 72 62.5 C72 71.5 64 77 54 77 H30 V26 Z" fill="white" />
      <!-- Upper negative space cutout -->
      <path d="M42 36 H52 C56 36 59 38 59 41.5 C59 45 56 47 52 47 H42 V36 Z" fill="#E88A38" />
      <!-- Lower negative space cutout -->
      <path d="M42 55 H53 C57.5 55 61 57.5 61 61.5 C61 65.5 57.5 68 53 68 H42 V55 Z" fill="#D97706" />
      <!-- Tech AI node cut / smart accent -->
      <circle cx="70" cy="50" r="3" fill="#F8F6F0" />
    </svg>
  `;
}


/**
 * Render Split-Screen Login Page.
 */
export function renderLoginPage({ error = null, email = "", pendingCheckout = null } = {}) {
  const pendingCheckoutAlert = pendingCheckout
    ? `
      <div id="login-checkout-alert" class="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs flex items-start gap-3 mb-4">
        <div class="text-base shrink-0">🛒</div>
        <div>
          <span class="font-bold text-white block">Order in Progress</span>
          <span class="text-amber-200/90 leading-relaxed">
            Please sign in or create an account to complete your purchase of
            <strong class="text-white">${escapeHtml(pendingCheckout.productName)}</strong>
            (${formatPrice(pendingCheckout.price, pendingCheckout.currency)}).
          </span>
        </div>
      </div>
    `
    : "";
  const errorAlert = error
    ? `
      <div id="login-error-alert" class="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs flex items-start gap-2.5 mb-4 animate-shake">
        <svg class="w-4 h-4 text-rose-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span class="leading-relaxed">${escapeHtml(error)}</span>
      </div>
    `
    : "";

  return `
    <div class="max-w-6xl mx-auto py-6 lg:py-10">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-stretch">
        
        <!-- Left Side: Brand Showcase & Value Proposition -->
        <div class="lg:col-span-5 rounded-3xl border border-slate-800 bg-gradient-to-br from-slate-900 via-slate-950 to-slate-900 p-8 sm:p-10 flex flex-col justify-between relative overflow-hidden shadow-2xl">
          <div class="absolute -top-24 -left-24 w-72 h-72 bg-burnt-orange/15 rounded-full blur-3xl pointer-events-none"></div>
          <div class="absolute -bottom-20 -right-20 w-60 h-60 bg-amber-600/10 rounded-full blur-2xl pointer-events-none"></div>

          <div>
            <div class="flex items-center gap-3 mb-8">
              ${renderBuywiseLogo(56, "shadow-lg shadow-burnt-orange/30")}
              <div>
                <span class="font-heading font-black text-xl text-white tracking-tight flex items-center gap-1.5">
                  BUYWISE <span class="text-xs px-2 py-0.5 rounded bg-burnt-orange/20 text-burnt-orange font-mono font-bold">AI</span>
                </span>
                <p class="text-xs text-slate-400">Intelligent Shopping &amp; Responsible Checkout</p>
              </div>
            </div>

            <h2 class="text-2xl sm:text-3xl font-heading font-extrabold text-white tracking-tight leading-snug mb-4">
              Discover smarter.<br/>
              Compare deeper.<br/>
              <span class="text-transparent bg-clip-text bg-gradient-to-r from-burnt-orange to-amber-400">Spend responsibly.</span>
            </h2>
            <p class="text-xs text-slate-400 leading-relaxed mb-8">
              Sign in to unlock personalized purchase intelligence, active spending mandate policies, and authoritative PayPal settlement records.
            </p>

            <!-- Feature Badges -->
            <div class="space-y-3.5">
              <div class="flex items-start gap-3 p-3 rounded-2xl bg-slate-950/60 border border-slate-800/80">
                <div class="w-8 h-8 rounded-xl bg-burnt-orange/15 text-burnt-orange flex items-center justify-center shrink-0 text-sm font-bold mt-0.5">✦</div>
                <div>
                  <h4 class="text-xs font-bold text-white">AI Price Verification</h4>
                  <p class="text-[11px] text-slate-400 leading-relaxed">Multi-retailer price anomaly detection and fair-value scoring.</p>
                </div>
              </div>

              <div class="flex items-start gap-3 p-3 rounded-2xl bg-slate-950/60 border border-slate-800/80">
                <div class="w-8 h-8 rounded-xl bg-emerald-500/15 text-emerald-400 flex items-center justify-center shrink-0 text-sm font-bold mt-0.5">🛡️</div>
                <div>
                  <h4 class="text-xs font-bold text-white">Deterministic Mandates</h4>
                  <p class="text-[11px] text-slate-400 leading-relaxed">Hard spending caps, item price ceilings, and user override control.</p>
                </div>
              </div>

              <div class="flex items-start gap-3 p-3 rounded-2xl bg-slate-950/60 border border-slate-800/80">
                <div class="w-8 h-8 rounded-xl bg-amber-500/15 text-amber-400 flex items-center justify-center shrink-0 text-sm font-bold mt-0.5">🔒</div>
                <div>
                  <h4 class="text-xs font-bold text-white">PayPal Sandbox Settlement</h4>
                  <p class="text-[11px] text-slate-400 leading-relaxed">Strict approval-before-capture sequence with verifiable settlement proofs.</p>
                </div>
              </div>
            </div>
          </div>

          <!-- Bottom Footer Quote -->
          <div class="mt-8 pt-6 border-t border-slate-800/80 text-[11px] text-slate-500 flex items-center justify-between">
            <span>PayPal AI Hackathon 2026</span>
            <span class="text-burnt-orange/80 font-mono">v1.2 Secure</span>
          </div>
        </div>

        <!-- Right Side: Login Form Card -->
        <div class="lg:col-span-7 rounded-3xl border border-slate-800 bg-slate-900/80 p-8 sm:p-12 shadow-2xl backdrop-blur-md flex flex-col justify-center">
          <div class="max-w-md w-full mx-auto">
            <div class="mb-6">
              <span class="text-xs font-bold uppercase tracking-wider text-burnt-orange font-mono">Authentication</span>
              <h1 class="text-2xl sm:text-3xl font-heading font-extrabold text-white tracking-tight mt-1">Welcome back</h1>
              <p class="text-xs sm:text-sm text-slate-400 mt-1">Enter your credentials to access your account.</p>
            </div>

            ${errorAlert}
            ${pendingCheckoutAlert}

            <form id="login-form" class="space-y-4" novalidate>
              <div>
                <label for="login-email" class="block text-xs font-semibold text-slate-300 mb-1.5">Email Address</label>
                <div class="relative">
                  <input
                    type="email"
                    id="login-email"
                    name="email"
                    required
                    value="${escapeHtml(email)}"
                    placeholder="you@example.com"
                    autocomplete="email"
                    class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-burnt-orange focus:ring-1 focus:ring-burnt-orange transition-colors"
                  />
                </div>
              </div>

              <div>
                <label for="login-password" class="block text-xs font-semibold text-slate-300 mb-1.5">Password</label>
                <div class="relative">
                  <input
                    type="password"
                    id="login-password"
                    name="password"
                    required
                    placeholder="••••••••"
                    autocomplete="current-password"
                    class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-burnt-orange focus:ring-1 focus:ring-burnt-orange transition-colors"
                  />
                  <button
                    type="button"
                    class="js-toggle-password absolute right-3 top-3 text-slate-500 hover:text-slate-300 text-xs font-medium"
                    data-target="login-password"
                  >
                    Show
                  </button>
                </div>
              </div>

              <!-- Guest Session Automatic Migration Notice -->
              <div class="p-3 rounded-xl bg-slate-950/70 border border-slate-800 text-[11px] text-slate-400 flex items-center gap-2">
                <span class="text-emerald-400 font-bold">✓</span>
                <span>Active guest sessions &amp; past demo orders will be claimed to your account automatically.</span>
              </div>

              <button
                type="submit"
                id="btn-login-submit"
                class="w-full py-3.5 px-4 rounded-xl font-bold text-sm bg-gradient-to-r from-burnt-orange to-amber-600 hover:from-burnt-orange/90 hover:to-amber-500 text-white shadow-lg shadow-burnt-orange/20 transition-all flex items-center justify-center gap-2"
              >
                <span>Sign In</span>
                <span>→</span>
              </button>

              <div class="relative my-6 text-center">
                <div class="absolute inset-0 flex items-center"><div class="w-full border-t border-slate-800"></div></div>
                <span class="relative px-3 bg-slate-900 text-[11px] text-slate-500 font-medium uppercase tracking-wider">or</span>
              </div>

              <div class="space-y-3">
                <button
                  type="button"
                  class="js-btn-browse-guest w-full py-3 px-4 rounded-xl font-semibold text-xs bg-slate-950 hover:bg-slate-800 border border-slate-800 hover:border-slate-700 text-slate-300 hover:text-white transition-all flex items-center justify-center gap-2"
                >
                  <span>Browse as Guest</span>
                  <span class="text-slate-400 font-normal text-[11px]">— Continue browsing as Guest →</span>
                </button>
                <p class="text-xs text-slate-400 pt-2 border-t border-slate-800/80">
                  Don't have an account yet?
                  <a href="#register" class="text-burnt-orange hover:underline font-bold ml-1">Create an account</a>
                </p>
              </div>
            </form>
          </div>
        </div>

      </div>
    </div>
  `;
}


/**
 * Render Split-Screen Registration Page.
 */
export function renderRegisterPage({ error = null, name = "", email = "", pendingCheckout = null } = {}) {
  const pendingCheckoutAlert = pendingCheckout
    ? `
      <div id="register-checkout-alert" class="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs flex items-start gap-3 mb-4">
        <div class="text-base shrink-0">🛒</div>
        <div>
          <span class="font-bold text-white block">Order in Progress</span>
          <span class="text-amber-200/90 leading-relaxed">
            Create an account to complete your purchase of
            <strong class="text-white">${escapeHtml(pendingCheckout.productName)}</strong>
            (${formatPrice(pendingCheckout.price, pendingCheckout.currency)}).
          </span>
        </div>
      </div>
    `
    : "";
  const errorAlert = error
    ? `
      <div id="register-error-alert" class="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs flex items-start gap-2.5 mb-4 animate-shake">
        <svg class="w-4 h-4 text-rose-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span class="leading-relaxed">${escapeHtml(error)}</span>
      </div>
    `
    : "";

  return `
    <div class="max-w-6xl mx-auto py-6 lg:py-10">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 items-stretch">
        
        <!-- Left Side: Brand Showcase -->
        <div class="lg:col-span-5 rounded-3xl border border-slate-800 bg-gradient-to-br from-slate-900 via-slate-950 to-slate-900 p-8 sm:p-10 flex flex-col justify-between relative overflow-hidden shadow-2xl">
          <div class="absolute -top-24 -left-24 w-72 h-72 bg-burnt-orange/15 rounded-full blur-3xl pointer-events-none"></div>
          <div class="absolute -bottom-20 -right-20 w-60 h-60 bg-amber-600/10 rounded-full blur-2xl pointer-events-none"></div>

          <div>
            <div class="flex items-center gap-3 mb-8">
              ${renderBuywiseLogo(56, "shadow-lg shadow-burnt-orange/30")}
              <div>
                <span class="font-heading font-black text-xl text-white tracking-tight flex items-center gap-1.5">
                  BUYWISE <span class="text-xs px-2 py-0.5 rounded bg-burnt-orange/20 text-burnt-orange font-mono font-bold">AI</span>
                </span>
                <p class="text-xs text-slate-400">Intelligent Shopping &amp; Responsible Checkout</p>
              </div>
            </div>

            <h2 class="text-2xl sm:text-3xl font-heading font-extrabold text-white tracking-tight leading-snug mb-4">
              Join BUYWISE AI.<br/>
              <span class="text-transparent bg-clip-text bg-gradient-to-r from-burnt-orange to-amber-400">Smart shopping starts here.</span>
            </h2>
            <p class="text-xs text-slate-400 leading-relaxed mb-8">
              Create an account to retain your order history, manage personalized spending policies, and access deep AI product comparisons.
            </p>

            <!-- Feature Badges -->
            <div class="space-y-3.5">
              <div class="flex items-start gap-3 p-3 rounded-2xl bg-slate-950/60 border border-slate-800/80">
                <div class="w-8 h-8 rounded-xl bg-burnt-orange/15 text-burnt-orange flex items-center justify-center shrink-0 text-sm font-bold mt-0.5">✦</div>
                <div>
                  <h4 class="text-xs font-bold text-white">Seamless Guest History Claiming</h4>
                  <p class="text-[11px] text-slate-400 leading-relaxed">Your active browsing and guest orders will be securely claimed to your new account.</p>
                </div>
              </div>

              <div class="flex items-start gap-3 p-3 rounded-2xl bg-slate-950/60 border border-slate-800/80">
                <div class="w-8 h-8 rounded-xl bg-emerald-500/15 text-emerald-400 flex items-center justify-center shrink-0 text-sm font-bold mt-0.5">⚡</div>
                <div>
                  <h4 class="text-xs font-bold text-white">Persistent Spending Guardrails</h4>
                  <p class="text-[11px] text-slate-400 leading-relaxed">Save your deterministic budget rules across all your devices.</p>
                </div>
              </div>

              <div class="flex items-start gap-3 p-3 rounded-2xl bg-slate-950/60 border border-slate-800/80">
                <div class="w-8 h-8 rounded-xl bg-amber-500/15 text-amber-400 flex items-center justify-center shrink-0 text-sm font-bold mt-0.5">📊</div>
                <div>
                  <h4 class="text-xs font-bold text-white">Personal Shopping Analytics</h4>
                  <p class="text-[11px] text-slate-400 leading-relaxed">Track multi-currency spending, retailer distributions, and budget health.</p>
                </div>
              </div>
            </div>
          </div>

          <div class="mt-8 pt-6 border-t border-slate-800/80 text-[11px] text-slate-500 flex items-center justify-between">
            <span>PayPal AI Hackathon 2026</span>
            <span class="text-burnt-orange/80 font-mono">Safe &amp; Encrypted</span>
          </div>
        </div>

        <!-- Right Side: Registration Form Card -->
        <div class="lg:col-span-7 rounded-3xl border border-slate-800 bg-slate-900/80 p-8 sm:p-12 shadow-2xl backdrop-blur-md flex flex-col justify-center">
          <div class="max-w-md w-full mx-auto">
            <div class="mb-6">
              <span class="text-xs font-bold uppercase tracking-wider text-burnt-orange font-mono">Get Started</span>
              <h1 class="text-2xl sm:text-3xl font-heading font-extrabold text-white tracking-tight mt-1">Create your account</h1>
              <p class="text-xs sm:text-sm text-slate-400 mt-1">Sign up in seconds to start shopping with AI guardrails.</p>
            </div>

            ${errorAlert}
            ${pendingCheckoutAlert}

            <form id="register-form" class="space-y-4" novalidate>
              <div>
                <label for="register-name" class="block text-xs font-semibold text-slate-300 mb-1.5">Full Name</label>
                <input
                  type="text"
                  id="register-name"
                  name="name"
                  required
                  value="${escapeHtml(name)}"
                  placeholder="e.g. Alex Mercer"
                  autocomplete="name"
                  class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-burnt-orange focus:ring-1 focus:ring-burnt-orange transition-colors"
                />
              </div>

              <div>
                <label for="register-email" class="block text-xs font-semibold text-slate-300 mb-1.5">Email Address</label>
                <input
                  type="email"
                  id="register-email"
                  name="email"
                  required
                  value="${escapeHtml(email)}"
                  placeholder="you@example.com"
                  autocomplete="email"
                  class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-burnt-orange focus:ring-1 focus:ring-burnt-orange transition-colors"
                />
              </div>

              <div>
                <label for="register-password" class="block text-xs font-semibold text-slate-300 mb-1.5">Password</label>
                <div class="relative">
                  <input
                    type="password"
                    id="register-password"
                    name="password"
                    required
                    minlength="8"
                    maxlength="72"
                    placeholder="At least 8 characters"
                    autocomplete="new-password"
                    class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-burnt-orange focus:ring-1 focus:ring-burnt-orange transition-colors"
                  />
                  <button
                    type="button"
                    class="js-toggle-password absolute right-3 top-3 text-slate-500 hover:text-slate-300 text-xs font-medium"
                    data-target="register-password"
                  >
                    Show
                  </button>
                </div>
                <p class="text-[10px] text-slate-500 mt-1">Minimum 8 characters (max 72 bytes). Safe bcrypt hashing.</p>
              </div>

              <div>
                <label for="register-confirm-password" class="block text-xs font-semibold text-slate-300 mb-1.5">Confirm Password</label>
                <div class="relative">
                  <input
                    type="password"
                    id="register-confirm-password"
                    name="confirmPassword"
                    required
                    minlength="8"
                    maxlength="72"
                    placeholder="Re-enter your password"
                    autocomplete="new-password"
                    class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-burnt-orange focus:ring-1 focus:ring-burnt-orange transition-colors"
                  />
                  <button
                    type="button"
                    class="js-toggle-password absolute right-3 top-3 text-slate-500 hover:text-slate-300 text-xs font-medium"
                    data-target="register-confirm-password"
                  >
                    Show
                  </button>
                </div>
              </div>

              <div class="p-3 rounded-xl bg-slate-950/70 border border-slate-800 text-[11px] text-slate-400 flex items-center gap-2">
                <span class="text-burnt-orange font-bold">🔒</span>
                <span>Your active guest shopping session will be safely claimed upon sign up.</span>
              </div>

              <button
                type="submit"
                id="btn-register-submit"
                class="w-full py-3.5 px-4 rounded-xl font-bold text-sm bg-gradient-to-r from-burnt-orange to-amber-600 hover:from-burnt-orange/90 hover:to-amber-500 text-white shadow-lg shadow-burnt-orange/20 transition-all flex items-center justify-center gap-2"
              >
                <span>Create Account</span>
                <span>→</span>
              </button>

              <div class="relative my-6 text-center">
                <div class="absolute inset-0 flex items-center"><div class="w-full border-t border-slate-800"></div></div>
                <span class="relative px-3 bg-slate-900 text-[11px] text-slate-500 font-medium uppercase tracking-wider">or</span>
              </div>

              <div class="space-y-3">
                <button
                  type="button"
                  class="js-btn-browse-guest w-full py-3 px-4 rounded-xl font-semibold text-xs bg-slate-950 hover:bg-slate-800 border border-slate-800 hover:border-slate-700 text-slate-300 hover:text-white transition-all flex items-center justify-center gap-2"
                >
                  <span>Browse as Guest</span>
                  <span class="text-slate-400 font-normal text-[11px]">— Continue browsing as Guest →</span>
                </button>
                <p class="text-xs text-slate-400 pt-2 border-t border-slate-800/80">
                  Already have an account?
                  <a href="#login" class="text-burnt-orange hover:underline font-bold ml-1">Sign In</a>
                </p>
              </div>
            </form>
          </div>
        </div>

      </div>
    </div>
  `;
}


/**
 * Render Header Navigation Authentication Widget.
 */
export function renderUserNavWidget(user) {
  if (user) {
    const initials = (user.name || user.email || "U").substring(0, 1).toUpperCase();
    const displayName = escapeHtml(user.name || user.email?.split("@")[0] || "Account");
    return `
      <div id="user-nav-container" class="flex items-center gap-2">
        <div class="flex items-center gap-2 px-2.5 py-1 rounded-xl bg-slate-900 border border-slate-800 text-xs text-slate-200">
          <span class="w-6 h-6 rounded-lg bg-burnt-orange/20 text-burnt-orange flex items-center justify-center font-bold text-[11px] border border-burnt-orange/40">
            ${initials}
          </span>
          <span class="font-medium max-w-[110px] truncate" title="${escapeHtml(user.email || "")}">${displayName}</span>
        </div>
        <button
          type="button"
          id="btn-header-logout"
          class="px-2.5 py-1.5 rounded-xl bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-white border border-slate-800 text-xs font-semibold transition-colors"
          title="Sign out of your account"
        >
          Logout
        </button>
      </div>
    `;
  }

  return `
    <div id="user-nav-container" class="flex items-center gap-2">
      <a
        href="#login"
        class="nav-auth-login px-3 py-1.5 rounded-xl text-xs font-semibold text-slate-300 hover:text-white hover:bg-slate-900 border border-transparent transition-colors"
      >
        Sign In
      </a>
      <a
        href="#register"
        class="nav-auth-register px-3.5 py-1.5 rounded-xl text-xs font-bold bg-burnt-orange hover:bg-amber-600 text-white shadow-sm shadow-burnt-orange/20 transition-colors"
      >
        Create Account
      </a>
    </div>
  `;
}

/**
 * Render Authentication Required Modal when guest clicks Buy/Checkout.
 */
export function renderAuthRequiredModal({ productName, retailerName, price, currency }) {
  return `
    <div class="p-6 sm:p-8 max-w-lg mx-auto bg-slate-900 border border-slate-800 rounded-3xl shadow-2xl relative overflow-hidden text-left">
      <div class="absolute -top-20 -right-20 w-48 h-48 bg-burnt-orange/15 rounded-full blur-2xl pointer-events-none"></div>

      <div class="flex items-center justify-between mb-5">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-burnt-orange/20 border border-burnt-orange/30 text-burnt-orange flex items-center justify-center font-bold text-base shrink-0">
            🔒
          </div>
          <div>
            <h3 class="text-base font-bold text-white tracking-tight">Authentication Required</h3>
            <p class="text-xs text-slate-400">Complete your purchase securely</p>
          </div>
        </div>
        <button type="button" class="js-btn-close-auth-modal text-slate-500 hover:text-white text-lg p-1.5 rounded-lg hover:bg-slate-800 transition-colors" aria-label="Close">
          ✕
        </button>
      </div>

      <p class="text-xs text-slate-300 leading-relaxed mb-4">
        Please sign in or create an account to complete your purchase.
      </p>

      <!-- Saved Selection Card -->
      <div class="p-3.5 rounded-2xl bg-slate-950 border border-slate-800/90 mb-5 flex items-center justify-between">
        <div class="min-w-0 pr-3">
          <div class="text-[10px] uppercase font-mono tracking-wider text-burnt-orange font-bold">Saved Selection</div>
          <h4 class="text-xs font-bold text-white truncate mt-0.5">${escapeHtml(productName)}</h4>
          <span class="text-[11px] text-slate-400">${escapeHtml(retailerName || "Verified Retailer")}</span>
        </div>
        <div class="text-right shrink-0">
          <span class="text-sm font-extrabold text-white font-mono">${formatPrice(price, currency)}</span>
          <span class="block text-[10px] text-emerald-400 font-semibold">✓ Preserved</span>
        </div>
      </div>

      <!-- Action Choices -->
      <div class="space-y-2.5">
        <button
          type="button"
          class="js-btn-auth-modal-login w-full py-3 px-4 rounded-xl font-bold text-xs bg-gradient-to-r from-burnt-orange to-amber-600 hover:from-burnt-orange/90 hover:to-amber-500 text-white shadow-lg shadow-burnt-orange/20 transition-all flex items-center justify-center gap-2"
        >
          <span>Sign In to Complete Purchase</span>
          <span>→</span>
        </button>

        <button
          type="button"
          class="js-btn-auth-modal-register w-full py-2.5 px-4 rounded-xl font-semibold text-xs bg-slate-800 hover:bg-slate-700 text-white border border-slate-700 transition-all text-center"
        >
          Create New Account
        </button>

        <button
          type="button"
          class="js-btn-close-auth-modal w-full py-2 px-4 rounded-xl text-xs text-slate-400 hover:text-white transition-colors text-center"
        >
          Cancel &amp; Continue Browsing
        </button>
      </div>
    </div>
  `;
}

/**
 * Render Order Review Modal when returning from authentication.
 */
export function renderOrderReviewModal({ productName, retailerName, price, currency }) {
  return `
    <div class="p-6 sm:p-8 max-w-lg mx-auto bg-slate-900 border border-slate-800 rounded-3xl shadow-2xl relative overflow-hidden text-left">
      <div class="absolute -top-20 -right-20 w-48 h-48 bg-emerald-500/10 rounded-full blur-2xl pointer-events-none"></div>

      <div class="flex items-center justify-between mb-5">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 flex items-center justify-center font-bold text-base shrink-0">
            ✓
          </div>
          <div>
            <h3 class="text-base font-bold text-white tracking-tight">Review Your Order</h3>
            <p class="text-xs text-slate-400">Account connected · Ready for PayPal Sandbox</p>
          </div>
        </div>
        <button type="button" class="js-btn-cancel-resumed-order text-slate-500 hover:text-white text-lg p-1.5 rounded-lg hover:bg-slate-800 transition-colors" aria-label="Close">
          ✕
        </button>
      </div>

      <p class="text-xs text-slate-300 leading-relaxed mb-4">
        Your account has been linked. Review your selected item and continue to PayPal Sandbox approval.
      </p>

      <!-- Order Summary Card -->
      <div class="p-4 rounded-2xl bg-slate-950 border border-slate-800/90 mb-5 space-y-2">
        <div class="flex items-start justify-between">
          <div>
            <span class="text-[10px] uppercase font-mono tracking-wider text-slate-500">Item</span>
            <h4 class="text-xs font-bold text-white mt-0.5">${escapeHtml(productName)}</h4>
            <span class="text-[11px] text-slate-400">${escapeHtml(retailerName || "Demo Store")}</span>
          </div>
          <div class="text-right">
            <span class="text-[10px] uppercase font-mono tracking-wider text-slate-500">Total</span>
            <span class="block text-sm font-extrabold text-burnt-orange font-mono mt-0.5">${formatPrice(price, currency)}</span>
          </div>
        </div>
      </div>

      <!-- Action Choices -->
      <div class="space-y-2.5">
        <button
          type="button"
          class="js-btn-confirm-resumed-order w-full py-3.5 px-4 rounded-xl font-bold text-xs bg-gradient-to-r from-burnt-orange to-amber-600 hover:from-burnt-orange/90 hover:to-amber-500 text-white shadow-lg shadow-burnt-orange/20 transition-all flex items-center justify-center gap-2"
        >
          <span>Proceed to PayPal Sandbox</span>
          <span>→</span>
        </button>

        <button
          type="button"
          class="js-btn-cancel-resumed-order w-full py-2 px-4 rounded-xl text-xs text-slate-400 hover:text-white transition-colors text-center"
        >
          Cancel Order
        </button>
      </div>
    </div>
  `;
}

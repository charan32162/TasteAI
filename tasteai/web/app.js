const state = {
  token: localStorage.getItem("tasteai.token"),
  user: JSON.parse(localStorage.getItem("tasteai.user") || "null"),
  view: "discover",
  recommendations: [],
  guestTried: new Set(),
  guestHidden: new Set(),
  lastQuery: "",
  mode: "auto",
  searchVersion: 0,
  locationProvider: null
};

const $ = (selector) => document.querySelector(selector);
const FEATURE_LABELS = {
  spice: "Spice", sweet: "Sweetness", sour: "Sourness", salty: "Saltiness",
  richness: "Richness", oiliness: "Oiliness", smokiness: "Smokiness",
  crunch: "Crunch", texture: "Texture", portion_size: "Portion size",
  price_sensitivity: "Price sensitivity", adventure: "Adventurousness"
};
const FEATURE_DEFAULTS = { spice: 4, sweet: 5, sour: 5, salty: 5, richness: 5, oiliness: 5, smokiness: 5, crunch: 5, texture: 5, portion_size: 5, price_sensitivity: 6, adventure: 4 };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(options.headers || {})
    }
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && state.token) signOut(false);
    const error = new Error(data.detail || data.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[char]);
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(window.tasteaiToast);
  window.tasteaiToast = setTimeout(() => element.classList.remove("show"), 3200);
}

function signedIn() {
  return Boolean(state.token && state.user);
}

function updateAccount() {
  $("#account-name").textContent = state.user?.name || "Your Taste Twin";
  $("#account-email").textContent = state.user?.email || "Sign in to save your taste";
  $("#avatar").textContent = state.user?.name?.trim()?.[0]?.toUpperCase() || "T";
  $("#auth-action").textContent = signedIn() ? "Sign out" : "Sign in";
  document.querySelectorAll("[data-auth-only]").forEach((item) => item.classList.toggle("hidden", !signedIn()));
}

function openAuth(mode = "login") {
  setAuthMode(mode);
  $("#auth-dialog").showModal();
}

function setAuthMode(mode) {
  state.authMode = mode;
  const signup = mode === "signup";
  $("#auth-title").textContent = signup ? "Find your food twin." : "Welcome back.";
  $("#auth-submit").textContent = signup ? "Create my account" : "Sign in";
  $("#auth-switch-label").textContent = signup ? "Already have an account?" : "New here?";
  $("#auth-switch").textContent = signup ? "Sign in" : "Create an account";
  document.querySelectorAll(".signup-field").forEach((item) => item.classList.toggle("hidden", !signup));
  $("#auth-password").autocomplete = signup ? "new-password" : "current-password";
  $("#auth-message").textContent = "";
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".nav-link").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `view-${view}`));
  if (view === "taste" && signedIn()) loadTasteProfile().catch((error) => toast(error.message));
  if (view === "transfer" && signedIn()) loadTransferCatalog().catch((error) => toast(error.message));
  if (view === "saved" && signedIn()) loadSaved().catch((error) => toast(error.message));
  if (view === "history" && signedIn()) loadHistory().catch((error) => toast(error.message));
  if (["taste", "group", "transfer", "reviews", "saved", "history"].includes(view) && !signedIn()) openAuth();
}

function activeBudget() {
  const value = Number($("#budget-input").value);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function requestBody(query = $("#search-input").value) {
  return {
    query,
    budget_max: activeBudget(),
    exploration: Number($("#explore-range").value) / 100,
    limit: 12,
    mode: state.mode
  };
}

function renderCard(item, interactive = signedIn(), bestMatch = false) {
  const score = Number.isFinite(item.match_percent) ? `
    <div class="match-score"><span>${escapeHtml(item.confidence_label || "taste estimate")}</span><strong>${item.match_percent}<small>%</small></strong></div>
    <div class="score-track"><span style="width:${Math.max(0, Math.min(100, item.match_percent))}%"></span></div>
    <div class="score-labels"><span>Taste fit</span><strong>${item.components?.taste ?? "—"}</strong><span>Intent fit</span><strong>${item.components?.intent ?? "—"}</strong></div>
  ` : "";
  const attributeTags = ["spice", "crunch", "portion_size"].filter((feature) => Number.isFinite(item.attributes?.[feature])).map((feature) => `
    <span class="attribute-tag">${escapeHtml(feature === "portion_size" ? "portion" : feature)} ${escapeHtml(item.attributes[feature])}/10</span>
  `).join("");
  const guestTried = state.guestTried.has(item.dish_id);
  const feedbackTarget = item.evidence?.evidence_id
    ? `data-evidence-id="${escapeHtml(item.evidence.evidence_id)}"`
    : `data-id="${escapeHtml(item.dish_id)}"`;
  const actions = interactive ? `
    <div class="card-actions">
      <button class="button-primary" data-action="tried" ${feedbackTarget}>I tried it</button>
      <button class="icon-action" data-action="save" ${feedbackTarget} aria-label="Save ${escapeHtml(item.name)}">♡</button>
      <button class="icon-action" data-action="reject" ${feedbackTarget} aria-label="Not for me: ${escapeHtml(item.name)}">×</button>
    </div>
  ` : `
    <div class="card-actions guest-card-actions">
      <button class="button-primary" data-guest-action="tried" data-id="${escapeHtml(item.dish_id)}">${guestTried ? "Tried ✓" : "I've tried it"}</button>
      <button class="button-secondary" data-card-signin>Save</button>
      <button class="icon-action" data-guest-action="reject" data-id="${escapeHtml(item.dish_id)}" aria-label="Hide ${escapeHtml(item.name)} for this session">×</button>
    </div>
    <p class="session-note">${guestTried ? "Noted for this session only." : "Guest feedback stays in this browser session. Sign in to save it."}</p>
  `;
  return `
    <article class="dish-card${bestMatch ? " best-dish-card" : ""}">
      ${bestMatch ? `<span class="best-match-badge">BEST MATCH</span>` : ""}
      <div class="dish-topline"><span class="cuisine-tag">${escapeHtml(item.cuisine || (item.evidence ? "menu evidence" : ""))}</span><span class="dish-price">${item.price_inr == null ? "Price unknown" : `₹${escapeHtml(item.price_inr)}`}</span></div>
      <h3>${escapeHtml(item.name)}</h3><p class="restaurant-name">${escapeHtml(item.restaurant || "Demo Kitchen")}</p>
      <p class="dish-description">${escapeHtml(item.description)}</p>
      <div class="diet-row">${item.evidence ? "Approved menu evidence" : `<span class="diet-dot ${item.vegetarian ? "veg" : "nonveg"}"></span>${item.vegetarian ? "Vegetarian" : "Non-vegetarian"}`}<span class="demo-chip">${item.evidence ? "EVIDENCE" : "DEMO"}</span></div>
      ${attributeTags ? `<div class="attribute-tags">${attributeTags}</div>` : ""}
      ${score}
      ${item.explanation?.summary ? `<p class="explanation-summary"><strong>Why this for you:</strong> ${escapeHtml(item.explanation.summary)}</p>` : ""}
      ${item.why?.length ? `<ul class="why-list">${item.why.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}
      ${actions}<p class="provenance">${escapeHtml(item.source_label || "fictional demo catalog")}${signedIn() ? "" : " · guest mode"}</p>
    </article>`;
}

function renderRecommendations(items, clarification = null, scrollToResults = true) {
  const visibleItems = items.filter((item) => !state.guestHidden.has(item.dish_id));
  state.recommendations = visibleItems;
  $("#results-shell").hidden = false;
  $("#result-count").textContent = `${visibleItems.length} ${visibleItems.length === 1 ? "option" : "options"}`;
  $("#best-result").innerHTML = visibleItems.length
    ? renderCard(visibleItems[0], signedIn(), true)
    : `<div class="empty-result"><span>✳</span><h3>${clarification ? "I can't safely match that yet." : "No match just yet."}</h3><p>${escapeHtml(clarification || "Try a different craving or relax the budget and filters.")}</p></div>`;
  $("#results").innerHTML = visibleItems.length > 1
    ? visibleItems.slice(1).map((item) => renderCard(item)).join("")
    : "";
  $("#results").parentElement.hidden = visibleItems.length <= 1;
  if (scrollToResults) {
    requestAnimationFrame(() => $(".best-match-section").scrollIntoView({ behavior: "smooth", block: "start" }));
  }
}

function renderPipeline(pipeline) {
  if (!pipeline) return;
  $("#ai-pipeline-mode").textContent = pipeline.candidate_source === "approved_menu_evidence"
    ? "APPROVED EVIDENCE"
    : "FICTIONAL DEMO";
  $("#ai-pipeline-stages").innerHTML = (pipeline.stages || []).map((stage) => `
    <span class="pipeline-stage ${stage.status === "complete" ? "complete" : "skipped"}">
      <strong>${escapeHtml(stage.name.replaceAll("_", " "))}</strong>
      <small>${escapeHtml(stage.status)} · ${escapeHtml(stage.authority)}</small>
    </span>
  `).join("");
  $("#ai-pipeline-status").textContent = pipeline.hard_constraints_before_ranking
    ? "Constraints run before personalization and ranking. Explanations are rendered from grounded facts; optional LLM output cannot override them."
    : "The request could not enter the recommendation pipeline.";
}

function renderCatalogMatches(items, error = null) {
  const section = $("#catalog-match-section");
  section.hidden = !items.length && !error;
  $("#catalog-match-message").textContent = error
    ? `Direct catalog lookup unavailable: ${error}`
    : items.length ? `${items.length} fictional catalog ${items.length === 1 ? "item matches" : "items match"} your text.`
      : "";
  $("#catalog-matches").innerHTML = items.map((item) => renderCard(item)).join("");
}

async function discover(query = "", scrollToResults = true) {
  const version = ++state.searchVersion;
  const body = requestBody(query);
  state.lastQuery = query;
  state.guestHidden.clear();
  $("#search-submit").disabled = true;
  $("#search-message").textContent = "Finding the best fit from the demo menu…";
  $("#view-discover").classList.add("search-results-mode");
  try {
    let result;
    try {
      result = await api("/api/v1/food/search", { method: "POST", body: JSON.stringify(body) });
    } catch (error) {
      if (error.status !== 401 || signedIn()) throw error;
      result = await api("/api/v1/food/search", { method: "POST", body: JSON.stringify(body) });
    }
    let catalogMatches = [];
    let catalogError = null;
    if (query.trim()) {
      try {
        const params = new URLSearchParams({ query, limit: "100" });
        catalogMatches = await api(`/api/v1/food/catalog?${params}`);
      } catch (error) {
        catalogError = error.message;
      }
    }
    if (version !== state.searchVersion) return;
    const unsupported = result.intent.unsupported_constraints?.length > 0;
    const clarification = unsupported
      ? `The demo menu can't verify ${result.intent.unsupported_constraints.join(", ")}. No options are shown for safety.`
      : result.clarification;
    renderPipeline(result.pipeline);
    renderRecommendations(result.recommendations, clarification, scrollToResults);
    renderCatalogMatches(catalogMatches, catalogError);
    $("#search-message").textContent = result.guest_mode
      ? "Guest discovery · search and compare without an account. Sign in only to save or build a persistent Taste Twin."
      : "Personalized with your saved Taste DNA and food history.";
    if (!query) $("#search-message").textContent = `Showing demo menu matches. ${$("#search-message").textContent}`;
  } catch (error) {
    if (version !== state.searchVersion) return;
    $("#results-shell").hidden = false;
    $("#best-result").innerHTML = `<div class="feature-message" role="alert">${escapeHtml(error.message)} Try again when the local service is available.</div>`;
    $("#results").innerHTML = "";
    $("#results").parentElement.hidden = true;
    $("#search-message").textContent = "Search failed. Your request was not saved.";
  } finally {
    if (version === state.searchVersion) $("#search-submit").disabled = false;
  }
}

async function refreshLocationProvider() {
  const status = await api("/api/v1/providers/status");
  state.locationProvider = status;
  $("#location-message").innerHTML = status.places_enabled && status.provider === "google_places"
    ? `Google Places is enabled. Area text or device location is sent for this lookup only. <a href="${escapeHtml(status.privacy_url)}" target="_blank" rel="noopener noreferrer">Privacy</a> · <a href="${escapeHtml(status.terms_url)}" target="_blank" rel="noopener noreferrer">Terms</a>`
    : status.places_enabled && status.provider === "openstreetmap"
      ? "OpenStreetMap lookup is enabled. Area text or device location is sent for this lookup only."
    : `${escapeHtml(status.reason)} No location will be requested or sent.`;
  return status;
}

function renderLocationResults(response) {
  const evidenceScore = (place) => {
    const topEvidence = place.dish_evidence?.results?.[0];
    const score = topEvidence?.ranking?.score;
    return Number.isFinite(score) ? score : null;
  };
  const results = Array.isArray(response.results) ? [...response.results].sort((first, second) => {
    const firstScore = evidenceScore(first);
    const secondScore = evidenceScore(second);
    if (firstScore === null) return secondScore === null ? 0 : 1;
    if (secondScore === null) return -1;
    return secondScore - firstScore;
  }) : [];
  const topEvidencePlace = results.find((place) => evidenceScore(place) !== null);
  if (!results.length) {
    $("#location-results").innerHTML = `<p class="feature-message">${escapeHtml(response.message || response.reason || "No restaurant results were returned for that search.")}</p>`;
    return;
  }
  $("#location-results").innerHTML = `<div class="place-results">
    ${results.map((place) => `
      ${(() => {
        const evidence = place.dish_evidence || {};
        const evidenceResults = Array.isArray(evidence.results) ? evidence.results : [];
        const evidenceMarkup = evidenceResults.length
          ? evidenceResults.map((item) => {
            const dish = item.dish || {};
            const price = item.price || {};
            const provenance = item.provenance || {};
            const source = (provenance.sources || [])[0] || {};
            const score = item.ranking?.score;
            const priceLabel = price.state === "known" && price.amount != null
              ? `${price.currency ? `${escapeHtml(price.currency)} ` : "Currency unknown "}${escapeHtml(price.amount)}${price.variant ? ` · ${escapeHtml(price.variant)}` : ""}`
              : price.state === "known" && (price.minimum != null || price.maximum != null)
                ? `${escapeHtml(price.currency || "Currency unknown")} ${escapeHtml(price.minimum ?? "?")}–${escapeHtml(price.maximum ?? "?")}`
                : "Price not listed";
            const sourceName = source.attribution || source.provider_name || "Approved menu source";
            const sourceLink = typeof source.source_url === "string" && /^https:\/\//i.test(source.source_url)
              ? `<a href="${escapeHtml(source.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(sourceName)}</a>`
              : escapeHtml(sourceName);
            return `<div class="dish-evidence-item">
              <strong>${escapeHtml(dish.name || "Menu item")}</strong>
              <span>Evidence: ${escapeHtml(item.evidence_state || "unknown")} · ${escapeHtml(item.freshness?.state || "freshness unknown")}</span>
              ${Number.isFinite(score) ? `<span>Evidence ranking estimate: ${escapeHtml(score)} / 100</span>` : ""}
              <span>${priceLabel}</span>
              <span>${sourceLink}${source.captured_at ? ` · captured ${escapeHtml(source.captured_at)}` : ""}</span>
              <span>Availability: ${item.availability?.available_now === true ? "supported by source" : item.availability?.available_now === false ? "source marks unavailable" : "unknown"}</span>
              <p>${escapeHtml((item.why || []).join(" "))}</p>
            </div>`;
          }).join("")
          : `<p class="dish-evidence-empty">${escapeHtml(evidence.message || (evidence.status === "not_requested" ? "No dish query was provided; menu evidence was not checked." : "No approved menu evidence is attached to this restaurant candidate."))}</p>`;
        const source = place.source || {};
        const identity = place.identity_resolution || {};
        const distance = Number.isFinite(place.distance_km) ? `${escapeHtml(place.distance_km)} km` : "unknown";
        return `
      <article class="place-card">
        <div class="place-card-heading"><h3>${escapeHtml(place.name)}</h3>${place === topEvidencePlace ? '<span class="evidence-rank-badge">Top evidence match</span>' : ""}<span class="identity-state">${identity.status === "resolved" ? "Identity linked" : "Identity needs review"}</span></div>
        ${place.address ? `<p>${escapeHtml(place.address)}</p>` : ""}
        <div class="place-details">
          <span>Distance: ${distance}</span>
          <span>Cuisine: ${escapeHtml(place.cuisine || "unknown")}</span>
          <span>Rating: unknown</span>
          <span>Review evidence: unknown</span>
        </div>
        <section class="dish-evidence" aria-label="Dish evidence"><strong>Dish evidence</strong>${evidenceMarkup}</section>
        <div class="place-footer">
          ${typeof source.url === "string" && /^https:\/\//i.test(source.url) ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">Open map</a>` : ""}
          ${typeof source.attribution_url === "string" && /^https:\/\//i.test(source.attribution_url) ? `<a class="place-attribution" href="${escapeHtml(source.attribution_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.attribution || source.label || "Source attribution")}</a>` : `<span class="place-attribution" translate="no">${escapeHtml(source.attribution || source.label || "Restaurant data source")}</span>`}
        </div>
        <p class="form-note">Restaurant candidates are not recommendations by themselves. Dish evidence does not establish current availability. Results and location are transient.</p>
      </article>`;
      })()}
    `).join("")}
  </div><p class="form-note place-retention-note">Provider place results and location are shown transiently and are not saved to your account.</p>`;
}

async function runLocationSearch(body, button, knownStatus = null) {
  button.disabled = true;
  $("#location-results").innerHTML = "";
  $("#location-message").textContent = "Looking up restaurant candidates…";
  try {
    const provider = knownStatus || await refreshLocationProvider();
    if (!provider.places_enabled) return;
    const requestBody = {
      ...(body.city ? { location_query: body.city } : { latitude: body.latitude, longitude: body.longitude }),
      radius_meters: 5000,
      limit: 10,
      food_query: body.food_query || undefined,
      budget_max: body.budget_max || undefined,
      use_taste_profile: signedIn() && $("#location-use-taste-profile").checked
    };
    const response = await api("/api/v1/restaurants/discover", {
      method: "POST",
      body: JSON.stringify(requestBody)
    });
    renderLocationResults(response);
    $("#location-message").textContent = response.message || (Array.isArray(response.results) && response.results.length
      ? "Restaurant candidates found. Dish evidence appears only when an approved menu source matches."
      : "No restaurant candidates were returned.");
  } catch (error) {
    $("#location-results").innerHTML = `<p class="feature-message" role="alert">${escapeHtml(error.message)}</p>`;
    $("#location-message").textContent = "Location search failed. Your location was not saved.";
  } finally {
    button.disabled = false;
  }
}

function geolocationError(error) {
  if (error.code === 1) return new Error("Location access was denied. You can search by area instead.");
  if (error.code === 3) return new Error("Location lookup timed out. Try again or search by area.");
  return new Error("Your device could not provide a location. Try searching by area.");
}

async function loadTasteProfile() {
  const profile = await api("/api/v1/users/me/taste-profile");
  const form = $("#taste-form");
  form.innerHTML = Object.entries(FEATURE_LABELS).map(([key, label]) => `
    <label class="taste-slider"><span>${label}<strong id="value-${key}">${profile.features[key] ?? FEATURE_DEFAULTS[key]}</strong></span>
      <input type="range" min="0" max="10" value="${profile.features[key] ?? FEATURE_DEFAULTS[key]}" data-feature="${key}" data-edited="false" aria-label="${label}" />
      <small class="taste-source" data-feature-source="${key}">${featureProvenanceLabel(profile.feature_provenance?.[key])}</small>
      <span class="slider-ends"><span>Not much</span><span>A lot</span></span>
    </label>`).join("");
  form.querySelectorAll("[data-feature]").forEach((input) => input.addEventListener("input", () => {
    $(`#value-${input.dataset.feature}`).textContent = input.value;
    input.dataset.edited = "true";
  }));
  $("#cuisines-input").value = profile.preferred_cuisines.join(", ");
  $("#liked-input").value = profile.liked_ingredients.join(", ");
  $("#disliked-input").value = profile.disliked_ingredients.join(", ");
  $("#diet-input").value = profile.dietary_restrictions.join(", ");
  $("#taste-message").textContent = "";
  await loadMemories();
}

function splitList(value) {
  return value.split(",").map((item) => item.trim()).filter(Boolean).slice(0, 30);
}

function featureProvenanceLabel(source) {
  if (source === "explicit") return "Set by you";
  if (source === "inferred") return "Learned from your ratings";
  if (source === "legacy") return "Existing preference, source unknown";
  return "Default; not yet learned";
}

async function saveTasteProfile() {
  const features = {};
  $("#taste-form").querySelectorAll("[data-feature]").forEach((input) => {
    if (input.dataset.edited === "true") features[input.dataset.feature] = Number(input.value);
  });
  const saved = await api("/api/v1/users/me/taste-profile", {
    method: "PATCH",
    body: JSON.stringify({
      features,
      preferred_cuisines: splitList($("#cuisines-input").value),
      liked_ingredients: splitList($("#liked-input").value),
      disliked_ingredients: splitList($("#disliked-input").value),
      dietary_restrictions: splitList($("#diet-input").value)
    })
  });
  $("#taste-form").querySelectorAll("[data-feature]").forEach((input) => {
    input.dataset.edited = "false";
    const source = saved.feature_provenance?.[input.dataset.feature];
    $(`[data-feature-source="${input.dataset.feature}"]`).textContent = featureProvenanceLabel(source);
  });
  $("#taste-message").textContent = "Your Taste DNA is up to date.";
  toast("Your taste profile is saved.");
}

async function loadMemories() {
  const memories = await api("/api/v1/food-memory");
  $("#memories-list").innerHTML = memories.length ? memories.map((memory) => `
    <article class="memory-row"><span class="memory-kind">${escapeHtml(memory.permanence)}</span><div><strong>${escapeHtml(memory.subject)}</strong><p>${escapeHtml(memory.detail)}</p></div><button class="icon-action" data-delete-memory="${memory.id}" aria-label="Forget this">×</button></article>
  `).join("") : `<p class="muted-copy">No food memories yet. Tell us what to keep in mind.</p>`;
}

async function loadSaved() {
  const items = await api("/api/v1/food/saved");
  $("#saved-list").innerHTML = items.length ? items.map((item) => renderCard(item, true)).join("") : `<div class="empty-result"><span>♡</span><h3>Nothing saved yet.</h3><p>Tap the heart on a dish when something catches your eye.</p></div>`;
}

async function loadHistory() {
  const events = await api("/api/v1/food/history");
  $("#history-list").innerHTML = events.length ? events.map((event) => `
    <article class="history-row"><div class="history-mark">${event.action === "save" ? "♡" : event.action === "reject" ? "×" : "✳"}</div><div><strong>${escapeHtml(event.dish_name)}</strong><p>${escapeHtml(event.action)}${event.rating ? ` · rated ${event.rating}/5` : ""}${event.note ? ` · ${escapeHtml(event.note)}` : ""}</p></div><time>${new Date(event.created_at).toLocaleDateString()}</time></article>
  `).join("") : `<div class="empty-result"><span>↺</span><h3>Your food story starts here.</h3><p>Rate or save a dish to start building your Food Twin.</p></div>`;
}

async function loadTransferCatalog() {
  const dishes = await api("/api/v1/food/catalog?limit=100");
  $("#transfer-dish").innerHTML = dishes.map((dish) => `
    <option value="${escapeHtml(dish.dish_id)}">${escapeHtml(dish.name)} — ${escapeHtml(dish.restaurant)}</option>
  `).join("");
}

function renderGroupRecommendations(result) {
  const target = $("#group-results");
  if (result.unsupported_restrictions && Object.keys(result.unsupported_restrictions).length) {
    const details = Object.entries(result.unsupported_restrictions)
      .map(([name, restrictions]) => `${escapeHtml(name)}: ${restrictions.map(escapeHtml).join(", ")}`)
      .join("; ");
    target.innerHTML = `<p class="feature-message">No venue shown: the demo menu cannot verify these dietary restrictions (${details}).</p>`;
    return;
  }
  if (result.no_compatible_options) {
    target.innerHTML = `<p class="feature-message">No venue in the fictional demo menu has an eligible listed dish for everyone. Try adjusting budgets or exclusions.</p>`;
    return;
  }
  target.innerHTML = `<div class="group-results-heading"><h2>Possible group tables</h2><p>${escapeHtml(result.scoring_note)} ${escapeHtml(result.dietary_note)}</p></div>
    <div class="group-cards">${result.recommendations.map((item) => `
      <article class="group-card"><div class="group-card-heading"><div><span class="eyebrow">${escapeHtml(item.source_label)}</span><h3>${escapeHtml(item.restaurant)}</h3></div><strong>${escapeHtml(item.match_percent)}<small>%</small></strong></div>
      <p class="form-note">${escapeHtml(item.confidence_label)} · one listed dish per attendee</p>
      ${item.menu_options.map((option) => `<div class="group-pick"><span><strong>${escapeHtml(option.attendee)}</strong><small>${escapeHtml(option.dish_name)} · ₹${escapeHtml(option.price_inr)}</small></span><strong>${escapeHtml(option.match_percent)}%</strong></div>`).join("")}</article>
    `).join("")}</div>`;
}

function parseGroupParticipants(text) {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.length < 2 || lines.length > 8) throw new Error("Enter 2–8 attendees, one per line.");
  return lines.map((line, index) => {
    const separator = line.indexOf(":");
    const name = separator > 0 ? line.slice(0, separator).trim() : `Attendee ${index + 1}`;
    const query = separator > 0 ? line.slice(separator + 1).trim() : line;
    const restrictions = [...query.matchAll(/\b(vegetarian|vegan|plant-based)\b/gi)].map((match) => match[1].toLowerCase());
    const ingredientExclusions = [...query.matchAll(/\b(?:no|avoid|without|not)\s+(?:the\s+)?([a-z][a-z -]{1,24})/gi)]
      .map((match) => match[1].split(/\b(?:under|below|with|and|but|for)\b|[,.;!?]/i)[0].trim())
      .filter(Boolean);
    return {
      name,
      query,
      dietary_restrictions: [...new Set(restrictions)],
      ingredient_exclusions: [...new Set(ingredientExclusions)],
      use_my_taste_profile: /^(you|me|myself)$/i.test(name)
    };
  });
}

$("#group-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type='submit'], button:not([type])");
  button.disabled = true;
  $("#group-results").innerHTML = "";
  try {
    const participants = parseGroupParticipants($("#group-input").value);
    const result = await api("/api/v1/groups/recommend", {
      method: "POST",
      body: JSON.stringify({ participants })
    });
    renderGroupRecommendations(result);
  } catch (error) {
    $("#group-results").innerHTML = `<p class="feature-message">${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
});

$("#transfer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type='submit'], button:not([type])");
  button.disabled = true;
  $("#transfer-result").innerHTML = "";
  try {
    const friendFeatures = {};
    document.querySelectorAll("[data-friend-feature]").forEach((input) => {
      friendFeatures[input.dataset.friendFeature] = Number(input.value);
    });
    const result = await api("/api/v1/food/taste-transfer", {
      method: "POST",
      body: JSON.stringify({
        dish_id: $("#transfer-dish").value,
        friend_rating: Number($("#transfer-rating").value),
        friend_features: friendFeatures,
        consent_confirmed: $("#transfer-consent").checked
      })
    });
    const shared = result.shared_features.map((item) => escapeHtml(FEATURE_LABELS[item.feature] || item.feature)).join(", ") || "None in the entered dimensions";
    const different = result.different_features.map((item) => `${escapeHtml(FEATURE_LABELS[item.feature] || item.feature)} (${escapeHtml(item.your_level)} vs ${escapeHtml(item.friend_level)})`).join(", ") || "None in the entered dimensions";
    $("#transfer-result").innerHTML = `<article class="transfer-card"><span class="eyebrow">${escapeHtml(result.source_label)}</span><h2>${escapeHtml(result.dish_name)}</h2>
      <p>Your friend-reported rating: <strong>${escapeHtml(result.friend_rating)}/5</strong></p>
      <div class="transfer-stats"><span>Shared taste fit <strong>${escapeHtml(result.similarity_percent)}%</strong></span><span>Estimated fit for you <strong>${escapeHtml(result.estimated_you_match_percent)}%</strong></span></div>
      <p><strong>Similar:</strong> ${shared}</p><p><strong>Different:</strong> ${different}</p>
      <p class="form-note">${escapeHtml(result.confidence_label)}. ${escapeHtml(result.friend_rating_note)} ${escapeHtml(result.privacy_note)}</p></article>`;
  } catch (error) {
    $("#transfer-result").innerHTML = `<p class="feature-message">${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
});

$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type='submit'], button:not([type])");
  button.disabled = true;
  $("#review-results").innerHTML = "";
  try {
    const excerpts = $("#review-input").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    if (excerpts.length < 1 || excerpts.length > 100) throw new Error("Enter 1–100 non-empty excerpts.");
    if (excerpts.some((item) => item.length > 2000)) throw new Error("Each excerpt must be 2,000 characters or fewer.");
    const result = await api("/api/v1/reviews/analyze", {
      method: "POST",
      body: JSON.stringify({ excerpts })
    });
    const aspects = Object.entries(result.aspects);
    $("#review-results").innerHTML = `<div class="review-results-heading"><h2>Evidence from your excerpts</h2><p>${escapeHtml(result.review_count)} caller-supplied excerpts analyzed. No external review sources used.</p></div>
      ${aspects.length ? `<div class="review-aspects">${aspects.map(([aspect, detail]) => `
        <article class="review-aspect"><h3>${escapeHtml(aspect)} <span>${escapeHtml(detail.mentions)} mention${detail.mentions === 1 ? "" : "s"}</span></h3>
          <ul>${detail.evidence.map((excerpt) => `<li>“${escapeHtml(excerpt)}”</li>`).join("")}</ul></article>
      `).join("")}</div>` : `<p class="feature-message">No supported food-aspect terms were found in the submitted excerpts, so no aspect claims are shown.</p>`}
      <p class="form-note">Source: ${escapeHtml(result.source)}. Exact excerpts are shown as evidence; this is not a review summary or verification of their authenticity.</p>`;
  } catch (error) {
    $("#review-results").innerHTML = `<p class="feature-message">${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
});

function signOut(showMessage = true) {
  state.token = null;
  state.user = null;
  localStorage.removeItem("tasteai.token");
  localStorage.removeItem("tasteai.user");
  updateAccount();
  if (showMessage) toast("Signed out.");
  if (state.view !== "discover") setView("discover");
}

document.querySelectorAll(".nav-link").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
$("#auth-action").addEventListener("click", () => signedIn() ? signOut() : openAuth());
$("#auth-switch").addEventListener("click", () => setAuthMode(state.authMode === "signup" ? "login" : "signup"));
$("#continue-guest").addEventListener("click", () => {
  $("#auth-dialog").close();
  setView("discover");
  $("#search-input").focus();
});
$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const signup = state.authMode === "signup";
  const payload = {
    email: $("#auth-email").value,
    password: $("#auth-password").value,
    ...(signup ? { name: $("#auth-name").value } : {})
  };
  try {
    const response = await api(`/api/v1/auth/${signup ? "register" : "login"}`, { method: "POST", body: JSON.stringify(payload) });
    state.token = response.access_token;
    state.user = response.user;
    localStorage.setItem("tasteai.token", state.token);
    localStorage.setItem("tasteai.user", JSON.stringify(state.user));
    $("#auth-dialog").close();
    updateAccount();
    if (state.view === "taste") await loadTasteProfile();
    else if (state.view === "transfer") await loadTransferCatalog();
    else if (state.view === "saved") await loadSaved();
    else if (state.view === "history") await loadHistory();
    else if (state.view === "discover") await discover(state.lastQuery);
    toast(`Welcome${state.user.name ? `, ${state.user.name}` : ""}. Let's find your next favorite.`);
  } catch (error) {
    $("#auth-message").textContent = error.message;
  }
});

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = $("#search-input").value.trim();
  await discover(query);
});

$("#location-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const city = $("#location-area").value.trim();
  if (!city) {
    $("#location-message").textContent = "Enter an area, city, or landmark to search.";
    $("#location-area").focus();
    return;
  }
  const foodQuery = $("#location-food-query").value.trim() || $("#search-input").value.trim();
  const budget = Number($("#budget-input").value);
  await runLocationSearch({
    city,
    food_query: foodQuery,
    budget_max: Number.isInteger(budget) && budget > 0 ? budget : undefined
  }, $("#location-area-submit"));
});

$("#rag-query-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#rag-query-submit");
  const output = $("#rag-answer");
  button.disabled = true;
  output.hidden = false;
  output.textContent = "Checking approved menu evidence…";
  try {
    const result = await api("/api/v1/rag/query", {
      method: "POST",
      body: JSON.stringify({
        query: $("#rag-query").value.trim(),
        budget_max: (() => {
          const value = Number($("#budget-input").value);
          return Number.isInteger(value) && value > 0 ? value : undefined;
        })(),
        limit: 5
      })
    });
    const sources = result.results.map((item) => {
      const price = item.price_amount && item.price_currency ? ` · ${item.price_currency} ${item.price_amount}` : " · price unknown";
      const captured = item.captured_at ? ` · captured ${item.captured_at}` : " · capture date unknown";
      const provenance = ` · source ${item.source_type} · ${item.confidence} confidence`;
      return `<li>${escapeHtml(item.dish_name)} — ${escapeHtml(item.restaurant_name)}${escapeHtml(price)}${escapeHtml(captured)} · evidence ${escapeHtml(item.evidence_id)}${escapeHtml(provenance)}<details><summary>Evidence text</summary><pre>${escapeHtml(item.content)}</pre></details></li>`;
    }).join("");
    const answerState = result.insufficient_evidence
      ? "Could not verify from approved menu evidence."
      : result.generation?.status === "claims_validated"
        ? "AI-assisted claim selection; wording was validated and rendered from approved menu evidence."
        : "Verified from approved menu evidence; wording is composed from recorded facts.";
    output.innerHTML = `<p>${escapeHtml(result.answer)}</p>${sources ? `<ul>${sources}</ul>` : ""}<small>${escapeHtml(answerState)}</small>`;
  } catch (error) {
    output.textContent = `Menu evidence lookup failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
});

async function refreshRagStatus() {
  try {
    const status = await api("/api/v1/ai/status");
    $("#rag-status").textContent = status.rag.status === "empty"
      ? "No approved menu evidence is indexed yet. Answers will say when evidence is unavailable."
      : `Approved menu evidence is indexed (${status.rag.indexed_chunks} records). Only eligible evidence can support an answer.`;
    $("#ai-pipeline-status").textContent = `Parser: deterministic authority · RAG: ${status.rag.status} · embeddings: ${status.embeddings.status} · agent: ${status.agent.status}.`;
    $("#ai-pipeline-mode").textContent = status.llm.status === "enabled" || status.embeddings.status === "external_enabled"
      ? "OPTIONAL AI ENABLED"
      : "OFFLINE DEMO";
    $("#ai-pipeline-stages").innerHTML = [
      ["intent", "deterministic fallback ready"],
      ["evidence retrieval", status.rag.status],
      ["embeddings", status.embeddings.status],
      ["bounded agent", `${status.agent.max_steps} steps / ${status.agent.max_tool_calls} tools`]
    ].map(([name, value]) => `<span class="pipeline-stage complete"><strong>${escapeHtml(name)}</strong><small>${escapeHtml(value)}</small></span>`).join("");
  } catch (error) {
    $("#rag-status").textContent = `Evidence status is unavailable: ${error.message}`;
    $("#ai-pipeline-status").textContent = `AI status unavailable: ${error.message}`;
  }
}

$("#location-nearby-submit").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  $("#location-results").innerHTML = "";
  try {
    const provider = await refreshLocationProvider();
    if (!provider.places_enabled) return;
    if (!navigator.geolocation) {
      $("#location-message").textContent = "This browser does not provide location access. Search by area instead.";
      return;
    }
    $("#location-message").textContent = "Requesting location only for this lookup…";
    const position = await new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, (error) => reject(geolocationError(error)), {
        enableHighAccuracy: false,
        maximumAge: 60000,
        timeout: 10000
      });
    });
    await runLocationSearch({
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      food_query: $("#location-food-query").value.trim() || $("#search-input").value.trim(),
      budget_max: (() => {
        const budget = Number($("#budget-input").value);
        return Number.isInteger(budget) && budget > 0 ? budget : undefined;
      })()
    }, button, provider);
  } catch (error) {
    $("#location-results").innerHTML = `<p class="feature-message" role="alert">${escapeHtml(error.message)}</p>`;
    $("#location-message").textContent = "Location search failed. Your location was not saved.";
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", async () => {
  const prompt = button.dataset.prompt;
  state.mode = button.dataset.mode || "auto";
  $("#search-input").value = prompt;
  await discover(prompt);
}));

$("#search-input").addEventListener("input", () => {
  state.mode = "auto";
});

$("#explore-range").addEventListener("input", (event) => {
  const value = Number(event.target.value);
  $("#explore-value").textContent = value < 25 ? "Keep it familiar" : value < 70 ? "A little new" : "Surprise me";
});
$("#budget-input").addEventListener("change", () => {
  if (state.lastQuery) discover(state.lastQuery);
});

$("#results-shell").addEventListener("click", async (event) => {
  if (event.target.closest("[data-card-signin]")) {
    openAuth("signup");
    return;
  }
  const guestButton = event.target.closest("[data-guest-action]");
  if (guestButton) {
    const dishId = guestButton.dataset.id;
    if (guestButton.dataset.guestAction === "tried") {
      state.guestTried.add(dishId);
      renderRecommendations(state.recommendations);
      toast("Noted for this browser session only. Sign in to keep food history.");
    } else {
      state.guestHidden.add(dishId);
      renderRecommendations(state.recommendations);
      toast("Hidden for this search session only.");
    }
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button) return;
  button.disabled = true;
  try {
    const payload = { action: button.dataset.action };
    if (button.dataset.evidenceId) payload.evidence_id = Number(button.dataset.evidenceId);
    else payload.dish_id = button.dataset.id;
    if (payload.action === "tried") {
      const rating = Number(prompt("How did you like it? Rate from 1 to 5.", "4"));
      if (Number.isInteger(rating) && rating >= 1 && rating <= 5) payload.rating = rating;
      else payload.action = "tried";
    }
    await api("/api/v1/food/feedback", { method: "POST", body: JSON.stringify(payload) });
    toast(payload.action === "save" ? "Saved to your food list." : payload.action === "reject" ? "Got it. We'll show this less often." : "Thanks — your Taste Twin learned from that.");
    await discover(state.lastQuery);
  } catch (error) {
    button.disabled = false;
    toast(error.message);
  }
});

$("#save-taste").addEventListener("click", () => saveTasteProfile().catch((error) => toast(error.message)));
$("#memory-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/v1/food-memory", {
      method: "POST",
      body: JSON.stringify({ text: $("#memory-input").value, permanence: "explicit" })
    });
    $("#memory-input").value = "";
    await loadMemories();
    toast("Your food memory is saved.");
  } catch (error) { toast(error.message); }
});
$("#memories-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-memory]");
  if (!button) return;
  try {
    await api(`/api/v1/food-memory/${button.dataset.deleteMemory}`, { method: "DELETE" });
    await loadMemories();
    toast("Food memory removed.");
  } catch (error) { toast(error.message); }
});

$("#auth-dialog").addEventListener("click", (event) => {
  if (event.target === $("#auth-dialog")) $("#auth-dialog").close();
});
$("#auth-dialog").addEventListener("close", () => $("#auth-message").textContent = "");

updateAccount();
refreshLocationProvider().catch((error) => {
  $("#location-message").textContent = `Location provider status is unavailable: ${error.message}`;
});
refreshRagStatus();
discover("", false);

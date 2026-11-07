"use strict";

const PLATFORMS = { x: "X", instagram: "Instagram", xiaohongshu: "Xiaohongshu", tiktok: "TikTok", linkedin: "LinkedIn" };
const PAGE = 60;
const $ = (id) => document.getElementById(id);
const stored = (k, fallback) => { try { return localStorage.getItem(k) ?? fallback; } catch { return fallback; } };
const store = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

const state = {
  view: "home",        // home | search | topic | following | favorites | accounts
  query: "",
  topic: null,
  posts: [],           // posts in the current view, newest first
  favs: new Map(),     // key -> favourite record (cache status etc.)
  hiddenPlatforms: new Set(),
  kind: "all",
  sort: stored("sort", "recent") === "top" ? "top" : "recent",  // topic view only
  newKeys: new Set(),
  findQuery: "",       // favourites and following views, lower-cased
  offset: 0,
  hasMore: false,
  viewerIndex: -1,
  imageIndex: 0,
  pollTimer: null,
  searchAbort: null,
};

/* ---------- helpers ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const safe = (u) => (/^(https?:)?\/\//i.test(u || "") || /^\/(cache\/|api\/media\?)/.test(u || "") ? u : null);
/* Instagram and Xiaohongshu refuse to show their pictures on other sites, so those come through the app. */
const PROXY_HOSTS = ["cdninstagram.com", "fbcdn.net", "xhscdn.com", "xhscdn.net"];
function pic(u) {
  const s = safe(u);
  if (!s || s.startsWith("/")) return s;
  try {
    const url = new URL(s, location.href);
    if (url.protocol === "https:" && PROXY_HOSTS.some((d) => url.hostname === d || url.hostname.endsWith(`.${d}`)))
      return `/api/media?u=${encodeURIComponent(url.href)}`;
  } catch {}
  return s;
}
const keyOf = (p) => `${p.platform}:${p.id}`;
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });
const icon = (id) => `<svg class="icon" aria-hidden="true"><use href="#i-${id}"/></svg>`;

function ago(iso) {
  if (!iso) return "";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  const steps = [[60, "second"], [3600, "minute"], [86400, "hour"], [604800, "day"], [2629800, "week"], [31557600, "month"], [Infinity, "year"]];
  const div = { second: 1, minute: 60, hour: 3600, day: 86400, week: 604800, month: 2629800, year: 31557600 };
  for (const [limit, unit] of steps) if (Math.abs(s) < limit) return rtf.format(Math.round(s / div[unit]), unit);
  return "";
}
const bytes = (n) => (n == null ? "" : n === 0 ? "0 KB" : n < 1e6 ? `${Math.max(1, Math.round(n / 1e3))} KB` : n < 1e9 ? `${(n / 1e6).toFixed(1)} MB` : `${(n / 1e9).toFixed(2)} GB`);

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

/* ---------- rendering ---------- */
function statsHtml(p) {
  const items = [["heart", p.likes, "likes"], ["comment", p.comments, "comments"], ["repeat", p.shares, "shares"], ["eye", p.views, "views"]]
    .filter(([, v]) => v != null)
    .map(([i, v, label]) => `<span title="${v.toLocaleString()} ${label}">${icon(i)}${compact.format(v)}</span>`);
  return items.length ? `<div class="stats">${items.join("")}</div>` : "";
}

function cacheHtml(fav) {
  if (!fav) return "";
  switch (fav.cache_status) {
    case "cached": return `<div class="cache cached">${icon("disk")}Saved offline · ${bytes(fav.cache_bytes)}</div>`;
    case "pending": return `<div class="cache pending">${icon("disk")}Saving video…</div>`;
    case "failed": return `<div class="cache failed" title="${esc(fav.cache_error)}">${icon("alert")}Couldn't save video<button type="button" data-act="retry">Retry</button></div>`;
    default: return fav.post.media_type === "video" ? `<div class="cache">${icon("cloud")}Plays online only</div>` : "";
  }
}

function tileHtml(p, i) {
  const key = keyOf(p);
  const fav = state.favs.get(key);
  const label = PLATFORMS[p.platform] || p.platform;
  const thumb = pic(p.thumbnail_url || p.images[0]);
  const hasMedia = p.media_type !== "text";
  const priority = i < 6 ? ' fetchpriority="high"' : ' loading="lazy"';
  let frame = "";
  if (hasMedia) {
    const pills = [];
    if (p.media_type === "image" && p.images.length > 1) pills.push(`<span class="pill br">${icon("images")}${p.images.length}</span>`);
    frame = `<div class="frame${thumb ? "" : " empty"}">
      ${thumb ? `<img src="${esc(thumb)}" alt="" decoding="async"${priority} data-loading>` : ""}
      <span class="pill tl" data-p="${esc(p.platform)}"><span class="dot"></span>${esc(label)}</span>
      ${pills.join("")}
      ${p.media_type === "video" ? `<span class="play">${icon("play")}</span>` : ""}
    </div>`;
  }
  const meta = `<div class="meta">${hasMedia ? "" : `<span class="dot" data-p="${esc(p.platform)}"></span>`}
      <span class="who">${esc(p.author || label)}</span>${state.newKeys.has(key) ? `<span class="new">New</span>` : ""}<span class="when">${esc(ago(p.created_at))}</span></div>`;
  const find = state.view === "favorites" ? ` data-s="${esc(`${p.text} ${p.author || ""} ${label}`.toLowerCase())}"` : "";
  const also = p.also_on?.length ? ` data-also="${esc(p.also_on.map((a) => a.platform).join(" "))}"` : "";
  return `<article class="tile${hasMedia ? "" : " text-only"}" data-i="${i}" data-p="${esc(p.platform)}" data-kind="${p.media_type}"${find}${also}>
    <button class="fav" type="button" data-act="fav" aria-pressed="${!!fav}" aria-label="${fav ? "Remove from favourites" : "Add to favourites"}">${icon("star")}</button>
    <button class="tile-open" type="button" data-act="open" aria-label="Open ${esc(label)} post">
      ${frame}
      <div class="body">
        ${p.text ? `<p class="text">${esc(p.text)}</p>` : ""}
        ${meta}
        ${statsHtml(p)}
      </div>
    </button>
    ${state.view === "favorites" ? `<div class="body" style="padding-top:0">${cacheHtml(fav)}</div>` : ""}
  </article>`;
}

function renderFeed(append = false) {
  const feed = $("feed");
  const from = append ? feed.querySelectorAll(".tile").length : 0;
  const html = state.posts.slice(from).map((p, j) => tileHtml(p, from + j)).join("");
  if (append) feed.insertAdjacentHTML("beforeend", html);
  else feed.innerHTML = html;
  if (!state.posts.length) feed.innerHTML = emptyHtml();
  applyFilters();
  renderChips();
  $("more").hidden = !state.hasMore;
}

function emptyHtml() {
  if (state.view === "following") {
    const i = fol.info;
    if (state.findQuery) return `<div class="empty-state"><h2>No posts match</h2><p>Nothing from the accounts you follow mentions “${esc(state.findQuery)}”.</p></div>`;
    if (!i?.follows.length) return `<div class="empty-state"><h2>Your stream is empty</h2><p>Follow a few accounts above, or sign in to your own For you feeds. New posts land here, newest first, and SocialAll ${checksEvery(i)}.</p></div>`;
    if (!i.service.running && !i.follows.some(isForYou)) return `<div class="empty-state"><h2>Waiting for the feed service</h2><p>Start it above, and posts from the accounts you follow appear here.</p></div>`;
    if (i.foryou?.busy) {
      const b = i.foryou.busy;
      const title = { sign_in: `Sign in to ${PLATFORMS[b.platform] || b.platform}`, setup: "Getting ready" }[b.what] || "Reading your feed";
      return `<div class="empty-state"><h2>${esc(title)}</h2><p>${esc(b.step)}. Posts appear here when it's done.</p></div>`;
    }
    return `<div class="empty-state"><h2>No posts yet</h2><p>Press Refresh to check the accounts you follow now.</p></div>`;
  }
  if (state.view === "favorites")
    return `<div class="empty-state"><h2>No favourites yet</h2><p>Press the star on any post to keep it here. Videos you star are saved to a folder on this computer so they play offline.</p></div>`;
  if (state.view === "home")
    return `<div class="empty-state"><h2>Start with a topic</h2><p>Type anything: a brand, a hashtag, a person, an idea. Results from every connected platform land here, newest first.</p>
      ${state.demo ? `<p>You're seeing sample posts until you connect a platform. <button class="linkish" type="button" data-acct="">Connect accounts</button></p>` : ""}
      <div class="suggest">${["AI agents", "street food", "climate tech", "咖啡"].map((t) => `<button class="btn ghost" type="button" data-suggest="${esc(t)}">${esc(t)}</button>`).join("")}</div></div>`;
  return `<div class="empty-state"><h2>Nothing found</h2><p>No platform returned posts for this topic. Try a broader word, or <button class="linkish" type="button" data-acct="">check your accounts</button>.</p></div>`;
}

function skeleton(n = 8) {
  $("feed").innerHTML = Array.from({ length: n }, () => `<div class="skel"><div class="frame"></div><div class="line"></div><div class="line"></div></div>`).join("");
  $("more").hidden = true;
}

/* Filtering only toggles visibility, so it never rebuilds the feed. */
function applyFilters() {
  let shown = 0;
  const q = state.view === "favorites" ? state.findQuery : "";  // following asks the server instead
  const tiles = $("feed").querySelectorAll(".tile");
  for (const el of tiles) {
    // A collapsed cross-post shows while any of its platforms is shown.
    const platforms = el.dataset.also ? [el.dataset.p, ...el.dataset.also.split(" ")] : [el.dataset.p];
    const ok = platforms.some((k) => !state.hiddenPlatforms.has(k)) && (state.kind === "all" || el.dataset.kind === state.kind)
      && (!q || el.dataset.s?.includes(q));
    el.hidden = !ok;
    if (ok) shown++;
  }
  $("nomatch").hidden = !(q && tiles.length && !shown);
  return shown;
}

function renderChips() {
  const counts = {};
  for (const p of state.posts) for (const k of [p.platform, ...(p.also_on || []).map((a) => a.platform)]) counts[k] = (counts[k] || 0) + 1;
  const present = Object.keys(PLATFORMS).filter((k) => counts[k]);
  const following = state.view === "following";
  $("filters").hidden = !state.posts.length && !(following && state.findQuery);
  $("sort").hidden = state.view !== "topic";
  $("find").hidden = state.view !== "favorites" && !following;
  const hint = following ? "Find in these posts" : "Find in favourites";
  $("find-q").placeholder = hint;
  $("find-q").setAttribute("aria-label", hint);
  $("chips").innerHTML = present.map((k) =>
    `<button class="chip" type="button" data-p="${k}" aria-pressed="${!state.hiddenPlatforms.has(k)}"><span class="dot"></span>${PLATFORMS[k]}<span class="n">${counts[k]}</span></button>`).join("");
}

function setHeader(title, sub, refresh = false) {
  $("title").textContent = title;
  $("subtitle").textContent = sub;
  $("refresh").hidden = !refresh;
}

function showErrors(errors) {
  const entries = Object.entries(errors || {});
  const n = $("notice");
  n.hidden = !entries.length;
  n.innerHTML = entries.length
    ? `${icon("alert")}<div>${entries.map(([k, v]) => `<div><b>${esc(PLATFORMS[k] || k)}</b> didn't respond: ${esc(v)} <button class="linkish" type="button" data-acct="${esc(k)}">Open ${esc(PLATFORMS[k] || k)} account</button></div>`).join("")}</div>`
    : "";
}

function markNav() {
  const favs = $("nav-favs");
  state.view === "favorites" ? favs.setAttribute("aria-current", "page") : favs.removeAttribute("aria-current");
  const follow = $("nav-following");
  state.view === "following" ? follow.setAttribute("aria-current", "page") : follow.removeAttribute("aria-current");
  $("follow").hidden = state.view !== "following";
  if (state.view !== "following") stopServicePoll();
  const accts = $("nav-accounts");
  state.view === "accounts" ? accts.setAttribute("aria-current", "page") : accts.removeAttribute("aria-current");
  const onAccounts = state.view === "accounts";
  $("accounts").hidden = !onAccounts;
  $("feed").hidden = onAccounts;
  if (onAccounts) { $("filters").hidden = true; $("more").hidden = true; $("nomatch").hidden = true; }
  for (const b of document.querySelectorAll(".topic-btn")) {
    const on = state.view === "topic" && state.topic && +b.dataset.id === state.topic.id;
    on ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current");
  }
}

/* ---------- views ---------- */
/* ---------- deep links: #q=term, #topic=id, #favorites ---------- */
function parseHash(h) {
  const m = /^#(q|topic|accounts)=(.+)$/.exec(h);
  if (h === "#favorites") return { view: "favorites" };
  if (h === "#following") return { view: "following" };
  if (h === "#accounts") return { view: "accounts" };
  if (m?.[1] === "accounts") return { view: "accounts", focus: m[2] };
  if (m?.[1] === "topic" && /^\d+$/.test(m[2])) return { view: "topic", id: +m[2] };
  if (m?.[1] === "q") { try { const q = decodeURIComponent(m[2]).trim(); if (q) return { view: "search", q }; } catch {} }
  return { view: "home" };
}

/* nav: "push" adds a history entry, "replace" swaps the current one, false leaves the URL. */
function setHash(h, nav) {
  if (!nav || location.hash === h || (!h && !location.hash)) return;
  const url = h || location.pathname + location.search;
  nav === "replace" ? history.replaceState(null, "", url) : history.pushState(null, "", url);
}

async function route() {
  const r = parseHash(location.hash);
  if (r.view === "search") return runSearch(r.q, false, false);
  if (r.view === "favorites") return openFavorites(false);
  if (r.view === "following") return openFollowing(false);
  if (r.view === "accounts") return openAccounts(false, r.focus);
  if (r.view === "topic") {
    const topics = state.topicList || (await loadTopics());
    const t = topics.find((x) => x.id === r.id);
    if (t) return openTopic(t, false);
    toast("That topic no longer exists");
    return goHome("replace");
  }
  goHome(false);
}

async function runSearch(q, fresh = false, nav = "push") {
  q = q.trim();
  if (!q) return;
  setHash(`#q=${encodeURIComponent(q)}`, nav);
  state.searchAbort?.abort();
  const ctrl = (state.searchAbort = new AbortController());
  Object.assign(state, { view: "search", query: q, topic: null, offset: 0, hasMore: false, newKeys: new Set() });
  $("q").value = q;
  $("save").disabled = false;
  markNav();
  setHeader(q, "Searching every connected platform…");
  showErrors();
  skeleton();
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(q)}&limit=24${fresh ? "&fresh=1" : ""}`, { signal: ctrl.signal });
    state.posts = r.posts;
    setHeader(q, summary(r.posts), true);
    showErrors(r.errors);
    renderFeed();
  } catch (e) {
    if (e.name === "AbortError") return;
    state.posts = [];
    renderFeed();
    toast(`Search failed: ${e.message}`);
  }
}

function summary(posts) {
  const n = new Set(posts.map((p) => p.platform)).size;
  return `${posts.length} post${posts.length === 1 ? "" : "s"} from ${n} platform${n === 1 ? "" : "s"}`;
}

async function openTopic(t, nav = "push") {
  state.searchAbort?.abort();
  setHash(`#topic=${t.id}`, nav);
  Object.assign(state, { view: "topic", topic: t, query: t.name, offset: 0, newKeys: new Set() });
  $("q").value = t.name;
  $("save").disabled = true;
  markNav();
  setHeader(t.name, "Loading saved posts…", true);
  showErrors();
  skeleton();
  let posts;
  try {
    posts = await api(postsUrl(t.id));
  } catch (e) {
    toast(`Couldn't open topic: ${e.message}`);
    return goHome("replace");
  }
  if (state.view !== "topic" || state.topic.id !== t.id) return;
  if (!posts.length) return refreshTopic();
  state.posts = posts;
  state.hasMore = posts.length === PAGE;
  setHeader(t.name, topicSub(t), true);
  renderFeed();
}

const postsUrl = (id, offset = 0) => `/api/topics/${id}/posts?limit=${PAGE}&offset=${offset}&sort=${state.sort}`;

async function setSort(sort) {
  if (sort === state.sort) return;
  state.sort = sort;
  store("sort", sort);
  for (const x of $("sort").children) x.setAttribute("aria-pressed", String(x.dataset.sort === sort));
  if (state.view !== "topic") return;
  const t = state.topic;
  const posts = await api(postsUrl(t.id));
  if (state.view !== "topic" || state.topic.id !== t.id || state.sort !== sort) return;
  state.posts = posts;
  state.hasMore = posts.length === PAGE;
  renderFeed();
}

function topicSub(t) {
  return `${t.post_count ?? state.posts.length} saved posts${t.refreshed_at ? ` · updated ${ago(t.refreshed_at)}` : ""}`;
}

let loadingMore = false;
async function loadMore() {
  if (state.view === "following") return loadMoreFollowing();
  if (state.view !== "topic" || !state.hasMore || loadingMore) return false;
  const btn = $("more-btn");
  const { topic, sort } = state;
  loadingMore = btn.disabled = true;
  try {
    const next = await api(postsUrl(topic.id, state.posts.length));
    if (state.view !== "topic" || state.topic.id !== topic.id || state.sort !== sort) return false;
    state.posts.push(...next);
    state.hasMore = next.length === PAGE;
    renderFeed(true);
    return true;
  } catch (e) {
    toast(`Couldn't load more: ${e.message}`);
    return false;
  } finally {
    loadingMore = btn.disabled = false;
  }
}

/* Load the next page before the reader reaches the end. The observer only fires
   on changes, so after a load we check again in case filters hid the new tiles. */
const nearEnd = () => $("more-btn").getBoundingClientRect().top < innerHeight + 600;
async function autoLoad() {
  for (let i = 0; i < 3 && !$("more").hidden && nearEnd(); i++) if (!(await loadMore())) break;
}
new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) autoLoad(); }, { rootMargin: "600px" })
  .observe($("more"));

async function refreshTopic() {
  const btn = $("refresh");
  btn.classList.add("is-busy");
  btn.disabled = true;
  try {
    if (state.view === "search") return await runSearch(state.query, true, false);
    if (state.view === "following") return await refreshFollowing();
    const t = state.topic;
    const first = !state.posts.length;
    if (first) skeleton();
    const r = await api(`/api/topics/${t.id}/refresh`, { method: "POST" });
    const posts = await api(postsUrl(t.id));
    if (state.view !== "topic" || state.topic.id !== t.id) return;
    state.posts = posts;
    // On the first fill every post is new, so labels would be noise.
    state.newKeys = first ? new Set() : new Set(r.new_keys);
    toast(first ? `Saved ${r.saved} post${r.saved === 1 ? "" : "s"}` : r.new ? `${r.new} new post${r.new === 1 ? "" : "s"}` : "No new posts");
    state.hasMore = posts.length === PAGE;
    const topics = await loadTopics();
    state.topic = topics.find((x) => x.id === t.id) || t;
    setHeader(t.name, topicSub(state.topic), true);
    showErrors(r.errors);
    renderFeed();
  } catch (e) {
    toast(`Refresh failed: ${e.message}`);
  } finally {
    btn.classList.remove("is-busy");
    btn.disabled = false;
  }
}

async function openFavorites(nav = "push") {
  state.searchAbort?.abort();
  setHash("#favorites", nav);
  if (state.view !== "favorites") { state.findQuery = ""; $("find-q").value = ""; }
  Object.assign(state, { view: "favorites", topic: null, hasMore: false, newKeys: new Set() });
  $("save").disabled = true;
  markNav();
  showErrors();
  await loadFavorites();
  state.posts = [...state.favs.values()].map((f) => f.post);
  setHeader("Favourites", favSub(), false);
  renderFeed();
}

function favSub() {
  const list = [...state.favs.values()];
  const saved = list.filter((f) => f.cache_status === "cached");
  return list.length
    ? `${list.length} starred · ${saved.length} video${saved.length === 1 ? "" : "s"} saved offline`
    : "Posts you star are kept here";
}

/* ---------- favourites ---------- */
async function loadFavorites() {
  const list = await api("/api/favorites");
  state.favs = new Map(list.map((f) => [keyOf(f.post), f]));
  $("fav-count").textContent = list.length;
  schedulePoll();
  return list;
}

/* Poll only while a video is downloading and the tab is visible. */
function schedulePoll() {
  clearTimeout(state.pollTimer);
  const pending = [...state.favs.values()].some((f) => f.cache_status === "pending");
  if (!pending) return;
  state.pollTimer = setTimeout(async () => {
    if (document.hidden) return schedulePoll();
    const before = JSON.stringify([...state.favs.values()].map((f) => f.cache_status));
    await loadFavorites();
    const after = JSON.stringify([...state.favs.values()].map((f) => f.cache_status));
    if (before !== after) {
      if (state.view === "favorites") { setHeader("Favourites", favSub()); updateFavBadges(); }
      refreshViewerPanel();
      loadStorage();
    }
  }, 2000);
}

function updateFavBadges() {
  for (const el of $("feed").querySelectorAll(".tile")) {
    const p = state.posts[+el.dataset.i];
    const box = el.lastElementChild;
    if (p && box.classList.contains("body")) box.innerHTML = cacheHtml(state.favs.get(keyOf(p)));
  }
}

async function toggleFav(p) {
  const key = keyOf(p);
  const had = state.favs.get(key);
  try {
    if (had) {
      await api(`/api/favorites/${encodeURIComponent(p.platform)}/${encodeURIComponent(p.id)}`, { method: "DELETE" });
      state.favs.delete(key);
      toast(had.cache_file || had.cache_url ? "Removed, and deleted the saved video" : "Removed from favourites");
    } else {
      const f = await api("/api/favorites", { method: "POST", body: JSON.stringify({ platform: p.platform, id: p.id }) });
      state.favs.set(key, f);
      toast(f.cache_status === "pending" ? "Starred. Saving the video to your cache folder" : "Added to favourites");
    }
  } catch (e) {
    return toast(e.message);
  }
  $("fav-count").textContent = state.favs.size;
  schedulePoll();
  if (state.view === "favorites" && had) {
    state.posts = state.posts.filter((x) => keyOf(x) !== key);
    setHeader("Favourites", favSub());
    renderFeed();
    if (state.viewerIndex >= 0) closeViewer();
  } else {
    for (const b of document.querySelectorAll(`.tile[data-i] .fav`)) {
      const q = state.posts[+b.closest(".tile").dataset.i];
      if (q && keyOf(q) === key) {
        b.setAttribute("aria-pressed", String(!had));
        b.setAttribute("aria-label", had ? "Add to favourites" : "Remove from favourites");
      }
    }
  }
  refreshViewerPanel();
  loadStorage();
}

async function retry(p) {
  try {
    const f = await api(`/api/favorites/${encodeURIComponent(p.platform)}/${encodeURIComponent(p.id)}/retry`, { method: "POST" });
    state.favs.set(keyOf(p), f);
    updateFavBadges();
    refreshViewerPanel();
    schedulePoll();
  } catch (e) { toast(e.message); }
}

/* ---------- viewer ---------- */
const viewer = $("viewer");

function visibleIndexes() {
  return [...$("feed").querySelectorAll(".tile:not([hidden])")].map((el) => +el.dataset.i);
}

function openViewer(i) {
  state.viewerIndex = i;
  state.imageIndex = 0;
  renderStage();
  refreshViewerPanel();
  if (!viewer.open) viewer.showModal();
  updateSteps();
  warmNeighbours();
}

/* Fetch the next and previous posts' first picture so stepping feels instant.
   Never touches video bytes, and does nothing when the user asked to save data. */
function warmNeighbours() {
  if (navigator.connection?.saveData) return;
  const order = visibleIndexes();
  const at = order.indexOf(state.viewerIndex);
  for (const j of [order[at + 1], order[at - 1]]) {
    const p = state.posts[j];
    const src = p && pic(p.media_type === "image" ? p.images[0] : p.thumbnail_url);
    if (src) new Image().src = src;
  }
}

function closeViewer() {
  if (viewer.open) viewer.close(); // the close handler stops playback and frees the video
}

function stepPost(dir) {
  const order = visibleIndexes();
  const at = order.indexOf(state.viewerIndex);
  const next = order[at + dir];
  if (next != null) openViewer(next);
}

function updateSteps() {
  const order = visibleIndexes();
  const at = order.indexOf(state.viewerIndex);
  $("post-prev").disabled = at <= 0;
  $("post-next").disabled = at < 0 || at >= order.length - 1;
}

function renderStage() {
  const p = state.posts[state.viewerIndex];
  const stage = $("stage");
  const fav = state.favs.get(keyOf(p));
  if (p.media_type === "video") {
    const local = fav?.cache_status === "cached" ? fav.cache_url : null;
    const src = safe(local || p.video_url);
    if (src) {
      const poster = pic(p.thumbnail_url);
      stage.innerHTML = `<video controls autoplay playsinline preload="auto" ${poster ? `poster="${esc(poster)}"` : ""} src="${esc(src)}"></video>`;
      const v = stage.querySelector("video");
      // A missing offline copy falls back to the original; a link that expired falls back to the platform's player.
      const fallback = () => {
        if (local && p.video_url && v.src !== new URL(p.video_url, location.href).href) v.src = p.video_url;
        else if (safe(p.embed_url)) stage.innerHTML = `<iframe src="${esc(p.embed_url)}" allow="autoplay; encrypted-media; fullscreen" allowfullscreen title="Video player"></iframe>`;
      };
      v.addEventListener("error", fallback);
      return;
    }
    if (safe(p.embed_url)) {
      stage.innerHTML = `<iframe src="${esc(p.embed_url)}" allow="autoplay; encrypted-media; fullscreen" allowfullscreen title="Video player"></iframe>`;
      return;
    }
    if (pic(p.thumbnail_url) && safe(p.url)) {
      stage.innerHTML = `<a class="watch-out" href="${esc(p.url)}" target="_blank" rel="noopener">
          <img src="${esc(pic(p.thumbnail_url))}" alt=""><span class="btn">${icon("play")}<span>Watch on ${esc(PLATFORMS[p.platform] || p.platform)}</span></span></a>`;
      return;
    }
  }
  if (p.media_type === "image" && p.images.length) {
    const n = p.images.length;
    const k = state.imageIndex;
    stage.innerHTML = `<img src="${esc(pic(p.images[k]))}" alt="Image ${k + 1} of ${n}">
      ${n > 1 ? `
        <button class="icon-btn img-step l" type="button" data-img="-1" aria-label="Previous image" ${k === 0 ? "disabled" : ""}>${icon("left")}</button>
        <button class="icon-btn img-step r" type="button" data-img="1" aria-label="Next image" ${k === n - 1 ? "disabled" : ""}>${icon("right")}</button>
        <div class="carousel-nav">${p.images.map((_, j) => `<button type="button" data-goto="${j}" aria-label="Image ${j + 1}" aria-current="${j === k}"></button>`).join("")}</div>` : ""}`;
    // Warm the next image so stepping through is instant.
    if (k + 1 < n && pic(p.images[k + 1])) new Image().src = pic(p.images[k + 1]);
    const img = stage.querySelector("img");
    img.addEventListener("error", () => { if (img.isConnected) brokenImage(p); }, { once: true });
    return;
  }
  stage.innerHTML = `<div class="noimg"><p>${esc(p.text)}</p></div>`;
}

function brokenImage(p) {
  const label = PLATFORMS[p.platform] || p.platform;
  $("stage").innerHTML = `<div class="noimg"><p>${esc(p.text || "This image couldn't be loaded.")}</p>
    ${safe(p.url) ? `<a class="btn ghost" href="${esc(p.url)}" target="_blank" rel="noopener">${icon("external")}<span>Open on ${esc(label)}</span></a>` : ""}</div>`;
}

function alsoHtml(p) {
  if (!p.also_on?.length) return "";
  const items = p.also_on.map((a) => {
    const label = esc(PLATFORMS[a.platform] || a.platform);
    return safe(a.url) ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${label}</a>` : label;
  });
  return `<div class="also">Also on ${items.join(" · ")}</div>`;
}

function refreshViewerPanel() {
  if (state.viewerIndex < 0) return;
  const p = state.posts[state.viewerIndex];
  if (!p) return;
  const fav = state.favs.get(keyOf(p));
  const label = PLATFORMS[p.platform] || p.platform;
  const when = p.created_at ? new Date(p.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
  $("panel").innerHTML = `
    <div class="who" data-p="${esc(p.platform)}"><span class="dot"></span><b>${esc(label)}</b>${p.author ? `<span>${esc(p.author)}</span>` : ""}</div>
    ${when ? `<div class="sub">${esc(when)}</div>` : ""}
    ${p.text ? `<p class="full">${esc(p.text)}</p>` : ""}
    ${statsHtml(p)}
    ${alsoHtml(p)}
    ${cacheHtml(fav)}
    <div class="actions">
      <button class="btn${fav ? "" : " ghost"}" type="button" data-act="fav" aria-pressed="${!!fav}">${icon("star")}<span>${fav ? "Starred" : "Star"}</span></button>
      ${safe(p.url) ? `<a class="btn ghost" href="${esc(p.url)}" target="_blank" rel="noopener">${icon("external")}<span>Open on ${esc(label)}</span></a>` : ""}
    </div>`;
}

/* ---------- sidebar ---------- */
async function loadTopics() {
  const topics = await api("/api/topics");
  $("topics-empty").hidden = topics.length > 0;
  $("topics").innerHTML = topics.map((t) => `<li class="topic">
      <button class="topic-btn" type="button" data-id="${t.id}"><span>${esc(t.name)}</span><span class="count">${t.post_count}</span></button>
      <button class="topic-edit" type="button" data-edit="${t.id}" aria-label="Rename topic ${esc(t.name)}" title="Rename">${icon("pen")}</button>
      <button class="topic-del" type="button" data-del="${t.id}" aria-label="Delete topic ${esc(t.name)}">${icon("trash")}</button>
    </li>`).join("");
  state.topicList = topics;
  markNav();
  return topics;
}

async function loadSources() {
  const list = await api("/api/platforms");
  state.demo = list.some((p) => p.name === "demo");
  const real = list.filter((p) => p.name !== "demo");
  $("acct-count").textContent = `${real.filter((p) => p.configured).length}/${real.length}`;
  const demo = list.find((p) => p.name === "demo");
  $("sources").innerHTML = list.filter((p) => p.name !== "demo").map((p) =>
    `<li data-p="${esc(p.name)}" data-acct="${esc(p.name)}" title="${p.configured ? "Manage" : "Connect"} ${esc(p.label)}"><span class="dot"></span>${esc(p.label)}<span class="state${p.configured ? " ready" : " todo"}">${p.configured ? "Connected" : "Connect"}</span></li>`).join("")
    + (demo ? `<li title="${esc(demo.notes)}"><span class="dot"></span>Sample posts<span class="state ready">On</span></li>` : "");
}

async function loadStorage() {
  try {
    const s = await api("/api/favorites/storage");
    $("storage").textContent = `Video cache: ${s.files} file${s.files === 1 ? "" : "s"}, ${bytes(s.bytes)}`;
    $("storage").title = s.folder;
  } catch {}
}

/* ---------- following: accounts read through the free feed service (RSSHub) ---------- */
const fol = { info: null, platform: stored("follow-platform", "instagram"), kind: "account", adding: null, busy: false, poll: null, fyPoll: null, loadSeq: 0 };
const KIND_LABELS = { account: "Account", keyword: "Keyword", home: "For you" };
const isForYou = (f) => f.kind === "foryou";  // your own feeds, read by SocialAll's browser rather than RSSHub

async function openFollowing(nav = "push") {
  state.searchAbort?.abort();
  setHash("#following", nav);
  if (state.view !== "following") { state.findQuery = ""; $("find-q").value = ""; }
  Object.assign(state, { view: "following", topic: null, posts: [], hasMore: false, newKeys: new Set() });
  $("q").value = "";
  $("save").disabled = true;
  markNav();
  showErrors();
  setHeader("Following", "Loading…", false);
  skeleton(4);
  await loadFollowing();
  if (state.view !== "following") return;
  await loadFollowPosts();
}

async function loadFollowing() {
  try {
    fol.info = await api("/api/following");
  } catch (e) {
    toast(`Couldn't load Following: ${e.message}`);
    return null;
  }
  $("follow-count").textContent = fol.info.follows.length || "";
  if (state.view === "following") {
    renderFollowPanel();
    followHeader();
  }
  if (fol.info.service.starting) startServicePoll();
  if (fol.info.foryou?.busy) startForYouPoll();
  return fol.info;
}

const checksEvery = (i) => (i?.every_minutes ?? 15) > 0 ? `checks for more every ${i?.every_minutes ?? 15} minutes` : "checks when you press Refresh";

function followHeader() {
  const i = fol.info;
  if (!i) return;
  const n = i.follows.length;
  const checked = i.refreshed_at ? `checked ${ago(i.refreshed_at)}` : checksEvery(i);
  const accounts = i.follows.filter((f) => f.kind === "account").length;
  const what = accounts === n ? `${n} account${n === 1 ? "" : "s"}` : `${n} feeds`;
  setHeader("Following", n ? `${what} · ${i.posts} post${i.posts === 1 ? "" : "s"} · ${checked}`
    : "Follow accounts and see their new posts in one stream. Free, with no limit.", n > 0 && (i.service.running || i.follows.some(isForYou)));
}

const followPostsUrl = (offset = 0) =>
  `/api/following/posts?limit=${PAGE}&offset=${offset}${state.findQuery ? `&q=${encodeURIComponent(state.findQuery)}` : ""}`;

async function loadFollowPosts() {
  const seq = ++fol.loadSeq;
  try {
    const posts = await api(followPostsUrl());
    if (state.view !== "following" || seq !== fol.loadSeq) return;
    state.posts = posts;
    state.hasMore = posts.length === PAGE;
    renderFeed();
  } catch (e) { toast(`Couldn't load posts: ${e.message}`); }
}

async function loadMoreFollowing() {
  if (!state.hasMore || loadingMore) return false;
  const btn = $("more-btn");
  const seq = fol.loadSeq;
  loadingMore = btn.disabled = true;
  try {
    const next = await api(followPostsUrl(state.posts.length));
    if (state.view !== "following" || seq !== fol.loadSeq) return false;
    state.posts.push(...next);
    state.hasMore = next.length === PAGE;
    renderFeed(true);
    return true;
  } catch (e) {
    toast(`Couldn't load more: ${e.message}`);
    return false;
  } finally {
    loadingMore = btn.disabled = false;
  }
}

async function refreshFollowing(quiet = false) {
  const r = await api("/api/following/refresh", { method: "POST" });
  const reading = r.foryou_reading?.length ? `. Reading your ${r.foryou_reading.map((p) => PLATFORMS[p]).join(" and ")} For you feed${r.foryou_reading.length > 1 ? "s" : ""} too` : "";
  if (r.offline) { if (!quiet) toast(`The feed service isn't running. Start it above${reading}`); }
  else if (!quiet) toast((r.new ? `${r.new} new post${r.new === 1 ? "" : "s"}` : "No new posts") + reading);
  if (reading) startForYouPoll();
  await loadFollowing();
  if (state.view !== "following") return;
  const first = !state.posts.length;
  await loadFollowPosts();
  state.newKeys = first ? new Set() : new Set(r.new_keys || []);
  if (state.newKeys.size) renderFeed();
}

function serviceHtml(s) {
  if (s.running) return "";
  const steps = [];
  if (!s.local) {
    steps.push(`SocialAll reads feeds from <code>${esc(s.url)}</code>, which isn't answering. Check that RSSHub is running there.`);
  } else {
    steps.push(s.docker ? `Open <b>Docker Desktop</b> and wait until it says it's running.`
      : `Install <a href="${esc(s.docker_url)}" target="_blank" rel="noopener">Docker Desktop</a> (free) and open it once.`);
    steps.push(`Press <b>Start feed service</b>. The first start is a large download and takes a few minutes. After that it starts whenever Docker does.`);
  }
  const status = s.starting ? `<p class="svc-step">${icon("refresh")}<span>${esc(s.step || "Starting")}…</span></p>`
    : s.error ? `<p class="result bad">${icon("alert")}<span>${esc(s.error)}</span></p>` : "";
  return `<div class="follow-card svc">
      <div class="svc-head"><span class="tag">Free</span><div><h2>Start the feed service</h2>
        <p class="lead">RSSHub reads the accounts you follow. It's free, open source, and runs only on this Mac.</p></div></div>
      <ol class="steps big">${steps.map((x) => `<li>${x}</li>`).join("")}</ol>
      ${s.local ? `<div class="row"><button class="btn${s.starting ? " is-busy" : ""}" type="button" data-act="start"${s.starting ? " disabled" : ""}>${icon("refresh")}<span>${s.starting ? "Starting…" : "Start feed service"}</span></button>
        <a class="linkish" href="${esc(fol.info.docs_url)}" target="_blank" rel="noopener">About RSSHub</a></div>` : ""}
      ${status}
    </div>`;
}

function xTokenHtml(i) {
  const t = i.x_token;
  const from = t.source === "env" ? "in .env" : "saved";
  return `<div class="xtoken" data-p="x">
      <p class="note">${icon("lock")}<span><b>X shows nothing to people who are signed out</b>, so the feed service needs a login token from an X account. Use a spare account, not your main one: X can lock accounts it thinks are automated.</span></p>
      <label class="field" for="x-token"><span>${esc(t.label)}</span>
        <input id="x-token" name="${esc(t.key)}" type="password" autocomplete="off" spellcheck="false"
          placeholder="${esc(t.set ? `Token ${from} ${t.value}. Paste to replace` : t.placeholder)}">
        <small>${esc(t.help)}</small></label>
      <div class="row"><button class="btn ghost sm" type="button" data-act="save-token">Save token</button>
        ${t.set && t.source === "app" ? `<button class="linkish danger-link" type="button" data-act="remove-token">Remove token</button>` : ""}</div>
    </div>`;
}

function followChip(f) {
  const bad = f.error ? ` is-bad" title="${esc(f.error)}` : "";
  return `<li class="fchip${bad}" data-p="${esc(f.platform)}">
      <span class="dot"></span><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.label)}</a>
      ${f.error ? icon("alert") : `<span class="n">${f.post_count}</span>`}
      <button type="button" data-unfollow="${f.id}" aria-label="Unfollow ${esc(f.label)}" title="Unfollow">${icon("x")}</button>
    </li>`;
}

function renderFollowPanel() {
  const i = fol.info;
  if (!i || state.view !== "following") return;
  const typed = $("follow-handle")?.value || "";
  const token = $("x-token")?.value || "";
  const adding = fol.adding ?? !i.follows.length;
  const plat = i.platforms.find((p) => p.name === fol.platform) || i.platforms[0];
  const kinds = plat.kinds || [];
  const kind = kinds.find((k) => k.name === fol.kind);
  const hint = kind || plat;  // placeholder and help for what's being followed
  const xHelp = (plat.name === "x" && adding) || i.follows.some((f) => f.platform === "x" && f.error);
  const rss = i.follows.filter((f) => !isForYou(f));
  const errors = rss.filter((f) => f.error);
  $("follow").innerHTML = `
    ${rss.length || adding ? serviceHtml(i.service) : ""}
    ${rss.length || !adding ? `<div class="follow-bar">
        ${rss.length ? `<ul class="follow-list">${rss.map(followChip).join("")}</ul>` : ""}
        ${adding ? "" : `<button class="btn ghost sm" type="button" data-act="adding">${icon("plus")}<span>${rss.length ? "Follow another" : "Follow an account"}</span></button>`}
      </div>
      ${errors.length ? `<ul class="follow-errors">${errors.map((f) => `<li data-p="${esc(f.platform)}"><span class="dot"></span><b>${esc(f.label)}</b> ${esc(f.error)}</li>`).join("")}</ul>` : ""}` : ""}
    ${adding ? `<form class="follow-card follow-add" id="follow-add">
        <div class="add-head"><h2>${kind?.name === "keyword" ? "Follow a keyword on X" : kind?.name === "home" ? "Add the spare account's For you feed" : "Follow an account"}</h2>${i.follows.length ? `<button class="linkish" type="button" data-act="adding">Done</button>` : ""}</div>
        <div class="seg plats" role="group" aria-label="Platform">${i.platforms.map((p) =>
          `<button type="button" data-plat="${esc(p.name)}" data-p="${esc(p.name)}" aria-pressed="${p.name === plat.name}"><span class="dot"></span>${esc(p.label)}</button>`).join("")}</div>
        ${kinds.length ? `<div class="kinds" role="group" aria-label="What to follow on ${esc(plat.label)}">${["account", ...kinds.map((k) => k.name)].map((k) =>
          `<button type="button" data-kind-pick="${k}" aria-pressed="${k === (kind?.name || "account")}">${KIND_LABELS[k] || k}</button>`).join("")}</div>` : ""}
        <div class="add-row">
          ${kind?.name === "home" ? "" : `<input id="follow-handle" name="handle" type="text" autocomplete="off" spellcheck="false" autocapitalize="off"
            placeholder="${esc(hint.placeholder)}" aria-label="${esc(kind ? `${KIND_LABELS[kind.name]} to follow on ${plat.label}` : `${plat.label} account`)}" required>`}
          <button class="btn" type="submit"${fol.busy ? " disabled" : ""}>${icon("plus")}<span>${fol.busy ? "Adding…" : kind?.name === "home" ? "Add the For you feed" : "Follow"}</span></button>
        </div>
        <p class="note">${esc(hint.help)}</p>
      </form>` : ""}
    ${i.foryou ? forYouHtml(i.foryou) : ""}
    ${xHelp ? xTokenHtml(i) : ""}`;
  if ($("follow-handle")) $("follow-handle").value = typed;
  if ($("x-token")) $("x-token").value = token;
}

function forYouHtml(fy) {
  const busy = fy.busy;
  const on = fy.platforms.some((p) => p.on);
  const every = fy.every_hours > 0 ? `It reads them about every ${fy.every_hours} hour${fy.every_hours === 1 ? "" : "s"} while it's open` : "It reads them when you press Read now";
  const rows = fy.platforms.map((p) => {
    const mine = busy?.platform === p.name ? busy : null;
    const off = busy ? " disabled" : "";
    const signIn = (label) => `<button class="btn sm" type="button" data-fy="sign-in" data-fyp="${esc(p.name)}"${off}>${label}</button>`;
    const read = `<button class="btn ghost sm" type="button" data-fy="read" data-fyp="${esc(p.name)}"${off}>${icon("refresh")}<span>Read now</span></button>`;
    const out = `<button class="linkish danger-link" type="button" data-fy="sign-out" data-fyp="${esc(p.name)}">Sign out</button>`;
    let status, acts;
    if (mine) {
      status = `<span class="fy-step">${icon("refresh")}<span>${esc(mine.step)}</span></span>`;
      acts = mine.what === "sign_in" ? `<button class="btn ghost sm" type="button" data-fy="close">Close window</button>` : "";
    } else if (!p.on) {
      status = p.note && p.note_bad ? `<span class="fy-bad">${icon("alert")}<span>${esc(p.note)}</span></span>` : `<span>Not signed in</span>`;
      acts = signIn("Sign in");
    } else if (p.error) {
      status = `<span class="fy-bad">${icon("alert")}<span>${esc(p.error)}</span></span>`;
      acts = (p.sign_in_again ? signIn("Sign in again") : read) + out;
    } else {
      status = `<span>Signed in · ${p.read_at ? `read ${ago(p.read_at)}` : "not read yet"} · ${p.posts} post${p.posts === 1 ? "" : "s"}</span>`;
      acts = read + out;
    }
    return `<li data-p="${esc(p.name)}"><span class="dot"></span><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.label)}</a>
      <span class="fy-status">${status}</span><span class="fy-acts">${acts}</span></li>`;
  }).join("");
  return `<div class="follow-card fy">
      <div><h2>Your own For you feeds</h2>
        <p class="lead">What Instagram, TikTok and Xiaohongshu recommend to you, in this stream. Sign in once in a window SocialAll opens on this Mac. ${every}.</p></div>
      <ul class="fy-list">${rows}</ul>
      <details class="fy-how"${on ? "" : " open"}><summary>How this stays safe</summary>
        <p class="note">${icon("lock")}<span>You sign in on the platform's own page, so SocialAll never sees your password. The sign-in stays in SocialAll's own browser on this Mac, apart from your usual one. SocialAll only looks: it never likes, follows, comments or posts. Platforms don't allow automated reading, so now and then one may sign you out or ask you to prove you're a person, and the feed waits for you here.${fy.ready ? "" : " The first sign-in downloads a browser for SocialAll, about 150 MB."}</span></p>
      </details>
    </div>`;
}

async function forYouAct(what, platform) {
  const label = PLATFORMS[platform] || platform;
  if (what === "sign-out" && !confirm(`Sign out of ${label} in SocialAll? Its For you posts are removed from this stream.`)) return;
  const call = {
    "sign-in": [`/api/following/foryou/${platform}/sign-in`, "POST"],
    read: [`/api/following/foryou/${platform}/read`, "POST"],
    "sign-out": [`/api/following/foryou/${platform}`, "DELETE"],
    close: ["/api/following/foryou/close", "POST"],
  }[what];
  try {
    fol.info.foryou = await api(call[0], { method: call[1] });
  } catch (e) { return toast(e.message); }
  if (what === "sign-out") {
    toast(`Signed out of ${label}`);
    await loadFollowing();
    return loadFollowPosts();
  }
  renderFollowPanel();
  if (fol.info.foryou.busy) startForYouPoll();
}

/* While SocialAll's browser signs in or reads, check on it. When it's done, show what it found. */
function startForYouPoll() {
  if (fol.fyPoll) return;
  fol.fyPoll = setInterval(async () => {
    if (!fol.info) return;
    let fy;
    try { fy = await api("/api/following/foryou"); } catch { return; }
    const was = fol.info.foryou?.busy;
    fol.info.foryou = fy;
    if (fy.busy) {
      if (state.view === "following") renderFollowPanel();
      if (!was || was.what !== fy.busy.what) renderFeed();  // the empty state says what's happening
      return;
    }
    clearInterval(fol.fyPoll);
    fol.fyPoll = null;
    const note = was && fy.platforms.find((p) => p.name === was.platform)?.note;
    if (note) toast(note);
    await loadFollowing();
    if (state.view !== "following") return;
    const before = new Set(state.posts.map(keyOf));
    await loadFollowPosts();
    if (before.size) {
      state.newKeys = new Set(state.posts.map(keyOf).filter((k) => !before.has(k)));
      if (state.newKeys.size) renderFeed();
    }
  }, 2500);
}

async function followAccount(form) {
  const kinds = fol.info.platforms.find((p) => p.name === fol.platform)?.kinds || [];
  const kind = kinds.some((k) => k.name === fol.kind) ? fol.kind : "account";
  const handle = form.querySelector("#follow-handle")?.value.trim() || "";
  if ((!handle && kind !== "home") || fol.busy) return;
  fol.busy = true;
  renderFollowPanel();
  try {
    const r = await api("/api/following", { method: "POST", body: JSON.stringify({ platform: fol.platform, kind, handle }) });
    const label = r.follow.label;
    toast(!r.created ? `Already following ${label}`
      : r.offline ? `Following ${label}. Start the feed service to load its posts`
      : r.error ? `Following ${label}, but: ${r.error}`
      : `Following ${label} · ${r.new} post${r.new === 1 ? "" : "s"}`);
    if ($("follow-handle")) $("follow-handle").value = "";
  } catch (e) { toast(e.message); }
  fol.busy = false;
  fol.adding ??= true;  // keep the form open for the next one until Done
  await loadFollowing();
  await loadFollowPosts();
  $("follow-handle")?.focus({ preventScroll: true });
}

async function unfollow(id) {
  const f = fol.info?.follows.find((x) => x.id === id);
  if (!f) return;
  try { await api(`/api/following/${id}`, { method: "DELETE" }); toast(`Unfollowed ${f.label}`); }
  catch (e) { toast(e.message); }
  await loadFollowing();
  await loadFollowPosts();
}

async function saveXToken(remove = false) {
  const value = remove ? null : $("x-token")?.value.trim();
  if (!remove && !value) return toast("Paste the auth_token value first");
  if (remove && !confirm("Remove the X login token? Accounts you follow on X stop updating.")) return;
  try {
    const r = await api("/api/following/settings", { method: "PUT", body: JSON.stringify({ values: { TWITTER_AUTH_TOKEN: value } }) });
    if ($("x-token")) $("x-token").value = "";
    fol.info = r;
    toast(remove ? "X token removed" : r.restarted ? "Token saved. Restarting the feed service to use it" : "Token saved");
    if (r.restarted) startServicePoll();
  } catch (e) { return toast(e.message); }
  renderFollowPanel();
  followHeader();
}

async function startService() {
  try {
    fol.info.service = await api("/api/following/service/start", { method: "POST" });
  } catch (e) { return toast(e.message); }
  renderFollowPanel();
  startServicePoll();
}

/* While the service starts, check on it every few seconds. Once it answers, read the feeds. */
function startServicePoll() {
  if (fol.poll) return;
  fol.poll = setInterval(async () => {
    if (document.hidden || !fol.info) return;
    let s;
    try { s = await api("/api/following/service"); } catch { return; }
    const was = fol.info.service;
    fol.info.service = s;
    if (s.starting) return renderFollowPanel();
    stopServicePoll();
    if (s.running && !was.running) {
      toast("The feed service is running");
      await refreshFollowing(true);
    } else if (s.running) {
      await refreshFollowing(true);  // restarted with a new token
    } else {
      renderFollowPanel();
      followHeader();
      renderFeed();
    }
  }, 2500);
}
function stopServicePoll() { clearInterval(fol.poll); fol.poll = null; }

$("follow").addEventListener("click", (e) => {
  const pick = e.target.closest("[data-kind-pick]");
  if (pick) {
    fol.kind = pick.dataset.kindPick;
    renderFollowPanel();
    return $("follow-handle")?.focus({ preventScroll: true });
  }
  const plat = e.target.closest("[data-plat]");
  if (plat) {
    fol.platform = plat.dataset.plat;
    fol.kind = "account";
    store("follow-platform", fol.platform);
    renderFollowPanel();
    return $("follow-handle")?.focus({ preventScroll: true });
  }
  const un = e.target.closest("[data-unfollow]");
  if (un) return unfollow(+un.dataset.unfollow);
  const fy = e.target.closest("[data-fy]");
  if (fy) return forYouAct(fy.dataset.fy, fy.dataset.fyp);
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "start") startService();
  else if (act === "save-token") saveXToken();
  else if (act === "remove-token") saveXToken(true);
  else if (act === "adding") {
    fol.adding = !(fol.adding ?? !fol.info.follows.length);
    renderFollowPanel();
    if (fol.adding) $("follow-handle")?.focus({ preventScroll: true });
  }
});
$("follow").addEventListener("submit", (e) => {
  e.preventDefault();
  if (e.target.id === "follow-add") followAccount(e.target);
});
$("follow").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.id === "x-token") { e.preventDefault(); saveXToken(); }
});

/* ---------- accounts ---------- */
const accts = { list: [], providers: [], open: new Set(), busy: new Set() };

async function openAccounts(nav = "push", focus = null) {
  state.searchAbort?.abort();
  closeViewer();
  setHash(focus ? `#accounts=${encodeURIComponent(focus)}` : "#accounts", nav);
  Object.assign(state, { view: "accounts", topic: null, posts: [], hasMore: false, newKeys: new Set() });
  $("feed").innerHTML = "";
  $("q").value = "";
  $("save").disabled = true;
  markNav();
  showErrors();
  setHeader("Accounts", "Connect the platforms you want SocialAll to search.");
  if (focus && PLATFORMS[focus]) accts.open.add(focus);
  await loadAccounts();
  if (focus && PLATFORMS[focus]) {
    $(`acct-${focus}`)?.scrollIntoView({ block: "start", behavior: reduceMotion.matches ? "auto" : "smooth" });
    const p = accts.list.find((x) => x.name === focus);
    if (p?.test) toast(p.test.ok ? `${p.label}: ${p.account || "connected"}` : `${p.label}: ${p.test.message}`);
    setHash("#accounts", "replace"); // a reload shouldn't toast again
  }
}

async function loadAccounts() {
  try {
    const r = await api("/api/accounts");
    accts.list = r.platforms;
    accts.providers = r.providers;
    const n = r.platforms.filter((p) => p.connected).length;
    $("acct-intro").innerHTML = `${icon("lock")}<span>Keys and sign-ins are saved in ${esc(r.store)}, never in the page. Keys in <code>.env</code> still work for any platform you don't set up here.</span>`;
    setHeader("Accounts", `${n} of ${r.platforms.length} platforms connected`);
    renderAccounts();
  } catch (e) { toast(`Couldn't load accounts: ${e.message}`); }
}

function statusHtml(p) {
  if (p.via) {
    const v = accts.providers.find((x) => x.name === p.via);
    const t = v?.test;
    return t && !t.ok ? `<p class="acct-status bad">${icon("alert")}<span>Through ${esc(v.label)}, which isn't working: ${esc(t.message)}</span></p>`
      : `<p class="acct-status ok">${icon("check")}<span>Searching through ${esc(v?.label || p.via)}</span></p>`;
  }
  const src = p.source === "env" ? ` <span class="src">· from .env</span>` : p.source === "mixed" ? ` <span class="src">· app and .env</span>` : "";
  const exp = p.expires_in_days != null && p.expires_in_days < 14 ? ` · sign-in expires in ${p.expires_in_days} day${p.expires_in_days === 1 ? "" : "s"}` : "";
  if (p.connected && p.test?.ok) return `<p class="acct-status ok">${icon("check")}<span>Connected${p.account ? ` · ${esc(p.account)}` : ""}${esc(exp)}${src}</span></p>`;
  if (p.connected && p.test && !p.test.ok) return `<p class="acct-status bad">${icon("alert")}<span>Not working: ${esc(p.test.message)}${src}</span></p>`;
  if (p.connected) return `<p class="acct-status">${icon("check")}<span>Keys saved, not tested yet${esc(exp)}${src}</span></p>`;
  if (p.test?.ok) return `<p class="acct-status">${icon("check")}<span>Signed in. Fill in the rest to finish</span></p>`;
  if (p.test && !p.test.ok) return `<p class="acct-status bad">${icon("alert")}<span>${esc(p.test.message)}</span></p>`;
  return `<p class="acct-status">Not connected</p>`;
}

function fieldHtml(p, f) {
  const id = `f-${p.name}-${f.key}`;
  const from = f.source === "env" ? "In .env" : "Saved";
  const placeholder = f.secret ? (f.set ? `${from} ${f.value}. Type to replace` : f.placeholder || "Paste here") : f.placeholder;
  const value = !f.secret && f.source === "app" ? f.value || "" : "";
  const envNote = !f.secret && f.source === "env" ? ` (now from .env: ${f.value})` : "";
  return `<label class="field" for="${id}"><span>${esc(f.label)}${f.required ? "" : " <em>optional</em>"}</span>
    <input id="${id}" name="${esc(f.key)}" type="${f.secret ? "password" : "text"}" value="${esc(value)}" data-initial="${esc(value)}"
      placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false" ${f.secret ? 'data-secret="1"' : ""}>
    ${f.help || envNote ? `<small>${esc(f.help)}${esc(envNote)}</small>` : ""}</label>`;
}

function resultHtml(p) {
  if (!p.test) return "";
  const at = p.test.at ? new Date(p.test.at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
  return `<p class="result ${p.test.ok ? "ok" : "bad"}">${icon(p.test.ok ? "check" : "alert")}<span>${p.test.ok ? `Last test worked${p.account ? `: ${esc(p.account)}` : ""}` : esc(p.test.message)} ${at ? `<span class="at">· ${esc(at)}</span>` : ""}</span></p>`;
}

function accountHtml(p) {
  const open = accts.open.has(p.name);
  const busy = accts.busy.has(p.name);
  const o = p.oauth;
  const signin = o ? `<section class="acct-section">
      <div><h3>Sign in with ${esc(p.label === "Instagram" ? "Facebook (Meta)" : p.label)}</h3>
      <p class="lead">${esc(p.label)} needs a developer app of your own. Set it up once, then sign in here and SocialAll keeps the token.</p></div>
      <ol class="steps">${o.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
      <div class="copy-row"><code>${esc(o.redirect_uri)}</code><button class="btn ghost sm" type="button" data-act="copy" data-copy="${esc(o.redirect_uri)}">${icon("copy")}<span>Copy</span></button></div>
      <div class="fields">${o.app_fields.map((f) => fieldHtml(p, f)).join("")}</div>
      <div class="row"><button class="btn" type="button" data-act="signin"${busy ? " disabled" : ""}>Sign in with ${esc(p.label === "Instagram" ? "Facebook" : p.label)}</button>
        <a class="linkish" href="${esc(o.console_url)}" target="_blank" rel="noopener">Open the developer console</a></div>
    </section>` : "";
  const manual = `<section class="acct-section">
      <div><h3>${o ? "Or paste keys" : "Keys"}</h3><p class="lead">${esc(p.notes)}</p></div>
      <div class="fields">${p.fields.map((f) => fieldHtml(p, f)).join("")}</div>
      <div class="row"><button class="btn${o ? " ghost" : ""}" type="submit"${busy ? " disabled" : ""}>Save and test</button>
        ${p.docs_url ? `<a class="linkish" href="${esc(p.docs_url)}" target="_blank" rel="noopener">Where to get these</a>` : ""}</div>
    </section>`;
  const canDisconnect = p.official && (p.source === "app" || p.source === "mixed" || p.method);
  return `<article class="acct${open ? " is-open" : ""}" id="acct-${esc(p.name)}" data-name="${esc(p.name)}">
    <div class="acct-head" data-p="${esc(p.name)}">
      <span class="dot"></span>
      <div class="acct-name"><h2>${esc(p.label)}</h2>${statusHtml(p)}</div>
      <div class="acct-actions">
        ${p.official ? `<button class="btn ghost sm${busy ? " is-busy" : ""}" type="button" data-act="test"${busy ? " disabled" : ""}>${icon("refresh")}<span>Test</span></button>` : ""}
        <button class="btn ghost sm" type="button" data-act="toggle" aria-expanded="${open}" aria-controls="acct-body-${esc(p.name)}">${open ? "Close" : p.official ? "Manage" : "Use its own API"}</button>
      </div>
    </div>
    <form class="acct-body" id="acct-body-${esc(p.name)}"${open ? "" : " hidden"}>
      ${signin}${manual}
      ${resultHtml(p)}
      ${canDisconnect ? `<div class="row"><span class="spacer"></span><button class="linkish danger-link" type="button" data-act="disconnect">Disconnect ${esc(p.label)}</button></div>` : ""}
    </form>
  </article>`;
}

/* One-key services. Apify is free ($5 of usage a month); SocialCrawl is paid with a free trial. */
const PROVIDER_UI = {
  apify: {
    tag: "Free", title: "Free: one Apify key",
    lead: "Apify runs public-post scrapers for you. Its free plan gives $5 of usage every month with no card, and when that's used up searches pause until next month. You're never charged. None of your social accounts are used.",
    steps: (v) => [
      `Make a free account at <a href="${esc(v.signup_url)}" target="_blank" rel="noopener">apify.com</a> (Google sign-in works). No card needed.`,
      `Open <b>Settings → API &amp; Integrations</b> and copy your <b>Personal API token</b>.`,
      `Paste it here and press <b>Save and test</b>.`,
    ],
    keysLink: "Open Apify settings",
    cost: { x: 0.01, instagram: 0.03, tiktok: 0.03, linkedin: 0.02, xiaohongshu: 0.06 },
    estimate: (n) => n ? `One search across these uses about <b>$${n.toFixed(2)}</b> of your free $5, so roughly <b>${Math.floor(5 / n)} searches a month</b>. Each platform asks for 10 posts, and results take up to a minute because a scraper runs for each one.` : "Tick at least one platform.",
    extra: "Xiaohongshu starts off: Apify's scrapers for it need a paid plan. Repeat searches within 5 minutes are free. Saved topics refresh by themselves, one topic a day, which uses about 30 searches a month.",
    fine: "The scrapers come from different authors in the Apify Store and collect public posts without the platforms' permission. If one stops working, tell Claude and it can switch to another.",
    openByDefault: true,
  },
  socialcrawl: {
    tag: "Paid", title: "SocialCrawl: one key, all five (paid)",
    lead: "A paid search service with 100 free credits to try. Use it if you need Xiaohongshu or more searches than Apify's free allowance.",
    steps: (v) => [
      `Make an account at <a href="${esc(v.signup_url)}" target="_blank" rel="noopener">socialcrawl.dev</a> (100 free credits, no card).`,
      `In its dashboard, open <b>API Keys</b> and copy your key.`,
      `Paste it here and press <b>Save and test</b>.`,
    ],
    keysLink: "Open the SocialCrawl dashboard",
    cost: { x: 1, instagram: 1, tiktok: 1, linkedin: 5, xiaohongshu: 50 },
    estimate: (n) => `One search across these uses about <b>${n} credit${n === 1 ? "" : "s"}</b> (Xiaohongshu is 5 per note, 10 notes). After the free 100, credits are about £15 for 2,500.`,
    extra: "",
    fine: "SocialCrawl collects public posts without the platforms' permission, and it can see what you search for.",
    openByDefault: false,
  },
};
const costSum = (name, picked) => picked.reduce((t, p) => t + (PROVIDER_UI[name].cost[p] || 0), 0);

function providerHtml(v) {
  const ui = PROVIDER_UI[v.name];
  if (!ui) return "";
  const open = accts.open.has(v.name) || (ui.openByDefault && !v.connected && !accts.open.has(`-${v.name}`));
  const busy = accts.busy.has(v.name);
  const t = v.test;
  const status = !v.connected ? `<p class="acct-status">Not set up${ui.openByDefault ? " · takes about two minutes" : ""}</p>`
    : t && !t.ok ? `<p class="acct-status bad">${icon("alert")}<span>Not working: ${esc(t.message)}</span></p>`
    : t?.ok ? `<p class="acct-status ok">${icon("check")}<span>Connected${v.account ? ` · ${esc(v.account)}` : ""}</span></p>`
    : `<p class="acct-status">${icon("check")}<span>Key saved, not tested yet</span></p>`;
  const picked = v.platforms.filter((p) => p.enabled).map((p) => p.name);
  return `<article class="acct provider${ui.openByDefault ? " featured" : ""}${open ? " is-open" : ""}" id="acct-${esc(v.name)}" data-name="${esc(v.name)}" data-provider="1">
    <div class="acct-head">
      <span class="tag${ui.openByDefault ? "" : " muted"}">${esc(ui.tag)}</span>
      <div class="acct-name"><h2>${esc(ui.title)}</h2>${status}</div>
      <div class="acct-actions">
        ${v.connected ? `<button class="btn ghost sm${busy ? " is-busy" : ""}" type="button" data-act="test"${busy ? " disabled" : ""}>${icon("refresh")}<span>Test</span></button>` : ""}
        <button class="btn ghost sm" type="button" data-act="toggle" aria-expanded="${open}">${open ? "Close" : v.connected ? "Manage" : "Set up"}</button>
      </div>
    </div>
    <form class="acct-body"${open ? "" : " hidden"}>
      <section class="acct-section">
        <p class="lead">${esc(ui.lead)}</p>
        <ol class="steps big">${ui.steps(v).map((x) => `<li>${x}</li>`).join("")}</ol>
        <div class="fields one">${v.fields.map((f) => fieldHtml({ name: v.name }, f)).join("")}</div>
        <fieldset class="pick"><legend>Search these through ${esc(v.label)}</legend>
          ${v.platforms.map((p) => `<label data-p="${esc(p.name)}"><input type="checkbox" name="pv-platform" value="${esc(p.name)}"${p.enabled ? " checked" : ""}><span class="dot"></span>${esc(p.label)}<small>${esc(p.cost)}</small></label>`).join("")}
        </fieldset>
        <p class="note estimate">${ui.estimate(costSum(v.name, picked))}</p>
        ${ui.extra ? `<p class="note">${esc(ui.extra)}</p>` : ""}
        <div class="row"><button class="btn${ui.openByDefault ? "" : " ghost"}" type="submit"${busy ? " disabled" : ""}>Save and test</button>
          <a class="linkish" href="${esc(v.keys_url)}" target="_blank" rel="noopener">${esc(ui.keysLink)}</a></div>
        ${resultHtml(v)}
        <p class="note fine">${esc(ui.fine)} A platform you connect with its own API below uses that instead.</p>
        ${v.connected ? `<div class="row"><span class="spacer"></span><button class="linkish danger-link" type="button" data-act="disconnect">Remove ${esc(v.label)} key</button></div>` : ""}
      </section>
    </form>
  </article>`;
}

function renderAccounts() {
  if (state.view !== "accounts") return;
  // Keep what the user is typing when a card re-renders.
  const typed = {};
  for (const el of $("acct-list").querySelectorAll("input:not([type=checkbox])")) if (el.value !== el.dataset.initial) typed[el.id] = el.value;
  $("acct-list").innerHTML = accts.providers.map(providerHtml).join("")
    + `<h2 class="acct-group">Each platform's own API</h2>`
    + accts.list.map(accountHtml).join("");
  for (const [id, v] of Object.entries(typed)) { const el = $(id); if (el) el.value = v; }
}

const providerLabel = (name) => accts.providers.find((x) => x.name === name)?.label || name;
function setProvider(v) { accts.providers = accts.providers.map((x) => (x.name === v.name ? v : x)); }

async function saveProvider(name, form) {
  const values = changedValues(form);
  values[`${name.toUpperCase()}_PLATFORMS`] = [...form.querySelectorAll("input[name=pv-platform]:checked")].map((x) => x.value).join(",");
  await withBusy(name, async () => {
    try {
      const v = await api(`/api/accounts/provider/${name}`, { method: "PUT", body: JSON.stringify({ values }) });
      setProvider(v);
      if (v.connected) await testProvider(name, false);
      else toast(`Paste your ${v.label} key first`);
    } catch (e) { toast(e.message); }
  });
  await loadAccounts();
  afterAccountChange();
}

async function testProvider(name, busy = true) {
  const run = async () => {
    try {
      const r = await api(`/api/accounts/provider/${name}/test`, { method: "POST" });
      setProvider(r.provider);
      toast(r.ok ? `${r.provider.label} works${r.account ? `: ${r.account}` : ""}` : `${r.provider.label}: ${r.message}`);
    } catch (e) { toast(e.message); }
  };
  if (!busy) return run();
  await withBusy(name, run);
  await loadAccounts();
}

async function removeProvider(name) {
  const label = providerLabel(name);
  if (!confirm(`Remove your ${label} key? Platforms that searched through it stop until you add a key again.`)) return;
  try { setProvider(await api(`/api/accounts/provider/${name}`, { method: "DELETE" })); toast(`${label} key removed`); }
  catch (e) { toast(e.message); }
  await loadAccounts();
  afterAccountChange();
}

function changedValues(form) {
  const values = {};
  for (const el of form.querySelectorAll("input[name]:not([type=checkbox])")) {
    const v = el.value.trim();
    if (el.dataset.secret ? v : v !== el.dataset.initial.trim()) values[el.name] = v || null;
  }
  return values;
}

function replaceAccount(p) {
  accts.list = accts.list.map((x) => (x.name === p.name ? p : x));
}

async function withBusy(name, fn) {
  accts.busy.add(name);
  renderAccounts();
  try { return await fn(); } finally { accts.busy.delete(name); renderAccounts(); }
}

async function saveAccount(name, form, test = true) {
  const values = changedValues(form);
  return withBusy(name, async () => {
    try {
      if (Object.keys(values).length) {
        replaceAccount(await api(`/api/accounts/${name}`, { method: "PUT", body: JSON.stringify({ values }) }));
        for (const el of form.querySelectorAll("input[name]")) el.dataset.initial = el.value = el.dataset.secret ? "" : el.value.trim();
      }
      if (test) await testAccount(name, false);
      return true;
    } catch (e) { toast(e.message); return false; }
  }).finally(afterAccountChange);
}

async function testAccount(name, busy = true) {
  const run = async () => {
    try {
      const r = await api(`/api/accounts/${name}/test`, { method: "POST" });
      replaceAccount(r.platform);
      const label = PLATFORMS[name] || name;
      toast(r.ok ? `${label} works${r.account ? `: ${r.account}` : ""}` : `${label}: ${r.message}`);
    } catch (e) { toast(e.message); }
  };
  if (!busy) return run();
  await withBusy(name, run);
  afterAccountChange();
}

async function disconnectAccount(name) {
  const label = PLATFORMS[name] || name;
  if (!confirm(`Disconnect ${label}? SocialAll forgets the keys and sign-in it saved. Your app's ID and secret are kept so you can sign in again.`)) return;
  try {
    const p = await api(`/api/accounts/${name}`, { method: "DELETE" });
    replaceAccount(p);
    toast(p.connected ? `Removed. ${label} still has keys in .env` : `${label} disconnected`);
  } catch (e) { toast(e.message); }
  renderAccounts();
  afterAccountChange();
}

function afterAccountChange() {
  loadSources().then(() => { if (state.view === "accounts") setHeader("Accounts", `${accts.list.filter((p) => p.connected).length} of ${accts.list.length} platforms connected`); }).catch(() => {});
}

$("acct-list").addEventListener("click", async (e) => {
  const card = e.target.closest(".acct");
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!card || !act) return;
  const name = card.dataset.name;
  const form = card.querySelector("form");
  const isProvider = !!card.dataset.provider;
  if (isProvider && act !== "toggle") {
    if (act === "test") return testProvider(name);
    if (act === "disconnect") return removeProvider(name);
    return;
  }
  if (act === "toggle") {
    const wasOpen = card.classList.contains("is-open");
    accts.open.delete(name); accts.open.delete(`-${name}`);
    if (!wasOpen) accts.open.add(name);
    else if (isProvider) accts.open.add(`-${name}`); // remember that an open-by-default card was closed
    renderAccounts();
    if (accts.open.has(name)) $(`acct-${name}`).querySelector("input")?.focus({ preventScroll: true });
  } else if (act === "test") testAccount(name);
  else if (act === "disconnect") disconnectAccount(name);
  else if (act === "copy") {
    try { await navigator.clipboard.writeText(e.target.closest("[data-copy]").dataset.copy); toast("Callback URL copied"); }
    catch { toast("Couldn't copy. Select the URL and copy it"); }
  } else if (act === "signin") {
    if (!(await saveAccount(name, form, false))) return;
    const p = accts.list.find((x) => x.name === name);
    if (!p?.oauth?.ready) return toast(`Fill in your ${p.label} app's ID${p.oauth.app_fields.some((f) => f.key.endsWith("SECRET") && f.required) ? " and secret" : ""} first`);
    location.href = `/api/accounts/${name}/signin`; // comes back to #accounts=<name>
  }
});
$("acct-list").addEventListener("submit", (e) => {
  e.preventDefault();
  const card = e.target.closest(".acct");
  if (card?.dataset.provider) saveProvider(card.dataset.name, e.target);
  else if (card) saveAccount(card.dataset.name, e.target);
});
$("acct-list").addEventListener("change", (e) => {
  if (e.target.name !== "pv-platform") return;
  const form = e.target.form;
  const name = form.closest(".acct").dataset.name;
  const picked = [...form.querySelectorAll("input[name=pv-platform]:checked")].map((x) => x.value);
  const note = form.querySelector(".note.estimate");
  if (note) note.innerHTML = PROVIDER_UI[name].estimate(costSum(name, picked));
});
$("nav-accounts").addEventListener("click", () => openAccounts());
$("manage-sources").addEventListener("click", () => openAccounts());
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-acct]");
  if (b && !b.closest("#acct-list")) openAccounts("push", b.dataset.acct || null);
});

/* ---------- events (delegated) ---------- */
$("search").addEventListener("submit", (e) => { e.preventDefault(); runSearch($("q").value); });
$("q").addEventListener("input", () => { $("save").disabled = !$("q").value.trim(); });
$("save").addEventListener("click", async () => {
  const name = $("q").value.trim();
  if (!name) return;
  try {
    const t = await api("/api/topics", { method: "POST", body: JSON.stringify({ name }) });
    await loadTopics();
    // Keep the results already on screen instead of searching again.
    if (state.view === "search" && state.query.toLowerCase() === name.toLowerCase() && state.posts.length) {
      await api(`/api/topics/${t.id}/refresh`, { method: "POST" });
      const topics = await loadTopics();
      // Replace, so Back doesn't re-run the search that was just saved.
      return openTopic(topics.find((x) => x.id === t.id) || t, "replace");
    }
    openTopic(t, "replace");
  } catch (e) { toast(e.message); }
});
$("refresh").addEventListener("click", refreshTopic);

let findTimer;
$("find-q").addEventListener("input", (e) => {
  clearTimeout(findTimer);
  findTimer = setTimeout(() => {
    state.findQuery = e.target.value.trim().toLowerCase();
    state.view === "following" ? loadFollowPosts() : applyFilters();
  }, state.view === "following" ? 300 : 200);
});
$("find-q").addEventListener("keydown", (e) => {
  if (e.key === "Escape" && e.target.value) {
    e.stopPropagation(); e.preventDefault(); e.target.value = ""; state.findQuery = "";
    state.view === "following" ? loadFollowPosts() : applyFilters();
  }
});

/* Rename a topic in place. Enter saves, Esc or leaving the field cancels. */
function editTopic(li, t) {
  if (!li || !t || li.querySelector(".topic-form")) return;
  li.innerHTML = `<form class="topic-form"><input value="${esc(t.name)}" aria-label="Topic name" required></form>`;
  const input = li.querySelector("input");
  let done = false;
  const finish = () => { if (!done) { done = true; loadTopics(); } };
  input.focus();
  input.select();
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); finish(); } });
  input.addEventListener("blur", finish);
  li.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = input.value.trim();
    if (!name || name === t.name) return finish();
    try {
      const updated = await api(`/api/topics/${t.id}`, { method: "PATCH", body: JSON.stringify({ name }) });
      if (state.view === "topic" && state.topic?.id === t.id) {
        state.topic = { ...state.topic, ...updated };
        state.query = updated.name;
        $("q").value = updated.name;
        setHeader(updated.name, topicSub(state.topic), true);
      }
      toast("Renamed. The next refresh searches for the new name");
    } catch (err) { toast(err.message); }
    finish();
  });
}
$("more-btn").addEventListener("click", () => loadMore());
$("nav-favs").addEventListener("click", () => openFavorites());
$("nav-following").addEventListener("click", () => openFollowing());

$("topics").addEventListener("click", async (e) => {
  const edit = e.target.closest("[data-edit]");
  if (edit) return editTopic(edit.closest(".topic"), state.topicList.find((t) => t.id === +edit.dataset.edit));
  const del = e.target.closest("[data-del]");
  if (del) {
    const id = +del.dataset.del;
    await api(`/api/topics/${id}`, { method: "DELETE" });
    if (state.view === "topic" && state.topic?.id === id) goHome("replace");
    return loadTopics();
  }
  const b = e.target.closest(".topic-btn");
  if (b) openTopic(state.topicList.find((t) => t.id === +b.dataset.id));
});

$("feed").addEventListener("click", (e) => {
  const s = e.target.closest("[data-suggest]");
  if (s) return runSearch(s.dataset.suggest);
  const tile = e.target.closest(".tile");
  if (!tile) return;
  const p = state.posts[+tile.dataset.i];
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "fav") toggleFav(p);
  else if (act === "retry") retry(p);
  else if (act === "open") openViewer(+tile.dataset.i);
});
$("feed").addEventListener("load", (e) => { if (e.target.tagName === "IMG") e.target.removeAttribute("data-loading"); }, true);
$("feed").addEventListener("error", (e) => {
  if (e.target.tagName === "IMG") { e.target.closest(".frame")?.classList.add("empty"); e.target.remove(); }
}, true);

$("chips").addEventListener("click", (e) => {
  const c = e.target.closest(".chip");
  if (!c) return;
  const k = c.dataset.p;
  state.hiddenPlatforms.has(k) ? state.hiddenPlatforms.delete(k) : state.hiddenPlatforms.add(k);
  c.setAttribute("aria-pressed", String(!state.hiddenPlatforms.has(k)));
  applyFilters();
});
$("sort").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sort]");
  if (b) setSort(b.dataset.sort);
});
$("kind").addEventListener("click", (e) => {
  const b = e.target.closest("[data-kind]");
  if (!b) return;
  state.kind = b.dataset.kind;
  for (const x of $("kind").children) x.setAttribute("aria-pressed", String(x === b));
  applyFilters();
});

viewer.addEventListener("click", (e) => {
  if (e.target.closest("#viewer-close")) return closeViewer();
  if (e.target.closest("#post-prev")) return stepPost(-1);
  if (e.target.closest("#post-next")) return stepPost(1);
  const p = state.posts[state.viewerIndex];
  const img = e.target.closest("[data-img]");
  const go = e.target.closest("[data-goto]");
  if (img || go) {
    state.imageIndex = go ? +go.dataset.goto : Math.min(p.images.length - 1, Math.max(0, state.imageIndex + +img.dataset.img));
    return renderStage();
  }
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "fav") toggleFav(p);
  else if (act === "retry") retry(p);
});
viewer.addEventListener("close", () => {
  const last = state.viewerIndex;
  $("stage").innerHTML = "";
  state.viewerIndex = -1;
  // j/k in the viewer moves between posts, so return focus to the last one viewed.
  tileAt(last)?.querySelector(".tile-open")?.focus({ preventScroll: true });
  tileAt(last)?.scrollIntoView({ block: "nearest" });
});

/* ---------- shortcut sheet + phone library panel ---------- */
const keys = $("keys");
let keysOpener = null;
function openKeys() {
  if (keys.open || viewer.open) return;
  keysOpener = document.activeElement;
  keys.showModal();
}
keys.addEventListener("close", () => keysOpener?.focus?.({ preventScroll: true }));
keys.addEventListener("click", (e) => { if (e.target === keys || e.target.closest("[data-close]")) keys.close(); });
$("open-keys").addEventListener("click", openKeys);
$("library").addEventListener("click", () => {
  const open = $("rail").classList.toggle("is-open");
  $("library").setAttribute("aria-expanded", String(open));
});

document.addEventListener("keydown", (e) => {
  if (keys.open) return; // the dialog's own Esc closes it
  if (viewer.open) {
    const p = state.posts[state.viewerIndex];
    if (e.target.tagName === "VIDEO" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    const multi = p?.media_type === "image" && p.images.length > 1;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const d = e.key === "ArrowRight" ? 1 : -1;
      const k = state.imageIndex + d;
      if (multi && k >= 0 && k < p.images.length) { state.imageIndex = k; renderStage(); }
      else stepPost(d);
      e.preventDefault();
    } else if (e.key === "ArrowDown" || e.key === "j") { stepPost(1); e.preventDefault(); }
    else if (e.key === "ArrowUp" || e.key === "k") { stepPost(-1); e.preventDefault(); }
    else if (e.key === "f") toggleFav(p);
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
  if (e.key === "/") { e.preventDefault(); $("q").focus(); }
  else if (e.key === "?") { e.preventDefault(); openKeys(); }
  else if (state.view === "accounts") return;
  else if (e.key === "j" || e.key === "k") { focusTile(e.key === "j" ? 1 : -1); e.preventDefault(); }
  else if (e.key === "f") {
    const tile = document.activeElement?.closest?.("#feed .tile");
    if (tile) toggleFav(state.posts[+tile.dataset.i]);
  }
});

const isTyping = (el) => el?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el?.tagName);
const tileAt = (i) => (i >= 0 ? $("feed").querySelector(`.tile[data-i="${i}"]`) : null);
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

/* j/k scan the feed: focus moves across visible tiles; Enter opens, f stars. */
function focusTile(dir) {
  const order = visibleIndexes();
  if (!order.length) return;
  const cur = document.activeElement?.closest?.("#feed .tile");
  const at = cur && !cur.hidden ? order.indexOf(+cur.dataset.i) : -1;
  const next = at < 0 ? order[0] : order[Math.min(order.length - 1, Math.max(0, at + dir))];
  const el = tileAt(next);
  el.querySelector(".tile-open").focus({ preventScroll: true });
  el.scrollIntoView({ block: "nearest", behavior: reduceMotion.matches ? "auto" : "smooth" });
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) schedulePoll(); });

function goHome(nav = "push") {
  state.searchAbort?.abort();
  setHash("", nav);
  $("q").value = "";
  $("save").disabled = true;
  Object.assign(state, { view: "home", posts: [], topic: null, query: "" });
  setHeader("Find what people are posting", "One search pulls posts from X, Instagram, Xiaohongshu, TikTok and LinkedIn.");
  markNav();
  renderFeed();
}

/* ---------- boot ---------- */
for (const x of $("sort").children) x.setAttribute("aria-pressed", String(x.dataset.sort === state.sort));
if (parseHash(location.hash).view === "home") goHome(false);
addEventListener("popstate", () => { closeViewer(); route(); });
Promise.all([loadTopics(), loadFavorites(), loadSources(), loadStorage()]).catch((e) => toast(e.message)).finally(route);
if (parseHash(location.hash).view !== "following")
  api("/api/following").then((i) => { fol.info = i; $("follow-count").textContent = i.follows.length || ""; }).catch(() => {});

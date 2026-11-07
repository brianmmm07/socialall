"use strict";

const PLATFORMS = { x: "X", instagram: "Instagram", xiaohongshu: "Xiaohongshu", tiktok: "TikTok", linkedin: "LinkedIn" };
const PAGE = 60;
const $ = (id) => document.getElementById(id);
const stored = (k, fallback) => { try { return localStorage.getItem(k) ?? fallback; } catch { return fallback; } };
const store = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

const state = {
  view: "home",        // home | search | topic | favorites
  query: "",
  topic: null,
  posts: [],           // posts in the current view, newest first
  favs: new Map(),     // key -> favourite record (cache status etc.)
  hiddenPlatforms: new Set(),
  kind: "all",
  sort: stored("sort", "recent") === "top" ? "top" : "recent",  // topic view only
  newKeys: new Set(),
  findQuery: "",       // favourites view only, lower-cased  // posts that arrived in the last refresh of this topic
  offset: 0,
  hasMore: false,
  viewerIndex: -1,
  imageIndex: 0,
  pollTimer: null,
  searchAbort: null,
};

/* ---------- helpers ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const safe = (u) => (/^(https?:)?\/\//i.test(u || "") || /^\/cache\//.test(u || "") ? u : null);
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
  const thumb = safe(p.thumbnail_url || p.images[0]);
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
  if (state.view === "favorites")
    return `<div class="empty-state"><h2>No favourites yet</h2><p>Press the star on any post to keep it here. Videos you star are saved to a folder on this computer so they play offline.</p></div>`;
  if (state.view === "home")
    return `<div class="empty-state"><h2>Start with a topic</h2><p>Type anything: a brand, a hashtag, a person, an idea. Results from every connected platform land here, newest first.</p>
      <div class="suggest">${["AI agents", "street food", "climate tech", "咖啡"].map((t) => `<button class="btn ghost" type="button" data-suggest="${esc(t)}">${esc(t)}</button>`).join("")}</div></div>`;
  return `<div class="empty-state"><h2>Nothing found</h2><p>No platform returned posts for this topic. Try a broader word, or check the sources on the left.</p></div>`;
}

function skeleton(n = 8) {
  $("feed").innerHTML = Array.from({ length: n }, () => `<div class="skel"><div class="frame"></div><div class="line"></div><div class="line"></div></div>`).join("");
  $("more").hidden = true;
}

/* Filtering only toggles visibility, so it never rebuilds the feed. */
function applyFilters() {
  let shown = 0;
  const q = state.view === "favorites" ? state.findQuery : "";
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
  $("filters").hidden = !state.posts.length;
  $("sort").hidden = state.view !== "topic";
  $("find").hidden = state.view !== "favorites";
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
    ? `${icon("alert")}<div>${entries.map(([k, v]) => `<div><b>${esc(PLATFORMS[k] || k)}</b> didn't respond: ${esc(v)}</div>`).join("")}</div>`
    : "";
}

function markNav() {
  const favs = $("nav-favs");
  state.view === "favorites" ? favs.setAttribute("aria-current", "page") : favs.removeAttribute("aria-current");
  for (const b of document.querySelectorAll(".topic-btn")) {
    const on = state.view === "topic" && state.topic && +b.dataset.id === state.topic.id;
    on ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current");
  }
}

/* ---------- views ---------- */
async function runSearch(q, fresh = false) {
  q = q.trim();
  if (!q) return;
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

async function openTopic(t) {
  state.searchAbort?.abort();
  Object.assign(state, { view: "topic", topic: t, query: t.name, offset: 0, newKeys: new Set() });
  $("q").value = t.name;
  $("save").disabled = true;
  markNav();
  setHeader(t.name, "Loading saved posts…", true);
  showErrors();
  skeleton();
  const posts = await api(postsUrl(t.id));
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
    if (state.view === "search") return await runSearch(state.query, true);
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

async function openFavorites() {
  state.searchAbort?.abort();
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
    const src = p && safe(p.media_type === "image" ? p.images[0] : p.thumbnail_url);
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
      const poster = safe(p.thumbnail_url);
      stage.innerHTML = `<video controls autoplay playsinline preload="auto" ${poster ? `poster="${esc(poster)}"` : ""} src="${esc(src)}"></video>`;
      const v = stage.querySelector("video");
      v.addEventListener("error", () => { if (local && p.video_url) v.src = p.video_url; }, { once: true });
      return;
    }
    if (safe(p.embed_url)) {
      stage.innerHTML = `<iframe src="${esc(p.embed_url)}" allow="autoplay; encrypted-media; fullscreen" allowfullscreen title="Video player"></iframe>`;
      return;
    }
  }
  if (p.media_type === "image" && p.images.length) {
    const n = p.images.length;
    const k = state.imageIndex;
    stage.innerHTML = `<img src="${esc(safe(p.images[k]))}" alt="Image ${k + 1} of ${n}">
      ${n > 1 ? `
        <button class="icon-btn img-step l" type="button" data-img="-1" aria-label="Previous image" ${k === 0 ? "disabled" : ""}>${icon("left")}</button>
        <button class="icon-btn img-step r" type="button" data-img="1" aria-label="Next image" ${k === n - 1 ? "disabled" : ""}>${icon("right")}</button>
        <div class="carousel-nav">${p.images.map((_, j) => `<button type="button" data-goto="${j}" aria-label="Image ${j + 1}" aria-current="${j === k}"></button>`).join("")}</div>` : ""}`;
    // Warm the next image so stepping through is instant.
    if (k + 1 < n && safe(p.images[k + 1])) new Image().src = p.images[k + 1];
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
  const demo = list.find((p) => p.name === "demo");
  $("sources").innerHTML = list.filter((p) => p.name !== "demo").map((p) =>
    `<li data-p="${esc(p.name)}" title="${esc(p.notes)}"><span class="dot"></span>${esc(p.label)}<span class="state${p.configured ? " ready" : ""}">${p.configured ? "Connected" : "Needs keys"}</span></li>`).join("")
    + (demo ? `<li title="${esc(demo.notes)}"><span class="dot"></span>Sample posts<span class="state ready">On</span></li>` : "");
}

async function loadStorage() {
  try {
    const s = await api("/api/favorites/storage");
    $("storage").textContent = `Video cache: ${s.files} file${s.files === 1 ? "" : "s"}, ${bytes(s.bytes)}`;
    $("storage").title = s.folder;
  } catch {}
}

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
      return openTopic(topics.find((x) => x.id === t.id) || t);
    }
    openTopic(t);
  } catch (e) { toast(e.message); }
});
$("refresh").addEventListener("click", refreshTopic);

let findTimer;
$("find-q").addEventListener("input", (e) => {
  clearTimeout(findTimer);
  findTimer = setTimeout(() => { state.findQuery = e.target.value.trim().toLowerCase(); applyFilters(); }, 200);
});
$("find-q").addEventListener("keydown", (e) => {
  if (e.key === "Escape" && e.target.value) { e.stopPropagation(); e.preventDefault(); e.target.value = ""; state.findQuery = ""; applyFilters(); }
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
$("nav-favs").addEventListener("click", openFavorites);

$("topics").addEventListener("click", async (e) => {
  const edit = e.target.closest("[data-edit]");
  if (edit) return editTopic(edit.closest(".topic"), state.topicList.find((t) => t.id === +edit.dataset.edit));
  const del = e.target.closest("[data-del]");
  if (del) {
    const id = +del.dataset.del;
    await api(`/api/topics/${id}`, { method: "DELETE" });
    if (state.view === "topic" && state.topic?.id === id) goHome();
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

document.addEventListener("keydown", (e) => {
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

function goHome() {
  Object.assign(state, { view: "home", posts: [], topic: null, query: "" });
  setHeader("Find what people are posting", "One search pulls posts from X, Instagram, Xiaohongshu, TikTok and LinkedIn.");
  markNav();
  renderFeed();
}

/* ---------- boot ---------- */
for (const x of $("sort").children) x.setAttribute("aria-pressed", String(x.dataset.sort === state.sort));
goHome();
Promise.all([loadTopics(), loadFavorites(), loadSources(), loadStorage()]).catch((e) => toast(e.message));

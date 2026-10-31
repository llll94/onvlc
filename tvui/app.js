"use strict";
/* Biblioteca do onVLC na TV.
 * Navegação pelo controle: as setas andam entre "zonas" (abas, linhas de capas, botões) e o OK abre.
 * Os dados vêm de api/library (o PC, ou a cópia guardada pelo app da Mi Box quando o PC está desligado).
 * Tocar: window.onvlc.play(...) é a ponte do app Android; no navegador do PC só mostra um aviso.
 */
const $ = id => document.getElementById(id);
// dentro do onVLC do PC (?pc): mouse em vez de controle, e "Assistir" abre no player do PC
const PC = /[?&]pc\b/.test(location.search);
let dragging = false;
function host(cmd, data) { location.href = "/pc/" + cmd + (data ? "?d=" + encodeURIComponent(JSON.stringify(data)) : ""); }
const tv = $("tv"), view = $("view");
const TABS = ["Início", "Filmes", "Séries"];

let LIB = null, RAW = "", OFFLINE = false, byId = {};
let screen = { name: "home", tab: 0 };
const stack = [];
let zones = [], zi = 0, cols = [], onFocus = () => {};
let toastTimer = 0, loadTimer = 0;

// ---------- utilidades ----------
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const img = n => n ? "img/" + encodeURIComponent(n) : "";
const hm = min => min >= 60 ? `${Math.floor(min / 60)}h${String(min % 60).padStart(2, "0")}` : `${min} min`;
function clock(sec) {
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
const gb = n => (n / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " GB";
const plural = (n, a, b) => `${n} ${n === 1 ? a : b}`;
const byTitle = (a, b) => a.title.localeCompare(b.title, "pt-BR");

function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

function fit() {
  // clientWidth, não innerWidth: no WebView da Mi Box a página de 1920 px "alarga" o innerWidth
  // (a tela tem 960 px de CSS com densidade 2) e a escala ficava em 1, com zoom dobrado
  const w = document.documentElement.clientWidth || innerWidth, h = document.documentElement.clientHeight || innerHeight;
  tv.style.transform = `scale(${Math.min(w / 1920, h / 1080)})`;
}

// ---------- dados ----------
const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
function dateLabel(d) {
  if (!d) return "";
  const [y, m, day] = d.split("-").map(Number);
  const ymd = x => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  const now = new Date();
  if (d === ymd(now)) return "hoje";
  if (d === ymd(new Date(now - 864e5))) return "ontem";
  return `${day} ${MONTHS[m - 1]}${y !== now.getFullYear() ? " " + y : ""}`;
}

function index() {
  byId = {};
  LIB.movies.forEach(m => { m.kind = "movie"; byId[m.id] = m; });
  LIB.shows.forEach(s => {
    s.kind = "show";
    byId[s.id] = s;
    s.seasons.forEach(se => se.episodes.forEach(e => { e.kind = "episode"; e.show = s; byId[e.id] = e; }));
  });
  // o que saiu da pasta continua nos assistidos e nos favoritos, como item "indisponível" (com os dados guardados)
  const gone = (id, snap) => byId[id] || byId["h:" + id] || (byId["h:" + id] = {
    kind: "gone", id: "h:" + id, realId: id, title: snap.title, year: snap.year, runtime: snap.runtime || 0,
    genres: snap.genres || [], vote: snap.vote || 0, overview: snap.overview || "", poster: snap.poster || "",
    backdrop: snap.backdrop || "", logo: snap.logo || "", frame: snap.frame || "", quality: snap.quality || "",
    ep: snap.season ? `T${snap.season}:E${snap.episode} · ${snap.epTitle || ""}` : "", audio: [], subs: [] });
  const favIds = new Set((LIB.favorites || []).map(f => f.id));
  Object.values(byId).forEach(it => { it.fav = favIds.has(it.id); });
  const seen = new Set();
  LIB.watchedList = [];
  (LIB.history || []).forEach(h => {
    if (seen.has(h.id)) return;
    seen.add(h.id);
    const it = gone(h.id, h);
    if (it.kind === "gone") it.watched = it.watched || h.date;
    LIB.watchedList.push({ it, when: h.date, ep: h.kind === "episode" ? `T${h.season}:E${h.episode}` : "" });
  });
  LIB.favList = (LIB.favorites || []).map(f => { const it = gone(f.id, f); it.fav = true; return it; });
}
const nameOf = it => it.kind === "episode" ? it.show.title : it.title;
const epLabel = e => `T${e.season}:E${e.episode}`;
function backdropOf(it) {
  if (it.kind === "episode") return it.show.backdrop ? [it.show.backdrop, false] : [it.still || it.show.frame, true];
  return it.backdrop ? [it.backdrop, false] : [it.frame, true];
}
function episodesOf(show) { return show.seasons.flatMap(se => se.episodes); }

// ---------- fundo (troca suave entre duas camadas) ----------
let bgCur = "", bgFlip = false, bgInstant = false;
function setBg(it) {
  const [src, frame] = it ? backdropOf(it) : ["", false];
  if (src === bgCur) return;
  bgCur = src;
  bgFlip = !bgFlip;
  const on = $(bgFlip ? "bg-a" : "bg-b"), off = $(bgFlip ? "bg-b" : "bg-a");
  // trocando de aba: sem a transição (ela deixava ver o fundo da aba anterior)
  [on, off].forEach(el => { el.style.transition = bgInstant ? "none" : ""; });
  on.style.backgroundImage = src ? `url("${img(src)}")` : "none";
  on.classList.toggle("frame", frame);
  on.classList.add("on");
  off.classList.remove("on");
}

// ---------- pedaços de tela ----------
function titleBlock(it) {
  const t = it.kind === "episode" ? it.show : it;
  return t.logo ? `<img src="${img(t.logo)}" alt="${esc(t.title)}">` : `<h2>${esc(t.title)}</h2>`;
}
function metaLine(it) {
  const p = [];
  const t = it.kind === "episode" ? it.show : it;
  if (t.year) p.push(`<span>${t.year}</span>`);
  if (it.kind === "show") {
    const n = episodesOf(it).length;
    p.push(`<span>${it.seasons.length > 1 ? plural(it.seasons.length, "temporada", "temporadas") : plural(n, "episódio", "episódios")}</span>`);
  } else if (it.runtime) {
    p.push(`<span>${hm(it.runtime)}</span>`);
  }
  if (t.genres && t.genres.length) p.push(`<span>${esc(t.genres.join(", "))}</span>`);
  if (t.vote) p.push(`<span><span class="star">★</span> ${t.vote.toLocaleString("pt-BR")}</span>`);
  const q = it.kind === "show" ? (episodesOf(it)[0] || {}).quality : it.quality;
  if (q) p.push(`<span class="q">${esc(q)}</span>`);
  if (it.broken) p.push(`<span class="bad">Arquivo com problema</span>`);
  if (it.kind === "gone") p.push(`<span class="bad">Indisponível</span>`);
  if (it.watched) p.push(`<span class="seen-t">✓ Assistido ${dateLabel(it.watched) === "hoje" || dateLabel(it.watched) === "ontem" ? dateLabel(it.watched) : "em " + dateLabel(it.watched)}</span>`);
  return `<div class="meta">${p.join("")}</div>`;
}
function synopsis(text) {
  if (text) return `<div class="syn">${esc(text)}</div>`;
  return `<div class="syn none">${LIB.tmdb ? "Sem sinopse." : "Sem sinopse. Com a chave do TMDB no PC aparecem capa, fundo e sinopse."}</div>`;
}
const offlineNote = () => OFFLINE ? `<div class="note">Abra o onVLC no PC para assistir. Esta é a última lista que a TV recebeu.</div>` : "";

function progressBits(it) {
  if (!it.pos || !it.dur) return "";
  const left = Math.max(1, Math.round((it.dur - it.pos) / 60));
  return `<div class="sub">Parou em ${clock(it.pos)} · faltam ${hm(left)}</div>`
    + `<div class="prog"><b style="width:${(it.pos / it.dur * 100).toFixed(1)}%"></b></div>`;
}
function card(it, wide, zone, when) {
  const k = `${zone}:${it.id}`;
  const broken = (it.broken ? `<div class="badge">Com problema</div>` : "")
    + (when ? `<div class="when">${esc(when)}${it.kind === "gone" ? " · indisponível" : ""}</div>`
      : it.kind === "gone" ? `<div class="when">Indisponível</div>` : it.watched ? `<div class="seen">✓</div>` : "");
  if (wide) {
    let src, sub;
    if (it.kind === "episode") { src = it.still || it.show.backdrop || it.show.frame; sub = `${epLabel(it)} · ${it.title}`; }
    else { src = it.backdrop || it.frame; sub = ""; }
    return `<div class="card wide${it.broken ? " broken" : ""}" data-id="${it.id}" data-k="${k}"><img src="${img(src)}" alt="">${broken}
      <div class="cap"><div class="nm">${esc(nameOf(it))}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}${progressBits(it)}</div></div>`;
  }
  const poster = it.kind === "episode" ? it.show.poster : it.poster;
  const frame = it.kind === "episode" ? (it.still || it.show.frame) : it.frame;
  const inner = poster ? `<img src="${img(poster)}" alt="">`
    : `<img src="${img(frame)}" alt=""><div class="cap"><div class="nm">${esc(nameOf(it))}</div></div>`;
  return `<div class="card poster${it.broken ? " broken" : ""}${it.kind === "gone" ? " gone" : ""}" data-id="${it.id}" data-k="${k}">${inner}${broken}</div>`;
}

// ---------- foco ----------
function setZones(list, keepKey) {
  zones = list.filter(z => z.length);
  cols = zones.map(() => 0);
  zi = Math.min(zi, zones.length - 1);
  if (keepKey) {
    zones.forEach((z, a) => z.forEach((el, b) => { if (el.dataset.k === keepKey) { zi = a; cols[a] = b; } }));
  }
  focus();
}
function current() { return zones[zi] && zones[zi][cols[zi]]; }
function focus() {
  tv.querySelectorAll(".focus").forEach(el => el.classList.remove("focus"));
  const el = current();
  if (el) el.classList.add("focus");
  onFocus(el);
}
function move(dx, dy) {
  if (!zones.length) return;
  if (dy) {
    const n = Math.max(0, Math.min(zones.length - 1, zi + dy));
    if (n === zi) return;
    // na mesma coluna aproximada: de uma linha de 1 card para outra de 7, fica no começo
    cols[n] = Math.min(cols[n], zones[n].length - 1);
    zi = n;
  }
  if (dx) cols[zi] = Math.max(0, Math.min(zones[zi].length - 1, cols[zi] + dx));
  focus();
  const el = current();
  clearTimeout(tabTimer);
  if (dx && el && el.dataset.tab !== undefined && screen.name === "home") {
    tabTimer = setTimeout(() => switchTab(+el.dataset.tab), 220);  // passando rápido por uma aba, ela não monta
  }
}
let tabTimer = 0;
function switchTab(t) {
  if (screen.name !== "home" || screen.tab === t) return;
  plUnprepare();
  screen.tab = t;
  stack.length = 0;
  zi = 0;
  renderHome(`nav:${t}`, true);
}
function scrollCards(el) {
  const strip = el.parentElement;
  if (!strip.classList.contains("cards")) return;
  const x = Math.max(0, el.offsetLeft - strip.firstElementChild.offsetLeft - 2 * (el.offsetWidth + 28));
  strip.style.transform = `translateX(${-x}px)`;
}

// ---------- telas ----------
function homeRows() {
  const rows = [];
  const cont = LIB.continue.map(id => byId[id]).filter(Boolean);
  if (cont.length) rows.push({ title: "Continuar assistindo", items: cont, wide: true });
  if (LIB.favList.length) rows.push({ title: "Favoritos", items: LIB.favList });
  const all = [...LIB.movies, ...LIB.shows];
  if (all.length > 1) rows.push({ title: "Adicionados recentemente", items: [...all].sort((a, b) => b.added - a.added).slice(0, 20) });
  if (LIB.movies.length) rows.push({ title: "Filmes", items: [...LIB.movies].sort(byTitle) });
  if (LIB.shows.length) rows.push({ title: "Séries", items: [...LIB.shows].sort(byTitle) });
  if (LIB.watchedList.length) rows.push({ title: "Vocês já assistiram", items: LIB.watchedList.map(w => w.it),
    when: LIB.watchedList.map(w => [w.ep, dateLabel(w.when)].filter(Boolean).join(" · ")) });
  const genres = {};
  all.forEach(it => (it.genres || []).forEach(g => (genres[g] = genres[g] || []).push(it)));
  Object.entries(genres).filter(([, v]) => v.length > 1).sort((a, b) => b[1].length - a[1].length).slice(0, 4)
    .forEach(([g, v]) => rows.push({ title: g, items: v }));
  return rows;
}
function gridRows(items, label) {
  const rows = [];
  for (let i = 0; i < items.length; i += 7) rows.push({ title: i ? "" : label, items: items.slice(i, i + 7) });
  return rows;
}

function renderNav(active) {
  $("nav").innerHTML = TABS.map((t, i) => `<div class="tab${i === active ? " cur" : ""}" data-k="nav:${i}" data-tab="${i}">${t}</div>`).join("");
}

// cada aba é montada uma vez (até a lista mudar) e só trocada de lugar: as capas não somem e voltam
let tabCache = { raw: null, els: [] };
function homeEl(tab) {
  if (tabCache.raw !== RAW) tabCache = { raw: RAW, els: [] };
  if (tabCache.els[tab]) return tabCache.els[tab];
  let rows;
  if (tab === 1) rows = LIB.movies.length ? gridRows([...LIB.movies].sort(byTitle), `Filmes · ${LIB.movies.length}`) : [];
  else if (tab === 2) rows = LIB.shows.length ? gridRows([...LIB.shows].sort(byTitle), `Séries · ${LIB.shows.length}`) : [];
  else rows = homeRows();
  const el = document.createElement("div");
  el.className = "home";
  el.innerHTML = `<div class="hero"></div>
    <div class="rows"><div class="rows-in">${rows.map((r, ri) => `<section class="row">
      <h3>${esc(r.title)}</h3><div class="cards">${r.items.map((it, k) => card(it, r.wide, "r" + ri, r.when && r.when[k])).join("")}</div></section>`).join("")}
      ${rows.length ? "" : `<section class="row"><h3>${tab === 1 ? "Nenhum filme ainda" : "Nenhuma série ainda"}</h3></section>`}
    </div></div>`;
  el.firstItem = rows[0] && rows[0].items[0];
  return (tabCache.els[tab] = el);
}

function renderHome(keepKey, instant) {
  screen = { name: "home", tab: screen.tab || 0 };
  renderNav(screen.tab);
  const nothing = !LIB.movies.length && !LIB.shows.length;
  if (nothing) {
    setBg(null);
    view.innerHTML = `<div class="empty"><h2>A biblioteca está vazia</h2>
      <p>Coloque filmes e séries na pasta <b>Vídeos › onVLC</b> do PC (${esc(LIB.folder)}).</p>
      <p>Eles aparecem aqui sozinhos em menos de um minuto. Séries são reconhecidas pelo "S01E02" no nome.</p></div>`;
    setZones([[...$("nav").children]], keepKey || "nav:0");
    onFocus = () => {};
    return;
  }
  const home = homeEl(screen.tab);
  view.replaceChildren(home);
  const zs = [[...$("nav").children], ...[...home.querySelectorAll(".row .cards")].map(c => [...c.children])];
  onFocus = el => {
    const rowsIn = home.querySelector(".rows-in");
    if (!el || el.classList.contains("tab")) { rowsIn.style.transform = "translateY(0)"; return; }
    rowsIn.style.transform = `translateY(${-el.closest(".row").offsetTop}px)`;
    scrollCards(el);
    hero(byId[el.dataset.id]);
  };
  if (!keepKey) zi = 1;
  bgInstant = !!instant;
  setZones(zs, keepKey);
  if (zi === 0) hero(home.firstItem);
  bgInstant = false;
}

function hero(it) {
  if (!it) return;
  setBg(it);
  const text = it.kind === "episode" ? (it.overview || it.show.overview) : it.overview;
  const box = view.querySelector(".hero");
  if (!box) return;
  box.innerHTML = `<div class="logo">${titleBlock(it)}</div>${metaLine(it)}
    ${it.kind === "episode" ? `<div class="syn" style="-webkit-line-clamp:1">${esc(epLabel(it) + " · " + it.title)}</div>` : ""}
    ${OFFLINE ? offlineNote() : synopsis(text)}`;
}

function renderMovie(it, keepKey) {
  screen = { name: "movie", id: it.id };
  setBg(it);
  const btns = [];
  if (!it.broken) {
    if (it.pos) btns.push({ label: `Continuar de ${clock(it.pos)}`, play: true, act: () => play(it, false) },
      { label: "Do começo", act: () => play(it, true) });
    else btns.push({ label: it.watched ? "Assistir de novo" : "Assistir", play: true, act: () => play(it, true) });
  }
  btns.push(favButton(it));
  view.innerHTML = `<div class="detail"><div class="body">
      <div class="logo">${titleBlock(it)}</div>${metaLine(it)}${synopsis(it.overview)}
      ${it.broken ? `<div class="note">O PC não consegue abrir este arquivo. Ele pode estar incompleto ou corrompido.</div>` : offlineNote()}
      <div class="btns">${btns.map((b, i) => `<div class="btn" data-k="b:${i}" data-b="${i}">${b.play ? '<span class="tri"></span>' : ""}${esc(b.label)}</div>`).join("")}</div>
      <dl class="facts">
        <dt>Áudio</dt><dd>${esc([...new Set(it.audio)].join(" · ") || "—")}</dd>
        <dt>Legendas</dt><dd>${esc(it.subs.join(" · ") || "nenhuma")}</dd>
        <dt>Arquivo</dt><dd class="file">${esc(it.file)} · ${gb(it.size)}</dd>
      </dl></div></div>`;
  view.actions = btns;
  onFocus = () => {};
  zi = 0;
  setZones([[...view.querySelectorAll(".btn")]], keepKey);
  plPrepare(it);
  warm(it);
}

function renderShow(show, season, keepKey) {
  const seasons = show.seasons;
  const si = Math.max(0, Math.min(seasons.length - 1, season || 0));
  screen = { name: "show", id: show.id, season: si };
  const eps = episodesOf(show);
  const started = LIB.continue.map(id => byId[id]).find(e => e && e.kind === "episode" && e.show === show);
  const first = eps.find(e => !e.broken);
  const btns = [];
  if (started) btns.push({ label: `Continuar ${epLabel(started)}`, play: true, act: () => play(started, false) });
  else if (first) btns.push({ label: `Assistir ${epLabel(first)}`, play: true, act: () => play(first, true) });
  btns.push(favButton(show));
  setBg(show);
  view.innerHTML = `<div class="detail"><div class="body" style="top:150px">
      <div class="logo" style="height:170px">${titleBlock(show)}</div>${metaLine(show)}
      <div id="ep-text">${synopsis(show.overview)}</div>${offlineNote()}
      <div class="btns">${btns.map((b, i) => `<div class="btn" data-k="b:${i}">${b.play ? '<span class="tri"></span>' : ""}${esc(b.label)}</div>`).join("")}</div>
      ${seasons.length > 1 ? `<div class="seasons">${seasons.map((se, i) => `<div class="btn${i === si ? " cur" : ""}" data-k="s:${i}" data-season="${i}">Temporada ${se.n}</div>`).join("")}</div>` : ""}
    </div>
    <div class="eps"><section class="row"><h3>${seasons.length > 1 ? `Temporada ${seasons[si].n}` : "Episódios"}</h3>
      <div class="cards">${seasons[si].episodes.map(e => episodeCard(e)).join("")}</div></section></div></div>`;
  view.actions = btns;
  onFocus = el => {
    if (el && el.classList.contains("card")) {
      scrollCards(el);
      const e = byId[el.dataset.id];
      $("ep-text").innerHTML = `<div class="syn" style="-webkit-line-clamp:1;margin-top:18px;color:#fff;font-weight:700">${esc(epLabel(e) + " · " + e.title)}</div>`
        + (e.overview ? `<div class="syn" style="margin-top:8px">${esc(e.overview)}</div>` : "");
    } else {
      $("ep-text").innerHTML = synopsis(show.overview);
    }
  };
  const zs = [[...view.querySelectorAll(".btns .btn")], [...view.querySelectorAll(".seasons .btn")], [...view.querySelectorAll(".eps .card")]];
  zi = 0;
  setZones(zs, keepKey);
  const primary = started || first;
  plPrepare(primary);
  warm(primary);
}
function episodeCard(e) {
  const src = e.still || e.show.backdrop || e.show.frame;
  return `<div class="card wide${e.broken ? " broken" : ""}" data-id="${e.id}" data-k="e:${e.id}"><img src="${img(src)}" alt="">
    ${e.broken ? `<div class="badge">Com problema</div>` : ""}
    <div class="cap"><div class="nm">${e.episode}. ${esc(e.title)}</div>
    ${e.pos ? progressBits(e) : `<div class="sub">${e.runtime ? hm(e.runtime) : ""}</div>`}</div></div>`;
}

function renderSearching() {
  renderNav(-1);
  setBg(null);
  view.innerHTML = `<div class="empty"><div class="spinner"></div><h2>Procurando o PC</h2>
    <p>Os filmes ficam no computador. Abra o <b>onVLC</b> no PC e a TV acha sozinha.</p></div>`;
  zones = [];
}

function render(keepKey) {
  if (!LIB) return renderSearching();
  const it = byId[screen.id];
  if (screen.name === "gone" && it) return renderGone(it, keepKey);
  if (screen.name === "movie" && it) return renderMovie(it, keepKey);
  if (screen.name === "show" && it) return renderShow(it, screen.season, keepKey);
  renderHome(keepKey);
}

// ---------- ações ----------
function activate() {
  const el = current();
  if (!el) return;
  if (el.dataset.tab !== undefined) {
    clearTimeout(tabTimer);
    if (screen.tab !== +el.dataset.tab) return switchTab(+el.dataset.tab);
    if (zones.length > 1) { zi = 1; cols[1] = 0; focus(); }  // OK na aba já aberta: desce para as capas
    return;
  }
  if (el.dataset.season !== undefined) {
    renderShow(byId[screen.id], +el.dataset.season, el.dataset.k);
    return;
  }
  if (el.dataset.k && el.dataset.k.startsWith("b:")) {
    view.actions[+el.dataset.k.slice(2)].act();
    return;
  }
  const it = byId[el.dataset.id];
  if (!it) return;
  if (it.kind === "episode") return play(it, false);
  stack.push({ screen: { ...screen }, key: el.dataset.k });
  if (it.kind === "gone") renderGone(it);
  else if (it.kind === "movie") renderMovie(it);
  else renderShow(it, 0);
}

// ---------- favoritos ----------
function favButton(it) {
  return { label: it.fav ? "♥ Nos favoritos" : "♡ Favoritar", act: () => toggleFav(it) };
}
function apiPost(path, body) {
  const json = JSON.stringify(body);
  if (window.onvlc && window.onvlc.post) return window.onvlc.post(path, json);  // o app manda para o PC
  return fetch(path.slice(1), { method: "POST", headers: { "Content-Type": "application/json" }, body: json });
}
function toggleFav(it) {
  const id = it.realId || it.id, on = !it.fav;
  const pick = ["kind", "title", "year", "runtime", "genres", "vote", "overview", "poster", "backdrop", "logo", "frame", "quality"];
  const item = {};
  pick.forEach(k => { if (it[k] !== undefined) item[k] = it[k]; });
  apiPost("/api/favorite", { id, on, item });
  it.fav = on;
  toast(on ? "Adicionado aos favoritos" : "Tirado dos favoritos", 1800);
  render(current() && current().dataset.k);
  setTimeout(load, 600);
}

// ---------- vídeo que saiu da pasta ----------
function renderGone(it, keepKey) {
  screen = { name: "gone", id: it.id };
  setBg(it);
  const btns = [favButton(it)];
  const seen = it.watched ? `<span class="seen-t">✓ Assistido ${["hoje", "ontem"].includes(dateLabel(it.watched)) ? dateLabel(it.watched) : "em " + dateLabel(it.watched)}</span>` : "";
  view.innerHTML = `<div class="detail"><div class="body">
      <div class="logo">${titleBlock(it)}</div>
      <div class="meta">${[it.year && `<span>${it.year}</span>`, it.ep && `<span>${esc(it.ep)}</span>`, it.runtime && !it.ep && `<span>${hm(it.runtime)}</span>`,
        it.genres.length && `<span>${esc(it.genres.join(", "))}</span>`, it.vote && `<span><span class="star">★</span> ${it.vote.toLocaleString("pt-BR")}</span>`, seen]
        .filter(Boolean).join("")}</div>
      ${synopsis(it.overview)}
      <div class="note gone"><span><b>Indisponível.</b> Este arquivo não está mais na pasta onVLC do PC. Coloque ele de volta lá para assistir.</span></div>
      <div class="btns">${btns.map((b, i) => `<div class="btn" data-k="b:${i}">${esc(b.label)}</div>`).join("")}</div>
    </div></div>`;
  view.actions = btns;
  onFocus = () => {};
  zi = 0;
  setZones([[...view.querySelectorAll(".btn")]], keepKey);
}

// ---------- kit de abertura ----------
/* Ordem de prioridade para o app guardar o começo de cada vídeo na Mi Box: o que está na frente (página aberta),
 * o "Continuar assistindo" (com o ponto onde parou), os adicionados por último, o resto. Séries: o episódio
 * em andamento ou o primeiro. */
function warm(front) {
  if (!window.onvlc || !window.onvlc.warm || !LIB) return;
  const list = [], seen = new Set();
  const add = it => { if (it && !it.broken && !seen.has(it.id)) { seen.add(it.id); list.push(it); } };
  add(front);
  LIB.continue.forEach(id => add(byId[id]));
  const firstEp = s => episodesOf(s).find(e => !e.broken);
  [...LIB.movies, ...LIB.shows].sort((a, b) => b.added - a.added)
    .forEach(it => add(it.kind === "show" ? (LIB.continue.map(id => byId[id]).find(e => e && e.show === it) || firstEp(it)) : it));
  window.onvlc.warm(JSON.stringify(list.map(it => ({ id: it.id, size: it.size, file: it.file, bps: it.bps, pos: it.pos || 0 }))));
}

function play(it, fromStart) {
  if (it.broken) return toast("O PC não consegue abrir este arquivo.");
  if (OFFLINE) return toast("O PC está desligado. Abra o onVLC no PC e tente de novo.", 3500);
  const pos = fromStart ? 0 : (it.pos || 0);
  const title = it.kind === "episode" ? `${it.show.title} · ${epLabel(it)} · ${it.title}` : it.title;
  if (PC) return host("play", { id: it.id, pos });
  if (window.onvlc) {
    openPlayer(it, Math.round(pos * 1000));  // player.js: toca aqui mesmo, com o motor do VLC
  } else {
    toast(`Na TV, isto abre no VLC: ${title}${pos ? " em " + clock(pos) : ""}`, 3500);
  }
}

function back() {
  if (PL.open) return plBack();
  if (stack.length) {
    const prev = stack.pop();
    if (screen.name === "movie" || screen.name === "show") plUnprepare();
    screen = prev.screen;
    render(prev.key);
    return true;
  }
  if (screen.name === "home" && screen.tab !== 0) {
    screen.tab = 0;
    zi = 0;
    renderHome("nav:0");
    return true;
  }
  if (screen.name === "home" && zi > 1) {  // volta para o começo antes de sair
    zi = 1;
    cols[1] = 0;
    focus();
    return true;
  }
  if (PC) { host("close"); return true; }  // no PC: volta para o vídeo aberto
  return false;  // o app fecha
}
window.tvBack = back;
window.tvRefresh = () => load();

document.addEventListener("keydown", e => {
  const k = e.key;
  if (PL.open) {
    if (k === "Escape" || k === "Backspace" || k === "BrowserBack" || k === "GoBack") plBack();
    else if (k.startsWith("Arrow") || k === "Enter" || k === " ") plKey(k);
    else return;
    e.preventDefault();
    return;
  }
  if (k === "ArrowLeft") move(-1, 0);
  else if (k === "ArrowRight") move(1, 0);
  else if (k === "ArrowUp") move(0, -1);
  else if (k === "ArrowDown") move(0, 1);
  else if (k === "Enter" || k === " " || k === "MediaPlayPause") activate();
  else if (k === "Escape" || k === "Backspace" || k === "BrowserBack" || k === "GoBack") back();
  else return;
  e.preventDefault();
});
if (PC) {
  // mouse: passar por cima foca, a rodinha anda entre as linhas
  tv.addEventListener("mouseover", e => {
    const el = e.target.closest("[data-k]");
    if (!el || el === current() || PL.open) return;
    zones.forEach((z, a) => z.forEach((x, b) => { if (x === el) { zi = a; cols[a] = b; } }));
    focus();
  });
  // clicar, segurar e arrastar move a janela do onVLC (como em cima do vídeo); um clique simples abre
  let down = null;
  addEventListener("mousedown", e => { down = e.button === 0 ? { x: e.clientX, y: e.clientY } : null; });
  addEventListener("mouseup", () => { down = null; });
  addEventListener("mousemove", e => {
    if (!down || !(e.buttons & 1)) return;
    if (Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y) > 6) { down = null; dragging = true; host("move"); }
  });
  let wheelAt = 0;
  addEventListener("wheel", e => {
    if (Date.now() - wheelAt < 180 || PL.open) return;
    wheelAt = Date.now();
    move(0, e.deltaY > 0 ? 1 : -1);
  }, { passive: true });
}
tv.addEventListener("click", e => {
  const el = e.target.closest("[data-k]");
  if (!el) return;
  if (dragging) { dragging = false; return; }  // terminou de arrastar a janela: não é clique
  const same = el === current() || PC;  // no PC um clique já abre (o foco vem com o mouse)
  zones.forEach((z, a) => z.forEach((x, b) => { if (x === el) { zi = a; cols[a] = b; } }));
  focus();
  if (same || el.classList.contains("btn") || el.classList.contains("tab")) activate();
});

// ---------- carregar ----------
async function load() {
  clearTimeout(loadTimer);
  const wasOffline = OFFLINE;
  let text = null;
  try {
    const r = await fetch("api/library", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    OFFLINE = r.headers.get("X-onVLC-Offline") === "1";
    text = await r.text();
  } catch (err) {
    OFFLINE = true;
  }
  const pc = $("pc");
  pc.textContent = OFFLINE ? "PC desligado" : "PC ligado";
  pc.classList.toggle("off", OFFLINE);
  if (text && text !== RAW) {
    RAW = text;
    LIB = JSON.parse(text);
    index();
    if (!OFFLINE) warm();
    const key = current() && current().dataset.k;
    if (!PL.open) render(key);
  } else if (!LIB) {
    renderSearching();
  } else if (OFFLINE !== wasOffline && !PL.open) {
    const key = current() && current().dataset.k;
    render(key);  // mostra ou tira o aviso de PC desligado
  }
  const busy = $("busy");
  busy.hidden = !(LIB && LIB.busy && !OFFLINE);
  if (!busy.hidden) busy.textContent = `Lendo ${plural(LIB.busy, "vídeo novo", "vídeos novos")}`;
  loadTimer = setTimeout(load, OFFLINE || !LIB || LIB.busy ? 5000 : 30000);
}

function tick() {
  const d = new Date();
  $("clock").textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
if (PC) document.documentElement.classList.add("pc");
addEventListener("resize", fit);
fit();
tick();
setInterval(tick, 15000);
renderSearching();
load();

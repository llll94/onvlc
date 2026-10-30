"use strict";
/* Biblioteca do onVLC na TV.
 * Navegação pelo controle: as setas andam entre "zonas" (abas, linhas de capas, botões) e o OK abre.
 * Os dados vêm de api/library (o PC, ou a cópia guardada pelo app da Mi Box quando o PC está desligado).
 * Tocar: window.onvlc.play(...) é a ponte do app Android; no navegador do PC só mostra um aviso.
 */
const $ = id => document.getElementById(id);
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
function index() {
  byId = {};
  LIB.movies.forEach(m => { m.kind = "movie"; byId[m.id] = m; });
  LIB.shows.forEach(s => {
    s.kind = "show";
    byId[s.id] = s;
    s.seasons.forEach(se => se.episodes.forEach(e => { e.kind = "episode"; e.show = s; byId[e.id] = e; }));
  });
}
const nameOf = it => it.kind === "episode" ? it.show.title : it.title;
const epLabel = e => `T${e.season}:E${e.episode}`;
function backdropOf(it) {
  if (it.kind === "episode") return it.show.backdrop ? [it.show.backdrop, false] : [it.still || it.show.frame, true];
  return it.backdrop ? [it.backdrop, false] : [it.frame, true];
}
function episodesOf(show) { return show.seasons.flatMap(se => se.episodes); }

// ---------- fundo (troca suave entre duas camadas) ----------
let bgCur = "", bgFlip = false;
function setBg(it) {
  const [src, frame] = it ? backdropOf(it) : ["", false];
  if (src === bgCur) return;
  bgCur = src;
  bgFlip = !bgFlip;
  const on = $(bgFlip ? "bg-a" : "bg-b"), off = $(bgFlip ? "bg-b" : "bg-a");
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
function card(it, wide, zone) {
  const k = `${zone}:${it.id}`;
  const broken = it.broken ? `<div class="badge">Com problema</div>` : "";
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
  return `<div class="card poster${it.broken ? " broken" : ""}" data-id="${it.id}" data-k="${k}">${inner}${broken}</div>`;
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
  const all = [...LIB.movies, ...LIB.shows];
  if (all.length > 1) rows.push({ title: "Adicionados recentemente", items: [...all].sort((a, b) => b.added - a.added).slice(0, 20) });
  if (LIB.movies.length) rows.push({ title: "Filmes", items: [...LIB.movies].sort(byTitle) });
  if (LIB.shows.length) rows.push({ title: "Séries", items: [...LIB.shows].sort(byTitle) });
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

function renderHome(keepKey) {
  screen = { name: "home", tab: screen.tab || 0 };
  renderNav(screen.tab);
  const nothing = !LIB.movies.length && !LIB.shows.length;
  if (nothing) {
    setBg(null);
    view.innerHTML = `<div class="empty"><h2>A biblioteca está vazia</h2>
      <p>Coloque filmes e séries na pasta <b>Vídeos › onVLC TV</b> do PC (${esc(LIB.folder)}).</p>
      <p>Eles aparecem aqui sozinhos em menos de um minuto. Séries são reconhecidas pelo "S01E02" no nome.</p></div>`;
    setZones([[...$("nav").children]], keepKey || "nav:0");
    onFocus = () => {};
    return;
  }
  let rows;
  if (screen.tab === 1) rows = LIB.movies.length ? gridRows([...LIB.movies].sort(byTitle), `Filmes · ${LIB.movies.length}`) : [];
  else if (screen.tab === 2) rows = LIB.shows.length ? gridRows([...LIB.shows].sort(byTitle), `Séries · ${LIB.shows.length}`) : [];
  else rows = homeRows();
  view.innerHTML = `<div class="hero" id="hero"></div>
    <div class="rows"><div class="rows-in" id="rows-in">${rows.map((r, ri) => `<section class="row">
      <h3>${esc(r.title)}</h3><div class="cards">${r.items.map(it => card(it, r.wide, "r" + ri)).join("")}</div></section>`).join("")}
      ${rows.length ? "" : `<section class="row"><h3>${screen.tab === 1 ? "Nenhum filme ainda" : "Nenhuma série ainda"}</h3></section>`}
    </div></div>`;
  const zs = [[...$("nav").children], ...[...view.querySelectorAll(".row .cards")].map(c => [...c.children])];
  onFocus = el => {
    const rowsIn = $("rows-in");
    if (!el || el.classList.contains("tab")) { rowsIn.style.transform = "translateY(0)"; return; }
    rowsIn.style.transform = `translateY(${-el.closest(".row").offsetTop}px)`;
    scrollCards(el);
    hero(byId[el.dataset.id]);
  };
  if (!keepKey) zi = 1;
  setZones(zs, keepKey);
  if (zi === 0) hero(rows[0] && rows[0].items[0]);
}

function hero(it) {
  if (!it) return;
  setBg(it);
  const text = it.kind === "episode" ? (it.overview || it.show.overview) : it.overview;
  $("hero").innerHTML = `<div class="logo">${titleBlock(it)}</div>${metaLine(it)}
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
    else btns.push({ label: "Assistir", play: true, act: () => play(it, true) });
  }
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
  if (screen.name === "movie" && it) return renderMovie(it, keepKey);
  if (screen.name === "show" && it) return renderShow(it, screen.season, keepKey);
  renderHome(keepKey);
}

// ---------- ações ----------
function activate() {
  const el = current();
  if (!el) return;
  if (el.dataset.tab !== undefined) {
    plUnprepare();
    screen.tab = +el.dataset.tab;
    stack.length = 0;
    zi = 0;
    renderHome(`nav:${screen.tab}`);
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
  if (it.kind === "movie") renderMovie(it);
  else renderShow(it, 0);
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
tv.addEventListener("click", e => {
  const el = e.target.closest("[data-k]");
  if (!el) return;
  const same = el === current();
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
addEventListener("resize", fit);
fit();
tick();
setInterval(tick, 15000);
renderSearching();
load();

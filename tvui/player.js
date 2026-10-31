"use strict";
/* Player do onVLC na TV.
 * O vídeo é desenhado pelo motor do VLC (app Android) atrás desta página; aqui ficam só a barra, os menus
 * e os avisos, com fundo transparente. Comandos vão por window.onvlc.*, e o estado volta a cada meio
 * segundo em window.onPlayer({state, time, length, audio, spu, cache...}).
 * Controle: OK pausa/confirma, ← → 10 s (com a barra em foco, procura com miniatura), ↑ ↓ entre barra
 * e botões, Voltar fecha painel → esconde a barra → volta para a biblioteca.
 */
const PL = {
  open: false, it: null, st: {}, ui: true, focus: "bar", btn: 0, scrub: null, panel: null, pIdx: [0, 0],
  pausedSince: 0, next: null, nextOff: false, thumbs: { n: 0, have: [] }, thumbTimer: 0, hideTimer: 0, lastKey: 0, streak: 0,
};
const ICON = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/></svg>',
  fwd: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v4h-4"/></svg>',
  cc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 10.5a2 2 0 1 0 0 3M17 10.5a2 2 0 1 0 0 3"/></svg>',
  next: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 5v14l10-7z"/><rect x="16" y="5" width="3" height="14" rx="1"/></svg>',
};

// ---------- dados do vídeo aberto ----------
const plDur = () => PL.st.length || PL.it.dur || 1;
const plTime = () => PL.scrub !== null ? PL.scrub : (PL.st.time || 0);
function plNextEpisode() {
  if (PL.it.kind !== "episode") return null;
  const eps = episodesOf(PL.it.show);
  const i = eps.indexOf(PL.it);
  return eps.slice(i + 1).find(e => !e.broken) || null;
}
function plThumb(t) {
  const have = PL.thumbs.have, n = PL.thumbs.n;
  if (!have.length || !n) return "";
  const want = Math.max(0, Math.min(n - 1, Math.floor(t / plDur() * n)));
  let best = have[0];
  for (const i of have) if (Math.abs(i - want) < Math.abs(best - want)) best = i;
  return `img/thumb_${PL.it.id}_${best}.jpg`;
}
function plButtons() {
  const b = [{ id: "pp" }, { id: "b10", icon: "back", n: "10" }, { id: "f10", icon: "fwd", n: "10" }, { sp: true },
    { id: "tracks", icon: "cc", label: "Áudio e legendas" }];
  if (plNextEpisode()) b.push({ id: "nextep", icon: "next", label: "Próximo episódio" });
  return b;
}
const plRealButtons = () => plButtons().filter(b => !b.sp);

// ---------- abrir e fechar ----------
function openPlayer(it, posMs) {
  clearTimeout(loadTimer);
  Object.assign(PL, { open: true, it, st: { state: "opening", time: posMs / 1000, length: it.dur, buffering: 0 }, ui: true, focus: "bar", btn: 0,
    scrub: null, panel: null, pausedSince: 0, next: null, nextOff: false, thumbs: { n: 0, have: [] }, subPicked: false });
  document.documentElement.classList.add("playing");
  plBuild();
  PL.req = plRequest(it, posMs);
  window.onvlc.play(PL.req);
  plPoke();
  plLoadThumbs();
  // abertura travada (aconteceu uma vez com o PC ocupado): tenta de novo uma vez
  clearTimeout(PL.watchdog);
  PL.retried = false;
  PL.watchdog = setTimeout(function retry() {
    if (!PL.open || PL.it !== it || PL.st.state !== "opening") return;
    if (PL.retried) { toast("O filme não abriu. O PC está ligado?"); return closePlayer(true); }
    PL.retried = true;
    toast("Demorou para abrir, tentando de novo…");
    window.onvlc.stop();
    window.onvlc.play(PL.req);
    PL.watchdog = setTimeout(retry, 15000);
  }, 15000);
}

/** O pedido de abrir um vídeo (o mesmo para preparar e para tocar: o app compara id e posição). */
function plRequest(it, posMs) {
  const title = it.kind === "episode" ? `${it.show.title} · ${epLabel(it)} · ${it.title}` : it.title;
  return JSON.stringify({ id: it.id, size: it.size, file: it.file, title, pos: posMs, bps: it.bps,
    subs: (it.extSubs || []).length });
}

/** Na página de um filme: o app já abre ele pausado, escondido, e o "Assistir" fica imediato. */
function plPrepare(it) {
  if (!window.onvlc || !window.onvlc.prepare || !it || it.broken || OFFLINE || PL.open) return;
  window.onvlc.prepare(plRequest(it, Math.round((it.pos || 0) * 1000)));
}
function plUnprepare() { if (window.onvlc && window.onvlc.unprepare && !PL.open) window.onvlc.unprepare(); }
function closePlayer(stopEngine) {
  if (!PL.open) return;
  PL.open = false;
  clearTimeout(PL.thumbTimer);
  clearTimeout(PL.hideTimer);
  clearTimeout(PL.watchdog);
  if (stopEngine && window.onvlc) window.onvlc.stop();
  const el = $("player");
  if (el) el.remove();
  document.documentElement.classList.remove("playing");
  render(current() && current().dataset.k);
  load();
}
window.onPlayerClosed = () => closePlayer(false);

async function plLoadThumbs(still = 0) {
  const id = PL.it.id, before = PL.thumbs.have.length;
  try {
    const r = await fetch(`api/thumbs/${id}`, { cache: "no-store" });
    if (r.ok && PL.open && PL.it.id === id) PL.thumbs = await r.json();
  } catch (e) { /* sem miniaturas: a prévia mostra só o horário */ }
  // pergunta de novo enquanto o PC está gerando; 5 vezes seguidas sem nenhuma nova (alguma falhou), desiste
  still = PL.thumbs.have.length > before ? 0 : still + 1;
  if (PL.open && PL.it.id === id && (!PL.thumbs.n || PL.thumbs.have.length < PL.thumbs.n) && still < 5) {
    PL.thumbTimer = setTimeout(() => plLoadThumbs(still), 4000);
  }
}

// ---------- estado vindo do motor ----------
window.onPlayer = st => {
  if (!PL.open || st.id !== PL.it.id) return;
  const was = PL.st.state;
  PL.st = st;
  if (st.state === "paused" && was !== "paused") PL.pausedSince = Date.now();
  if (st.state !== "paused") PL.pausedSince = 0;
  if (st.state === "error") { toast("Não consegui tocar este arquivo."); return closePlayer(true); }
  if (st.state === "ended") {
    const nx = plNextEpisode();
    if (nx && !PL.nextOff) return openPlayer(nx, 0);
    return closePlayer(true);
  }
  // próximo episódio: aparece nos últimos 25 s
  const nx = plNextEpisode(), left = (st.length || 0) - (st.time || 0);
  if (nx && !PL.nextOff && st.length && left <= 25 && st.state === "playing") {
    if (PL.next === null) { PL.next = nx; PL.btn = 0; plRenderNext(); }
    else plUpdateNext(left);
  }
  if (st.state === "playing") plAutoSub();
  plUpdate();
};

// ---------- tela ----------
function plBuild() {
  const it = PL.it, t = it.kind === "episode" ? it.show : it;
  const old = $("player");
  if (old) old.remove();
  const el = document.createElement("div");
  el.id = "player";
  const logo = t.logo ? `<img src="${img(t.logo)}" alt="">` : `<h2>${esc(t.title)}</h2>`;
  el.innerHTML = `<div class="pl-dim" id="pl-dim"></div>
    <div class="pl-paused" id="pl-paused"><div class="k">Você está assistindo</div><div class="logo">${logo}</div>
      <div class="m">${it.kind === "episode" ? `<span>${esc(epLabel(it) + " · " + it.title)}</span>` : `<span>${t.year || ""}</span><span>${esc(it.quality || "")}</span>`}</div>
      <div class="s">${esc(it.overview || t.overview || "")}</div></div>
    <div class="pl-center" id="pl-wait"><div class="spinner"></div><span id="pl-wait-t">Abrindo</span><small id="pl-wait-s"></small></div>
    <div class="pl-jump l" id="pl-jl">−10</div><div class="pl-jump r" id="pl-jr">+10</div>
    <div class="pl-ui" id="pl-ui">
      <div class="pl-top"><div><div class="logo">${logo}</div><div class="t2" id="pl-t2"></div></div>
        <div class="sp"></div><div class="pl-pill" id="pl-pill" hidden><i></i><span></span></div><div class="pl-clock" id="pl-clock"></div></div>
      <div class="pl-bottom">
        <div class="pl-barrow"><div class="pl-time" id="pl-t"></div>
          <div class="pl-bar" id="pl-bar"><div class="pl-segs"><div class="pl-seg"><div class="c"></div><div class="p"></div></div></div>
            <div class="pl-head" id="pl-head"></div><div class="pl-prev" id="pl-prev" hidden><img id="pl-prev-i" alt=""><div class="pt"><b id="pl-prev-t"></b></div></div></div>
          <div class="pl-time r" id="pl-r"></div></div>
        <div class="pl-btns" id="pl-btns"></div>
      </div></div>
    <div class="pl-panel" id="pl-panel"></div>
    <div id="pl-next"></div>`;
  tv.appendChild(el);
  plRenderButtons();
  plUpdate();
}

function plRenderButtons() {
  let i = -1;
  $("pl-btns").innerHTML = plButtons().map(b => {
    if (b.sp) return `<div class="sp"></div>`;
    i++;
    const icon = b.id === "pp" ? (PL.st.state === "paused" ? "play" : "pause") : b.icon;
    return `<div class="pl-b" data-b="${i}">${ICON[icon]}${b.n ? `<span class="n">${b.n}</span>` : ""}${b.label ? `<span>${b.label}</span>` : ""}</div>`;
  }).join("");
  plFocus();
}

function plFocus() {
  $("pl-bar").classList.toggle("focus", PL.focus === "bar" && !PL.panel);
  [...$("pl-btns").querySelectorAll(".pl-b")].forEach((b, k) => b.classList.toggle("focus", PL.focus === "btns" && PL.btn === k && !PL.panel));
}

function plUpdate() {
  if (!PL.open) return;
  const st = PL.st, d = plDur(), t = plTime();
  const seg = $("pl-bar").querySelector(".pl-seg");
  const ranges = (st.cache && st.cache.ranges) || [];
  seg.querySelector(".p").style.width = Math.max(0, Math.min(1, (st.time || 0) / d)) * 100 + "%";
  // trechos já guardados na Mi Box
  seg.querySelector(".c").innerHTML = ranges.map(([x, y]) => { x = Math.max(x, 0); y = Math.min(y, d); return y > x ? `<i style="position:absolute;top:0;bottom:0;left:${x / d * 100}%;width:${(y - x) / d * 100}%;background:rgba(255,255,255,.38)"></i>` : ""; }).join("");
  const px = Math.max(0, Math.min(1, t / d)) * 100;
  $("pl-head").style.left = px + "%";
  $("pl-t").textContent = clock(t);
  $("pl-r").textContent = "-" + clock(Math.max(0, d - t));
  // procurando: miniatura e horário
  const prev = $("pl-prev");
  prev.hidden = PL.scrub === null;
  if (PL.scrub !== null) {
    prev.style.left = px + "%";
    const src = plThumb(PL.scrub);
    const im = $("pl-prev-i");
    if (src && im.getAttribute("src") !== src) im.src = src;
    im.style.visibility = src ? "visible" : "hidden";
    $("pl-prev-t").textContent = clock(PL.scrub);
  }
  // linha de cima: ano · qualidade · áudio
  const audio = plAudioList().find(a => a.id === st.audioId);
  const it = PL.it;
  $("pl-t2").textContent = (it.kind === "episode" ? [epLabel(it) + " · " + it.title] : [it.year, it.quality])
    .concat(audio ? [audio.name] : []).filter(Boolean).join(" · ");
  const ahead = st.cache && st.cache.ahead;
  const pill = $("pl-pill");
  pill.hidden = !(ahead > 1);
  if (!pill.hidden) pill.querySelector("span").textContent = `${clock(ahead)} guardados à frente`;
  const d2 = new Date();
  $("pl-clock").textContent = `${String(d2.getHours()).padStart(2, "0")}:${String(d2.getMinutes()).padStart(2, "0")}`;
  // esperando o motor
  const waiting = st.state === "opening" || (st.buffering < 100 && st.state !== "paused");
  $("pl-wait").hidden = !waiting;
  if (waiting) {
    $("pl-wait-t").textContent = st.state === "opening" ? "Abrindo" : "Carregando este trecho";
    $("pl-wait-s").textContent = st.state === "opening" ? "" : "a Mi Box está guardando os próximos minutos";
  }
  // pausado há um tempo: escurece e mostra o que está assistindo
  const pausedLong = st.state === "paused" && PL.pausedSince && Date.now() - PL.pausedSince > 2500 && !PL.panel && PL.next === null;
  $("pl-dim").classList.toggle("on", pausedLong || !!PL.panel);
  $("pl-paused").classList.toggle("on", pausedLong);
  $("pl-ui").classList.toggle("hide", !PL.ui || pausedLong || !!PL.panel);
  const pp = $("pl-btns").querySelector('[data-b="0"]');
  if (pp) {
    const want = st.state === "paused" ? ICON.play : ICON.pause;
    if (!pp.innerHTML.startsWith(want)) pp.innerHTML = want;
  }
}

// ---------- painéis ----------
function plAudioList() {
  const list = (PL.st.audio || []).filter(a => a.id >= 0), lib = PL.it.audio || [];
  // a biblioteca sabe os canais ("Inglês 7.1"); quando as contas batem, usa os nomes dela
  return list.map((a, k) => ({ id: a.id, name: list.length === lib.length ? lib[k] : plTrackName(a.name) }));
}
// nomes do motor em português: "Track 7 - [Chinese]" → "Chinês", "American English SDH - [English]" → "Inglês americano (SDH)"
const LANGS = { english: "Inglês", portuguese: "Português", spanish: "Espanhol", french: "Francês", german: "Alemão", italian: "Italiano",
  japanese: "Japonês", korean: "Coreano", chinese: "Chinês", arabic: "Árabe", dutch: "Holandês", russian: "Russo", thai: "Tailandês",
  indonesian: "Indonésio", malay: "Malaio", polish: "Polonês", turkish: "Turco", hindi: "Hindi", swedish: "Sueco", danish: "Dinamarquês",
  norwegian: "Norueguês", finnish: "Finlandês", greek: "Grego", hebrew: "Hebraico", czech: "Tcheco", hungarian: "Húngaro", romanian: "Romeno",
  vietnamese: "Vietnamita", ukrainian: "Ucraniano" };
function plTrackName(raw) {
  const m = /^(.*?)\s*-\s*\[([^\]]+)\]$/.exec(raw || "");
  if (!m) return raw || "Faixa";
  const lang = LANGS[m[2].toLowerCase()] || m[2];
  const d = m[1].toLowerCase();
  if (/^track \d+$/.test(d) || !d) return lang;
  const bits = [lang];
  if (/american/.test(d)) bits.push("americano");
  if (/british/.test(d)) bits.push("britânico");
  if (/brazil|brasil/.test(d)) bits.push("do Brasil");
  if (/europe|portugal/.test(d)) bits.push("de Portugal");
  if (/latin/.test(d)) bits.push("latino");
  let name = bits.join(" ");
  if (/sdh|cc/.test(d)) name += " (SDH)";
  if (/forced/.test(d)) name += " (só falas estrangeiras)";
  if (bits.length === 1 && !/sdh|cc|forced/.test(d)) name += ` · ${m[1]}`;
  return name;
}
function plSpuList() {
  const list = (PL.st.spu || []).filter(s => s.id >= 0), ext = PL.it.extSubs || [];
  // as de arquivo .srt entram depois das do próprio vídeo, na ordem em que foram passadas
  const first = list.length - ext.length;
  const named = list.map((s, k) => ({ id: s.id, name: k >= first && ext[k - first] ? ext[k - first].name : plTrackName(s.name) }));
  return [{ id: -1, name: "Desligadas" }, ...named];
}
// filme em outra língua com legenda em português: já começa com ela
function plAutoSub() {
  if (PL.subPicked || !(PL.st.spu || []).length || !(PL.st.audio || []).length) return;
  PL.subPicked = true;
  const audio = ((PL.st.audio || []).find(a => a.id === PL.st.audioId) || {}).name || "";
  if (/portugu|\[por\]|\bpt\b/i.test(audio)) return;
  // a do Brasil primeiro (o Project Hail Mary tem as duas), nunca a "só falas estrangeiras"
  const pts = plSpuList().filter(s => s.id >= 0 && /^Português/.test(s.name) && !/estrangeiras/.test(s.name));
  const pt = pts.find(s => /Brasil/.test(s.name)) || pts.find(s => !/Portugal/.test(s.name)) || pts[0];
  if (pt && pt.id !== PL.st.spuId) { window.onvlc.spu(pt.id); PL.st.spuId = pt.id; }
}
function plRenderPanel() {
  const p = $("pl-panel");
  p.classList.toggle("on", PL.panel === "tracks");
  if (PL.panel !== "tracks") return;
  const col = (list, cur, c) => list.map((o, k) =>
    `<div class="pl-opt${PL.pIdx[0] === c && PL.pIdx[1] === k ? " focus" : ""}"><i>${o.id === cur ? "✓" : ""}</i><span>${esc(o.name)}</span></div>`).join("");
  p.innerHTML = `<div><h3>Áudio</h3><div class="pl-list">${col(plAudioList(), PL.st.audioId, 0)}</div></div>`
    + `<div><h3>Legendas</h3><div class="pl-list">${col(plSpuList(), PL.st.spuId, 1)}</div></div>`;
  // listas longas (o Homem-Aranha tem 18 legendas): rola para deixar a opção em foco no meio
  p.querySelectorAll(".pl-list").forEach((list, c) => {
    const cur = c === 0 ? PL.st.audioId : PL.st.spuId;
    const items = [...list.children];
    const idx = PL.pIdx[0] === c ? PL.pIdx[1] : Math.max(0, (c === 0 ? plAudioList() : plSpuList()).findIndex(o => o.id === cur));
    const el = items[idx];
    list.classList.toggle("more", list.scrollHeight > list.clientHeight + 4);
    if (el) list.scrollTop = Math.max(0, el.offsetTop - list.clientHeight / 2 + el.offsetHeight / 2);
  });
}
function plRenderNext() {
  const box = $("pl-next");
  if (PL.next === null) { box.innerHTML = ""; return; }
  const e = PL.next;
  const src = e.still || e.show.backdrop || e.show.frame;
  box.innerHTML = `<div class="pl-next">${src ? `<img src="${img(src)}" alt="">` : ""}<div class="nb"><div class="k">Próximo episódio</div>
    <div class="nt">${esc(epLabel(e) + " · " + e.title)}</div>
    <div class="row2"><div class="pl-b${PL.btn === 0 ? " focus" : ""}"><div class="fill" id="pl-fill"></div><span id="pl-next-t">Assistir agora</span></div>
    <div class="pl-b${PL.btn === 1 ? " focus" : ""}"><span>Créditos</span></div></div></div></div>`;
}
function plUpdateNext(left) {
  const t = $("pl-next-t"), f = $("pl-fill");
  if (t) t.textContent = `Assistir em ${Math.max(1, Math.ceil(left))}`;
  if (f) f.style.width = Math.max(0, Math.min(100, (25 - left) / 25 * 100)) + "%";
}

// ---------- controle ----------
function plPoke() {
  PL.ui = true;
  clearTimeout(PL.hideTimer);
  PL.hideTimer = setTimeout(() => {
    if (PL.open && PL.st.state === "playing" && !PL.panel && PL.scrub === null) { PL.ui = false; plUpdate(); }
  }, 5000);
}
function plFlash(side) {
  const el = $(side < 0 ? "pl-jl" : "pl-jr");
  el.classList.add("on");
  setTimeout(() => el.classList.remove("on"), 450);
}
function plSeek(t) {
  t = Math.max(0, Math.min(plDur() - 2, t));
  PL.st.time = t;
  window.onvlc.seek(t);
  plUpdate();
}
function plToggle() { window.onvlc.toggle(); }
// segurar ← → acelera: 10 s, depois 30 s, depois 1 min por toque
function plStep() {
  const now = Date.now();
  PL.streak = now - PL.lastKey < 450 ? PL.streak + 1 : 0;
  PL.lastKey = now;
  return PL.streak > 12 ? 60 : PL.streak > 4 ? 30 : 10;
}

function plKey(k) {
  plLog(k);
  const left = k === "ArrowLeft", right = k === "ArrowRight", up = k === "ArrowUp", down = k === "ArrowDown";
  const ok = k === "Enter" || k === " ";
  const dx = left ? -1 : right ? 1 : 0, dy = up ? -1 : down ? 1 : 0;
  if (PL.next !== null) {
    if (dx) { PL.btn = Math.max(0, Math.min(1, PL.btn + dx)); plRenderNext(); }
    else if (ok) { if (PL.btn === 0) openPlayer(PL.next, 0); else { PL.nextOff = true; PL.next = null; plRenderNext(); } }
    return;
  }
  if (PL.panel === "tracks") {
    const lens = [plAudioList().length, plSpuList().length];
    if (dx) { PL.pIdx[0] = Math.max(0, Math.min(1, PL.pIdx[0] + dx)); PL.pIdx[1] = Math.min(PL.pIdx[1], Math.max(0, lens[PL.pIdx[0]] - 1)); }
    if (dy) PL.pIdx[1] = Math.max(0, Math.min(lens[PL.pIdx[0]] - 1, PL.pIdx[1] + dy));
    if (ok) {
      const o = (PL.pIdx[0] === 0 ? plAudioList() : plSpuList())[PL.pIdx[1]];
      if (o) {
        if (PL.pIdx[0] === 0) { window.onvlc.audio(o.id); PL.st.audioId = o.id; } else { window.onvlc.spu(o.id); PL.st.spuId = o.id; }
      }
    }
    plRenderPanel();
    return;
  }
  if (!PL.ui) {  // barra escondida: atalhos diretos
    if (dx) { plSeek((PL.st.time || 0) + dx * plStep()); plFlash(dx); return; }
    if (ok) { plToggle(); plPoke(); plUpdate(); return; }
    PL.focus = "bar";
    plPoke();
    plFocus();
    plUpdate();
    return;
  }
  plPoke();
  if (PL.focus === "bar") {
    if (dx) PL.scrub = Math.max(0, Math.min(plDur() - 2, (PL.scrub !== null ? PL.scrub : PL.st.time || 0) + dx * plStep()));
    else if (down) { PL.scrub = null; PL.focus = "btns"; PL.btn = 0; }
    else if (ok) { if (PL.scrub !== null) { plSeek(PL.scrub); PL.scrub = null; } else plToggle(); }
  } else {
    const bs = plRealButtons();
    if (dx) PL.btn = Math.max(0, Math.min(bs.length - 1, PL.btn + dx));
    else if (up) PL.focus = "bar";
    else if (ok) {
      const b = bs[PL.btn].id;
      if (b === "pp") plToggle();
      if (b === "b10") { plSeek((PL.st.time || 0) - 10); plFlash(-1); }
      if (b === "f10") { plSeek((PL.st.time || 0) + 10); plFlash(1); }
      if (b === "tracks") {
        PL.panel = "tracks";
        PL.pIdx = [0, Math.max(0, plAudioList().findIndex(a => a.id === PL.st.audioId))];
        plRenderPanel();
      }
      if (b === "nextep") return openPlayer(plNextEpisode(), 0);
    }
  }
  plFocus();
  plUpdate();
}

// registro curto dos últimos botões no player (lido pelo PC com tools\tvjs.py "KEYLOG")
window.KEYLOG = [];
function plLog(what) {
  KEYLOG.push(`${new Date().toTimeString().slice(0, 8)} ${what} panel=${PL.panel} ui=${PL.ui} focus=${PL.focus} scrub=${PL.scrub !== null}`);
  if (KEYLOG.length > 40) KEYLOG.shift();
}

function plBack() {
  plLog("voltar");
  if (PL.next !== null) { PL.nextOff = true; PL.next = null; plRenderNext(); return true; }
  if (PL.panel) { PL.panel = null; plRenderPanel(); plPoke(); plFocus(); plUpdate(); return true; }
  if (PL.scrub !== null) { PL.scrub = null; plUpdate(); return true; }
  if (PL.ui && PL.st.state === "playing") { PL.ui = false; plUpdate(); return true; }
  closePlayer(true);  // a posição vai para o PC pelo app
  return true;
}

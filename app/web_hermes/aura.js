/* =====================================================================
   Atlas - the page logic.
   Talks to the Python side through window.pywebview.api (see app/app.py).
   Sections: basics, rich text, messages + status line, chats list,
   pictures, sending, modes, composer, dialog, browser pane, window,
   keyboard, terminal (hidden mode), loading screen, front page, start.
   ===================================================================== */

/* ---------- particle loader: vanilla port of Hermes' loader.tsx, type "fourier-flow" ----------
   window.auraLoader(host) draws into host (an element sized by CSS, color = currentColor).
   Returns stop(). The loop also stops by itself once host leaves the page. */
(function () {
  "use strict";
  var TWO_PI = Math.PI * 2, NS = "http://www.w3.org/2000/svg";

  function baseCurve(name, o) {
    return {
      durationMs: o.durationMs, name: name, particleCount: o.particleCount,
      point: o.point || function () { return { x: 50, y: 50 }; },
      pulseDurationMs: o.pulseDurationMs, rotate: o.rotate || false,
      rotationDurationMs: o.rotationDurationMs || 36000,
      strokeWidth: o.strokeWidth, trailSpan: o.trailSpan
    };
  }
  var FOURIER = baseCurve("Fourier Flow", {
    durationMs: 2200, particleCount: 92, pulseDurationMs: 2000, rotationDurationMs: 15000, strokeWidth: 4.2, trailSpan: 0.31
  });
  FOURIER.point = function (progress, detailScale) {
    var t = progress * TWO_PI;
    var mix = 1 + detailScale * 0.16;
    var x = 17 * Math.cos(t) + 7.5 * Math.cos(3 * t + 0.6 * mix) + 3.2 * Math.sin(5 * t - 0.4);
    var y = 15 * Math.sin(t) + 8.2 * Math.sin(2 * t + 0.25) - 4.2 * Math.cos(4 * t - 0.5 * mix);
    return { x: 50 + x, y: 50 + y };
  };

  function buildPath(config, detailScale, steps) {
    var out = [];
    for (var i = 0; i <= steps; i++) {
      var p = config.point(i / steps, detailScale);
      out.push((i === 0 ? "M" : "L") + " " + p.x.toFixed(2) + " " + p.y.toFixed(2));
    }
    return out.join(" ");
  }
  function detailScaleFor(time, config, phaseOffset) {
    var pulseProgress = ((time + phaseOffset * config.pulseDurationMs) % config.pulseDurationMs) / config.pulseDurationMs;
    var pulseAngle = pulseProgress * TWO_PI;
    return 0.52 + ((Math.sin(pulseAngle + 0.55) + 1) / 2) * 0.48;
  }
  function normalizeProgress(progress) { return ((progress % 1) + 1) % 1; }
  function particleFor(config, index, progress, detailScale, strokeScale) {
    var tailOffset = index / (config.particleCount - 1);
    var point = config.point(normalizeProgress(progress - tailOffset * config.trailSpan), detailScale);
    var fade = Math.pow(1 - tailOffset, 0.56);
    return { opacity: 0.04 + fade * 0.96, radius: (0.9 + fade * 2.7) * strokeScale, x: point.x, y: point.y };
  }
  function rotationFor(time, config, phaseOffset) {
    if (!config.rotate) return 0;
    return -(((time + phaseOffset * config.rotationDurationMs) % config.rotationDurationMs) / config.rotationDurationMs) * 360;
  }

  window.auraLoader = function (host, opts) {
    opts = opts || {};
    var config = FOURIER, pathSteps = opts.pathSteps || 240, strokeScale = opts.strokeScale || 1;
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 100 100"); svg.setAttribute("fill", "none"); svg.setAttribute("aria-hidden", "true");
    var g = document.createElementNS(NS, "g");
    var path = document.createElementNS(NS, "path");
    path.setAttribute("opacity", "0.1"); path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-linecap", "round"); path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-width", String(config.strokeWidth * strokeScale));
    g.appendChild(path);
    var dots = [];
    for (var i = 0; i < config.particleCount; i++) {
      var c = document.createElementNS(NS, "circle"); c.setAttribute("fill", "currentColor");
      g.appendChild(c); dots.push(c);
    }
    svg.appendChild(g);
    host.textContent = "";
    host.appendChild(svg);

    var frame = 0, stopped = false, startedAt = performance.now(), phaseOffset = Math.random();
    function render(now) {
      if (stopped) return;
      var time = now - startedAt;
      var progress = ((time + phaseOffset * config.durationMs) % config.durationMs) / config.durationMs;
      var detailScale = detailScaleFor(time, config, phaseOffset);
      g.setAttribute("transform", "rotate(" + rotationFor(time, config, phaseOffset) + " 50 50)");
      path.setAttribute("d", buildPath(config, detailScale, pathSteps));
      for (var k = 0; k < dots.length; k++) {
        var p = particleFor(config, k, progress, detailScale, strokeScale);
        dots[k].setAttribute("cx", p.x.toFixed(2)); dots[k].setAttribute("cy", p.y.toFixed(2));
        dots[k].setAttribute("r", p.radius.toFixed(2)); dots[k].setAttribute("opacity", p.opacity.toFixed(3));
      }
    }
    var still = (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches)
      || document.body.classList.contains("calm");
    function loop(now) {
      if (stopped) return;
      if (!host.isConnected) { stopped = true; return; }
      render(now);
      frame = requestAnimationFrame(loop);
    }
    render(performance.now());                     // reduced motion: this one static frame only
    if (!still) frame = requestAnimationFrame(loop);
    return function stop() { stopped = true; cancelAnimationFrame(frame); };
  };
})();

(function () {
  "use strict";

  /* ---------- basics ---------- */
  var api = null, started = false;
  var currentId = null, sessions = [], busy = false, stopFn = null;
  var $ = function (id) { return document.getElementById(id); };
  var messagesEl = $("messages"), input = $("input"), sendBtn = $("sendBtn");
  var noop = function () {};
  var calmMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  var MODES = [
    { id: "claude", label: "Atlas", tip: "Smart and thorough: Atlas plans and hands the work to the helper" },
    { id: "auto", label: "Quick", tip: "Fast, no thinking: Atlas hands the work to the helper" },
    { id: "qwen", label: "Local", tip: "Runs only on this PC, free" },
    { id: "polish", label: "Polish", tip: "Atlas makes the website of this chat look great" }
  ];
  var mode = "claude";

  function uiLog(m) { try { if (api && api.ui_log) api.ui_log(String(m)); } catch (x) { /* ignore */ } }
  window.addEventListener("error", function (e) { uiLog("JS error: " + e.message + " @" + (e.lineno || "?")); });
  window.addEventListener("unhandledrejection", function (e) { uiLog("promise error: " + (e.reason && (e.reason.message || e.reason))); });

  var toastTimer = 0;
  function toast(text) {
    var t = $("toast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3500);
  }

  /* Scrolling: follow new text only while the user is at the bottom. */
  var stick = true;
  messagesEl.addEventListener("scroll", function () { /* follow new text only while you are (almost) at the bottom */
    stick = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 32;
  }, { passive: true });
  messagesEl.addEventListener("wheel", function (e) { if (e.deltaY < 0) stick = false; }, { passive: true }); /* scrolling up = stop following at once */
  messagesEl.addEventListener("touchmove", function () { stick = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 32; }, { passive: true });
  function scrollDown(force) {
    if (force || stick) { messagesEl.scrollTop = messagesEl.scrollHeight; stick = true; }
  }
  /* Following an answer: keep scrolling while it is short, but never past its START, so you can read it from the top. */
  function followAnswer(el) {
    if (!stick || !el) return;
    var box = (el.closest && el.closest(".msg")) || el;
    var bottom = messagesEl.scrollHeight - messagesEl.clientHeight;
    var topLimit = messagesEl.scrollTop + (box.getBoundingClientRect().top - messagesEl.getBoundingClientRect().top) - 16;
    var target = Math.min(bottom, topLimit);
    if (target > messagesEl.scrollTop) messagesEl.scrollTop = target;
  }

  /* ---------- rich text (markdown-lite; builds DOM nodes, never innerHTML) ---------- */
  function addText(el, s) {
    s.split("\n").forEach(function (part, k) {
      if (k) el.appendChild(document.createElement("br"));
      if (part) el.appendChild(document.createTextNode(part));
    });
  }
  function addInline(el, s) {
    var re = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g, last = 0, m, n;
    while ((m = re.exec(s))) {
      addText(el, s.slice(last, m.index));
      if (m[0].charAt(0) === "`") { n = document.createElement("code"); n.textContent = m[0].slice(1, -1); }
      else { n = document.createElement("strong"); n.textContent = m[0].slice(2, -2); }
      el.appendChild(n);
      last = re.lastIndex;
    }
    addText(el, s.slice(last));
  }
  function copyText(s) {
    function fallback() {
      var t = document.createElement("textarea");
      t.value = s; t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); } catch (e) { /* ignore */ }
      t.remove();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(s).catch(fallback);
    fallback();
    return Promise.resolve();
  }
  var ICON_COPY = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/></svg>';
  var ICON_OK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  /* A copy button: icon (+ optional word), shows a check for 1.5 s after copying. getText() is read at click time. */
  function copyButton(cls, label, word, getText) {
    var btn = document.createElement("button"), t = 0;
    btn.type = "button"; btn.className = cls; btn.title = label;
    btn.setAttribute("aria-label", label);
    function paint(done) {
      btn.innerHTML = done ? ICON_OK : ICON_COPY;
      if (word) btn.appendChild(document.createTextNode(done ? "Copied" : word));
      btn.classList.toggle("done", done);
    }
    paint(false);
    btn.addEventListener("click", function () {
      copyText(getText()).then(function () {
        paint(true); clearTimeout(t);
        t = setTimeout(function () { paint(false); }, 1500);
      });
    });
    return btn;
  }
  function codeBlock(code, lang) {
    var wrap = document.createElement("div"); wrap.className = "code";
    var bar = document.createElement("div"); bar.className = "code-bar";
    var l = document.createElement("span"); l.textContent = lang || "code";
    var btn = copyButton("copy", "Copy code", "Copy", function () { return code; });
    bar.appendChild(l); bar.appendChild(btn);
    var pre = document.createElement("pre"), c = document.createElement("code");
    c.textContent = code;
    pre.appendChild(c);
    wrap.appendChild(bar); wrap.appendChild(pre);
    return wrap;
  }
  function choiceBlock(options) {
    var wrap = document.createElement("div"); wrap.className = "choices";
    options.forEach(function (o) {
      var b = document.createElement("button"); b.type = "button"; b.className = "choice"; b.textContent = o;
      b.addEventListener("click", function () {
        if (wrap.classList.contains("done") || busy) return;
        wrap.classList.add("done"); b.classList.add("picked");
        input.value = o; input.dispatchEvent(new Event("input")); send();
      });
      wrap.appendChild(b);
    });
    return wrap;
  }

  /* ---------- pictures inside answers: a markdown image or a path to a picture file shows the picture ---------- */
  var picCache = {};
  var IMG_PATH_RE = /!\[[^\]\n]*\]\(\s*<?([^)>\n]+?)>?\s*\)|((?:[A-Za-z]:\\|file:\/\/\/)[^\\\/:*?"<>|\r\n`]+(?:[\\\/][^\\\/:*?"<>|\r\n`]+)*\.(?:png|jpe?g|gif|webp|bmp|mp4|webm|mov|m4v|mkv))/gi;

  /* ---------- picture viewer: click a picture in the chat to inspect it (wheel = zoom, drag = move) ---------- */
  function openLightbox(url) {
    var old = document.getElementById("lightbox"); if (old) old.remove();
    var box = document.createElement("div"); box.id = "lightbox"; box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Picture viewer");
    var im = document.createElement("img"); im.src = url; im.draggable = false; im.alt = "Picture";
    var bar = document.createElement("div"); bar.className = "lb-bar";
    function mk(txt, label, fn) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = txt; b.title = label;
      b.setAttribute("aria-label", label); b.addEventListener("click", function (e) { e.stopPropagation(); fn(); });
      bar.appendChild(b); return b;
    }
    var hint = document.createElement("span"); hint.className = "lb-hint";
    hint.textContent = "Wheel: zoom · Drag: move · Double-click: 100% · Esc: close";
    var scale = 1, fit = 1, tx = 0, ty = 0, drag = null;
    function apply() { im.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + scale + ")"; }
    function fitIt() {
      var w = im.naturalWidth || 1, h = im.naturalHeight || 1;
      fit = Math.min((window.innerWidth * 0.94) / w, (window.innerHeight * 0.88) / h, 1);
      scale = fit; tx = 0; ty = 0; apply();
    }
    function zoomAt(f, cx, cy) {
      var ns = Math.max(fit * 0.5, Math.min(scale * f, 16));
      var r = im.getBoundingClientRect();
      var ox = cx - (r.left + r.width / 2), oy = cy - (r.top + r.height / 2);
      var k = ns / scale; tx -= ox * (k - 1); ty -= oy * (k - 1); scale = ns; apply();
    }
    function close() { box.remove(); document.removeEventListener("keydown", onKey, true); }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "+" || e.key === "=") zoomAt(1.25, window.innerWidth / 2, window.innerHeight / 2);
      else if (e.key === "-") zoomAt(0.8, window.innerWidth / 2, window.innerHeight / 2);
      else if (e.key === "0") fitIt();
    }
    mk("+", "Zoom in", function () { zoomAt(1.25, window.innerWidth / 2, window.innerHeight / 2); });
    mk("−", "Zoom out", function () { zoomAt(0.8, window.innerWidth / 2, window.innerHeight / 2); });
    mk("Fit", "Fit to window", fitIt);
    mk("100%", "Actual size", function () { scale = 1; tx = 0; ty = 0; apply(); });
    mk("×", "Close", close);
    box.appendChild(im); box.appendChild(bar); box.appendChild(hint);
    box.addEventListener("wheel", function (e) { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY); }, { passive: false });
    im.addEventListener("mousedown", function (e) { e.preventDefault(); drag = { x: e.clientX - tx, y: e.clientY - ty, moved: false }; im.classList.add("grab"); });
    window.addEventListener("mousemove", function mv(e) {
      if (!document.body.contains(box)) { window.removeEventListener("mousemove", mv); return; }
      if (drag) { tx = e.clientX - drag.x; ty = e.clientY - drag.y; drag.moved = true; apply(); }
    });
    window.addEventListener("mouseup", function up() {
      if (!document.body.contains(box)) { window.removeEventListener("mouseup", up); return; }
      drag = null; im.classList.remove("grab");
    });
    im.addEventListener("dblclick", function () { if (Math.abs(scale - 1) < 0.01) fitIt(); else { scale = 1; tx = 0; ty = 0; apply(); } });
    box.addEventListener("click", function (e) { if (e.target === box) close(); });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(box);
    if (im.complete && im.naturalWidth) fitIt(); else im.addEventListener("load", fitIt);
  }
  var picBad = {}, picPending = {};
  function fmtBytes(n) { n = Number(n) || 0; return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
  function getMedia(path, force, cb) {
    if (!force && picCache[path] && picCache[path].url) { cb(picCache[path]); return; }
    if (!force && picBad[path] && Date.now() - picBad[path] < 8000) { cb(null); return; }   // do not ask again on every streamed chunk
    if (picPending[path]) { picPending[path].push(cb); return; }
    picPending[path] = [cb];
    function done(r) {
      var cbs = picPending[path] || []; delete picPending[path];
      if (r && r.url) { picCache[path] = r; delete picBad[path]; } else { delete picCache[path]; picBad[path] = Date.now(); r = null; }
      cbs.forEach(function (f) { f(r); });
    }
    if (!api || !api.media_info) { done(null); return; }
    api.media_info(path).then(done).catch(function () { done(null); });
  }
  var MEDIA_ICO_EXPAND = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6"/><path d="M10 20H4v-6"/><path d="M20 4l-7 7"/><path d="M4 20l7-7"/></svg>';
  var MEDIA_ICO_PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5l11 7-11 7z"/></svg>';
  function imageBlock(path) {
    var name = String(path).split(/[\\\/]/).pop();
    var card = document.createElement("div"); card.className = "media-card is-loading";
    var frame = document.createElement("div"); frame.className = "media-frame";
    var bar = document.createElement("div"); bar.className = "media-bar";
    var meta = document.createElement("span"); meta.className = "media-meta"; meta.textContent = name; meta.title = path;
    var acts = document.createElement("span"); acts.className = "media-acts";
    bar.appendChild(meta); card.appendChild(frame); card.appendChild(bar); card.appendChild(acts);
    var cur = null, isVid = /\.(mp4|webm|mov|m4v|mkv)$/i.test(name);
    function mkBtn(label, svg, fn) {
      var b = document.createElement("button"); b.type = "button"; b.className = "media-btn";
      b.title = label; b.setAttribute("aria-label", label);
      b.innerHTML = svg;
      b.addEventListener("click", function (e) { e.stopPropagation(); fn(b); });
      acts.appendChild(b); return b;
    }
    function copyIt(b) {
      var old = b.innerHTML;
      if (!api || !api.copy_media) { toast("Could not copy."); return; }
      api.copy_media(path).then(function (r) {
        var ok = !!(r && r.ok);
        if (ok) { b.innerHTML = ICO.ok; b.classList.add("ok"); }
        toast(ok ? (isVid ? "Video copied. Paste it in a folder or chat." : "Picture copied.") : "Could not copy it.");
        setTimeout(function () { b.innerHTML = old; b.classList.remove("ok"); }, 1600);
      }).catch(function () { toast("Could not copy it."); });
    }
    function retryIt() { load(true); }
    function buttons(failed) {
      acts.textContent = "";
      if (failed) return;
      mkBtn("Copy", ICO.copy, copyIt);
      if (!isVid) mkBtn("Expand", MEDIA_ICO_EXPAND, function () { if (cur) openLightbox(cur.url); });
      if (isVid) mkBtn("Open", MEDIA_ICO_PLAY, function () { if (api && api.open_media) api.open_media(path); });
      mkBtn("Retry", ICO.redo, retryIt);
    }
    function fail(msg) {
      card.classList.remove("is-loading"); card.classList.add("is-failed");
      frame.textContent = "";
      var box = document.createElement("div"); box.className = "media-fail";
      var t = document.createElement("div"); t.textContent = msg; box.appendChild(t);
      var again = document.createElement("button"); again.type = "button"; again.className = "media-btn";
      again.innerHTML = "<span>Try again</span>"; again.addEventListener("click", retryIt); box.appendChild(again);
      var ask = document.createElement("button"); ask.type = "button"; ask.className = "media-btn";
      ask.innerHTML = "<span>Ask Atlas to show it another way</span>";
      ask.addEventListener("click", function () {
        if (busy) { toast("Wait for Atlas to finish first, or press Stop."); return; }
        input.value = "The " + (isVid ? "video" : "picture") + " " + name + " did not show in the chat. Please show it another way.";
        input.dispatchEvent(new Event("input")); send();
      });
      box.appendChild(ask);
      if (isVid) { var op = document.createElement("button"); op.type = "button"; op.className = "media-btn"; op.innerHTML = "<span>Open in player</span>";
        op.addEventListener("click", function () { if (api && api.open_media) api.open_media(path); }); box.appendChild(op); }
      frame.appendChild(box); buttons(true);
    }
    function show(r) {
      cur = r; isVid = r.kind === "video";
      card.classList.remove("is-loading", "is-failed"); frame.textContent = "";
      var el = document.createElement(isVid ? "video" : "img"); el.className = "media-el";
      if (isVid) { el.controls = true; el.preload = "metadata"; el.setAttribute("playsinline", ""); }
      else { el.alt = name; el.title = "Click to open and zoom"; el.addEventListener("click", function () { openLightbox(r.url); }); }
      el.addEventListener("error", function () { fail(isVid ? "This video can't be played here." : "This picture can't be shown."); });
      el.addEventListener(isVid ? "loadedmetadata" : "load", function () {
        var w = isVid ? el.videoWidth : el.naturalWidth, h = isVid ? el.videoHeight : el.naturalHeight;
        meta.textContent = name + " · " + (w && h ? w + "×" + h + " · " : "") + fmtBytes(r.size);
      });
      el.src = r.url; frame.appendChild(el);
      meta.textContent = name + " · " + fmtBytes(r.size); buttons(false);
    }
    function load(force) {
      card.classList.add("is-loading"); card.classList.remove("is-failed"); frame.textContent = "";
      acts.textContent = "";
      getMedia(path, force, function (r) { if (r) show(r); else fail("Could not find this " + (isVid ? "video" : "picture") + "."); });
    }
    load(false);
    return card;
  }
  function imagePathsIn(line) {
    var found = [], m;
    IMG_PATH_RE.lastIndex = 0;
    while ((m = IMG_PATH_RE.exec(line))) {
      var p = (m[1] || m[2] || "").trim();
      if (p && /\.(png|jpe?g|gif|webp|bmp|mp4|webm|mov|m4v|mkv)$/i.test(p) && found.indexOf(p) < 0) found.push(p);
    }
    return found;
  }
  function renderRich(el, text) {
    el._raw = String(text || ""); // what the message Copy button copies
    var out = document.createDocumentFragment();
    var lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    var i = 0, para = [], list = null, listType = null, m;
    var pendingImgs = [];
    function flushImgs() { pendingImgs.forEach(function (pth) { out.appendChild(imageBlock(pth)); }); pendingImgs = []; }
    function flushPara() {
      if (!para.length) { flushImgs(); return; }
      var p = document.createElement("p");
      addInline(p, para.join("\n"));
      out.appendChild(p);
      para = [];
      flushImgs();
    }
    function flushAll() { flushPara(); list = null; listType = null; }
    while (i < lines.length) {
      var line = lines[i];
      if ((m = /^\s*```\s*([\w+#.-]*)\s*$/.exec(line))) {
        flushAll();
        var code = []; i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
        i++;
        if (m[1] === "choices") { var opts = code.map(function (x) { return x.trim(); }).filter(Boolean);
          if (opts.length) { out.appendChild(choiceBlock(opts)); continue; } }
        out.appendChild(codeBlock(code.join("\n"), m[1]));
        continue;
      }
      if (/^\s*$/.test(line)) { flushAll(); i++; continue; }
      var imgs = imagePathsIn(line);
      if (imgs.length) {
        var rest = line.replace(IMG_PATH_RE, "").replace(/[`*_:()\[\]<>-]/g, "").replace(/\b(saved|screenshot|picture|image|here|to|at|is|as|the|in|file|path)\b/gi, "").trim();
        if (!rest) { flushAll(); imgs.forEach(function (pth) { out.appendChild(imageBlock(pth)); }); i++; continue; }
        imgs.forEach(function (pth) { pendingImgs.push(pth); });
      }
      if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
        flushAll();
        var h = document.createElement("h" + (m[1].length + 2));
        addInline(h, m[2]);
        out.appendChild(h);
        i++; continue;
      }
      var ul = /^\s*[-*•]\s+(.*)$/.exec(line), ol = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
      if (ul || ol) {
        flushPara();
        var type = ul ? "ul" : "ol";
        if (!list || listType !== type) {
          list = document.createElement(type); listType = type;
          if (ol && ol[1] !== "1") list.start = parseInt(ol[1], 10);
          out.appendChild(list);
        }
        var li = document.createElement("li");
        addInline(li, ul ? ul[1] : ol[2]);
        list.appendChild(li);
        i++; continue;
      }
      list = null; listType = null;
      para.push(line); i++;
    }
    flushPara();
    el.textContent = "";
    el.appendChild(out); // one DOM update per render (cheap while streaming)
  }


  /* ---------- message actions: copy, reply (quote), edit (your messages), try again (last answer) ---------- */
  var editing = false;
  var ICO = {
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/></svg>',
    ok: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    reply: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 6L4 12l6 6"/><path d="M4 12h9a7 7 0 0 1 7 7"/></svg>',
    edit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20l1-4L16 5a2 2 0 0 1 3 3L8 19z"/><path d="M14 7l3 3"/></svg>',
    redo: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14-4L4 9"/><path d="M4 4v5h5"/><path d="M4 13a8 8 0 0 0 14 4l2-2"/><path d="M20 20v-5h-5"/></svg>'
  };
  function actBtn(cls, label, svg, fn) {
    var x = document.createElement("button");
    x.type = "button"; x.className = "act " + cls; x.title = label; x.setAttribute("aria-label", label);
    x.innerHTML = svg; x.addEventListener("click", fn);
    return x;
  }
  function rawOf(b) { return (b._raw != null ? b._raw : b.innerText) || ""; }
  function putInInput(text) {
    input.value = text; input.dispatchEvent(new Event("input")); autoGrow(); focusInput();
    try { input.setSelectionRange(text.length, text.length); } catch (x) { /* ignore */ }
  }
  function quoteReply(b) {
    var t = rawOf(b).replace(/\s+/g, " ").trim();
    if (!t) return;
    if (t.length > 160) t = t.slice(0, 157) + "...";
    putInInput("Re: “" + t + "”\n");
  }
  function editMessage(b) {
    if (busy) { toast("Wait for Atlas to finish first, or press Stop."); return; }
    var t = rawOf(b).replace(/^\(edited\) /, "").replace(/\nAttached: [^\n]*$/, "").trim();
    if (!t) return;
    editing = true; putInInput(t);
  }
  function tryAgain() {
    if (busy) { toast("Wait for Atlas to finish first, or press Stop."); return; }
    if (pics.length || docs.length) { toast("Send or remove your attachment first."); return; }
    var draft = input.value;
    input.value = "Please try again with a different answer.";
    send();
    input.value = draft; autoGrow();
  }
  function extraActions(acts, b, role) {
    acts.appendChild(actBtn("reply", "Reply to this message", ICO.reply, function () { quoteReply(b); }));
    if (role === "user") acts.appendChild(actBtn("edit", "Edit and send again", ICO.edit, function () { editMessage(b); }));
    else acts.appendChild(actBtn("regen", "Try again (another answer)", ICO.redo, tryAgain));
  }
  function ownCopy(b) {
    var btn = actBtn("copy", "Copy message", ICO.copy, function () {
      copyText(rawOf(b)).then(function () {
        btn.innerHTML = ICO.ok; btn.classList.add("done");
        setTimeout(function () { btn.innerHTML = ICO.copy; btn.classList.remove("done"); }, 1500);
      });
    });
    return btn;
  }
  /* "Try again" only shows on the very last message, and only when that is an answer. */
  function markLastReply() {
    var all = messagesEl.querySelectorAll(".msg"), last = all.length ? all[all.length - 1] : null;
    Array.prototype.forEach.call(messagesEl.querySelectorAll(".msg.last-reply"), function (m) { if (m !== last) m.classList.remove("last-reply"); });
    if (last && last.classList.contains("assistant") && last.querySelector(".act.regen")) last.classList.add("last-reply");
  }
  if (window.MutationObserver) new MutationObserver(markLastReply).observe(messagesEl, { childList: true });
  /* The input says "Reply to Atlas..." as soon as the chat has messages (like Claude), "Message Atlas..." on an empty chat. */
  var basePlaceholder = input.placeholder || "Message Atlas...";
  var IDEAS = [
    "Build me a tiny website about my cat...", "Show me what takes the most space in my Downloads folder...",
    "Give me a fun fact I can tell my friends...", "Plan a surprise date night...",
    "Ask me a silly question, I dare you...", "Make me a small game to play...",
    "Find a dinner recipe using only rice and eggs...", "Teach me something cool in 30 seconds...",
    "Write a poem about Monday...", "Why is my PC slow? Let's find out...",
    "Explain quantum physics like I'm five...", "What should we build today?",
    "Teach me 5 useful Greek phrases...", "Help me politely say no to a plan...",
    "Open YouTube and find a chill playlist...", "Invent a board game I can play with a pen and paper...",
    "Tell me a riddle and don't give the answer yet...", "Help me write a funny text to cheer up a friend...",
    "Suggest 5 weekend plans that cost almost nothing...", "Make me a 10-minute workout I can do at home...",
    "Explain how the internet actually works...", "Give me a random skill I can learn this week...",
    "Write a short bedtime story about a robot...", "Quiz me on world capitals...",
    "Plan a 3-day trip to somewhere I've never been...", "Teach me how to cook the perfect omelette...",
    "Roast my taste in music, gently...", "What are some good habits to start today?",
    "Create a meal plan for the week...", "Help me name my future cat...",
    "Tell me about a historical event most people don't know...", "Write me a motivational speech for a Monday...",
    "Show me how big my biggest files are...", "Give me a tongue twister and let's see if I can say it...",
    "Explain a confusing word in simple terms...", "Make a tiny website that shows a countdown...",
    "Help me pick a movie for tonight...", "Teach me basic chess opening ideas...",
    "Write a haiku about coffee...", "What would a perfect day look like for me?",
    "Give me a 5-minute brain teaser...", "Find a calm study playlist for me...",
    "Explain a science fact that sounds fake but is true...", "Help me write a polite email to say I'll be late...",
    "Suggest a creative project for a rainy day...", "Teach me 5 useful Spanish phrases for travel...",
    "Tell me a joke only a programmer would get...", "Help me plan a small birthday surprise...",
    "What should I learn first to start coding?", "Describe an animal I've probably never heard of...",
    "Give me a challenge to complete in 15 minutes..."
  ];
  var emptyIdea = "";
  // a shuffled "bag": every idea is shown once before any repeats, never the same twice in a row, remembered between starts
  function pickIdea() {
    var bag = [], last = "";
    try { bag = JSON.parse(localStorage.getItem("atlasIdeaBag") || "[]"); last = localStorage.getItem("atlasIdeaLast") || ""; } catch (e) { bag = []; }
    bag = bag.filter(function (i) { return typeof i === "number" && i >= 0 && i < IDEAS.length; });
    if (!bag.length) {
      bag = IDEAS.map(function (_, i) { return i; });
      for (var i = bag.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = bag[i]; bag[i] = bag[j]; bag[j] = t; }
      if (IDEAS[bag[bag.length - 1]] === last && bag.length > 1) bag.unshift(bag.pop());   // the next one to be taken is not the last shown
    }
    var idx = bag.pop();
    try { localStorage.setItem("atlasIdeaBag", JSON.stringify(bag)); localStorage.setItem("atlasIdeaLast", IDEAS[idx]); } catch (e) { /* ignore */ }
    return IDEAS[idx];
  }
  function paintPlaceholder() {
    var want;
    if (messagesEl.querySelector(".msg")) { emptyIdea = ""; want = basePlaceholder.replace(/^Message (Aura|Atlas)/, "Reply to Atlas"); }
    else { if (!emptyIdea) emptyIdea = pickIdea(); want = emptyIdea; }
    if (input.placeholder !== want) input.placeholder = want;
  }
  if (window.MutationObserver) new MutationObserver(paintPlaceholder).observe(messagesEl, { childList: true });
  paintPlaceholder();
  input.addEventListener("input", function () { if (!input.value) editing = false; });

  /* Select text in a message: a small Reply button floats next to the selection and quotes just that part. */
  var selBtn = null, selText = "";
  function selHide() { if (selBtn) { selBtn.remove(); selBtn = null; } selText = ""; }
  function selCheck() {
    var s = window.getSelection();
    if (!s || s.isCollapsed || !s.rangeCount) { selHide(); return; }
    var txt = s.toString().replace(/\s+/g, " ").trim();
    if (!txt) { selHide(); return; }
    var r = s.getRangeAt(0), n = r.commonAncestorContainer;
    n = n.nodeType === 1 ? n : n.parentNode;
    var bub = n && n.closest ? n.closest(".bubble") : null;
    if (!bub || !messagesEl.contains(bub)) { selHide(); return; }
    selText = txt;
    if (!selBtn) {
      selBtn = document.createElement("button"); selBtn.type = "button"; selBtn.className = "sel-reply";
      selBtn.setAttribute("aria-label", "Reply to the selected text");
      selBtn.innerHTML = ICO.reply + "<span>Reply</span>";
      selBtn.addEventListener("mousedown", function (e) { e.preventDefault(); }); /* keep the selection alive */
      selBtn.addEventListener("click", function () {
        var t = selText; if (!t) return;
        if (t.length > 300) t = t.slice(0, 297) + "...";
        var d = input.value;
        selHide();
        try { window.getSelection().removeAllRanges(); } catch (x) { /* ignore */ }
        putInInput("Re: “" + t + "”\n" + d);
      });
      document.body.appendChild(selBtn);
    }
    var rect = r.getBoundingClientRect(), w = selBtn.offsetWidth || 74;
    var top = rect.top - 38; if (top < 8) top = rect.bottom + 8;
    var left = Math.max(8, Math.min(window.innerWidth - w - 8, rect.left + rect.width / 2 - w / 2));
    selBtn.style.top = Math.round(top) + "px"; selBtn.style.left = Math.round(left) + "px";
  }
  document.addEventListener("mouseup", function () { setTimeout(selCheck, 0); });
  document.addEventListener("keyup", function (e) { if (e.shiftKey || e.key === "Escape") setTimeout(selCheck, 0); });
  messagesEl.addEventListener("scroll", selHide, { passive: true });
  window.addEventListener("resize", selHide);

  /* ---------- messages ---------- */
  function addBubble(role, text, kind) {
    var wrap = document.createElement("div");
    wrap.className = "msg " + role;
    if (role === "assistant") {
      var badge = document.createElement("div");
      badge.className = "badge";
      badge.textContent = "Atlas";
      wrap.appendChild(badge);
    }
    var b = document.createElement("div");
    b.className = "bubble" + (kind ? " " + kind : "");
    if (role === "assistant" && !kind) renderRich(b, text); else { b.textContent = text; b._raw = text; }
    wrap.appendChild(b);
    if (!kind) { // copy button that appears on hover (Hermes style)
      var acts = document.createElement("div"); acts.className = "msg-actions";
      acts.appendChild(copyButton("act", "Copy message", "", function () { return b._raw != null ? b._raw : b.innerText; }));
      extraActions(acts, b, role);
      wrap.appendChild(acts);
    }
    messagesEl.appendChild(wrap);
    if (role === "user") scrollDown(true); else followAnswer(b);
    return b;
  }
  function addChip(text) {
    var c = document.createElement("div");
    c.className = "chip"; c.textContent = text;
    messagesEl.appendChild(c);
  }

  /* Status line like Claude's: animated symbol, shimmering word that fits the phase, live timer. */
  var GLYPHS = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"];
  var STATUS = {
    think:  { seq: false, words: ["Thinking"] },
    look:   { seq: false, words: ["Looking at the picture"] },
    read:   { seq: false, words: ["Reading files"] },
    search: { seq: false, words: ["Searching"] },
    edit:   { seq: false, words: ["Editing files"] },
    work:   { seq: false, words: ["Working"] },
    run:    { seq: false, words: ["Running a command"] },
    build:  { seq: false, words: ["Building and testing"] },
    web:    { seq: false, words: ["Looking it up"] },
    plan:   { seq: false, words: ["Planning"] },
    write:  { seq: false, words: ["Writing the answer"] },
    big:    { seq: true,  words: ["Planning", "Splitting the job", "Building the pieces", "Testing the pieces", "Connecting everything", "Checking the result"] },
    polish: { seq: true,  words: ["Reviewing", "Looking for rough edges", "Polishing", "Refining the details", "Final checks"] },
    wait:   { seq: false, words: ["Waiting for the other chat"] },
    stop:   { seq: true,  words: ["Stopping"] }
  };
  var CHIP_PREFIX = "";
  var CHIP = { read: "Read files", search: "Searched", edit: "Edited files", run: "Ran a command", work: "Completed a task",
               build: "Built and tested", web: "Looked something up", plan: "Planned the steps" };

  /* Creates the status line at the bottom of the chat. Returns { el, phase(p), stop() }.
     sinceMs: how long the work has already been running (when coming back to a chat that is still working). */
  function startStatus(phase, sinceMs) {
    messagesEl.querySelectorAll(".working").forEach(function (o) { o.remove(); }); // never show two
    var w = document.createElement("div");
    w.className = "working";
    w.setAttribute("role", "status");
    var g = document.createElement("span"); g.className = "st-glyph ld"; g.setAttribute("aria-hidden", "true");
    var stopLoader = window.auraLoader ? window.auraLoader(g) : null;
    if (!stopLoader) g.textContent = GLYPHS[4];
    var v = document.createElement("span"); v.className = "st-verb";
    var m = document.createElement("span"); m.className = "st-meta";
    w.appendChild(g); w.appendChild(v); w.appendChild(m);
    messagesEl.appendChild(w);
    scrollDown();

    var cur = STATUS[phase] ? phase : "think", idx = -1, last = "", t0 = Date.now() - (sinceMs || 0), gi = 4, ticks = 0, nextWord = 0, fixed = "";
    function pick() {
      if (fixed) { v.textContent = fixed + "…"; nextWord = Date.now() + 3200; return; } // a specific step is shown
      var list = STATUS[cur] || STATUS.think, word;
      if (list.seq) { idx = Math.min(idx + 1, list.words.length - 1); word = list.words[idx]; }
      else { do { word = list.words[Math.floor(Math.random() * list.words.length)]; } while (word === last && list.words.length > 1); }
      if (cur === "think" && thinkIsOff()) word = "Working";
      last = word; v.textContent = word + "…";
      nextWord = Date.now() + 3200 + Math.random() * 1200;
    }
    function meta() {
      var s = Math.floor((Date.now() - t0) / 1000);
      var time = s < 60 ? s + "s" : Math.floor(s / 60) + "m " + (s % 60) + "s";
      var text = time + (document.body.classList.contains("can-stop") ? " · esc to stop" : "");
      if (m.textContent !== text) m.textContent = text;
    }
    pick(); meta();
    // One timer drives everything: glyph every 140 ms, timer text and word changes when due.
    var timer = setInterval(function () {
      if (!calmMotion && !stopLoader) { gi = (gi + 1) % GLYPHS.length; g.textContent = GLYPHS[gi]; }
      if (calmMotion || ++ticks % 7 === 0) meta();
      if (Date.now() >= nextWord) pick();
    }, calmMotion ? 1000 : 140);
    return {
      el: w,
      phase: function (p) { if (p === "act") p = "work"; if (p !== cur && STATUS[p]) { cur = p; idx = -1; fixed = ""; pick(); } },
      say: function (text) { fixed = String(text || ""); pick(); },
      stop: function () { clearInterval(timer); if (stopLoader) stopLoader(); w.remove(); }
    };
  }

  /* Progress block for Local/Quick/Big/Polish (Claude-style): ONE quiet line with the current step and a timer;
     a chevron shows the steps done so far (no repeats, newest last). When the answer arrives it folds into
     "Worked for 14 s · 5 steps" above it (nothing at all when there were no steps).
     Returns { el, phase(p), step(it), settle(), stop() }. */
  var PG_PHASE = { think: "Thinking", look: "Looking at the picture", write: "Writing the answer",
                   wait: "Waiting for the other chat", stop: "Stopping", big: "Working on the project", polish: "Polishing" };
  var PG_FILLER = /^(working on (it|this)|completed a task|on it)\.*$/i, PG_MAX = 12, pgSeq = 0;
  function fmtSecs(s) { return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s"; }
  function startProgress(phase, sinceMs) {
    messagesEl.querySelectorAll(".working, .progress.live").forEach(function (o) { o.remove(); }); // never show two
    var w = document.createElement("div"); w.className = "progress live";
    var head = document.createElement("button"); head.type = "button"; head.className = "pg-head";
    var listId = "pg-list-" + (++pgSeq);
    head.setAttribute("aria-expanded", "false"); head.setAttribute("aria-controls", listId); head.disabled = true;
    var g = document.createElement("span"); g.className = "pg-glyph"; g.setAttribute("aria-hidden", "true");
    var stopLoader = window.auraLoader ? window.auraLoader(g) : null;
    if (!stopLoader) g.classList.add("dot");
    var t = document.createElement("span"); t.className = "pg-text"; t.setAttribute("aria-live", "polite");
    var m = document.createElement("span"); m.className = "pg-time";
    var c = document.createElement("span"); c.className = "pg-chev"; c.setAttribute("aria-hidden", "true");
    head.appendChild(g); head.appendChild(t); head.appendChild(m); head.appendChild(c);
    var list = document.createElement("ol"); list.className = "pg-list"; list.id = listId; list.hidden = true;
    w.appendChild(head); w.appendChild(list);
    messagesEl.appendChild(w);
    scrollDown();
    head.addEventListener("click", function () {
      var open = head.getAttribute("aria-expanded") !== "true";
      head.setAttribute("aria-expanded", open ? "true" : "false");
      list.hidden = !open; w.classList.toggle("open", open);
    });
    var steps = [], latest = "", cur = PG_PHASE[phase] ? phase : "think", showStep = false, settled = false;
    var t0 = Date.now() - (sinceMs || 0);
    function secs() { return Math.floor((Date.now() - t0) / 1000); }
    function paint() {
      var txt = showStep ? latest : ((cur === "think" && thinkIsOff()) ? "Working" : PG_PHASE[cur]) + "…";
      if (t.textContent !== txt) t.textContent = txt;
    }
    function meta() {
      var txt = fmtSecs(secs()) + (document.body.classList.contains("can-stop") ? " · esc to stop" : "");
      if (m.textContent !== txt) m.textContent = txt;
    }
    paint(); meta();
    var timer = setInterval(meta, calmMotion ? 1000 : 500);
    function quiet() { clearInterval(timer); if (stopLoader) { stopLoader(); stopLoader = null; } }
    return {
      el: w,
      phase: function (p) {
        if (settled || !PG_PHASE[p]) return;          // step kinds (read, run...) come with their own text
        if (p === "think" && latest) return;          // thinking between steps: keep showing the last step
        cur = p; showStep = false; paint();
      },
      step: function (it) {
        var txt = String((it && it.text) || "").replace(/\s+/g, " ").trim();
        if (settled || !txt || PG_FILLER.test(txt)) return;
        latest = txt; showStep = true; paint();
        if (steps.indexOf(txt) === -1) {
          steps.push(txt);
          var li = document.createElement("li"); li.textContent = txt; li.title = txt;
          list.appendChild(li);
          while (list.children.length > PG_MAX) list.removeChild(list.firstChild);
          head.disabled = false; w.classList.add("has-steps");
        }
      },
      settle: function () {                           // the answer is here: fold into one summary line
        if (settled) return;
        settled = true; quiet();
        if (!steps.length) { w.remove(); return; }
        w.classList.remove("live"); w.classList.add("done");
        t.removeAttribute("aria-live"); g.textContent = ""; g.className = "pg-glyph ok";
        t.textContent = "Worked for " + fmtSecs(secs()) + " · " + steps.length + (steps.length === 1 ? " step" : " steps");
        m.textContent = "";
        head.setAttribute("aria-expanded", "false"); list.hidden = true; w.classList.remove("open");
      },
      stop: function () { if (!settled) { settled = true; quiet(); w.remove(); } }
    };
  }

  /* ---------- chats work on their own: the page only watches the open one ---------- */
  var running = {}, view = null; // running: chat id -> working; view: the live work shown for the open chat
  function anyRunning() { return Object.keys(running).length > 0; }
  function paintBusy() {
    busy = !!(currentId && running[currentId]);
    var mine = !!(busy && view && view.sid === currentId);
    sendBtn.disabled = busy;
    stopFn = mine ? view.stop : null;
    document.body.classList.toggle("can-stop", mine);
    document.body.classList.toggle("busy", busy);
  }
  function paintRunDots() {
    Array.prototype.forEach.call($("sessionList").querySelectorAll(".session"), function (r) {
      var on = !!running[r.dataset.id];
      r.classList.toggle("working", on);
      r.setAttribute("aria-label", (r.title || "Chat") + (on ? " (Atlas is working on it)" : ""));
    });
  }
  function setRunning(sid, on) {
    if (sid && !!running[sid] !== !!on) {
      if (on) running[sid] = true; else delete running[sid];
      paintRunDots();
    }
    paintBusy();
  }
  /* Only for things that must wait for every chat (update, delete all). */
  function guardBusy() {
    if (!anyRunning()) return false;
    toast("Atlas is still working on a chat. Wait for it to finish, or press Stop.");
    return true;
  }
  /* Which chats work right now on the Python side: dots in the chat list, and a safety net if the open
     chat's answer finished without the page noticing (it then reloads the chat, which shows the answer). */
  function pollBusy() {
    api.busy_chats().then(function (list) {
      var now = {};
      (list || []).forEach(function (id) { now[id] = true; });
      Object.keys(running).forEach(function (id) { if (!now[id] && !(view && view.sid === id)) delete running[id]; });
      Object.keys(now).forEach(function (id) { running[id] = true; });
      if (view && view.live && !now[view.sid]) { if (++view.lost >= 3) view.resync(); }
      else if (view) view.lost = 0;
      paintRunDots(); paintBusy();
    }).catch(noop).then(function () { setTimeout(pollBusy, 2000); });
  }

  /* ---------- chats list (latest 7, then "Show all") ---------- */
  var RECENT = 7, showAllChats = false;
  function sessionRow(s) {
    var row = document.createElement("div");
    var active = s.id === currentId, title = s.title || "New chat", working = !!running[s.id];
    row.className = "session" + (active ? " active" : "") + (working ? " working" : "");
    row.tabIndex = 0;
    row.dataset.id = s.id;
    row.setAttribute("role", "button");
    if (active) row.setAttribute("aria-current", "true");
    row.title = title;
    row.setAttribute("aria-label", title + (working ? " (Atlas is working on it)" : ""));
    var span = document.createElement("span");
    span.textContent = title;
    var dot = document.createElement("i");
    dot.className = "run-dot"; dot.setAttribute("aria-hidden", "true"); dot.title = "Atlas is working on this chat";
    var del = document.createElement("button");
    del.type = "button"; del.className = "del"; del.textContent = "×";
    del.title = "Delete chat";
    del.setAttribute("aria-label", "Delete chat " + title);
    del.addEventListener("click", function (e) { e.stopPropagation(); deleteChat(s); });
    row.appendChild(span);
    row.appendChild(dot);
    row.appendChild(del);
    row.addEventListener("click", function () { if (s.id !== currentId) openSession(s.id); });
    row.addEventListener("keydown", function (e) {
      if (e.target === row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); row.click(); }
    });
    return row;
  }
  function renderSessions() {
    var list = $("sessionList"), frag = document.createDocumentFragment();
    var lbl = document.querySelector(".side-label");
    // remember which chat row had keyboard focus, so a refresh does not throw the focus away
    var fa = document.activeElement, focusId = null;
    if (fa && list.contains(fa) && fa.closest(".session")) focusId = fa.closest(".session").dataset.id;
    if (lbl) lbl.textContent = "Recent";
    if (!sessions.length) {
      var e = document.createElement("div");
      e.className = "empty"; e.textContent = "No chats yet";
      frag.appendChild(e);
    } else {
      var visible = showAllChats ? sessions : sessions.slice(0, RECENT);
      if (!showAllChats) { // keep the open chat visible even if it is an older one
        for (var k = RECENT; k < sessions.length; k++) if (sessions[k].id === currentId) { visible = visible.concat([sessions[k]]); break; }
      }
      visible.forEach(function (s) { frag.appendChild(sessionRow(s)); });
      if (sessions.length > 0) frag.appendChild(moreButton(list));
    }
    list.textContent = "";
    list.appendChild(frag);
    if (focusId) {
      var again = Array.prototype.filter.call(list.querySelectorAll(".session"), function (r) { return r.dataset.id === focusId; })[0];
      if (again) again.focus();
    }
  }
  function moreButton(list) {
    var more = document.createElement("button"), older = sessions.length - RECENT;
    more.type = "button"; more.className = "more-chats";
    more.setAttribute("aria-haspopup", "dialog");
    more.title = "Open the full chat history";
    var label = document.createElement("span");
    label.textContent = "All chats";
    more.appendChild(label);
    more.insertAdjacentHTML("beforeend", '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="'
      + "M9 6l6 6-6 6" + '"/></svg>');
    more.addEventListener("click", histOpen);
    return more;
  }
  /* ---------- full chat history page (like Claude's "Chats" page), opened from "All chats" in the sidebar ---------- */
  var hvw = null;
  function ago(ms) {
    var s = Math.max(0, (Date.now() - ms) / 1000);
    if (s < 60) return "just now";
    var m = Math.floor(s / 60); if (m < 60) return m + (m === 1 ? " minute ago" : " minutes ago");
    var h = Math.floor(m / 60); if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
    var d = Math.floor(h / 24); if (d < 30) return d + (d === 1 ? " day ago" : " days ago");
    var mo = Math.floor(d / 30); if (mo < 12) return mo + (mo === 1 ? " month ago" : " months ago");
    var y = Math.floor(d / 365); return y + (y === 1 ? " year ago" : " years ago");
  }
  function histGroup(ms) {
    var n = new Date(), t0 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime(), D = 86400000;
    if (!ms) return "Older";
    if (ms >= t0) return "Today";
    if (ms >= t0 - D) return "Yesterday";
    if (ms >= t0 - 6 * D) return "Previous 7 days";
    if (ms >= t0 - 29 * D) return "Previous 30 days";
    return "Older";
  }
  function histRender() {
    if (!hvw) return;
    var q = hvw.q.value.trim().toLowerCase(), list = hvw.list;
    var rows = sessions.filter(function (s) { return !q || (s.title || "").toLowerCase().indexOf(q) >= 0; });
    hvw.count.textContent = q ? rows.length + " of " + sessions.length + " chats" : sessions.length + (sessions.length === 1 ? " chat" : " chats");
    list.textContent = "";
    if (!rows.length) {
      var e = document.createElement("div"); e.className = "hv-empty";
      e.textContent = sessions.length ? "No chats match your search." : "No chats yet. Start one and it shows up here.";
      list.appendChild(e); return;
    }
    var lastGroup = "";
    rows.forEach(function (s, i) {
      var title = s.title || "New chat", ms = s.updated ? s.updated / 1e6 : 0, g = histGroup(ms);
      if (g !== lastGroup) {
        lastGroup = g;
        var gh = document.createElement("div"); gh.className = "hv-group"; gh.textContent = g; list.appendChild(gh);
      }
      var r = document.createElement("div");
      r.className = "hv-row" + (s.id === currentId ? " active" : ""); r.tabIndex = 0; r.setAttribute("role", "button");
      r.setAttribute("aria-label", title);
      r.style.animationDelay = Math.min(i, 12) * 18 + "ms";
      var main = document.createElement("div"); main.className = "hv-main";
      var t = document.createElement("span"); t.className = "hv-title"; t.textContent = title; t.title = title;
      var tm = document.createElement("span"); tm.className = "hv-time";
      tm.textContent = ms ? "Last message " + ago(ms) : "";
      main.appendChild(t); main.appendChild(tm);
      r.appendChild(main);
      if (running[s.id]) { var dot = document.createElement("i"); dot.className = "hv-dot"; dot.title = "Atlas is working on this chat"; r.appendChild(dot); }
      var del = document.createElement("button"); del.type = "button"; del.className = "hv-del"; del.textContent = "×";
      del.title = "Delete chat"; del.setAttribute("aria-label", "Delete chat " + title);
      del.addEventListener("click", function (ev) { ev.stopPropagation(); deleteChat(s); });
      r.appendChild(del);
      function go() { var id = s.id; histClose(true); if (id !== currentId) openSession(id); else focusInput(); }
      r.addEventListener("click", go);
      r.addEventListener("keydown", function (ev) { if (ev.target === r && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); go(); } });
      list.appendChild(r);
    });
  }
  function histOpen() {
    if (hvw) { hvw.q.focus(); return; }
    var main = $("main"); if (!main) return;
    var el = document.createElement("div"); el.id = "histView"; el.setAttribute("role", "region"); el.setAttribute("aria-label", "Chat history");
    el.innerHTML = '<div class="hv-in"><div class="hv-head"><h2>Chats</h2><button type="button" class="hv-new">+ New chat</button>' +
      '<button type="button" class="hv-x" aria-label="Close history" title="Close (Esc)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
      '<label class="hv-search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/></svg>' +
      '<input class="hv-q" type="text" placeholder="Search your chats..." spellcheck="false" autocomplete="off" aria-label="Search your chats"></label>' +
      '<div class="hv-count" role="status"></div><div class="hv-list"></div></div>';
    main.appendChild(el);
    hvw = { el: el, q: el.querySelector(".hv-q"), list: el.querySelector(".hv-list"), count: el.querySelector(".hv-count") };
    el.querySelector(".hv-x").addEventListener("click", function () { histClose(); });
    el.querySelector(".hv-new").addEventListener("click", function () { histClose(true); if (api) newChat().then(freshStart); });
    hvw.q.addEventListener("input", histRender);
    el.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      var items = [hvw.q].concat(Array.prototype.slice.call(hvw.list.querySelectorAll(".hv-row")));
      var i = items.indexOf(document.activeElement); if (i < 0) return;
      e.preventDefault();
      var n = Math.max(0, Math.min(items.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
      items[n].focus();
    });
    histRender();
    refreshSessions(); // fresh list and times
    hvw.q.focus();
  }
  function histClose(keepFocus) {
    if (!hvw) return;
    hvw.el.remove(); hvw = null;
    if (!keepFocus && !document.querySelector(".modal-back")) focusInput();
  }
  var searchBtn = $("searchChat");
  if (searchBtn) searchBtn.addEventListener("click", function () { if (!api) { toast("One moment, still loading."); return; } histOpen(); });
  document.addEventListener("keydown", function (e) { /* Ctrl+K: search your chats */
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (api && !document.querySelector(".modal-back")) histOpen();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (!hvw || e.key !== "Escape" || document.querySelector(".modal-back")) return;
    e.preventDefault(); e.stopPropagation(); /* Esc closes the history first; it must not stop a running answer */
    if (document.activeElement === hvw.q && hvw.q.value) { hvw.q.value = ""; histRender(); } else histClose();
  }, true);
  function deleteChat(s) {
    var text = "“" + (s.title || "New chat") + "” and all its messages will be removed. This can’t be undone.";
    if (running[s.id]) text = "Atlas is still working on this chat; that work stops. " + text;
    askConfirm({ title: "Delete this chat?", text: text, ok: "Delete", danger: true }).then(function (yes) {
      if (!yes) return;
      if (view && view.sid === s.id) view.detach();
      return api.delete_session(s.id).then(function (ok) {
        if (!ok) toast("Could not delete this chat.");
        else {
          setRunning(s.id, false);
          if (currentId === s.id) { currentId = null; messagesEl.textContent = ""; }
        }
        return refreshSessions();
      }).then(function () {
        if (!currentId) return sessions.length ? openSession(sessions[0].id) : newChat();
      });
    }).catch(function () { toast("Could not delete this chat."); });
  }
  function refreshSessions() {
    return api.list_sessions().then(function (list) {
      sessions = list || [];
      renderSessions();
      histRender();
    }).catch(function () { toast("Could not load your chats."); });
  }
  /* A saved message. The internal [Who] tag is dropped; [App] = an error Atlas showed, [Note] = "Stopped." */
  function showStored(m) {
    var raw = m.text || "";
    if (m.role === "user") { addBubble("user", raw); return; }
    var tag = (/^\[([A-Za-z]+)\]\s?/.exec(raw) || [])[1];
    addBubble("assistant", raw.replace(/^\[[A-Za-z]+\]\s?/, ""), tag === "App" ? "error" : tag === "Note" ? "note" : null);
  }
  /* Open a chat. If it is still working (maybe started before you switched away), its live state comes back:
     text so far, step lines and the running timer. The work of the chat you leave keeps going. */
  function openSession(id) {
    histClose(true);
    if (view) view.detach();
    currentId = id;
    messagesEl.textContent = "";
    messagesEl.classList.add("loading"); // no front-page flash while the chat loads
    stick = true;
    renderSessions(); paintBusy();
    return api.open_chat(id).then(function (data) {
      if (currentId !== id) return;
      if (!data || data.error) { addBubble("assistant", "This chat could not be opened.", "error"); return; }
      (data.messages || []).forEach(showStored);
      if (data.run) attachRun(id, data.run).start(); else setRunning(id, false);
      scrollDown(true);
    }).catch(function () {
      if (currentId === id) addBubble("assistant", "This chat could not be opened.", "error");
    }).then(function () { if (currentId === id) messagesEl.classList.remove("loading"); }); // an older, slower load must not end a newer one
  }
  /* ---------- slash commands (like the terminal): type / to see them ---------- */
  var SLASH = [
    { n: "btw", a: "<question>", d: "Ask a quick side question. Your chat and any running work stay untouched.", args: true },
    { n: "new", al: ["clear"], d: "Start a new chat" },
    { n: "mode", a: "aura | quick | local | polish", d: "Switch mode", args: true },
    { n: "think", al: ["effort"], a: "off | low | medium | high", d: "Change how much Atlas thinks", args: true },
    { n: "chats", al: ["resume", "search"], d: "Open your chat history" },
    { n: "copy", d: "Copy Atlas's last answer" },
    { n: "export", d: "Save this chat as a file" },
    { n: "preview", d: "Open or close the preview" },
    { n: "settings", d: "Open settings" },
    { n: "stop", d: "Stop what Atlas is doing" },
    { n: "help", d: "Show all commands" }
  ];
  function slashFind(name) {
    name = String(name).toLowerCase();
    for (var i = 0; i < SLASH.length; i++) { var c = SLASH[i]; if (c.n === name || (c.al && c.al.indexOf(name) >= 0)) return c; }
    return null;
  }

  /* a small card above the box (used by /btw and /help); it is not part of the chat */
  var cardEl = null;
  function cardClose() { if (cardEl) { cardEl.remove(); cardEl = null; } }
  function showCard(title, build) {
    cardClose();
    var wrap = $("composerWrap"); if (!wrap) return null;
    var c = document.createElement("div"); c.className = "side-card"; c.setAttribute("role", "region"); c.setAttribute("aria-label", title);
    var h = document.createElement("div"); h.className = "sc-head";
    var tt = document.createElement("span"); tt.textContent = title;
    var x = document.createElement("button"); x.type = "button"; x.className = "sc-x"; x.textContent = "×"; x.title = "Close"; x.setAttribute("aria-label", "Close");
    x.addEventListener("click", function () { cardClose(); focusInput(); });
    h.appendChild(tt); h.appendChild(x);
    var body = document.createElement("div"); body.className = "sc-body"; build(body);
    c.appendChild(h); c.appendChild(body);
    wrap.insertBefore(c, wrap.firstChild); cardEl = c;
    return body;
  }
  function btwAsk(q) {
    if (!api || !api.btw) { toast("One moment, still loading."); return; }
    var body = showCard("BTW · side question", function (b) {
      var qq = document.createElement("div"); qq.className = "sc-q"; qq.textContent = q;
      var aa = document.createElement("div"); aa.className = "sc-a wait"; aa.textContent = "One moment";
      b.appendChild(qq); b.appendChild(aa);
    });
    if (!body) return;
    var ans = body.querySelector(".sc-a"), mine = cardEl;
    api.btw(currentId, q).then(function (r) {
      if (cardEl !== mine) return;
      ans.classList.remove("wait");
      if (r && r.ok) renderRich(ans, r.text || "No answer."); else ans.textContent = (r && r.error) || "Could not answer right now.";
    }).catch(function () { if (cardEl === mine) { ans.classList.remove("wait"); ans.textContent = "Could not answer right now."; } });
  }
  function helpCard() {
    showCard("Commands", function (b) {
      SLASH.forEach(function (c) {
        var row = document.createElement("div"); row.className = "sc-row";
        var n = document.createElement("span"); n.className = "sl-n"; n.textContent = "/" + c.n + (c.a ? " " + c.a : "");
        var d = document.createElement("span"); d.className = "sl-d2"; d.textContent = c.d;
        row.appendChild(n); row.appendChild(d); b.appendChild(row);
      });
    });
  }
  var SLASH_RUN = {
    btw: function (q) { btwAsk(q); },
    "new": function () { if (!api) { toast("One moment, still loading."); return; } newChat().then(freshStart); },
    mode: function (arg) {
      var id = { aura: "claude", quick: "auto", local: "qwen", polish: "polish" }[String(arg).toLowerCase()], picked = null;
      MODES.forEach(function (m) { if (m.id === id) picked = m; });
      if (!picked) { toast("Modes: aura, quick, local, polish."); return; }
      setMode(picked); toast("Mode: " + picked.label + ".");
    },
    think: function (arg) {
      var lv = String(arg).toLowerCase();
      if (mode === "auto") { toast("Quick never thinks."); return; }
      if (mode === "polish") { toast("Polish always works at full strength."); return; }
      if (THINK_STEPS.indexOf(lv) < 0) { toast("Thinking is " + thinkLevel + ". Use /think off, low, medium or high."); return; }
      thinkLevel = lv;
      try { localStorage.setItem("auraThink", lv); } catch (e) { /* ignore */ }
      paintThink(); pushThink(); toast("Thinking: " + lv + ".");
    },
    chats: function () { histOpen(); },
    copy: function () {
      var all = messagesEl.querySelectorAll(".msg.assistant .bubble:not(.error):not(.note)"), b = all[all.length - 1];
      if (!b) { toast("Nothing to copy yet."); return; }
      copyText(rawOf(b)).then(function () { toast("Copied Atlas's last answer."); });
    },
    "export": function () {
      if (!api || !currentId) { toast("Open a chat first."); return; }
      api.export_chat(currentId).then(function (r) { toast((r && r.status) || "Could not save the chat."); }).catch(function () { toast("Could not save the chat."); });
    },
    preview: function () { $("paneToggle").click(); },
    settings: function () { openSettings(); },
    stop: function () { if (stopFn) stopFn(); else toast("Nothing is running."); },
    help: function () { helpCard(); }
  };
  function slashGuess(name) { // closest command: starts the same way, or is one or two letters away
    name = String(name).toLowerCase();
    function dist(a, b) {
      var d = [], i, j;
      for (i = 0; i <= a.length; i++) { d[i] = [i]; }
      for (j = 1; j <= b.length; j++) { d[0][j] = j; }
      for (i = 1; i <= a.length; i++) for (j = 1; j <= b.length; j++)
        d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      return d[a.length][b.length];
    }
    var best = null, bestD = 99;
    SLASH.forEach(function (c) {
      [c.n].concat(c.al || []).forEach(function (w) {
        var k = (name.indexOf(w) === 0 || w.indexOf(name) === 0) ? 1 : dist(name, w);
        if (k < bestD) { bestD = k; best = c; }
      });
    });
    return bestD <= 2 ? best : null;
  }
  /* returns true when the text was a command (it is then never sent as a message) */
  function slashRun(text) {
    var m = /^\/([A-Za-z]+)(?:\s+([\s\S]*))?$/.exec(text);
    if (!m) return false; // "/usr/bin ..." and normal text go through as messages
    var c = slashFind(m[1]), arg = (m[2] || "").trim();
    if (!c) {
      var g = slashGuess(m[1]);
      toast("Unknown command /" + m[1] + "." + (g ? " Did you mean /" + g.n + "?" : " Type /help to see them."));
      return true;
    }
    if (c.n === "btw" && !arg) { toast("Type your question after /btw, like: /btw what does that mean?"); return true; }
    input.value = ""; autoGrow(); slashHide();
    SLASH_RUN[c.n](arg);
    return true;
  }

  /* the menu that opens while you type "/" */
  var slashEl = null, slashItems = [], slashSel = 0;
  function slashHide() { if (slashEl) { slashEl.remove(); slashEl = null; } slashItems = []; slashSel = 0; }
  function slashPick(c) { // fills the command; runs it straight away when it needs no extra words
    if (c.args) { input.value = "/" + c.n + " "; input.dispatchEvent(new Event("input")); focusInput(); }
    else { input.value = "/" + c.n; slashHide(); send(); }
  }
  function slashShow() {
    var m = /^\/([A-Za-z]*)$/.exec(input.value);
    if (!m) { slashHide(); return; }
    var q = m[1].toLowerCase();
    slashItems = SLASH.filter(function (c) { return !q || c.n.indexOf(q) === 0 || (c.al || []).some(function (a) { return a.indexOf(q) === 0; }); });
    if (!slashItems.length) { slashHide(); return; }
    if (slashSel >= slashItems.length) slashSel = 0;
    if (!slashEl) {
      slashEl = document.createElement("div"); slashEl.id = "slashMenu"; slashEl.setAttribute("role", "listbox"); slashEl.setAttribute("aria-label", "Commands");
      document.body.appendChild(slashEl);
    }
    slashEl.textContent = "";
    slashItems.forEach(function (c, i) {
      var row = document.createElement("div"); row.className = "sl-item" + (i === slashSel ? " sel" : ""); row.setAttribute("role", "option");
      var n = document.createElement("span"); n.className = "sl-n"; n.textContent = "/" + c.n + (c.a ? " " + c.a : "");
      var d = document.createElement("span"); d.className = "sl-d"; d.textContent = c.d;
      row.appendChild(n); row.appendChild(d);
      row.addEventListener("mousedown", function (e) { e.preventDefault(); slashPick(c); });
      slashEl.appendChild(row);
    });
    var r = input.getBoundingClientRect();
    slashEl.style.left = r.left + "px"; slashEl.style.width = r.width + "px"; slashEl.style.bottom = (window.innerHeight - r.top + 10) + "px";
    var sel = slashEl.querySelector(".sel"); if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
  }
  input.addEventListener("input", slashShow);
  input.addEventListener("keydown", function (e) { // runs before the normal Enter handling
    if (!slashEl) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); e.stopImmediatePropagation();
      slashSel = (slashSel + (e.key === "ArrowDown" ? 1 : -1) + slashItems.length) % slashItems.length; slashShow();
    } else if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && !e.isComposing)) {
      e.preventDefault(); e.stopImmediatePropagation(); slashPick(slashItems[slashSel]);
    } else if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); slashHide(); }
  }, true);
  input.addEventListener("blur", function () { setTimeout(slashHide, 150); });
  window.addEventListener("resize", slashHide);

  /* ---------- empty chat: the box sits in the middle of the screen (like Claude) and moves down once you send ---------- */
  (function () {
    var mainEl = $("main"), wrap = $("composerWrap");
    if (!mainEl || !wrap) return;
    var lastTop = -1; // where the box sat in the layout before the chat changed (offsetTop ignores the glide itself)
    function remember() { lastTop = wrap.offsetTop; }
    function sync() {
      var empty = messagesEl.childElementCount === 0 && !messagesEl.classList.contains("loading") && !(typeof termVisible === "function" && termVisible());
      if (empty === mainEl.classList.contains("is-empty")) return;
      var before = lastTop >= 0 ? lastTop : wrap.offsetTop;
      mainEl.classList.toggle("is-empty", empty);
      var after = wrap.offsetTop;
      lastTop = after;
      var d = before - after;
      if (calmMotion || !d) return;
      wrap.style.transition = "none"; wrap.style.transform = "translateY(" + d + "px)";
      void wrap.offsetWidth;
      wrap.style.transition = "transform .45s cubic-bezier(.2,.7,.2,1)"; wrap.style.transform = "";
      setTimeout(function () { wrap.style.transition = ""; }, 520);
    }
    if (window.MutationObserver) new MutationObserver(sync).observe(messagesEl, { childList: true, attributes: true, attributeFilter: ["class"] });
    if (window.ResizeObserver) new ResizeObserver(function () { if (!wrap.style.transition) remember(); }).observe(mainEl);
    window.addEventListener("resize", remember);
    sync(); remember();
  })();

  /* ---------- start page: greeting + a few ideas under the box (like Claude's home screen) ---------- */
  var STARTS = [
    { label: "Build", text: "Build me a website about ", icon: '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>' },
    { label: "Learn", text: "Explain to me in simple words: ", icon: '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v15H5.5A1.5 1.5 0 0 0 4 20.5zM20 5.5A1.5 1.5 0 0 0 18.5 4H13v15h5.5a1.5 1.5 0 0 1 1.5 1.5z"/>' },
    { label: "Organize", text: "Show me what is in my Downloads folder and help me tidy it up.", icon: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>' },
    { label: "Write", text: "Help me write ", icon: '<path d="M4 20l1-4L16 5a2 2 0 0 1 3 3L8 19zM14 7l3 3"/>' },
    { label: "Fix my PC", text: "My PC feels slow. Find out why and tell me what you see.", icon: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>' }
  ];
  function greetWord() { var h = new Date().getHours(); return h < 5 ? "Hello" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }
  function paintGreeting() {
    var g = $("startGreet"), t = document.querySelector(".wl-title");
    if (g) g.querySelector("h1").textContent = greetWord();
    if (t) {
      t.textContent = "";
      var a = document.createElement("span"); a.className = "wl-hi"; a.textContent = greetWord();
      var b = document.createElement("span"); b.className = "wl-sub"; b.textContent = "What are we building today?";
      t.appendChild(a); t.appendChild(b);
    }
  }
  (function buildStartPage() {
    var wrap = $("composerWrap"); if (!wrap || $("startGreet")) return;
    var g = document.createElement("div"); g.id = "startGreet";
    g.innerHTML = "<h1></h1><p>What are we building today?</p>";
    wrap.insertBefore(g, wrap.firstChild);
    var chips = document.createElement("div"); chips.id = "startChips"; chips.setAttribute("role", "group"); chips.setAttribute("aria-label", "Ideas");
    STARTS.forEach(function (s) {
      var b = document.createElement("button"); b.type = "button"; b.className = "start-chip";
      b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + s.icon + "</svg>";
      var l = document.createElement("span"); l.textContent = s.label; b.appendChild(l);
      b.addEventListener("click", function () { putInInput(s.text); });
      chips.appendChild(b);
    });
    wrap.appendChild(chips);
    paintGreeting();
  })();

  /* A new chat looks like the first start: nothing left from the last chat, the opening screen plays again. */
  var introT = 0;
  function freshStart() {
    cardClose(); paintGreeting();
    try { if (paneOpen()) closeBrowser(); } catch (x) { /* ignore */ }
    pics = []; docs = []; renderThumbs();
    editing = false; input.value = ""; autoGrow();
    messagesEl.classList.remove("intro"); void messagesEl.offsetWidth; messagesEl.classList.add("intro");
    clearTimeout(introT); introT = setTimeout(function () { messagesEl.classList.remove("intro"); }, 1000);
    focusInput();
  }
  function newChat() {
    histClose(true);
    return api.new_session().then(function (id) {
      if (!id) throw new Error("no id");
      if (view) view.detach();
      currentId = id;
      messagesEl.textContent = "";
      messagesEl.classList.remove("loading");
      stick = true;
      paintBusy();
      return refreshSessions();
    }).catch(function () { toast("Could not start a new chat. Please try again."); });
  }

  /* ---------- pictures: attach, paste, drag and drop ---------- */
  var pics = [], docs = [], MAX_PIC = 8 * 1024 * 1024, MAX_DOC = 25 * 1024 * 1024;
  function focusInput() { input.focus(); }
  function focusInputSoon() { setTimeout(focusInput, 0); setTimeout(focusInput, 150); }
  function picUrl(p) { return "data:" + p.media_type + ";base64," + p.data; }
  function renderThumbs() {
    var el = $("thumbs"); el.textContent = "";
    pics.forEach(function (p, i) {
      var d = document.createElement("div"); d.className = "th";
      var im = document.createElement("img"); im.src = picUrl(p); im.alt = "Picture " + (i + 1);
      var x = document.createElement("button"); x.type = "button"; x.textContent = "×";
      x.title = "Remove picture";
      x.setAttribute("aria-label", "Remove picture " + (i + 1));
      x.addEventListener("click", function () { pics.splice(i, 1); renderThumbs(); focusInput(); });
      d.appendChild(im); d.appendChild(x); el.appendChild(d);
    });
    docs.forEach(function (f, i) {
      var d = document.createElement("div"); d.className = "th doc";
      var n = document.createElement("span"); n.textContent = f.name; n.title = f.path;
      var x = document.createElement("button"); x.type = "button"; x.textContent = "\u00d7";
      x.title = "Remove file"; x.setAttribute("aria-label", "Remove file " + f.name);
      x.addEventListener("click", function () { docs.splice(i, 1); renderThumbs(); focusInput(); });
      d.appendChild(n); d.appendChild(x); el.appendChild(d);
    });
  }
  /* Any other file (text, code, PDF, zip...): remember where it is; the assistant reads it from that path. */
  function addDoc(f) {
    if (f.size > MAX_DOC) { toast("That file is too big (max 25 MB)."); return; }
    function keep(p) { docs.push({ name: f.name, path: p }); renderThumbs(); focusInput(); }
    if (f.pywebviewFullPath) { keep(f.pywebviewFullPath); return; }
    if (!api) { toast("One moment, still loading."); return; }
    var r = new FileReader();
    r.onload = function () {
      var s = String(r.result);
      api.save_dropped(f.name, s.slice(s.indexOf(",") + 1)).then(function (res) {
        if (res && res.path) keep(res.path); else toast("Could not add that file.");
      }).catch(function () { toast("Could not add that file."); });
    };
    r.onerror = function () { toast("Could not read that file."); };
    r.readAsDataURL(f);
  }
  function addFiles(files) {
    Array.prototype.forEach.call(files, function (f) {
      if (!f.type || f.type.indexOf("image/") !== 0) { addDoc(f); return; }
      if (f.size > MAX_PIC) { toast("That picture is too big (max 8 MB)."); return; }
      var r = new FileReader();
      r.onload = function () {
        var s = String(r.result);
        pics.push({ media_type: f.type, data: s.slice(s.indexOf(",") + 1) });
        renderThumbs();
        focusInput();
      };
      r.onerror = function () { toast("Could not read that picture."); };
      r.readAsDataURL(f);
    });
  }
  function hasFiles(e) {
    var t = e.dataTransfer && e.dataTransfer.types;
    return !!t && Array.prototype.indexOf.call(t, "Files") !== -1;
  }

  /* ---------- sending ---------- */
  /* While Atlas works, look for a website it creates or changes (only while busy, no overlapping calls). */
  function watchSites(since) {
    var timer = setInterval(function () { checkSite(since); }, 4000);
    return function () { clearInterval(timer); checkSite(since); };
  }

  /* ---------- Details: a terminal-style log of what Atlas really runs (opens only when you press it) ---------- */
  function detailsWidget() {
    var rows = [], seq = 0, open = false, MAXROWS = 200;
    var root = document.createElement("div"); root.className = "dt-root";
    var btn = document.createElement("button"); btn.type = "button"; btn.className = "dt-btn"; btn.setAttribute("aria-expanded", "false");
    var panel = document.createElement("div"); panel.className = "dt-panel"; panel.hidden = true;
    var bar = document.createElement("div"); bar.className = "dt-bar";
    var ttl = document.createElement("span"); ttl.textContent = "What Atlas is doing";
    var copy = document.createElement("button"); copy.type = "button"; copy.className = "dt-copy"; copy.textContent = "Copy log";
    bar.appendChild(ttl); bar.appendChild(copy);
    var list = document.createElement("div"); list.className = "dt-list"; list.setAttribute("role", "log"); list.tabIndex = 0;
    panel.appendChild(bar); panel.appendChild(list);
    root.appendChild(btn); root.appendChild(panel);
    function label() { btn.textContent = "Details · " + rows.length + (rows.length === 1 ? " step" : " steps"); }
    function atBottom() { return list.scrollHeight - list.scrollTop - list.clientHeight < 28; }
    function toBottom() { list.scrollTop = list.scrollHeight; }
    btn.addEventListener("click", function () {
      open = !open; panel.hidden = !open; root.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) { toBottom(); try { root.scrollIntoView({ block: "nearest" }); } catch (x) { /* ignore */ } }
    });
    copy.addEventListener("click", function () {
      var text = rows.map(function (r) { return r.n + ". " + r.text + (r.detail ? "\n" + r.detail : "") + (r.out ? "\n" + r.out : ""); }).join("\n\n");
      copyText(text).then(function () { toast("Log copied."); });
    });
    function paintOut(r) {
      if (!r.out) return;
      if (!r.outEl) { r.outEl = document.createElement("pre"); r.outEl.className = "dt-out"; r.el.appendChild(r.outEl); }
      r.outEl.textContent = r.out;
    }
    return {
      root: root,
      mount: function (parent) { parent.appendChild(root); },
      detach: function () { if (root.parentNode) root.parentNode.removeChild(root); return root; },
      count: function () { return rows.length; },
      getScroll: function () { return list.scrollTop; },
      setScroll: function (v) { list.scrollTop = v; },
      add: function (it) {
        var stick = !open || atBottom();
        var r = { n: ++seq, text: String(it.text || "Step"), detail: String(it.detail || ""), out: "" };
        r.el = document.createElement("div"); r.el.className = "dt-item";
        var line = document.createElement("div"); line.className = "dt-line";
        var n = document.createElement("span"); n.className = "dt-n"; n.textContent = String(r.n);
        var t = document.createElement("span"); t.className = "dt-t"; t.textContent = r.text;
        line.appendChild(n); line.appendChild(t); r.el.appendChild(line);
        if (r.detail) { var c = document.createElement("pre"); c.className = "dt-cmd"; c.textContent = r.detail; r.el.appendChild(c); }
        list.appendChild(r.el); rows.push(r);
        if (rows.length > MAXROWS) { var old = rows.shift(); if (old.el.parentNode) old.el.remove(); }
        label();
        if (open && stick) toBottom();
      },
      result: function (out) {
        var r = rows[rows.length - 1]; if (!r) return;
        var stick = !open || atBottom();
        r.out = (r.out ? r.out + "\n" + out : String(out)).slice(0, 1500);
        paintOut(r);
        if (open && stick) toBottom();
      }
    };
  }

  /* The live work of the open chat: status line with timer, streamed text (Atlas mode), step lines
     ("Reading notes.pdf", "Saving index.html"...), then the answer. run = { kind: "aura"|"job", phase,
     elapsed (s), items (what already happened, when coming back to the chat) }. Returns a view:
     start() begins polling, stop() asks Python to stop, detach() only stops watching (the work goes on),
     fail(msg) shows a start error, resync() reloads the chat from what Python saved. */
  function attachRun(sid, run) {
    if (view) view.detach();
    var kind = run.kind === "aura" ? "aura" : "job", finished = false, timer = 0, delay = 80;
    var cur = null, pending = null, raf = 0, stopAsked = 0, elapsed = Math.max(0, +run.elapsed || 0);
    var st = kind === "job" ? startProgress(run.phase || "think", elapsed * 1000) : startStatus(run.phase || "think", elapsed * 1000);
    var stopWatch = watchSites(Date.now() / 1000 - 1 - elapsed);
    var v = { sid: sid, kind: kind, live: false, lost: 0 };
    function flush() {
      raf = 0;
      if (pending) { renderRich(pending.el, pending.text); followAnswer(pending.el); }
      pending = null;
    }
    function keepStatusLast() { // only move the bar when something came after it; keep an open log where it was
      if (messagesEl.lastElementChild !== st.el) {
        var sc = dt ? dt.getScroll() : 0;
        messagesEl.appendChild(st.el);
        if (dt) dt.setScroll(sc);
      }
      scrollDown();
    }
    var dt = null; // the Details log of this run; created with the first step
    function dtAdd(it) {
      if (!it || (!it.detail && !it.text)) return;
      if (!dt) { dt = detailsWidget(); dt.mount(st.el); }
      dt.add(it);
    }
    function dtResult(out) { if (dt && out) dt.result(out); }
    function newBubble() { cur = addBubble("assistant", ""); keepStatusLast(); }
    function chip(it) { dtAdd(it); if (kind === "job") { st.step(it); return; } if (st.say && it.text && !PG_FILLER.test(it.text)) st.say(it.text); cur = null; }
    function close(keepRunning) {
      if (finished) return false;
      finished = true; v.live = false;
      clearTimeout(timer);
      if (raf) { cancelAnimationFrame(raf); flush(); }
      st.stop(); stopWatch();
      if (view === v) view = null;
      if (keepRunning) paintBusy(); else setRunning(sid, false);
      return true;
    }
    function finish(e) {
      if (finished) return;
      if (raf) { cancelAnimationFrame(raf); flush(); } // draw the last text first
      if (cur && !cur.textContent.trim() && cur.parentNode) cur.parentNode.remove(); // never leave an empty bubble
      if (kind === "job") st.settle(); // the progress block stays as one quiet summary line above the answer
      var keepDt = (kind === "aura" && dt && dt.count()) ? dt.detach() : null; // Atlas mode: the log stays under the answer
      close(false);
      if (e.t === "closed") addBubble("assistant", "Atlas stopped unexpectedly. Please send your message again.", "error");
      else if (e.note) addBubble("assistant", e.text || "Stopped.", "note");
      else if (e.error) addBubble("assistant", e.text || "Something went wrong. Please try again.", "error");
      else if (kind === "job") addBubble("assistant", e.text || "No answer.");
      if (keepDt) messagesEl.appendChild(keepDt);
      if (cur && cur.parentNode) followAnswer(cur); else scrollDown(); // only moves while a short answer is being followed
      refreshSessions();
      if (!document.querySelector(".modal-back") && !termVisible()) focusInput();
    }
    function handle(e) {
      if (e.t === "new_bubble") newBubble();
      else if (e.t === "text") {
        st.phase("write");
        if (!cur) newBubble();
        pending = { el: cur, text: e.text || "" };
        if (!raf) raf = requestAnimationFrame(flush);
      } else if (e.t === "tool" || e.t === "step") { st.phase(e.kind || "work"); chip(e); keepStatusLast(); }
      else if (e.t === "result") dtResult(e.out);
      else if (e.t === "phase") { if (!stopAsked) st.phase(e.kind || "think"); }
      else if (e.t === "done" || e.t === "closed") { finish(e); return true; }
      return false;
    }
    function poll() {
      if (finished) return;
      (kind === "aura" ? api.chat_poll(sid) : api.job_poll(sid)).then(function (lines) {
        if (finished) return;
        lines = lines || [];
        for (var k = 0; k < lines.length; k++) {
          var e; try { e = JSON.parse(lines[k]); } catch (x) { continue; }
          if (handle(e)) return;
        }
        if (stopAsked && Date.now() - stopAsked > 6000) { finish({ t: "done", note: true, text: "Stopped." }); return; }
        delay = lines.length ? 80 : Math.min(delay + 40, 400); // fast while text flows, calmer while waiting
        timer = setTimeout(poll, delay);
      }).catch(function () { if (!finished) timer = setTimeout(poll, 600); });
    }
    (run.items || []).forEach(function (it) { if (it.k === "text") cur = addBubble("assistant", it.text || ""); else { chip(it); if (it.out) dtResult(it.out); } });
    keepStatusLast();
    v.start = function () { if (!finished && !v.live) { v.live = true; poll(); } return v; };
    v.stopped = function () { return !!stopAsked; };
    v.fail = function (msg) {
      if (close(false)) { addBubble("assistant", msg || "Atlas could not start. Please try again.", "error"); refreshSessions(); }
    };
    v.detach = function () { close(true); };
    v.stop = function () {
      if (finished || stopAsked) return;
      stopAsked = Date.now();
      st.phase("stop");
      api.chat_stop(sid).catch(noop);
      v.start(); // keep listening: Python answers with "Stopped." (or the reply, if it was just finishing)
    };
    v.resync = function () {
      if (!close(true)) return;
      delete running[sid];
      if (currentId === sid) openSession(sid);
    };
    view = v;
    setRunning(sid, true);
    return v;
  }

  function send() {
    var text = input.value.trim();
    if (text.charAt(0) === "/" && slashRun(text)) return; // a slash command, not a message
    /* nothing typed on an empty chat: Send / Enter sends the idea shown in the box */
    if (!text && !pics.length && !docs.length && emptyIdea && !messagesEl.querySelector(".msg")) text = emptyIdea.replace(/\.{3}$/, "");
    if ((!text && !pics.length && !docs.length) || busy) return;
    if (!api || !currentId) { toast("One moment, the chat is still loading."); return; }
    var images = pics.slice(), files = docs.slice(), shown = text;
    if (editing) { editing = false; if (text) { text = "(edited) " + text; shown = text; } }
    if (images.length && mode === "polish") { toast("Pictures work in Atlas, Quick and Local modes."); return; }
    pics = []; docs = []; renderThumbs();
    input.value = "";
    input.dispatchEvent(new Event("input")); // re-sizes the box and resets the send button colour
    if (files.length) {
      shown = (shown + "\nAttached: " + files.map(function (f) { return f.name; }).join(", ")).trim();
      text = (text + "\n\n[Attached files. Read them from these paths:\n" + files.map(function (f) { return f.path; }).join("\n") + "]").trim();
    }
    var ub = addBubble("user", shown);
    images.forEach(function (p) {
      var im = document.createElement("img"); im.src = picUrl(p); im.alt = "Sent picture";
      ub.insertBefore(im, ub.firstChild);
    });
    startWork(currentId, mode, text, images);
    setTimeout(refreshSessions, 700); // the chat is listed once its first message is saved
  }
  /* Atlas mode streams from the chat's own background process; the other modes run as a background job.
     Either way the answer is saved into THIS chat, even if you switch to another one meanwhile. */
  function startWork(sid, m, text, images) {
    pushThink();
    var v = attachRun(sid, { kind: (m === "claude" || m === "auto") ? "aura" : "job", elapsed: 0, items: [],
                             phase: m === "polish" ? "polish" : (images.length ? "look" : "think") });
    var imgs = images.length ? images : null;
    ((m === "claude" || m === "auto") ? api.chat_send(sid, text, imgs, m) : api.job_start(sid, text, m, imgs)).then(function (r) {
      if (!r || !r.ok) { v.fail((r && r.error) || "Atlas could not start. Please try again."); return; }
      if (v.stopped()) api.chat_stop(sid).catch(noop); // Stop was pressed while it was starting
      v.start();
    }).catch(function () { v.fail("Atlas could not start. Please try again."); });
  }


  /* ---------- modes ---------- */
  /* ---------- thinking switch (Off / Low / Medium / High) ---------- */
  var THINK_STEPS = ["off", "low", "medium", "high"], thinkLevel = "medium";
  try { var tl0 = localStorage.getItem("auraThink"); if (THINK_STEPS.indexOf(tl0) >= 0) thinkLevel = tl0; } catch (e) {}
  function thinkIsOff() { return mode === "auto" || (mode !== "polish" && thinkLevel === "off"); } // Quick never thinks; Off means off
  function paintThink() {
    var b = $("thinkBtn"); if (!b) return;
    var locked = mode === "auto" || mode === "polish";
    var lv = mode === "auto" ? "off" : mode === "polish" ? "high" : thinkLevel;
    var name = lv.charAt(0).toUpperCase() + lv.slice(1), n = THINK_STEPS.indexOf(lv); // bars lit: off 0, low 1, medium 2, high 3
    b.innerHTML = '<span class="tk-l">Thinking</span><span class="tk-m" aria-hidden="true"><i' + (n > 0 ? ' class="on"' : '') + '></i><i' +
      (n > 1 ? ' class="on"' : '') + '></i><i' + (n > 2 ? ' class="on"' : '') + '></i></span><span class="tk-v">' + name + '</span>';
    b.setAttribute("aria-label", "Thinking: " + name + (locked ? "" : ". Click to change."));
    b.classList.toggle("on", lv !== "off");
    b.disabled = locked;
    b.title = mode === "auto" ? "Quick never thinks" : mode === "polish" ? "Polish always thinks" : "Click to change how much Atlas thinks";
  }
  function pushThink() { try { if (api && api.set_think) api.set_think(thinkLevel); } catch (e) {} }
  (function initThink() {
    var b = $("thinkBtn"); if (!b) return;
    b.addEventListener("click", function () {
      thinkLevel = THINK_STEPS[(THINK_STEPS.indexOf(thinkLevel) + 1) % THINK_STEPS.length];
      try { localStorage.setItem("auraThink", thinkLevel); } catch (e) {}
      paintThink(); pushThink();
    });
    paintThink();
  })();

  function buildPills() {
    var wrap = $("pills");
    MODES.forEach(function (m) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "pill" + (m.id === mode ? " active" : "");
      b.textContent = m.label;
      b.title = m.tip;
      b.dataset.id = m.id;
      b.setAttribute("aria-pressed", m.id === mode ? "true" : "false");
      b.addEventListener("click", function () { setMode(m); });
      wrap.appendChild(b);
    });
    $("pillHint").textContent = MODES[0].tip;
  }
  function setMode(m) {
    mode = m.id; paintThink();
    Array.prototype.forEach.call($("pills").children, function (c) {
      var on = c.dataset.id === mode;
      c.classList.toggle("active", on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });
    $("pillHint").textContent = m.tip;
  }

  /* ---------- composer ---------- */
  function autoGrow() {
    if (!input.value) { input.style.height = ""; return; } // empty: one row, whatever the hint text says
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }
  (function refitOnResize() { // the chat gets narrower/wider (preview, sidebar, window): measure the text again
    var wrap = $("inputWrap"), lastW = 0;
    function check() { var w = wrap ? wrap.clientWidth : 0; if (w && w !== lastW) { lastW = w; autoGrow(); } }
    if (window.ResizeObserver && wrap) new ResizeObserver(check).observe(wrap);
    window.addEventListener("resize", check);
  })();

  /* ---------- confirm dialog ---------- */
  function askConfirm(o) {
    return new Promise(function (resolve) {
      ovSuspend();
      var prev = document.activeElement, closed = false;
      var back = document.createElement("div"); back.className = "modal-back";
      var card = document.createElement("div"); card.className = "modal" + (o.danger ? " danger" : "");
      card.setAttribute("role", "alertdialog"); card.setAttribute("aria-modal", "true");
      card.setAttribute("aria-labelledby", "mTitle"); card.setAttribute("aria-describedby", "mText");
      var ico = document.createElement("div"); ico.className = "modal-ico"; ico.setAttribute("aria-hidden", "true");
      ico.innerHTML = o.danger
        ? '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v5M14 11v5"/></svg>'
        : '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>';
      var h = document.createElement("h3"); h.id = "mTitle"; h.textContent = o.title;
      var p = document.createElement("p"); p.id = "mText"; p.textContent = o.text;
      var row = document.createElement("div"); row.className = "modal-btns";
      var no = document.createElement("button"); no.type = "button"; no.className = "m-no"; no.textContent = o.cancel || "Cancel";
      var yes = document.createElement("button"); yes.type = "button"; yes.className = "m-yes"; yes.textContent = o.ok || "OK";
      row.appendChild(no); row.appendChild(yes);
      card.appendChild(ico); card.appendChild(h); card.appendChild(p); card.appendChild(row);
      back.appendChild(card); document.body.appendChild(back);
      requestAnimationFrame(function () { back.classList.add("show"); });
      function close(v) {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKey, true);
        back.classList.remove("show");
        setTimeout(function () {
          back.remove();
          ovResume();
          if (prev && prev.focus && document.contains(prev)) { try { prev.focus(); } catch (e) { /* ignore */ } } else focusInput();
        }, 180);
        resolve(v);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); }
        else if (e.key === "Tab") { e.preventDefault(); (document.activeElement === no ? yes : no).focus(); }
      }
      document.addEventListener("keydown", onKey, true);
      no.addEventListener("click", function () { close(false); });
      yes.addEventListener("click", function () { close(true); });
      back.addEventListener("mousedown", function (e) { if (e.target === back) close(false); });
      (o.danger ? no : yes).focus();
    });
  }

  /* ---------- browser pane (right side): websites Atlas builds + normal browsing ---------- */
  var HOME = "https://www.bing.com/";
  var lastSite = null, hist = [], hpos = -1, curUrl = "", navSeq = 0;
  var pane = $("preview"), frame = $("pvFrame"), host = $("pvOverlayHost");

  function isLocalUrl(u) { return /^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(u); }
  function cleanUrl(u) { return u.replace(/[?&]t=\d+$/, ""); }
  function embedUrl(u) {
    var m = u.match(/^https?:\/\/(?:www\.|m\.)?youtube\.com\/watch\?(?:[^#]*&)?v=([\w-]{6,20})/i)
         || u.match(/^https?:\/\/youtu\.be\/([\w-]{6,20})/i)
         || u.match(/^https?:\/\/(?:www\.|m\.)?youtube\.com\/(?:shorts|live)\/([\w-]{6,20})/i);
    return m ? "https://www.youtube.com/embed/" + m[1] + "?autoplay=1&enablejsapi=1" : u;
  }
  function paneOpen() { return pane.classList.contains("open"); }
  function setPaneOpen(on) {
    pane.classList.toggle("open", on);
    $("pvTab").hidden = on;
    var tg = $("paneToggle");
    tg.classList.toggle("on", on);
    tg.setAttribute("aria-pressed", on ? "true" : "false");
  }
  function updNav() { $("pvBack").disabled = hpos <= 0; $("pvFwd").disabled = hpos >= hist.length - 1; }

  var loadingTimer = 0;
  function setLoading(on) {
    pane.classList.toggle("loading", on);
    clearTimeout(loadingTimer);
    if (on) loadingTimer = setTimeout(function () { pane.classList.remove("loading"); }, 15000);
  }
  frame.addEventListener("load", function () { if (frame.getAttribute("src") !== "about:blank") setLoading(false); });

  function loadInFrame(u) {
    ovHide();
    $("pvBlocked").hidden = true; $("pvStart").hidden = true; frame.hidden = false;
    setLoading(true);
    frame.src = "about:blank"; // forces a real reload, even for the same address
    setTimeout(function () { frame.src = u; }, 30);
  }

  /* ---- video control: Atlas can play / pause / mute the YouTube video in the pane and read its REAL state ---- */
  var ytInfo = { paused: null, muted: false, time: 0, seen: 0 };
  frame.setAttribute("allow", "autoplay; encrypted-media; fullscreen; picture-in-picture");
  function ytPost(obj) { try { frame.contentWindow.postMessage(JSON.stringify(obj), "*"); } catch (e) { /* not loaded yet */ } }
  function ytListen() { ytPost({ event: "listening", id: 1, channel: "widget" }); }
  window.addEventListener("message", function (e) {
    if (!frame || e.source !== frame.contentWindow) return;
    var d = e.data;
    if (typeof d === "string") { try { d = JSON.parse(d); } catch (x) { return; } }
    if (!d || typeof d !== "object") return;
    if (d.event === "onReady") ytListen();
    var i = d.info;
    if (d.event === "infoDelivery" && i) {
      if (typeof i.playerState === "number") ytInfo.paused = (i.playerState !== 1 && i.playerState !== 3); // 1 = playing, 3 = buffering
      if (typeof i.muted === "boolean") ytInfo.muted = i.muted;
      if (typeof i.currentTime === "number") ytInfo.time = Math.round(i.currentTime);
      ytInfo.seen = Date.now();
    }
  });
  frame.addEventListener("load", function () {
    ytInfo = { paused: null, muted: false, time: 0, seen: 0 };
    var n = 0, t = setInterval(function () { ytListen(); if (++n > 16 || ytInfo.seen) clearInterval(t); }, 500);
  });
  /* The app asks this (through the control door); the answer is left in window.__mres. */
  window.auraMediaStart = function (action) {
    window.__mres = null;
    function done(o) { window.__mres = JSON.stringify(o); }
    if (typeof ovOn !== "undefined" && ovOn) { done({ use: "overlay" }); return; } // a real browser view: the app handles that one
    if (!frame || frame.hidden || !/youtube\.com\/embed\//.test(frame.src || "")) { done({ ok: false, status: "There is no video in the Atlas browser." }); return; }
    var cmd = { play: "playVideo", pause: "pauseVideo", mute: "mute", unmute: "unMute" }[action];
    if (action === "toggle") cmd = ytInfo.paused ? "playVideo" : "pauseVideo";
    ytListen();
    if (cmd) ytPost({ event: "command", func: cmd, args: [] });
    setTimeout(function () {
      ytListen();
      setTimeout(function () {
        if (!ytInfo.seen || ytInfo.paused === null) { done({ ok: false, status: "The video player did not answer. It may still be loading." }); return; }
        done({ ok: true, paused: !!ytInfo.paused, muted: !!ytInfo.muted, time: ytInfo.time });
      }, 700);
    }, 900);
  };

  /* Sites that refuse a normal frame (YouTube, Google...) get a real browser view fastened exactly over the pane. */
  var ovOn = false, ovSuspended = false, ovTimer = 0;
  function ovRect() {
    var r = host.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    return [r.left * d, r.top * d, r.width * d, r.height * d].map(Math.round);
  }
  function ovSync() {
    if (!ovOn || ovSuspended || !api) return;
    var r = ovRect();
    api.overlay_move(r[0], r[1], r[2], r[3]).catch(noop);
  }
  function ovStartWatch() { if (!ovTimer) ovTimer = setInterval(ovSync, 500); } // only while the view is shown
  function ovHide() {
    if (ovOn && api) api.overlay_hide(true).catch(noop);
    ovOn = false; host.hidden = true;
    clearInterval(ovTimer); ovTimer = 0;
  }
  function ovSuspend() { ovSuspended = true; if (ovOn && api) api.overlay_hide().catch(noop); }
  function ovResume() { ovSuspended = false; ovSync(); }
  window.addEventListener("resize", ovSync);
  if (window.ResizeObserver) new ResizeObserver(ovSync).observe(host);

  function showBlockedCard(u) {
    ovHide();
    setLoading(false);
    $("pvStart").hidden = true; frame.hidden = true; $("pvBlocked").hidden = false;
    $("pvBlockedUrl").textContent = u;
  }
  function showOverlay(u, seq) {
    $("pvStart").hidden = true; frame.hidden = true; $("pvBlocked").hidden = true;
    if (!api) { showBlockedCard(u); return; }
    host.hidden = false;
    setLoading(true);
    setTimeout(function () { // wait for the pane to finish sliding in
      if (seq !== navSeq || !paneOpen()) return;
      var r = ovRect();
      api.overlay_open(u, r[0], r[1], r[2], r[3]).then(function (res) {
        var ok = !!(res && res.ok);
        if (seq !== navSeq || !paneOpen()) { if (ok && !ovOn) api.overlay_hide(true).catch(noop); return; }
        setLoading(false);
        if (!ok) { showBlockedCard(u); return; }
        ovOn = true; ovStartWatch();
        if (ovSuspended) api.overlay_hide().catch(noop); else ovSync();
      }).catch(function () { if (seq === navSeq) showBlockedCard(u); });
    }, 320);
  }

  function unwrapBing(u) { // Bing result links are redirect wrappers; use the real address
    try {
      if (!/^https?:\/\/(?:www\.)?bing\.com\/ck\/a\?/i.test(u)) return u;
      var p = new URL(u).searchParams.get("u");
      if (p && p.indexOf("a1") === 0) {
        var s = atob(p.slice(2).replace(/-/g, "+").replace(/_/g, "/"));
        if (/^https?:\/\//i.test(s)) return s;
      }
    } catch (e) { /* keep the original address */ }
    return u;
  }
  function navigate(u, push) {
    u = unwrapBing(u);
    var seq = ++navSeq;
    setPaneOpen(true); curUrl = u; syncSiteBar();
    if (push !== false) { hist = hist.slice(0, hpos + 1); hist.push(u); hpos = hist.length - 1; }
    updNav();
    $("pvAddr").value = cleanUrl(u);
    var target = embedUrl(u);
    if (isLocalUrl(u) || !api) { loadInFrame(target); return; }
    setLoading(true);
    api.frame_check(target).then(function (r) {
      if (seq !== navSeq) return; // the user went somewhere else meanwhile
      if (r && r.embeddable === false) showOverlay(u, seq); else loadInFrame(target);
    }).catch(function () { if (seq === navSeq) loadInFrame(target); });
  }
  function goText(t) {
    if (!t || !t.trim()) return;
    if (!api) { toast("One moment, still loading."); return; }
    api.normalize_url(t).then(function (u) { if (u) navigate(u); else toast("That address doesn't look right."); }).catch(noop);
  }
  function showSite(r, force) {
    if (!force && lastSite && lastSite.path === r.path && lastSite.mtime === r.mtime && paneOpen()) return;
    var same = lastSite && lastSite.path === r.path;
    lastSite = r;
    if (currentId && api && r.path) api.remember_site(currentId, r.path).catch(noop);
    navigate(r.url + "?t=" + Date.now(), !same);
  }
  var siteCheckBusy = false, siteCheckAgain = null; // one check at a time; a request meanwhile runs right after
  function checkSite(since) {
    if (!api) return;
    if (siteCheckBusy) { siteCheckAgain = since; return; }
    siteCheckBusy = true;
    api.site_check(since).then(function (r) { if (r && r.found) showSite(r); }).catch(noop)
      .then(function () {
        siteCheckBusy = false;
        if (siteCheckAgain !== null) { var next = siteCheckAgain; siteCheckAgain = null; checkSite(next); }
      });
  }
  function newestSite() {
    if (!api) { toast("One moment, still loading."); return; }
    api.site_check(Date.now() / 1000 - 86400).then(function (r) {
      if (r && r.found) showSite(r, true); else toast("No website found yet. Ask Atlas to build one.");
    }).catch(function () { toast("Could not look for a website."); });
  }
  function showingSite() { return lastSite && curUrl.indexOf(lastSite.url) === 0; }
  function openBrowser() {
    if (showingSite()) { showSite(lastSite, true); return; }
    if (curUrl) { navigate(curUrl, false); return; }
    setPaneOpen(true); setTimeout(function () { $("pvAddr").focus(); }, 60);
  }
  function closeBrowser() { navSeq++; ovHide(); setLoading(false); setPaneOpen(false); }

  /* ---------- preview tools: shown while a website Atlas built is open (screen size, auto-reload) ---------- */
  var DEVS = [
    ["phone", "Phone", '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7.5" y="3" width="9" height="18" rx="2"/><path d="M11 18h2"/></svg>'],
    ["tablet", "Tablet", '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M11 18h2"/></svg>'],
    ["desktop", "Desktop", '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="12" rx="1.5"/><path d="M9 20h6M12 17v3"/></svg>']
  ];
  var autoReload = true;
  try { autoReload = localStorage.getItem("auraAutoReload") !== "off"; } catch (x) { /* ignore */ }
  function setDevice(d) {
    pane.dataset.dev = d;
    Array.prototype.forEach.call(pane.querySelectorAll(".ps-dev button"), function (b) {
      var on = b.dataset.dev === d; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  function buildSiteBar() {
    var bar = document.createElement("div"); bar.className = "pv-site"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Preview tools");
    var html = '<span class="ps-kick">PREVIEW</span><span class="ps-name" id="psName"></span><span class="ps-fill"></span><div class="ps-dev" role="group" aria-label="Screen size">';
    DEVS.forEach(function (d) { html += '<button type="button" data-dev="' + d[0] + '" title="' + d[1] + ' size" aria-label="' + d[1] + ' size" aria-pressed="false">' + d[2] + '</button>'; });
    html += '</div><button type="button" class="ps-auto" id="psAuto" title="Reload by itself when the site changes"><i aria-hidden="true"></i><span>Auto</span></button>';
    bar.innerHTML = html;
    pane.insertBefore(bar, pane.querySelector(".pv-body"));
    Array.prototype.forEach.call(bar.querySelectorAll(".ps-dev button"), function (b) { b.addEventListener("click", function () { setDevice(b.dataset.dev); }); });
    var auto = bar.querySelector("#psAuto");
    function paintAuto() { auto.setAttribute("aria-pressed", autoReload ? "true" : "false"); }
    auto.addEventListener("click", function () {
      autoReload = !autoReload; paintAuto();
      try { localStorage.setItem("auraAutoReload", autoReload ? "on" : "off"); } catch (x) { /* ignore */ }
      toast(autoReload ? "Preview reloads by itself when the site changes." : "Auto reload is off.");
    });
    paintAuto(); setDevice("desktop");
  }
  function syncSiteBar() {
    var on = !!showingSite();
    pane.classList.toggle("is-site", on);
    var n = $("psName");
    if (on && n && lastSite && lastSite.path) {
      var parts = String(lastSite.path).split(/[\\/]/);
      n.textContent = parts.slice(-2).join("/"); n.title = lastSite.path;
    }
  }
  buildSiteBar();
  setInterval(function () { /* only reloads THIS site, and only when its file really changed */
    if (!autoReload || !api || !paneOpen() || !showingSite() || document.hidden || siteCheckBusy) return;
    api.site_check(lastSite.mtime + 0.001).then(function (r) {
      if (r && r.found && lastSite && r.path === lastSite.path && r.mtime !== lastSite.mtime) showSite(r);
    }).catch(noop);
  }, 3000);

  $("pvBack").addEventListener("click", function () { if (hpos > 0) { hpos--; navigate(hist[hpos], false); } });
  $("pvFwd").addEventListener("click", function () { if (hpos < hist.length - 1) { hpos++; navigate(hist[hpos], false); } });
  $("pvReload").addEventListener("click", function () { if (showingSite()) showSite(lastSite, true); else if (curUrl) navigate(curUrl, false); });
  $("pvHome").addEventListener("click", function () { navigate(HOME); });
  $("pvAddr").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); goText(this.value); } });
  $("pvAddr").addEventListener("focus", function () { this.select(); });
  $("pvWin").addEventListener("click", function () { if (api) api.open_browser_window(curUrl ? cleanUrl(curUrl) : HOME).catch(noop); });
  $("pvOpen").addEventListener("click", function () {
    if (!api || !curUrl) return;
    (isLocalUrl(curUrl) && lastSite ? api.open_external(lastSite.path) : api.open_url(cleanUrl(curUrl))).catch(noop);
  });
  $("pvBlockWin").addEventListener("click", function () { if (api && curUrl) api.open_browser_window(cleanUrl(curUrl)).catch(noop); });
  $("pvBlockExt").addEventListener("click", function () { if (api && curUrl) api.open_url(cleanUrl(curUrl)).catch(noop); });
  $("pvNewest").addEventListener("click", newestSite);
  $("pvClose").addEventListener("click", closeBrowser);
  $("pvTab").addEventListener("click", openBrowser);
  $("paneToggle").addEventListener("click", function () { if (paneOpen()) closeBrowser(); else openBrowser(); });
  updNav();
  $("pvTab").hidden = paneOpen(); // visible from the start while the pane is closed

  /* The assistant can ask for a website to be opened (control door -> poll_open). */
  function pollOpen() {
    api.poll_open().then(function (list) {
      if (list && list.length) { uiLog("open request: " + list[list.length - 1]); navigate(list[list.length - 1]); }
    }).catch(noop).then(function () { setTimeout(pollOpen, 1500); });
  }

  /* ---------- window: own top strip (no native title bar), sidebar, window buttons ---------- */
  var sbHidden = false;
  try { sbHidden = localStorage.getItem("auraSidebar") === "0"; } catch (e) { /* ignore */ }
  function setSidebar(show) {
    sbHidden = !show;
    document.body.classList.toggle("sb-hidden", sbHidden);
    $("sbToggle").setAttribute("aria-expanded", show ? "true" : "false");
    try { localStorage.setItem("auraSidebar", show ? "1" : "0"); } catch (e) { /* ignore */ }
    requestAnimationFrame(ovSync);
  }
  setSidebar(!sbHidden);
  function showMax(r) {
    var on = !!(r && r.max), b = $("wcMax");
    b.classList.toggle("is-max", on);
    b.title = on ? "Restore" : "Maximize"; b.setAttribute("aria-label", b.title);
  }
  function syncMax() { if (api && api.win_is_max) api.win_is_max().then(showMax).catch(noop); }
  function dragWin(e) {
    if (e.button !== 0 || !api) return;
    e.preventDefault();
    api.win_drag().then(syncMax).catch(noop);
  }

  // resize edges: the window has no Windows frame (so no shadow); thin invisible strips resize it instead
  (function () {
    var st = document.createElement("style");
    st.textContent = ".rz{position:fixed;z-index:2147483000;-webkit-app-region:no-drag}.rz-n,.rz-s{left:8px;right:8px;height:5px}.rz-n{top:0;cursor:ns-resize}.rz-s{bottom:0;cursor:ns-resize}.rz-e,.rz-w{top:8px;bottom:8px;width:5px}.rz-e{right:0;cursor:ew-resize}.rz-w{left:0;cursor:ew-resize}.rz-ne,.rz-nw,.rz-se,.rz-sw{width:10px;height:10px}.rz-ne{top:0;right:0;cursor:nesw-resize}.rz-sw{bottom:0;left:0;cursor:nesw-resize}.rz-nw{top:0;left:0;cursor:nwse-resize}.rz-se{bottom:0;right:0;cursor:nwse-resize}body.win-max .rz{display:none}";
    document.head.appendChild(st);
    ["n", "s", "e", "w", "ne", "nw", "se", "sw"].forEach(function (k) {
      var d = document.createElement("div");
      d.className = "rz rz-" + k;
      d.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        try { window.pywebview.api.win_resize(k); } catch (x) { /* ignore */ }
      });
      document.body.appendChild(d);
    });
  })();

  // right-click menu (inputs): spelling suggestions, Undo/Redo, Cut/Copy/Paste/Select all, Fix spelling of the whole box
  (function () {
    var st = document.createElement("style");
    st.textContent = "#ctxMenu{position:fixed;z-index:2147483600;min-width:210px;padding:5px;border-radius:10px;background:var(--overlay,#1a1b1f);border:1px solid var(--line2,rgba(255,255,255,.12));box-shadow:0 12px 32px rgba(0,0,0,.38),0 2px 6px rgba(0,0,0,.2);font:13px/1.2 var(--font-ui,system-ui,sans-serif);color:var(--text,#ddd);animation:ctxIn .1s ease-out;user-select:none}@keyframes ctxIn{from{opacity:0;transform:translateY(-3px) scale(.98)}to{opacity:1;transform:none}}#ctxMenu button{display:flex;align-items:center;justify-content:space-between;gap:24px;width:100%;text-align:left;padding:7px 10px;border:0;border-radius:6px;background:none;color:inherit;font:inherit;cursor:default}#ctxMenu button:hover:not(:disabled){background:var(--hover,rgba(255,255,255,.08));color:var(--hi,#fff)}#ctxMenu button:disabled{opacity:.38}#ctxMenu kbd{font:11px var(--font-ui,system-ui,sans-serif);color:var(--dim,#888);letter-spacing:.02em}#ctxMenu hr{border:0;border-top:1px solid var(--line,rgba(255,255,255,.08));margin:5px 4px}#ctxMenu .ctx-sug{font-weight:600;color:var(--acc-text,var(--acc,#8af))}#ctxMenu .ctx-note{padding:6px 10px;color:var(--dim,#888);font-size:12px}";
    document.head.appendChild(st);
    var inp = document.getElementById("input");
    if (inp) { inp.setAttribute("spellcheck", "true"); inp.setAttribute("lang", "en"); }
    var menu = null;
    function close() { if (menu) { menu.remove(); menu = null; } }
    function insert(el, text) {
      el.focus();
      if (!document.execCommand("insertText", false, text) && "value" in el) {
        var a = el.selectionStart || 0, b = el.selectionEnd || 0;
        el.value = el.value.slice(0, a) + text + el.value.slice(b);
        el.selectionStart = el.selectionEnd = a + text.length;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
    function wordAt(el) {
      if (!("value" in el)) return null;
      var v = el.value, a = el.selectionStart, b = el.selectionEnd;
      if (a !== b) { var s = v.slice(a, b); return /^[A-Za-z'’-]{2,40}$/.test(s) ? { a: a, b: b, w: s } : null; }
      var l = a, r = a;
      while (l > 0 && /[A-Za-z'’-]/.test(v[l - 1])) l--;
      while (r < v.length && /[A-Za-z'’-]/.test(v[r])) r++;
      return r - l >= 2 ? { a: l, b: r, w: v.slice(l, r) } : null;
    }
    document.addEventListener("contextmenu", function (e) {
      var t = e.target, editable = !!(t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && /^(text|search|url|email|password|)$/i.test(t.type)) || t.isContentEditable));
      var sel = String(window.getSelection ? window.getSelection() : "");
      if (editable && "selectionStart" in t && t.selectionStart !== t.selectionEnd) sel = t.value.slice(t.selectionStart, t.selectionEnd);
      if (!editable && !sel) return;
      e.preventDefault();
      close();
      var api = window.pywebview && window.pywebview.api;
      menu = document.createElement("div");
      menu.id = "ctxMenu";
      var top = document.createElement("div");   // spelling suggestions land here when they arrive
      menu.appendChild(top);
      function item(label, hint, enabled, fn, cls) {
        var b = document.createElement("button");
        b.type = "button"; b.disabled = !enabled;
        var s = document.createElement("span"); s.textContent = label; if (cls) s.className = cls;
        b.appendChild(s);
        if (hint) { var k = document.createElement("kbd"); k.textContent = hint; b.appendChild(k); }
        b.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
        b.addEventListener("click", function () { close(); fn(); });
        return b;
      }
      function add(b) { menu.appendChild(b); }
      function rule() { menu.appendChild(document.createElement("hr")); }
      if (editable) {
        add(item("Undo", "Ctrl+Z", true, function () { t.focus(); document.execCommand("undo"); }));
        add(item("Redo", "Ctrl+Y", true, function () { t.focus(); document.execCommand("redo"); }));
        rule();
        add(item("Cut", "Ctrl+X", !!sel, function () { t.focus(); if (!document.execCommand("cut") && navigator.clipboard) { navigator.clipboard.writeText(sel).catch(function () {}); document.execCommand("delete"); } }));
      }
      add(item("Copy", "Ctrl+C", !!sel, function () { if (!document.execCommand("copy") && navigator.clipboard) navigator.clipboard.writeText(sel).catch(function () {}); }));
      if (editable) {
        add(item("Paste", "Ctrl+V", true, function () {
          if (api && api.clipboard_text) api.clipboard_text().then(function (s) { if (s) insert(t, s); }).catch(function () {});
          else if (navigator.clipboard && navigator.clipboard.readText) navigator.clipboard.readText().then(function (s) { if (s) insert(t, s); }).catch(function () {});
        }));
        add(item("Select all", "Ctrl+A", true, function () { t.focus(); if (t.select) t.select(); else document.execCommand("selectAll"); }));
        if (api && api.spell_check && "value" in t && t.value.trim()) {
          rule();
          add(item("Fix spelling in the whole box", "", true, function () {
            var orig = t.value;
            api.spell_check(orig, "all").then(function (r) {
              if (r && r.text && r.text !== orig && t.value === orig) { t.focus(); t.select(); insert(t, r.text); }
            }).catch(function () {});
          }));
          var wd = wordAt(t);
          if (wd) {
            var note = document.createElement("div"); note.className = "ctx-note"; note.textContent = "Checking “" + wd.w + "”…";
            top.appendChild(note);
            api.spell_check(wd.w, "word").then(function (r) {
              if (!menu || !top.isConnected) return;
              top.textContent = "";
              var list = (r && r.suggestions) || [];
              if (!list.length) { return; }
              list.slice(0, 5).forEach(function (sg) {
                top.appendChild(item(sg, "", true, function () { t.focus(); t.setSelectionRange(wd.a, wd.b); insert(t, sg); }, "ctx-sug"));
              });
              top.appendChild(document.createElement("hr"));
            }).catch(function () { top.textContent = ""; });
          }
        }
      }
      document.body.appendChild(menu);
      var r = menu.getBoundingClientRect();
      menu.style.left = Math.max(4, Math.min(e.clientX, innerWidth - r.width - 4)) + "px";
      menu.style.top = Math.max(4, Math.min(e.clientY, innerHeight - r.height - 4)) + "px";
    });
    window.addEventListener("mousedown", function (e) { if (menu && !menu.contains(e.target)) close(); }, true);
    window.addEventListener("blur", close);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  })();
  function toggleMax() { if (api) api.win_max().then(showMax).catch(noop); }
  var maxTimer = 0;
  window.addEventListener("resize", function () { clearTimeout(maxTimer); maxTimer = setTimeout(syncMax, 250); });
  $("sbToggle").addEventListener("click", function () { setSidebar(sbHidden); });
  $("tsDrag").addEventListener("mousedown", dragWin);
  $("tsDrag").addEventListener("dblclick", toggleMax);

  /* ---- live reminder: pops up like a notification, wherever she is in the app ---- */
  (function () {
    var box = $("reminder"); if (!box) return;
    document.body.appendChild(box); // a fixed notification must not live inside #main
    var txt = box.querySelector(".rm-text"), seenKey = "auraReminderSeen", cur = { id: "", text: "" }, last = 0, showing = false, focusSecs = 0;
    function seen() { try { return localStorage.getItem(seenKey) || ""; } catch (e) { return ""; } }
    function splashUp() { var s = $("splash"); return !!(s && s.isConnected && !s.classList.contains("hide")); }
    function hide() {
      if (!showing) return;
      showing = false; box.classList.add("out");
      setTimeout(function () { if (!showing) { box.hidden = true; box.classList.remove("out"); } }, 350);
    }
    function markSeen() {
      try { localStorage.setItem(seenKey, cur.id); } catch (e) { /* ignore */ }
      if (api && api.reminder_seen && cur.id) api.reminder_seen(cur.id).catch(noop); // remembered on this PC for good
      hide();
    }
    function show() {
      if (!cur.text || cur.id === seen()) { hide(); return; }
      if (splashUp()) { setTimeout(show, 250); return; } // pop up right after the loading screen so the animation is seen
      if (showing) { if (txt.textContent !== cur.text) { txt.textContent = cur.text; focusSecs = 0; } return; } // a different message restarts the 15 seconds; a re-check does not
      txt.textContent = cur.text;
      showing = true; focusSecs = 0;
      box.classList.remove("out"); box.hidden = false;
      box.classList.remove("pop"); void box.offsetWidth; box.classList.add("pop"); // play the pop-up animation
    }
    box.querySelector(".rm-x").addEventListener("click", markSeen);
    // she has read it when she starts typing, or after 15 seconds with Atlas in front of her; then it is gone for good
    var inEl = $("input"); if (inEl) inEl.addEventListener("input", function () { if (showing) markSeen(); });
    setInterval(function () { if (showing && !splashUp() && document.hasFocus() && ++focusSecs >= 15) markSeen(); }, 1000);
    function load() {
      if (!api || !api.get_reminder) return;
      last = Date.now();
      api.get_reminder().then(function (r) {
        if (!r) return;
        cur = { id: String(r.id || ""), text: String(r.text || "") };
        show();
      }).catch(noop);
    }
    var firstTry = setInterval(function () { if (api) { clearInterval(firstTry); load(); } }, 300); // first read the moment the app is ready
    setInterval(function () { if (api && Date.now() - last > (document.hasFocus() ? 4000 : 15000)) load(); }, 1000); // a new reminder shows up within seconds
    window.addEventListener("focus", function () { if (Date.now() - last > 2000) load(); });
  })();

  /* ---- app menu (top-left): the same things a File / Help menu would hold ---- */
  (function () {
    var btn = $("appMenuBtn"); if (!btn) return;
    var menu = document.createElement("div"); menu.id = "appMenu"; menu.setAttribute("role", "menu"); menu.hidden = true;
    var items = [
      ["New chat", "", function () { $("newChat").click(); }],
      ["Search chats", "Ctrl+K", function () { $("searchChat").click(); }],
      ["-"],
      ["Save this chat as a file", "", function () {
        if (!api || !currentId) { toast("Open a chat first."); return; }
        api.export_chat(currentId).then(function (r) { toast((r && r.status) || "Could not save the chat."); }).catch(function () { toast("Could not save the chat."); });
      }],
      ["Open chats folder", "", function () { if (api) api.open_chats_folder().then(function (r) { if (r && r.status !== "Opened.") toast(r.status); }).catch(noop); }],
      ["-"],
      ["Settings", "", function () { $("settingsBtn").click(); }],
      ["Help and commands", "/help", function () { helpCard(); }],
      ["-"],
      ["Exit Atlas", "", function () { if (api) api.win_close().catch(noop); }]
    ];
    items.forEach(function (it) {
      if (it[0] === "-") { var hr = document.createElement("div"); hr.className = "am-sep"; menu.appendChild(hr); return; }
      var b = document.createElement("button"); b.type = "button"; b.setAttribute("role", "menuitem");
      var l = document.createElement("span"); l.textContent = it[0]; b.appendChild(l);
      if (it[1]) { var k = document.createElement("kbd"); k.textContent = it[1]; b.appendChild(k); }
      b.addEventListener("click", function () { close(); it[2](); });
      menu.appendChild(b);
    });
    document.body.appendChild(menu);
    function close() { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    function open() {
      var r = btn.getBoundingClientRect();
      menu.style.left = Math.max(8, r.left) + "px"; menu.style.top = (r.bottom + 6) + "px";
      menu.hidden = false; btn.setAttribute("aria-expanded", "true");
    }
    btn.addEventListener("mousedown", function (e) { e.stopPropagation(); }); // a click here must not drag the window
    btn.addEventListener("click", function (e) { e.stopPropagation(); menu.hidden ? open() : close(); });
    document.addEventListener("mousedown", function (e) { if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !menu.hidden) { close(); e.stopPropagation(); } }, true);
    window.addEventListener("blur", close);
  })();
  $("splash").addEventListener("mousedown", dragWin); // the loading screen covers the title bar: it can still move the window
  document.querySelector("#sidebar .brand").addEventListener("mousedown", dragWin);
  (function () {  // minimize with a short slide-and-fade, and the reverse when the window comes back
    var wasMin = false, inTimer = null;
    function comeBack() {
      if (!wasMin || document.hidden) return;
      wasMin = false;
      document.body.classList.remove("win-out");
      document.body.classList.add("win-in");
      clearTimeout(inTimer);
      inTimer = setTimeout(function () { document.body.classList.remove("win-in"); }, 320);
    }
    $("wcMin").addEventListener("click", function () {
      if (!api) return;
      wasMin = true;
      document.body.classList.remove("win-in");
      api.win_min().catch(noop);
      setTimeout(function () { if (wasMin && !document.hidden && document.hasFocus()) comeBack(); }, 2500); // minimize did not happen: undo
    });
    document.addEventListener("visibilitychange", comeBack);
    window.addEventListener("focus", comeBack);
  })();
  $("wcMax").addEventListener("click", toggleMax);
  $("wcClose").addEventListener("click", function () { if (api) api.win_close().catch(noop); });

  /* ---------- keyboard + press feedback ---------- */
  function termVisible() { return $("termView").style.display !== "none"; }
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key && e.key.toLowerCase() === "b") {
      e.preventDefault(); setSidebar(sbHidden); return;
    }
    if (e.key === "Escape" && stopFn) { e.preventDefault(); stopFn(); return; }
    // Typing anywhere goes to the text box (letters, digits, symbols; Space/Enter keep working on buttons).
    if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing || !e.key || e.key.length !== 1 || e.key === " ") return;
    var a = document.activeElement;
    if (a && (a === input || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return;
    if (document.querySelector(".modal-back") || termVisible() || !$("modeMenu").hidden) return;
    input.focus();
  });

  /* Keep Tab inside a dialog (focus trap). */
  function trapTab(card, e) {
    var f = Array.prototype.filter.call(card.querySelectorAll("button, select, input, textarea, [tabindex]:not([tabindex='-1'])"),
      function (el) { return !el.disabled && el.offsetParent !== null; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1], a = document.activeElement;
    if (e.shiftKey && (a === first || !card.contains(a))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (a === last || !card.contains(a))) { e.preventDefault(); first.focus(); }
  }

  /* A quiet press feedback (small scale/fade dip) whenever a button or chat row is pressed. */
  document.addEventListener("click", function (e) {
    if (calmMotion || !e.target || !e.target.closest) return;
    var t = e.target.closest("button, .session");
    if (!t || t.disabled) return;
    t.classList.remove("press");
    void t.offsetWidth; // restart the animation
    t.classList.add("press");
  }, true);
  document.addEventListener("animationend", function (e) {
    if (e.animationName === "press") e.target.classList.remove("press");
  });

  /* ---------- hidden terminal mode (scripts load only on first use) ---------- */
  var term = null, fit = null, termTimer = 0;
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script"); s.src = src; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }
  function loadTerminalLibs() {
    if (window.Terminal && window.FitAddon) return Promise.resolve();
    var css = document.createElement("link"); css.rel = "stylesheet"; css.href = "vendor/xterm.css";
    document.head.appendChild(css);
    return loadScript("vendor/xterm.js").then(function () { return loadScript("vendor/addon-fit.js"); });
  }
  function createTerminal() {
    term = new window.Terminal({ fontSize: 15, lineHeight: 1.45, letterSpacing: 0.2, cursorBlink: true,
      cursorStyle: "bar", cursorWidth: 2, scrollback: 5000,
      fontFamily: "'Cascadia Code','Cascadia Mono',Consolas,monospace",
      theme: { background: "#0F1117", foreground: "#E6E8EE", cursor: "#4D5BFF", cursorAccent: "#0F1117",
        selectionBackground: "#2E3550", black: "#1B1E27", red: "#F2706B", green: "#7FD18B",
        yellow: "#E8C46A", blue: "#7C84F8", magenta: "#C792EA", cyan: "#6FC7D6", white: "#B3B7C4",
        brightBlack: "#5A6070", brightRed: "#FF8A85", brightGreen: "#98E2A3", brightYellow: "#F2D58A",
        brightBlue: "#9EA5FF", brightMagenta: "#D8A9F2", brightCyan: "#8FDCE8", brightWhite: "#F3F4F7" } });
    fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open($("termHost"));
    term.onData(function (d) { api.term_write(d).catch(noop); });
    term.onResize(function (s) { api.term_resize(s.cols, s.rows).catch(noop); });
    window.addEventListener("resize", function () { if (fit && termVisible()) fit.fit(); });
  }
  function readTerminal() {
    api.term_read().then(function (s) { if (s && term) term.write(s); }).catch(noop);
  }
  function showTerm(on) {
    $("main").style.display = on ? "none" : "flex";
    $("termView").style.display = on ? "flex" : "none";
    if (!on) { clearInterval(termTimer); termTimer = 0; focusInput(); return; }
    loadTerminalLibs().then(function () {
      if (!term) createTerminal();
      fit.fit();
      return api.term_start(term.cols, term.rows);
    }).then(function (r) {
      if (!r || (r.status !== "running" && r.status !== "started")) { toast((r && r.status) || "Could not start the terminal."); return; }
      if (!termTimer) termTimer = setInterval(readTerminal, 40); // only while the terminal is shown
      term.focus();
    }).catch(function () { toast("Could not start the terminal."); });
  }
  window.auraTerminal = showTerm; // hidden mode: auraTerminal(true) from the dev console

  /* ---------- start: called once the Python side is ready ---------- */
  function ready() {
    if (started || !window.pywebview || !window.pywebview.api) return;
    started = true;
    api = window.pywebview.api;
    try { requestAnimationFrame(function () { setTimeout(function () { try { api.win_ready(); } catch (e) { /* ignore */ } }, 60); }); } catch (e) { /* ignore */ }
    try { if (window.auraTheme) window.auraTheme.set(window.auraTheme.get()); } catch (e) { /* ignore */ }
    api.get_version().then(function (v) { $("version").textContent = "v" + v; }).catch(noop);
    refreshSessions().then(function () {
      if (sessions.length && sessions[0].title === "New chat") return openSession(sessions[0].id);
      return newChat();
    }).catch(function () { try { newChat(); } catch (e) { /* nothing more to do */ } });
    pollOpen();
    pollBusy();
    syncMax();
    startUpdateChecks(2700); // toast after the splash is gone
    input.focus();
  }

  input.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.isComposing) return;
    if (settings.enterSends ? !e.shiftKey : (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
  });
  input.addEventListener("input", autoGrow);
  sendBtn.addEventListener("click", send);
  $("stopBtn").addEventListener("click", function () { if (stopFn) stopFn(); });
  $("newChat").addEventListener("click", function () {
    if (!api) { toast("One moment, still loading."); return; }
    newChat().then(freshStart); // the chat you leave keeps working in the background
  });
  $("attachBtn").addEventListener("click", function () { $("fileIn").click(); });
  $("fileIn").addEventListener("change", function () { addFiles($("fileIn").files); $("fileIn").value = ""; focusInputSoon(); });
  $("fileIn").addEventListener("cancel", focusInputSoon);
  document.addEventListener("paste", function (e) {
    var files = (e.clipboardData && e.clipboardData.files) || [];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  var dragDepth = 0;
  function endDrag() { dragDepth = 0; document.body.classList.remove("drag"); }
  document.addEventListener("dragenter", function (e) { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; document.body.classList.add("drag"); });
  document.addEventListener("dragover", function (e) { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener("dragleave", function (e) { if (hasFiles(e) && --dragDepth <= 0) endDrag(); });
  document.addEventListener("drop", function (e) {
    if (!hasFiles(e)) return;
    e.preventDefault(); endDrag();
    addFiles(e.dataTransfer.files);
  });
  $("backChat").addEventListener("click", function () { showTerm(false); });
  $("restartClaude").addEventListener("click", function () {
    if (!term) return;
    api.term_restart().then(function () { term.reset(); return api.term_start(term.cols, term.rows); }).catch(noop);
  });
  /* ---------- settings ---------- */
  var DEFAULTS = { size: "normal", calm: false, enterSends: true, startMode: "claude" };
  var settings = (function () {
    var o = {}, k;
    try { o = JSON.parse(localStorage.getItem("auraSettings") || "{}") || {}; } catch (e) { o = {}; }
    for (k in DEFAULTS) if (typeof o[k] !== typeof DEFAULTS[k]) o[k] = DEFAULTS[k];
    return o;
  })();
  var osCalm = calmMotion;
  function saveSettings() { try { localStorage.setItem("auraSettings", JSON.stringify(settings)); } catch (e) { /* ignore */ } }
  function applySettings() {
    document.body.classList.toggle("ts-small", settings.size === "small");
    document.body.classList.toggle("ts-large", settings.size === "large");
    document.body.classList.toggle("calm", settings.calm);
    calmMotion = osCalm || settings.calm;
    var h = $("hint");
    if (h) h.innerHTML = settings.enterSends
      ? "<kbd>Enter</kbd> send &middot; <kbd>Shift+Enter</kbd> new line &middot; <kbd>Esc</kbd> stop"
      : "<kbd>Ctrl+Enter</kbd> send &middot; <kbd>Enter</kbd> new line &middot; <kbd>Esc</kbd> stop";
    if (input.value) autoGrow(); // text size changed: re-fit the box
  }
  applySettings();

  /* ---------- updates: quiet check at start, badge on Settings, progress overlay, self-restart ---------- */
  var updAvail = "", updBusy = false, updOv = null, updPending = "";  // updPending = version installed, restart still to do
  function setUpdateBadge(v) {
    updAvail = v || "";
    ["settingsBtn", "railSettings"].forEach(function (id) {
      var b = $(id); if (!b) return;
      b.classList.toggle("has-update", !!updAvail);
      b.setAttribute("aria-label", updAvail ? "Settings (update available)" : "Settings");
      b.title = updAvail ? "Settings · Update available" : "Settings";
    });
  }
  function updKey(e) {
    if (!updOv || e.altKey) return;
    var err = updOv.classList.contains("err") || updOv.classList.contains("ask");
    if (err && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); updHide(); return; }
    if (err && updOv.contains(e.target) && (e.key === "Tab" || e.key === "Enter" || e.key === " ")) return;
    e.preventDefault(); e.stopPropagation(); /* nothing else while Atlas updates */
  }
  function updShow() {
    if (updOv) return updOv;
    updOv = document.createElement("div"); updOv.id = "updOv";
    updOv.setAttribute("role", "alertdialog"); updOv.setAttribute("aria-modal", "true");
    updOv.setAttribute("aria-labelledby", "updTitle"); updOv.setAttribute("aria-describedby", "updStep");
    updOv.innerHTML = '<div class="upd-card" tabindex="-1"><div class="upd-ring" aria-hidden="true"></div>' +
      '<h3 id="updTitle">Updating Atlas</h3><p id="updStep" aria-live="polite"></p>' +
      '<div class="upd-bar" aria-hidden="true"><i></i></div>' +
      '<div class="upd-actions" hidden><button type="button" class="set-btn" id="updClose">Close</button>' +
      '<button type="button" class="set-btn primary" id="updRetry">Retry</button></div></div>';
    Array.prototype.forEach.call(document.body.children, function (el) {
      if (!el.inert) { el.inert = true; el.setAttribute("data-upd-inert", ""); } /* no clicks or focus behind */
    });
    document.body.appendChild(updOv);
    updOv.querySelector("#updRetry").addEventListener("click", function () { if (updPending) restartNow(); else runUpdate(); });
    updOv.querySelector("#updClose").addEventListener("click", updHide);
    document.addEventListener("keydown", updKey, true);
    requestAnimationFrame(function () { if (updOv) { updOv.classList.add("show"); updOv.querySelector(".upd-card").focus(); } });
    return updOv;
  }
  function updHide() {
    if (!updOv || updBusy) return;
    document.removeEventListener("keydown", updKey, true);
    Array.prototype.forEach.call(document.querySelectorAll("[data-upd-inert]"), function (el) { el.inert = false; el.removeAttribute("data-upd-inert"); });
    updOv.remove(); updOv = null;
    if (updPending) toast("Update installed. Restart Atlas when you are ready (Settings > Update).");
    if (!document.querySelector(".modal-back")) ovResume();
    var b = document.querySelector("#setUpBtn");
    if (b) b.focus(); else focusInput();
  }
  function updStep(text, failed) {
    var o = updShow();
    o.classList.toggle("err", !!failed);
    o.querySelector("#updTitle").textContent = failed ? "Update did not finish" : "Updating Atlas";
    o.querySelector("#updStep").textContent = text;
    o.querySelector(".upd-actions").hidden = !failed;
    if (failed) o.querySelector("#updRetry").focus();
  }
  function runUpdate() {
    if (!api || updBusy) return;
    if (!updOv && guardBusy()) return; /* never cut off an answer that is still coming in */
    updBusy = true; ovSuspend();
    updStep("Downloading the update...");
    api.update_download().then(function (r) {
      if (!r || !r.ok) throw r;
      updStep("Installing...");
      return api.update_install();
    }).then(function (r) {
      if (!r || !r.ok) throw r;
      updPending = r.version || updAvail || "new"; updBusy = false;
      askRestart();
    }).catch(function (r) {
      updBusy = false;
      updStep((r && r.status) || "The update did not finish. Atlas is still running.", true);
    });
  }
  /* The update is installed. Let the person choose: restart now, or later (the new version starts the next time Atlas opens). */
  function askRestart() {
    var o = updShow();
    o.classList.remove("err"); o.classList.add("ask");
    o.querySelector("#updTitle").textContent = "Update installed";
    o.querySelector("#updStep").textContent = "Restart Atlas to start using the new version, or do it later.";
    o.querySelector(".upd-actions").hidden = false;
    o.querySelector("#updClose").textContent = "Later";
    o.querySelector("#updRetry").textContent = "Restart now";
    o.querySelector("#updRetry").focus();
    setUpdateBadge("");
    var b = document.querySelector("#setUpBtn"), n = document.querySelector("#setUpNote");
    if (b && n) paintUpdateRow(b, n);
  }
  function restartNow() {
    if (!api || updBusy) return;
    if (!updOv && guardBusy()) return; /* never cut off an answer that is still coming in */
    updBusy = true; if (!updOv) ovSuspend();
    var o = updShow(); o.classList.remove("ask");
    updStep("Restarting Atlas...");
    api.update_restart().then(function (r) {
      if (!r || !r.ok) throw r;
      /* this window closes in a moment and the new Atlas opens by itself; the overlay stays until then */
    }).catch(function (r) {
      updBusy = false;
      o.querySelector("#updClose").textContent = "Close"; o.querySelector("#updRetry").textContent = "Retry";
      updStep((r && r.status) || "Atlas could not restart. It is still running.", true);
    });
  }
  function paintUpdateRow(btn, note) {
    if (updPending) {
      note.textContent = "Version " + updPending + " is installed. Restart Atlas to start using it.";
      btn.textContent = "Restart now"; btn.classList.add("primary");
      return;
    }
    if (!updAvail) return;
    note.textContent = "Version " + updAvail + " is ready. Atlas restarts by itself afterwards.";
    btn.textContent = "Update now"; btn.classList.add("primary");
  }
  function checkForUpdates(btn, note) {
    if (!api || btn.classList.contains("busy")) return;
    if (updPending) { restartNow(); return; }
    if (updAvail) { runUpdate(); return; }
    btn.classList.add("busy"); btn.setAttribute("aria-busy", "true"); note.textContent = "Checking...";
    api.check_update().then(function (r) {
      if (r && r.newer) { setUpdateBadge(r.version || "new"); paintUpdateRow(btn, note); }
      else note.textContent = (r && r.status) || "Could not check for updates right now.";
    }).catch(function () { note.textContent = "Could not check for updates right now."; })
      .then(function () { btn.classList.remove("busy"); btn.removeAttribute("aria-busy"); });
  }

  /* Discord-style pop-up at the top when a new version is ready (once per version per session) */
  var updPopShown = "";
  function updPopHide() {
    var p = $("updPop"); if (!p) return;
    p.classList.remove("show"); setTimeout(function () { if (p.parentNode) p.remove(); }, 300);
  }
  function updPop(v) {
    if (!v || updPopShown === v || updBusy) return;
    try { if (sessionStorage.getItem("updPopSeen") === v) return; } catch (x) { /* ignore */ }
    updPopShown = v;
    updPopHide();
    var p = document.createElement("div"); p.id = "updPop"; p.setAttribute("role", "status");
    p.innerHTML = '<div class="up-ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg></div>' +
      '<div class="up-txt"><b>Update available</b><span>Atlas ' + (v === "new" ? "has a new version" : "v" + String(v).replace(/[^0-9a-zA-Z.\-]/g, "")) + ' is ready.</span></div>' +
      '<button type="button" class="up-go">Update now</button><button type="button" class="up-later" aria-label="Later">Later</button>';
    document.body.appendChild(p);
    p.querySelector(".up-go").addEventListener("click", function () {
      try { sessionStorage.setItem("updPopSeen", v); } catch (x) { /* ignore */ }
      updPopHide(); runUpdate();
    });
    p.querySelector(".up-later").addEventListener("click", function () {
      try { sessionStorage.setItem("updPopSeen", v); } catch (x) { /* ignore */ }
      updPopHide();
    });
    requestAnimationFrame(function () { p.classList.add("show"); });
  }
  function quietCheck() {
    api.check_update().then(function (r) {
      if (r && r.newer) { setUpdateBadge(r.version || "new"); updPop(r.version || "new"); }
    }).catch(noop);
  }

  function startUpdateChecks(splashMs) {
    if (api.update_notice) api.update_notice().then(function (v) {
      if (v) setTimeout(function () { toast("Updated to v" + v); }, splashMs);
    }).catch(noop);
    setTimeout(function () { /* quiet check, never installs anything by itself */
      quietCheck();
      setInterval(quietCheck, 30 * 60 * 1000); /* and again every 30 minutes while Atlas stays open */
    }, 5000);
  }

  function openSettings() {
    if (document.querySelector(".modal-back")) return;
    ovSuspend();
    var prev = document.activeElement, closed = false;
    var back = document.createElement("div"); back.className = "modal-back";
    var card = document.createElement("div"); card.className = "modal settings";
    card.setAttribute("role", "dialog"); card.setAttribute("aria-modal", "true"); card.setAttribute("aria-labelledby", "setTitle");
    var X = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    card.innerHTML =
      '<div class="settings-head"><h3 id="setTitle">Settings</h3><button type="button" class="settings-x" aria-label="Close">' + X + '</button></div>' +
      '<div class="settings-body">' +
      '<section class="set-sec"><h4>Updates</h4>' +
        '<div class="set-row"><div class="t"><b>Atlas <span id="setVer"></span></b><small id="setUpNote">See if a newer version is ready.</small></div><button type="button" class="set-btn" id="setUpBtn">Check now</button></div></section>' +
      '<section class="set-sec"><h4>Look</h4>' +
        '<div class="set-row"><div class="t"><b>Theme</b><small>System follows your Windows setting.</small></div><div class="seg" id="setTheme" role="group" aria-label="Theme"></div></div>' +
        '<div class="set-row"><div class="t"><b>Text size</b><small>Size of the words in the chat.</small></div><div class="seg" id="setSize" role="group" aria-label="Text size"></div></div>' +
        '<div class="set-row"><div class="t"><b>Calm effects</b><small>Turns off fades and other small animations.</small></div><button type="button" class="sw" id="setCalm" role="switch" aria-label="Calm effects"></button></div></section>' +
      '<section class="set-sec"><h4>Chat</h4>' +
        '<div class="set-row"><div class="t"><b>Enter sends the message</b><small>Off: Enter makes a new line and Ctrl+Enter sends.</small></div><button type="button" class="sw" id="setEnter" role="switch" aria-label="Enter sends the message"></button></div>' +
        '<div class="set-row"><div class="t"><b>Start Atlas in mode</b><small>The mode that is picked when Atlas opens.</small></div><select class="set-select" id="setMode" aria-label="Start mode"></select></div></section>' +
      '<section class="set-sec"><h4>Your chats</h4>' +
        '<div class="set-row"><div class="t"><b>Save this chat as a file</b><small>A plain text file you can keep or send.</small></div><button type="button" class="set-btn" id="setExport">Save</button></div>' +
        '<div class="set-row"><div class="t"><b>Open the chats folder</b><small>Where Atlas keeps your chats on this PC.</small></div><button type="button" class="set-btn" id="setFolder">Open</button></div>' +
        '<div class="set-row"><div class="t"><b>Delete all chats</b><small>Removes every chat. This can’t be undone.</small></div><button type="button" class="set-btn danger" id="setWipe">Delete all</button></div></section>' +
      '<section class="set-sec"><h4>Shortcuts</h4><div class="keys">' +
        '<kbd>Ctrl+B</kbd><span>Show or hide the chat list</span><kbd>Esc</kbd><span>Stop Atlas while it is answering</span>' +
        '<kbd>Enter</kbd><span>Send (or new line, see above)</span><kbd>Shift+Enter</kbd><span>New line</span></div></section>' +
      '<section class="set-sec"><h4>About</h4><p class="set-about">Atlas — your personal assistant, built by Amar &amp; Arry.</p></section>' +
      '</div><div class="set-foot"><button type="button" class="set-btn" id="setDone">Done</button></div>';
    back.appendChild(card); document.body.appendChild(back);
    requestAnimationFrame(function () { back.classList.add("show"); });
    var q = function (id) { return card.querySelector("#" + id); };

    if (api) api.get_version().then(function (v) { q("setVer").textContent = "v" + v; }).catch(noop);
    q("setUpBtn").addEventListener("click", function () { checkForUpdates(q("setUpBtn"), q("setUpNote")); });
    paintUpdateRow(q("setUpBtn"), q("setUpNote"));

    var seg = q("setSize");
    function paintSeg() { Array.prototype.forEach.call(seg.children, function (b) { var on = b.dataset.v === settings.size; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false"); }); }
    [["small", "Small"], ["normal", "Normal"], ["large", "Large"]].forEach(function (o) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = o[1]; b.dataset.v = o[0];
      b.addEventListener("click", function () { settings.size = o[0]; saveSettings(); applySettings(); paintSeg(); });
      seg.appendChild(b);
    });
    function toggle(id, key) {
      var b = q(id);
      function paint() { b.setAttribute("aria-checked", settings[key] ? "true" : "false"); }
      b.addEventListener("click", function () { settings[key] = !settings[key]; saveSettings(); applySettings(); paint(); });
      paint();
    }
    toggle("setCalm", "calm"); toggle("setEnter", "enterSends"); paintSeg();
    var tseg = q("setTheme"), TH = window.auraTheme;
    function paintTheme() { Array.prototype.forEach.call(tseg.children, function (b) { var on = TH && b.dataset.v === TH.get(); b.classList.toggle("on", !!on); b.setAttribute("aria-pressed", on ? "true" : "false"); }); }
    [["auto", "System"], ["light", "Light"], ["dark", "Dark"]].forEach(function (o) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = o[1]; b.dataset.v = o[0];
      b.addEventListener("click", function () { if (TH) TH.set(o[0]); paintTheme(); });
      tseg.appendChild(b);
    });
    paintTheme();
    var sel = q("setMode");
    MODES.forEach(function (m) { var o = document.createElement("option"); o.value = m.id; o.textContent = m.label; sel.appendChild(o); });
    sel.value = settings.startMode;
    sel.addEventListener("change", function () { settings.startMode = sel.value; saveSettings(); });

    q("setExport").addEventListener("click", function () {
      if (!api || !currentId) { toast("Open a chat first."); return; }
      api.export_chat(currentId).then(function (r) { toast((r && r.status) || "Could not save the chat."); }).catch(function () { toast("Could not save the chat."); });
    });
    q("setFolder").addEventListener("click", function () {
      if (api) api.open_chats_folder().then(function (r) { if (r && r.status !== "Opened.") toast(r.status); }).catch(noop);
    });
    q("setWipe").addEventListener("click", function () {
      if (!api || guardBusy()) return;
      askConfirm({ title: "Delete all chats?", text: "Every chat (" + sessions.length + ") and all its messages will be removed. This can’t be undone.", ok: "Delete all", danger: true })
        .then(function (yes) {
          if (!yes) return;
          return api.delete_all_sessions().then(function () {
            currentId = null; messagesEl.textContent = "";
            return refreshSessions().then(newChat);
          }).then(function () { toast("All chats deleted."); });
        }).catch(function () { toast("Could not delete the chats."); });
    });

    function close() {
      if (closed) return; closed = true;
      document.removeEventListener("keydown", onKey, true);
      back.classList.remove("show");
      setTimeout(function () {
        back.remove(); ovResume();
        if (prev && prev.focus && document.contains(prev)) { try { prev.focus(); } catch (e) { /* ignore */ } } else focusInput();
      }, 180);
    }
    function onKey(e) {
      /* Esc closes settings only when no confirm dialog is stacked on top of it */
      if (document.querySelectorAll(".modal-back").length !== 1) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "Tab") trapTab(card, e);
    }
    document.addEventListener("keydown", onKey, true);
    back.addEventListener("mousedown", function (e) { if (e.target === back) close(); });
    card.querySelector(".settings-x").addEventListener("click", close);
    q("setDone").addEventListener("click", close);
    q("setDone").focus();
  }
  $("settingsBtn").addEventListener("click", openSettings);
  buildPills();
  MODES.forEach(function (m) { if (m.id === settings.startMode && m.id !== mode) setMode(m); });

  /* ---------- loading screen: Hermes particle loader + "Connecting…", hidden after about 2.4 s (300 ms fade) ---------- */
  (function () {
    var s = $("splash"), t0 = Date.now();
    if (!s) return;
    var host = $("spLoader"), stopLoader = host && window.auraLoader ? window.auraLoader(host) : null;
    var hidden = false;
    function hide() {
      if (hidden) return;
      hidden = true;
      setTimeout(function () {
        s.classList.add("hide");
        setTimeout(function () { if (stopLoader) stopLoader(); s.remove(); }, 400);
      }, Math.max(0, 2400 - (Date.now() - t0)));
    }
    if (document.readyState === "complete") hide(); else window.addEventListener("load", hide);
    setTimeout(hide, 6000); // failsafe: never stay on the loading screen
  })();

  /* ---------- front page: now the #welcome block in index.html (dataset kept for compatibility) ---------- */
  messagesEl.dataset.typed = "What shall we build?";

  if (window.pywebview && window.pywebview.api) ready();
  window.addEventListener("pywebviewready", ready);
  (function () {  // the ready event can be missed or come before the API exists: keep trying for a while
    var tries = 0, t = setInterval(function () {
      if (started || ++tries > 120) { clearInterval(t); return; }
      ready();
    }, 500);
  })();
})();

/* ---------- Hermes-style cosmetics only (no app logic): icon rail, chat filter, top-bar title,
   send-button state, scroll-to-bottom button, light/dark switch ---------- */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var root = document.documentElement, body = document.body;
  var forward = function (railId, targetId) {
    var r = $(railId), t = $(targetId);
    if (r && t) r.addEventListener("click", function () { t.click(); });
  };

  // theme: follows Windows ("auto") until the user picks light or dark; the choice is remembered
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  function isLight() { var t = root.dataset.theme; return t === "light" || (t !== "dark" && !!mq && mq.matches); }
  function paintTheme() {
    root.classList.toggle("is-light", isLight());
    try { if (window.pywebview && window.pywebview.api && window.pywebview.api.win_theme) window.pywebview.api.win_theme(isLight()); } catch (e) { /* ignore */ }
  }
  function setTheme(t) {
    root.dataset.theme = t;
    try { localStorage.setItem("auraTheme", t); } catch (e) { /* ignore */ }
    paintTheme();
  }
  try { var th = localStorage.getItem("auraTheme"); root.dataset.theme = (th === "light" || th === "dark") ? th : "auto"; } catch (e) { root.dataset.theme = "auto"; }
  paintTheme();
  if (mq) { if (mq.addEventListener) mq.addEventListener("change", paintTheme); else if (mq.addListener) mq.addListener(paintTheme); }
  window.auraTheme = { get: function () { return root.dataset.theme; }, set: setTheme };
  $("railTheme").addEventListener("click", function () { setTheme(isLight() ? "dark" : "light"); });

  // rail buttons forward to the real controls
  forward("railChat", "sbToggle");
  forward("railNew", "newChat");
  forward("railBrowser", "paneToggle");
  forward("railSettings", "settingsBtn");
  $("railLogo").addEventListener("mousedown", function (e) {
    var b = document.querySelector("#sidebar .brand");
    if (b && e.button === 0) b.dispatchEvent(new MouseEvent("mousedown", { button: 0, bubbles: true, cancelable: true }));
  });
  function syncRail() {
    $("railChat").classList.toggle("on", !body.classList.contains("sb-hidden"));
    $("railBrowser").classList.toggle("on", $("preview").classList.contains("open"));
  }
  new MutationObserver(syncRail).observe(body, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(syncRail).observe($("preview"), { attributes: true, attributeFilter: ["class"] });
  syncRail();

  // chat filter (client-side, on the rendered rows) + centered title of the open chat
  var list = $("sessionList"), filter = $("chatFilter"), title = $("chatTitle");
  var noMatch = document.createElement("div");
  noMatch.className = "side-empty"; noMatch.setAttribute("role", "status");
  noMatch.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/></svg><span>No chats match</span>';
  function applyFilter() {
    var q = filter.value.trim().toLowerCase(), shown = 0;
    var rows = list.querySelectorAll(".session");
    Array.prototype.forEach.call(rows, function (row) {
      var out = !!q && (row.title || row.textContent).toLowerCase().indexOf(q) < 0;
      row.classList.toggle("filtered-out", out);
      if (!out) shown++;
    });
    var want = !!q && rows.length > 0 && shown === 0;
    if (want && noMatch.parentNode !== list) list.appendChild(noMatch);
    else if (!want && noMatch.parentNode) noMatch.remove();
  }
  // arrow keys move between chat rows
  list.addEventListener("keydown", function (e) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    var a = document.activeElement, cur = a && a.closest ? a.closest(".session, .more-chats") : null;
    if (!cur) return;
    var rows = Array.prototype.filter.call(list.querySelectorAll(".session, .more-chats"), function (r) { return !r.classList.contains("filtered-out"); });
    var i = rows.indexOf(cur); if (i < 0) return;
    e.preventDefault();
    var n = e.key === "Home" ? 0 : e.key === "End" ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
    rows[n].focus();
  });
  function syncTitle() {
    var a = list.querySelector(".session.active span");
    var t = a ? a.textContent : "";
    if (title.textContent !== t) title.textContent = t;
  }
  filter.addEventListener("input", applyFilter);
  filter.addEventListener("keydown", function (e) { if (e.key === "Escape" && filter.value) { e.stopPropagation(); filter.value = ""; applyFilter(); } });
  new MutationObserver(function (recs) {
    // ignore the observer's own "no matches" note going in or out (avoids a feedback loop)
    if (recs.every(function (r) { return Array.prototype.every.call(r.addedNodes, function (n) { return n === noMatch; })
      && Array.prototype.every.call(r.removedNodes, function (n) { return n === noMatch; }); })) return;
    applyFilter(); syncTitle();
  }).observe(list, { childList: true });
  syncTitle();

  // send button turns accent when there is text
  var input = $("input"), composer = $("composer");
  function syncSend() { composer.classList.toggle("has-text", input.value.trim().length > 0); }
  ["input", "change"].forEach(function (ev) { input.addEventListener(ev, syncSend); }); // the app fires "input" after clearing the box

  // round scroll-to-bottom button
  var msgs = $("messages"), down = $("toBottom");
  function syncDown() { down.classList.toggle("show", msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight > 240); }
  msgs.addEventListener("scroll", syncDown);
  new MutationObserver(syncDown).observe(msgs, { childList: true });
  down.addEventListener("click", function () { msgs.scrollTo({ top: msgs.scrollHeight, behavior: "smooth" }); input.focus(); });

  // mode picker (Hermes model-menu style): menu items .click() the hidden real pills, so the app logic is unchanged
  var DESC = {
    claude: "Smart and thorough, hands tasks to the builder",
    auto: "Fast, no thinking, hands tasks to the builder",
    qwen: "Runs only on this PC, free",
    polish: "One careful design and quality pass"
  };
  var pills = $("pills"), modeBtn = $("modeBtn"), modeLabel = $("modeLabel"), menu = $("modeMenu"), kb = -1;
  var CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  function items() { return Array.prototype.slice.call(menu.children); }
  function buildMenu() {
    menu.textContent = "";
    Array.prototype.forEach.call(pills.children, function (p) {
      p.tabIndex = -1;
      var it = document.createElement("button");
      it.type = "button"; it.className = "mode-item"; it.setAttribute("role", "menuitemradio"); it.tabIndex = -1;
      it.dataset.id = p.dataset.id;
      var b = document.createElement("b"); b.textContent = p.textContent;
      var s = document.createElement("small"); s.textContent = DESC[p.dataset.id] || p.title || "";
      var t = document.createElement("span"); t.append(b, s);
      it.appendChild(t); it.insertAdjacentHTML("beforeend", CHECK);
      it.addEventListener("click", function () { p.click(); closeMenu(true); });
      it.addEventListener("mousemove", function () { setKb(items().indexOf(it), false); });
      menu.appendChild(it);
    });
    syncMode();
  }
  function syncMode() {
    var act = pills.querySelector(".pill.active");
    if (act && modeLabel.textContent !== act.textContent) modeLabel.textContent = act.textContent;
    items().forEach(function (it) { it.setAttribute("aria-checked", act && it.dataset.id === act.dataset.id ? "true" : "false"); });
  }
  function setKb(i, focus) {
    var list = items(); if (!list.length) return;
    kb = (i + list.length) % list.length;
    list.forEach(function (it, j) { it.classList.toggle("kb", j === kb); });
    if (focus) list[kb].focus();
  }
  var hideTimer = 0;
  function isOpen() { return modeBtn.getAttribute("aria-expanded") === "true"; }
  function openMenu() {
    if (!menu.children.length) buildMenu();
    clearTimeout(hideTimer);
    menu.hidden = false; modeBtn.setAttribute("aria-expanded", "true");
    void menu.offsetWidth; menu.classList.add("in"); // fade/slide in
    var list = items(), cur = list.findIndex(function (it) { return it.getAttribute("aria-checked") === "true"; });
    setKb(cur < 0 ? 0 : cur, true);
  }
  function closeMenu(refocus) {
    if (!isOpen()) return;
    modeBtn.setAttribute("aria-expanded", "false"); kb = -1;
    menu.classList.remove("in"); // fade out, then hide
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { if (!isOpen()) menu.hidden = true; }, 150);
    items().forEach(function (it) { it.classList.remove("kb"); });
    if (refocus) modeBtn.focus();
  }
  modeBtn.addEventListener("click", function () { if (!isOpen()) openMenu(); else closeMenu(true); });
  modeBtn.addEventListener("keydown", function (e) {
    if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !isOpen()) { e.preventDefault(); openMenu(); }
  });
  menu.addEventListener("keydown", function (e) {
    var list = items();
    if (e.key === "ArrowDown") { e.preventDefault(); setKb(kb + 1, true); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setKb(kb - 1, true); }
    else if (e.key === "Home") { e.preventDefault(); setKb(0, true); }
    else if (e.key === "End") { e.preventDefault(); setKb(list.length - 1, true); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (list[kb]) list[kb].click(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
    else if (e.key === "Tab") { closeMenu(false); }
  });
  document.addEventListener("mousedown", function (e) {
    if (isOpen() && !menu.contains(e.target) && !modeBtn.contains(e.target)) closeMenu(false);
  }, true);
  // pills are built by the app at startup and their "active" class changes with every mode switch (also by Settings)
  new MutationObserver(function (recs) {
    if (recs.some(function (r) { return r.type === "childList"; })) buildMenu(); else syncMode();
  }).observe(pills, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  if (pills.children.length) buildMenu();
})();

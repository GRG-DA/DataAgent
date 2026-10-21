/* ═══════════════════════════════════════════════════════════════════
   DataAgent — fx-core.js  (v1)
   The single motion + interaction engine for the whole site.
   Replaces motion.js · motion-scroll.js · pro.js

   Contents
     1.  Env flags + public store (window.FX)
     2.  Smooth scroll engine (Lenis ⇄ ScrollTrigger)
     3.  Intro curtain
     4.  Custom cursor
     5.  Scroll progress
     6.  Line-mask headings
     7.  Reveal engine (3D-aware stagger)
     8.  Card tilt + spotlight
     9.  Magnetic elements
    10.  Counters
    11.  Marquee (velocity reactive)
    12.  Sticky showcase scrub
    13.  Pinned horizontal rail
    14.  Depth parallax
    15.  Docs scroll-spy + reading progress
    16.  Navbar state
    17.  Toast
    18.  Page transitions
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var doc = document;
  var root = doc.documentElement;

  /* ── 1. Environment + store ─────────────────────────────────────── */
  /* Motion preference. ?fx=full forces the full experience (demos, QA),
     ?fx=off enforces the calm experience. Defaults to the OS setting. */
  var FX_PARAM = (location.search.match(/[?&]fx=([a-z]+)/i) || [])[1];
  var REDUCED = FX_PARAM === 'off' ? true
              : FX_PARAM === 'full' ? false
              : window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var COARSE  = window.matchMedia('(hover: none), (pointer: coarse)').matches;
  var GSAP    = !!window.gsap;
  var ST      = !!(window.gsap && window.ScrollTrigger);

  var clamp = function (v, a, b) { return v < a ? a : v > b ? b : v; };
  var lerp  = function (a, b, t) { return a + (b - a) * t; };
  var now   = function () {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  };

  /* ── Engine budgets ────────────────────────────────────────────────
     Hard caps so a long page can never turn the motion layer into a
     long task. Every one of these is a ceiling, not a target. */
  var FX_CONFIG = {
    /* GSAP-tilted elements: each costs a quickTo pair plus a glare node. */
    MAX_TILT: 24,
    /* Magnetic elements: each costs two more quickTo tweens. */
    MAX_MAGNETIC: 40,
    /* Reveal targets styled per animation frame — keeps boot cheap. */
    REVEAL_CHUNK: 48,
    /* The reveal system's own safety net (see initReveals). */
    REVEAL_FAILSAFE_MS: 4000,
    /* How often the scroll progress is published to CSS as --fx-scroll. */
    SCROLL_VAR_INTERVAL: 120
  };

  var FX = window.FX = {
    reduced: REDUCED,
    coarse: COARSE,
    ready: false,
    gsap: GSAP,
    /* Set by fx-webgl.js — true once the WebGL stage is drawing. */
    webgl: false,
    config: FX_CONFIG,
    scroll: { y: 0, progress: 0, velocity: 0, dir: 1 },
    pointer: { x: 0, y: 0, nx: 0, ny: 0 },
    lenis: null
  };

  /* A class the CSS can hang the "no motion at all" rules off. It also
     covers ?fx=off, which the prefers-reduced-motion media query cannot
     see. */
  if (REDUCED) root.classList.add('fx-reduced');

  var bus = {};
  FX.on = function (name, cb) {
    (bus[name] = bus[name] || []).push(cb);
    return FX;
  };
  function emit(name, payload) {
    var list = bus[name];
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      try { list[i](payload); } catch (e) { /* keep the site alive */ }
    }
  }

  /* Smooth scroll position that works with or without Lenis. */
  function scrollY() {
    return FX.lenis ? FX.lenis.scroll : (window.scrollY || root.scrollTop || 0);
  }
  function docHeight() {
    return Math.max(1, (doc.documentElement.scrollHeight || 0) - window.innerHeight);
  }
  FX.scrollTo = function (target, offset) {
    offset = offset || 0;
    if (FX.lenis) {
      FX.lenis.scrollTo(target, { offset: offset, duration: 1.05 });
    } else if (typeof target === 'number') {
      window.scrollTo({ top: target + offset });
    } else if (target && target.scrollIntoView) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  /* ── 2. Scroll engine ───────────────────────────────────────────── */
  function initScroll() {
    if (typeof window.Lenis === 'function' && !REDUCED) {
      var lenis = new window.Lenis({
        duration: 1.05,
        easing: function (t) { return clamp(1 - Math.pow(1 - t, 3.2), 0, 1); },
        smoothWheel: true,
        wheelMultiplier: 0.95,
        touchMultiplier: 1.15
      });
      FX.lenis = lenis;
      window.__daLenis = lenis;
      if (ST) {
        lenis.on('scroll', function (e) {
          window.ScrollTrigger.update();
          if (e && typeof e.velocity === 'number') FX.scroll.velocity = e.velocity;
        });
        window.gsap.ticker.add(function (time) { lenis.raf(time * 1000); });
        window.gsap.ticker.lagSmoothing(0);
      } else {
        var raf = function (t) { lenis.raf(t); requestAnimationFrame(raf); };
        requestAnimationFrame(raf);
      }
    }

    var last = 0;
    var lastVar = 0;
    function tick(now) {
      var y = scrollY();
      FX.scroll.y = y;
      FX.scroll.progress = clamp(y / docHeight(), 0, 1);
      FX.scroll.dir = y >= last ? 1 : -1;
      if (!FX.lenis) FX.scroll.velocity = y - last;
      last = y;

      /* Publish the scroll progress so CSS can drift the ambient layers
         (see .fx-scroll in premium.css). Throttled: a slow drift does
         not need 60 updates a second, and each write invalidates style. */
      if (!REDUCED && now - lastVar > FX_CONFIG.SCROLL_VAR_INTERVAL) {
        lastVar = now;
        root.style.setProperty('--fx-scroll', FX.scroll.progress.toFixed(4));
      }

      emit('tick', FX.scroll);
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);

    window.addEventListener('resize', function () {
      if (ST) window.ScrollTrigger.refresh();
    });
    window.addEventListener('load', function () {
      if (ST) window.ScrollTrigger.refresh();
    }, { once: true });
    if (doc.fonts && doc.fonts.ready && ST) {
      doc.fonts.ready.then(function () { window.ScrollTrigger.refresh(); }).catch(function () {});
    }
  }

  /* ── 3. Intro curtain ───────────────────────────────────────────── */
  function initIntro() {
    var overlay = doc.createElement('div');
    overlay.className = 'fx-preloader';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = '<div class="fx-preloader-mark"><span class="fx-preloader-orbit"></span><span>DataAgent</span></div>';
    doc.body.appendChild(overlay);

    var firstVisit = false;
    try {
      firstVisit = sessionStorage.getItem('da-fx-seen') !== '1';
      sessionStorage.setItem('da-fx-seen', '1');
    } catch (e) { firstVisit = true; }

    if (!firstVisit) {
      overlay.remove();
      root.classList.add('fx-ready');
      FX.ready = true;
      emit('ready');
      return;
    }

    var done = function () {
      if (overlay.dataset.done === '1') return;
      overlay.dataset.done = '1';
      overlay.classList.add('is-done');
      root.classList.add('fx-ready');
      FX.ready = true;
      emit('ready');
      setTimeout(function () { if (overlay.parentNode) overlay.remove(); }, 900);
    };

    if (REDUCED) { done(); return; }

    var elapsed = 0;
    var timer = setInterval(function () {
      elapsed += 100;
      if (elapsed >= 900) { clearInterval(timer); done(); }
    }, 100);
    setTimeout(function () { clearInterval(timer); done(); }, 2600);
  }

  /* ── 4. Custom cursor ───────────────────────────────────────────── */
  function initCursor() {
    if (COARSE || REDUCED) return;
    var ring = doc.createElement('div');
    var dot = doc.createElement('div');
    ring.className = 'fx-cursor';
    dot.className = 'fx-cursor-dot';
    ring.setAttribute('aria-hidden', 'true');
    dot.setAttribute('aria-hidden', 'true');
    doc.body.appendChild(ring);
    doc.body.appendChild(dot);

    var mx = window.innerWidth / 2, my = window.innerHeight / 2;
    var rx = mx, ry = my, dx = mx, dy = my;
    var seen = false;

    window.addEventListener('pointermove', function (e) {
      mx = e.clientX; my = e.clientY;
      FX.pointer.x = mx; FX.pointer.y = my;
      FX.pointer.nx = (mx / window.innerWidth) * 2 - 1;
      FX.pointer.ny = (my / window.innerHeight) * 2 - 1;
      if (!seen) {
        seen = true;
        rx = dx = mx; ry = dy = my;
        doc.body.classList.add('fx-cursor-on');
      }
    }, { passive: true });

    window.addEventListener('pointerdown', function () { ring.classList.add('is-down'); });
    window.addEventListener('pointerup', function () { ring.classList.remove('is-down'); });
    doc.addEventListener('mouseleave', function () { doc.body.classList.remove('fx-cursor-on'); });
    doc.addEventListener('mouseenter', function () { if (seen) doc.body.classList.add('fx-cursor-on'); });

    var HOVER = 'a[href], button, .btn, .card, .pill-tabs button, input, textarea, select, .faq-item summary, [data-cursor]';
    doc.addEventListener('pointerover', function (e) {
      if (e.target.closest && e.target.closest(HOVER)) ring.classList.add('is-hover');
    });
    doc.addEventListener('pointerout', function (e) {
      if (e.target.closest && e.target.closest(HOVER)) ring.classList.remove('is-hover');
    });

    (function loop() {
      rx = lerp(rx, mx, 0.16);
      ry = lerp(ry, my, 0.16);
      dx = lerp(dx, mx, 0.42);
      dy = lerp(dy, my, 0.42);
      ring.style.transform = 'translate3d(' + rx.toFixed(2) + 'px,' + ry.toFixed(2) + 'px,0)';
      dot.style.transform = 'translate3d(' + dx.toFixed(2) + 'px,' + dy.toFixed(2) + 'px,0)';
      requestAnimationFrame(loop);
    })();
  }

  /* ── 5. Scroll progress ─────────────────────────────────────────── */
  function initScrim() {
    var scrim = doc.createElement('div');
    scrim.className = 'fx-scrim';
    scrim.setAttribute('aria-hidden', 'true');
    doc.body.appendChild(scrim);
  }

  function initProgress() {
    if (REDUCED) return;
    var bar = doc.createElement('div');
    bar.className = 'fx-progress';
    bar.setAttribute('aria-hidden', 'true');
    doc.body.appendChild(bar);
    var target = 0, current = 0;
    FX.on('tick', function (s) { target = s.progress; });
    (function loop() {
      current = lerp(current, target, 0.14);
      bar.style.transform = 'scaleX(' + current.toFixed(4) + ')';
      requestAnimationFrame(loop);
    })();
  }

  /* ── 6. Line-mask headings ──────────────────────────────────────── */
  function wrapLines(el) {
    if (!el || el.dataset.fxLines === '1') return;

    /* Headings that already declare their lines (e.g. the hero title)
       reuse those nodes as the clipping masks. */
    var declared = el.querySelectorAll(':scope > .line');
    if (declared.length) {
      el.dataset.fxLines = '1';
      Array.prototype.forEach.call(declared, function (line, idx) {
        var inner = line.innerHTML;
        line.classList.add('fx-line');
        line.innerHTML = '<span>' + inner + '</span>';
        line.style.setProperty('--fx-delay', (idx * 95) + 'ms');
      });
      el.classList.add('fx-lines');
      return;
    }

    var html = el.innerHTML;
    if (html.indexOf('<br') === -1) {
      el.dataset.fxLines = '1';
      el.innerHTML = '<span class="fx-line"><span>' + html + '</span></span>';
      el.classList.add('fx-lines');
      return;
    }
    var parts = html.split(/<br\s*\/?>/i);
    el.dataset.fxLines = '1';
    el.innerHTML = parts.map(function (line) {
      return '<span class="fx-line"><span>' + line + '</span></span>';
    }).join('');
    el.classList.add('fx-lines');
  }

  function initHeadings() {
    var sel = '.hero-title, .page-header h1, .display-lg, .display-xl, .section-title';
    var nodes = doc.querySelectorAll(sel);
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest('.navbar, .footer, .docs-sidebar')) continue;
      wrapLines(el);
      if (el.querySelectorAll('.fx-line').length > 1) {
        var lines = el.querySelectorAll('.fx-line');
        for (var j = 0; j < lines.length; j++) {
          lines[j].style.setProperty('--fx-delay', (j * 95) + 'ms');
        }
      }
    }
  }

  /* -- 7. Reveal engine ------------------------------------------------
     Contract: a reveal target MUST end up visible.
       * whatever is already on screen at load reveals immediately
       * an IntersectionObserver reveals on scroll
       * a scroll sweep catches targets that an instant jump skipped
       * a failsafe pass repairs the page if the observer never fires
     Only opacity and transform are animated, so nothing reflows and
     nothing shifts. */
  function initReveals() {
    var t0 = now();
    var targets = [];
    var seen = new Set();
    var io = null;

    function add(el) {
      if (!el || seen.has(el) || el.dataset.fxReveal === 'off') return;
      if (el.closest('.navbar, .footer, .fx-preloader')) return;
      seen.add(el);
      targets.push(el);
    }

    var auto = doc.querySelectorAll(
      '.animate-on-scroll, .card, .stat-panel, .plan-card, .blog-card, .req-card, ' +
      '.rail-card, .highlight-box, .faq-item, .contact-link-card, .arch-card, ' +
      '.learn-panel, .download-card, .showcase-col > *, .timeline-item, .check-list li, ' +
      '.section-header'
    );
    for (var i = 0; i < auto.length; i++) add(auto[i]);

    /* Stagger siblings that appear next to each other. The delays are
       computed as data first -- the DOM writes happen later, in chunks. */
    var delays = new Array(targets.length);
    var groups = {};
    var groupSeq = 0;
    targets.forEach(function (el, idx) {
      var parent = el.parentElement;
      if (!parent) { delays[idx] = '0ms'; return; }
      var id = parent.dataset.fxGroup;
      if (!id) { id = 'g' + (++groupSeq); parent.dataset.fxGroup = id; }
      var n = groups[id] || 0;
      groups[id] = n + 1;
      delays[idx] = Math.min(n, 6) * 70 + 'ms';
    });

    /* Editorial hairline under the section headers: a short accent rule
       that draws itself as the header reveals. */
    doc.querySelectorAll('.section-header').forEach(function (el) {
      el.classList.add('fx-rule');
    });

    function reveal(el) {
      if (!el || el.classList.contains('is-in')) return;
      el.classList.add('is-in');
      if (io) io.unobserve(el);
    }
    function revealAll() {
      for (var k = 0; k < targets.length; k++) reveal(targets[k]);
    }

    /* Apply the reveal classes in chunks -- one long style recalc for a
       hundred cards is a long task on a slow phone. */
    var cursor = 0;
    function applyChunk() {
      var end = Math.min(cursor + FX_CONFIG.REVEAL_CHUNK, targets.length);
      for (; cursor < end; cursor++) {
        var el = targets[cursor];
        el.classList.add('fx-reveal');
        el.style.setProperty('--fx-delay', delays[cursor]);
        if (el.matches('.card, .plan-card, .download-card, .stat-panel, .rail-card, .blog-card, .req-card, .contact-link-card')) {
          el.classList.add('fx-spot');
        }
      }
      if (cursor < targets.length) requestAnimationFrame(applyChunk);
    }
    applyChunk();

    /* Masked headings -- held back until the curtain lifts, so the
       line reveal is actually seen rather than played behind it. */
    function armMaskedHeadings() {
      var masked = doc.querySelectorAll('.fx-lines');
      Array.prototype.forEach.call(masked, function (h) {
        if (h.dataset.fxArmed === '1') return;
        h.dataset.fxArmed = '1';
        var obs = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) {
            if (e.isIntersecting) { e.target.classList.add('is-in'); obs.disconnect(); }
          });
        }, { threshold: 0.3 });
        obs.observe(h);
        if (h.getBoundingClientRect().top < window.innerHeight * 0.92) h.classList.add('is-in');
      });
    }

    if (REDUCED) {
      /* Completely static: everything is visible, nothing is masked. */
      revealAll();
      doc.querySelectorAll('.fx-lines').forEach(function (h) { h.classList.add('is-in'); });
      FX.boot.reveal = Math.round((now() - t0) * 10) / 10;
      return;
    }

    if (!('IntersectionObserver' in window)) {
      /* No observer: show everything rather than hide content. */
      revealAll();
      armMaskedHeadings();
      FX.boot.reveal = Math.round((now() - t0) * 10) / 10;
      return;
    }

    io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        reveal(entry.target);
        io.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    targets.forEach(function (el) { io.observe(el); });

    /* Sweep: instant jumps can skip elements past the viewport between frames */
    var raf = 0;
    function sweep() {
      raf = 0;
      var fold = window.innerHeight * 0.96;
      for (var k = 0; k < targets.length; k++) {
        var el = targets[k];
        if (el.classList.contains('is-in')) continue;
        if (el.getBoundingClientRect().top < fold) reveal(el);
      }
    }
    window.addEventListener('scroll', function () {
      if (!raf) raf = requestAnimationFrame(sweep);
    }, { passive: true });

    /* Whatever is on screen right now reveals now -- a restored scroll
       position or a #hash jump must never leave content invisible. */
    function revealInView() {
      var fold = window.innerHeight * 0.96;
      for (var k = 0; k < targets.length; k++) {
        var el = targets[k];
        if (el.classList.contains('is-in')) continue;
        var r = el.getBoundingClientRect();
        if (r.top < fold && r.bottom > 0) reveal(el);
      }
    }
    revealInView();
    window.addEventListener('resize', revealInView, { passive: true });
    window.addEventListener('hashchange', function () {
      requestAnimationFrame(function () { revealInView(); sweep(); });
    });

    var armed = false;
    function arm() { if (armed) return; armed = true; armMaskedHeadings(); }
    FX.on('ready', arm);
    if (FX.ready) arm();
    setTimeout(arm, 1700);

    /* -- The failsafe -------------------------------------------------
       A reveal must never be able to hide content for good. After
       REVEAL_FAILSAFE_MS we look for targets the observer should have
       revealed by now:
         * anything on screen reveals
         * anything the visitor has already scrolled past reveals
       And if the observer has demonstrably failed -- something was passed
       and is still hidden -- the whole page is revealed. */
    setTimeout(function () {
      var missed = 0;
      var fold = window.innerHeight * 1.05;
      for (var k = 0; k < targets.length; k++) {
        var el = targets[k];
        if (el.classList.contains('is-in')) continue;
        var r = el.getBoundingClientRect();
        if (r.bottom < 0) { missed++; continue; }
        if (r.top < fold) reveal(el);
      }
      if (missed > 0) revealAll();
    }, FX_CONFIG.REVEAL_FAILSAFE_MS);

    FX.boot.reveal = Math.round((now() - t0) * 10) / 10;
  }

  /* ── 8. Card tilt + spotlight ───────────────────────────────────── */
  function initTilt() {
    if (COARSE || REDUCED || !GSAP) return;
    var sel = '.card, .plan-card, .download-card, .stat-panel, .rail-card, .hero-panel, .blog-card';
    var nodes = doc.querySelectorAll(sel);

    /* A 3D tilt costs a quickTo pair and a glare node per element, and
       every one of them is a composited layer while the pointer moves.
       Only the first MAX_TILT elements (document order = what the
       visitor actually sees first) get the tilt; the rest keep the
       pointer-tracked spotlight, which is nearly free. */
    var budget = FX_CONFIG.MAX_TILT;

    nodes.forEach(function (el) {
      if (el.dataset.fxTilt === '1') return;
      if (el.closest('.showcase-col')) return;
      if (budget <= 0) return;
      budget--;
      el.dataset.fxTilt = '1';
      el.classList.add('fx-tilt');

      var glare = doc.createElement('span');
      glare.className = 'fx-glare';
      glare.setAttribute('aria-hidden', 'true');
      el.appendChild(glare);

      var quickRX = window.gsap.quickTo(el, 'rotationX', { duration: 0.5, ease: 'power3.out' });
      var quickRY = window.gsap.quickTo(el, 'rotationY', { duration: 0.5, ease: 'power3.out' });
      var quickY  = window.gsap.quickTo(el, 'y', { duration: 0.6, ease: 'power3.out' });
      var quickS  = window.gsap.quickTo(el, 'scale', { duration: 0.6, ease: 'power3.out' });
      window.gsap.set(el, { transformPerspective: 1200, transformOrigin: 'center' });

      var big = el.matches('.download-card, .hero-panel');

      el.addEventListener('pointermove', function (e) {
        var r = el.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width;
        var py = (e.clientY - r.top) / r.height;
        var max = big ? 4.5 : 7;
        quickRY((px - 0.5) * 2 * max);
        quickRX(-(py - 0.5) * 2 * max);
        quickY(-(big ? 4 : 7));
        quickS(big ? 1.012 : 1.02);
        el.style.setProperty('--mx', (px * 100).toFixed(1) + '%');
        el.style.setProperty('--my', (py * 100).toFixed(1) + '%');
        el.style.setProperty('--gx', (px * 100).toFixed(1) + '%');
        el.style.setProperty('--gy', (py * 100).toFixed(1) + '%');
      }, { passive: true });

      el.addEventListener('pointerleave', function () {
        quickRX(0); quickRY(0); quickY(0); quickS(1);
      });
    });
  }

  /* ── 9. Magnetic elements ───────────────────────────────────────── */
  function initMagnetic() {
    if (COARSE || REDUCED || !GSAP) return;
    var sel = '.btn, .nav-cta, .scroll-top, .nav-logo, .hero-float, .section-badge';
    /* Same budget rule as the tilt: two quickTo tweens per element. */
    var budget = FX_CONFIG.MAX_MAGNETIC;
    doc.querySelectorAll(sel).forEach(function (el) {
      if (el.dataset.fxMag === '1') return;
      if (budget <= 0) return;
      budget--;
      el.dataset.fxMag = '1';
      var qx = window.gsap.quickTo(el, 'x', { duration: 0.55, ease: 'power3.out' });
      var qy = window.gsap.quickTo(el, 'y', { duration: 0.55, ease: 'power3.out' });
      var strength = el.matches('.nav-cta, .nav-logo') ? 5 : 9;

      el.addEventListener('pointermove', function (e) {
        var r = el.getBoundingClientRect();
        var dx = (e.clientX - (r.left + r.width / 2)) / r.width;
        var dy = (e.clientY - (r.top + r.height / 2)) / r.height;
        qx(clamp(dx * strength * 2, -strength * 2, strength * 2));
        qy(clamp(dy * strength, -strength, strength));
      }, { passive: true });

      el.addEventListener('pointerleave', function () { qx(0); qy(0); });
    });
  }

  /* ── 10. Counters ───────────────────────────────────────────────── */
  function initCounters() {
    var nodes = doc.querySelectorAll('[data-count]');
    if (!nodes.length) return;
    nodes.forEach(function (el) {
      if (el.dataset.fxCount === '1') return;
      el.dataset.fxCount = '1';
      var target = parseFloat(el.getAttribute('data-count')) || 0;
      var suffix = el.getAttribute('data-suffix') || '';
      var prefix = el.getAttribute('data-prefix') || '';
      var run = function () {
        if (GSAP && !REDUCED) {
          var proxy = { v: 0 };
          window.gsap.to(proxy, {
            v: target, duration: 1.7, ease: 'power3.out',
            onUpdate: function () {
              el.textContent = prefix + Math.round(proxy.v).toLocaleString() + suffix;
            }
          });
        } else {
          el.textContent = prefix + Math.round(target).toLocaleString() + suffix;
        }
      };
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { run(); io.disconnect(); } });
      }, { threshold: 0.5 });
      io.observe(el);
    });
  }

  /* ── 11. Marquee ────────────────────────────────────────────────── */
  function initMarquee() {
    doc.querySelectorAll('.marquee').forEach(function (m) {
      var track = m.querySelector('.marquee-track');
      if (!track || track.dataset.fxMarquee === '1') return;
      track.dataset.fxMarquee = '1';
      var group = track.querySelector('.marquee-group');
      var unit = group ? group.offsetWidth : track.scrollWidth / 2;
      if (!unit) return;

      if (!GSAP || REDUCED) {
        track.style.animation = 'none';
        return;
      }
      var speed = unit / 46; // px per second
      var state = { x: 0 };
      var skew = 0;
      window.gsap.ticker.add(function (t, dt) {
        var boost = clamp(Math.abs(FX.scroll.velocity) / 60, 0, 4);
        state.x -= (speed * (1 + boost)) * (dt / 1000);
        if (state.x <= -unit) state.x += unit;
        track.style.transform = 'translate3d(' + state.x.toFixed(2) + 'px,0,0)';
        var want = clamp(FX.scroll.velocity / 22, -6, 6);
        skew = lerp(skew, want, 0.08);
        track.style.skewX = skew.toFixed(2) + 'deg';
      });
    });
  }

  /* ── 12. Sticky showcase scrub ──────────────────────────────────── */
  function initShowcase() {
    doc.querySelectorAll('[data-scrolly]').forEach(function (section) {
      var cards = section.querySelectorAll('[data-step]');
      var visual = section.querySelector('.showcase-visual, .showcase-mock, .mock-stack');
      var progress = section.querySelector('[data-scrolly-progress]');
      if (!cards.length) return;

      function setStep(idx) {
        cards.forEach(function (c, i) {
          var on = i === idx;
          c.classList.toggle('is-active', on);
          c.style.opacity = on ? 1 : 0.55;
          c.style.transform = on ? 'translate3d(0,0,0)' : 'translate3d(0,10px,0)';
        });
        if (visual) {
          var panels = visual.querySelectorAll('.mock-panel');
          panels.forEach(function (p, i) { p.classList.toggle('is-active', i === idx); });
        }
      }

      if (REDUCED) { setStep(0); return; }

      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          var idx = parseInt(e.target.dataset.step, 10) || 0;
          setStep(idx);
        });
      }, { threshold: 0.55, rootMargin: '-18% 0px -28% 0px' });
      cards.forEach(function (c) { io.observe(c); });

      if (progress) {
        var onTick = function (s) {
          var r = section.getBoundingClientRect();
          var total = r.height - window.innerHeight;
          var p = total > 0 ? clamp(-r.top / total, 0, 1) : 0;
          progress.style.transform = 'scaleY(' + p.toFixed(4) + ')';
        };
        FX.on('tick', onTick);
        onTick(FX.scroll);
      }
      setStep(0);
    });
  }

  /* ── 13. Pinned horizontal rail ─────────────────────────────────── */
  function initRails() {
    if (!ST || REDUCED) return;
    doc.querySelectorAll('[data-rail]').forEach(function (section) {
      var track = section.querySelector('.rail-track');
      var rail = section.querySelector('.rail');
      if (!track || !rail) return;

      var distance = function () { return Math.max(0, track.scrollWidth - window.innerWidth + 80); };
      if (distance() < 120) return;

      window.gsap.to(track, {
        x: function () { return -distance(); },
        ease: 'none',
        scrollTrigger: {
          trigger: section,
          start: 'top top',
          end: function () { return '+=' + distance(); },
          pin: true,
          scrub: 1,
          anticipatePin: 1,
          invalidateOnRefresh: true
        }
      });

      var hint = section.querySelector('.rail-hint');
      if (hint) {
        window.ScrollTrigger.create({
          trigger: section,
          start: 'top 60%',
          end: 'bottom top',
          onEnter: function () { hint.classList.add('is-in'); },
          onLeave: function () { hint.classList.remove('is-in'); }
        });
      }
    });
  }

  /* ── 14. Depth parallax ─────────────────────────────────────────── */
  function initParallax() {
    if (!ST || REDUCED) return;
    var sel = '[data-fx-parallax], .hero-float, .hero-stage-glow, .tile-art, .cta-art, .da-mark, .privacy-frame';
    doc.querySelectorAll(sel).forEach(function (el) {
      if (el.dataset.fxPar === '1') return;
      el.dataset.fxPar = '1';
      var depth = parseFloat(el.getAttribute('data-fx-parallax'));
      if (isNaN(depth)) depth = el.matches('.hero-float, .da-mark') ? 16 : 10;
      el.classList.add('fx-parallax');
      window.gsap.to(el, {
        yPercent: -depth,
        ease: 'none',
        scrollTrigger: {
          trigger: el.closest('section') || el,
          start: 'top bottom',
          end: 'bottom top',
          scrub: 1
        }
      });
    });

    // Hero copy drifts slightly slower than the page for depth
    var hero = doc.querySelector('.hero-content');
    if (hero) {
      window.gsap.to(hero, {
        yPercent: 8, opacity: 0.55, ease: 'none',
        scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: true }
      });
    }
  }

  /* ── 15. Docs scroll-spy + reading progress ─────────────────────── */
  function initScrollSpy() {
    var nav = doc.querySelector('.docs-nav');
    var content = doc.querySelector('.docs-content');
    if (!nav || !content) return;

    var links = Array.prototype.slice.call(nav.querySelectorAll('a[href^="#"]'));
    var heads = links.map(function (a) { return doc.getElementById(a.getAttribute('href').slice(1)); }).filter(Boolean);
    if (!heads.length) return;

    if (!doc.querySelector('.read-progress')) {
      var bar = doc.createElement('div');
      bar.className = 'read-progress';
      bar.setAttribute('aria-hidden', 'true');
      var host = doc.querySelector('.docs-layout') || content;
      host.style.position = host.style.position || 'relative';
      host.prepend(bar);
      if (GSAP && ST && !REDUCED) {
        window.gsap.to(bar, {
          scaleX: 1, ease: 'none',
          scrollTrigger: { trigger: content, start: 'top 40%', end: 'bottom bottom', scrub: 0.3 }
        });
      }
    }

    var lastActive = null;
    function setActive(id) {
      links.forEach(function (a) {
        a.classList.toggle('active', a.getAttribute('href') === '#' + id);
      });
    }
    var raf = 0;
    function update() {
      raf = 0;
      var line = window.innerHeight * 0.3;
      var current = heads[0];
      for (var i = 0; i < heads.length; i++) {
        if (heads[i].getBoundingClientRect().top <= line) current = heads[i];
        else break;
      }
      if (current !== lastActive) { lastActive = current; setActive(current.id); }
    }
    window.addEventListener('scroll', function () { if (!raf) raf = requestAnimationFrame(update); }, { passive: true });
    update();

    links.forEach(function (a) {
      a.addEventListener('click', function (e) {
        var id = a.getAttribute('href').slice(1);
        var target = doc.getElementById(id);
        if (!target) return;
        e.preventDefault();
        FX.scrollTo(target, -100);
        lastActive = target;
        setActive(id);
        history.replaceState(null, '', '#' + id);
      });
    });
  }

  /* ── 16. Navbar state ───────────────────────────────────────────── */
  function initNav() {
    var nav = doc.querySelector('.navbar');
    if (!nav) return;
    FX.on('tick', function (s) {
      nav.classList.toggle('is-stuck', s.y > 24);
    });
  }

  /* ── 17. Toast ──────────────────────────────────────────────────── */
  function initToast() {
    var toast = doc.querySelector('.toast');
    if (!toast || toast.dataset.fxToast === '1') return;
    toast.dataset.fxToast = '1';
    var close = toast.querySelector('.toast-close');
    var dismissed = false;
    function dismiss() {
      if (dismissed || !toast.isConnected) return;
      dismissed = true;
      if (GSAP && !REDUCED) {
        window.gsap.to(toast, { y: 26, opacity: 0, duration: 0.4, ease: 'power2.in' });
      }
      toast.classList.add('hide');
      setTimeout(function () { if (toast.parentNode) toast.remove(); }, 480);
    }
    if (close) close.addEventListener('click', dismiss);
    setTimeout(dismiss, 15000);
  }

  /* ── 18. Page transitions ───────────────────────────────────────── */
  function initTransitions() {
    var overlay = doc.createElement('div');
    overlay.className = 'fx-transition';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = '<span class="fx-transition-bar"></span>';
    doc.body.appendChild(overlay);

    var leaving = false;
    doc.addEventListener('click', function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var a = e.target.closest && e.target.closest('a[href]');
      if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
      var href = a.getAttribute('href');
      if (!href || href.charAt(0) === '#' || /^(mailto|tel|javascript):/i.test(href)) return;
      var url;
      try { url = new URL(href, location.href); } catch (err) { return; }
      if (url.origin !== location.origin) return;
      if (url.pathname === location.pathname && url.hash) return;
      e.preventDefault();
      if (leaving) return;
      leaving = true;
      if (FX.lenis) FX.lenis.stop();
      overlay.classList.add('is-active');
      setTimeout(function () { location.href = url.href; }, 520);
    });

    window.addEventListener('pageshow', function (e) {
      leaving = false;
      overlay.classList.remove('is-active');
      if (e.persisted) {
        root.classList.add('fx-ready');
        doc.querySelectorAll('.fx-reveal').forEach(function (el) { el.classList.add('is-in'); });
      }
    });
  }

  /* ── Hero choreography ─────────────────────────────────────────── */
  function initHero() {
    var hero = doc.querySelector('.hero');
    if (!hero || REDUCED || !GSAP) return;
    var g = window.gsap;
    var badge = hero.querySelector('.hero-badge');
    var desc = hero.querySelector('.hero-desc');
    var actions = hero.querySelector('.hero-actions');
    var pills = hero.querySelector('.hero-pills');
    var panel = hero.querySelector('.hero-panel');
    var floats = hero.querySelectorAll('.hero-float');

    g.set(hero, { perspective: 1300 });
    var tl = g.timeline({ defaults: { ease: 'power3.out' } });
    if (badge) tl.from(badge, { y: 16, opacity: 0, duration: 0.6 }, 0.05);
    if (desc) tl.from(desc, { y: 22, opacity: 0, duration: 0.85 }, 0.22);
    if (actions) tl.from(actions.children, { y: 20, opacity: 0, duration: 0.7, stagger: 0.08 }, 0.32);
    if (pills) tl.from(pills.children, { y: 14, opacity: 0, duration: 0.55, stagger: 0.06 }, 0.46);
    if (panel) tl.from(panel, {
      y: 54, opacity: 0, rotateY: -16, rotateX: 7, scale: 0.965,
      duration: 1.25, ease: 'expo.out', transformPerspective: 1300
    }, 0.12);
    if (floats.length) tl.from(floats, {
      y: 26, opacity: 0, scale: 0.93, duration: 0.9, stagger: 0.11
    }, 0.55);
  }

  function scheduleHero() {
    var fired = false;
    function run() { if (fired) return; fired = true; initHero(); }
    FX.on('ready', run);
    if (FX.ready) run();
    setTimeout(run, 1700);
  }

  /* ── Boot ───────────────────────────────────────────────────────── */
  function boot() {
    var t0 = now();
    FX.boot = { total: 0, reveal: 0 };

    initScroll();
    initIntro();
    initCursor();
    scheduleHero();
    initScrim();
    initProgress();
    initHeadings();
    initReveals();
    initTilt();
    initMagnetic();
    initCounters();
    initMarquee();
    initShowcase();
    initRails();
    initParallax();
    initScrollSpy();
    initNav();
    initToast();
    initTransitions();

    if (ST) {
      window.ScrollTrigger.refresh();
      window.addEventListener('load', function () { window.ScrollTrigger.refresh(); }, { once: true });
    }
    FX.boot.total = Math.round((now() - t0) * 10) / 10;
    FX.init = true;
    emit('init');
  }

  if (doc.readyState === 'loading') {
    doc.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();

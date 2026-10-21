/* ═══════════════════════════════════════════════════════════════════
   DataAgent — fx-webgl.js  (v1)
   The WebGL stage behind every page: an instanced "data lattice",
   a drifting particle field and an orbiting core, lit in the brand
   palette and driven by pointer + scroll.

   · One shared canvas (#fx-canvas), fixed, pointer-events:none
   · Scene vari"ies per page via <body data-fx-scene="…">
   · Optional bloom (dynamic import) with graceful degradation
   · Adaptive quality: FPS watchdog + hidden-tab + off-screen throttling
   · Skipped entirely for reduced motion / no WebGL (CSS aurora remains)

   The CSS aurora is the base layer and is always painted; this canvas
   is an enhancement on top of it. If WebGL is missing, blocklisted or
   the context is lost, the body gets .fx-no-webgl and the aurora is
   promoted to the full-strength fallback — never a blank rectangle.

   The bare specifier "three" resolves through the import map declared
   by every page. That map MUST keep URL-like values ("./vendor/…");
   a bare relative path is parsed as a bare specifier and the module
   never resolves at all.
   ═══════════════════════════════════════════════════════════════════ */

import * as THREE from 'three';

const body = document.body;
/* Mirrors js/fx-core.js — ?fx=full | ?fx=off, else the OS preference. */
const FX_PARAM = (location.search.match(/[?&]fx=([a-z]+)/i) || [])[1];
const REDUCED = FX_PARAM === 'off' ? true
              : FX_PARAM === 'full' ? false
              : window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* Promote the CSS aurora to the hero. Called for reduced motion, for a
   missing/blocklisted WebGL context, and when the context is lost. */
function useFallback(reason, quiet) {
  body.classList.add('fx-no-webgl');
  body.setAttribute('data-fx-webgl', 'fallback');
  if (window.FX) window.FX.webgl = false;
  const canvas = document.getElementById('fx-canvas');
  if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
  if (!quiet && window.console && window.console.info) {
    window.console.info('[fx] WebGL stage off (' + reason + ') — CSS aurora fallback active.');
  }
}

/* ── Scene presets ──────────────────────────────────────────────── */
const PRESETS = {
  home:      { cols: 15, rows: 9,  depth: 7, particles: 2200, core: true,  rings: true,  camZ: 26, spin: 0.045, wave: 1.0,  bloom: 0.8,  floor: true,  scale: 1.0  },
  features:  { cols: 16, rows: 7,  depth: 6, particles: 1500, core: false, rings: false, camZ: 31, spin: 0.03,  wave: 0.7,  bloom: 0.68, floor: true,  scale: 0.95 },
  docs:      { cols: 10, rows: 6,  depth: 5, particles: 1100, core: false, rings: false, camZ: 34, spin: 0.018, wave: 0.5,  bloom: 0.55, floor: false, scale: 1.05 },
  download:  { cols: 9,  rows: 12, depth: 5, particles: 1600, core: true,  rings: false, camZ: 31, spin: 0.035, wave: 1.3,  bloom: 0.75, floor: true,  scale: 0.95 },
  pricing:   { cols: 12, rows: 5,  depth: 5, particles: 1200, core: false, rings: false, camZ: 33, spin: 0.028, wave: 0.6,  bloom: 0.62, floor: true,  scale: 1.0  },
  blog:      { cols: 11, rows: 7,  depth: 6, particles: 1700, core: false, rings: false, camZ: 32, spin: 0.03,  wave: 0.8,  bloom: 0.68, floor: false, scale: 1.0  },
  about:     { cols: 10, rows: 7,  depth: 6, particles: 1900, core: true,  rings: true,  camZ: 31, spin: 0.04,  wave: 0.9,  bloom: 0.75, floor: true,  scale: 0.95 },
  calm:      { cols: 8,  rows: 5,  depth: 4, particles: 900,  core: false, rings: false, camZ: 35, spin: 0.016, wave: 0.45, bloom: 0.5,  floor: false, scale: 1.1  },
};

// The 3D hero's particle palette.
//
// It used to hard-code five saturated hues (violet, light violet, pink,
// orange, cyan) - the full brand set - which is why the hero still read as
// neon-on-black even after the CSS layer was toned down.
//
// The CSS palette is now ONE primary ramp plus ONE accent, with orange and
// rose reserved for rare emphasis. The hero follows the same rule: the
// violet family carries the scene and the cyan is the single cool accent.
// The values are read from the CSS custom properties when they are
// available, so the canvas can never drift from the stylesheet again.
const PALETTE = (function () {
  const css = (name, fallback) => {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name);
      return (v && v.trim()) || fallback;
    } catch (e) {
      return fallback;
    }
  };
  return [
    new THREE.Color(css('--primary', '#8b5cf6')),
    new THREE.Color(css('--primary-hi', '#a78bfa')),
    new THREE.Color(css('--primary-deep', '#7c3aed')),
    new THREE.Color(css('--accent', '#22d3ee')),
    new THREE.Color(css('--accent-soft', '#5fb8d6')),
  ];
})();

function sprite() {
  const size = 64;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.35, 'rgba(255,255,255,0.55)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function supportsWebGL() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (e) { return false; }
}

function boot() {
  if (REDUCED) {
    useFallback('reduced motion', true);
    return;
  }
  if (!supportsWebGL()) {
    useFallback('no WebGL context');
    return;
  }

  const key = body.dataset.fxScene || 'home';
  const cfg = Object.assign({}, PRESETS.home, PRESETS[key] || {});

  /* ── Renderer + scene ─────────────────────────────────────────── */
  const canvas = document.createElement('canvas');
  canvas.id = 'fx-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  body.appendChild(canvas);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false,
      alpha: true,
      powerPreference: 'high-performance'
    });
  } catch (err) {
    useFallback('renderer could not start');
    return;
  }
  body.setAttribute('data-fx-webgl', 'on');
  if (window.FX) window.FX.webgl = true;
  let dpr = Math.min(window.devicePixelRatio || 1, 1.6);
  renderer.setPixelRatio(dpr);
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x05040f, 0.019);

  const camera = new THREE.PerspectiveCamera(48, window.innerWidth / window.innerHeight, 0.1, 200);
  camera.position.set(0, 0.6, cfg.camZ);
  camera.lookAt(0, 0, 0);

  /* ── Lights ───────────────────────────────────────────────────── */
  scene.add(new THREE.AmbientLight(0x6d5bd0, 0.55));
  const key1 = new THREE.PointLight(0xa78bfa, 90, 60, 2);
  key1.position.set(-12, 10, 12);
  const key2 = new THREE.PointLight(0x22d3ee, 70, 60, 2);
  key2.position.set(14, -8, 10);
  const key3 = new THREE.PointLight(0xf472b6, 60, 50, 2);
  key3.position.set(0, 6, -14);
  scene.add(key1, key2, key3);

  /* ── World group (everything parallaxes together) ─────────────── */
  const world = new THREE.Group();
  scene.add(world);

  /* ── Data lattice: instanced blocks ───────────────────────────── */
  const count = cfg.cols * cfg.rows * cfg.depth;
  const geo = new THREE.BoxGeometry(1, 1, 1);
  const mat = new THREE.MeshStandardMaterial({
    metalness: 0.55,
    roughness: 0.34,
    transparent: true,
    opacity: 0.82,
    depthWrite: false
  });
  const lattice = new THREE.InstancedMesh(geo, mat, count);
  lattice.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

  const base = new Float32Array(count * 3);
  const phase = new Float32Array(count);
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  const spreadX = cfg.cols * 1.9;
  const spreadY = cfg.rows * 1.7;
  const spreadZ = cfg.depth * 3.1;

  let i = 0;
  for (let x = 0; x < cfg.cols; x++) {
    for (let y = 0; y < cfg.rows; y++) {
      for (let z = 0; z < cfg.depth; z++) {
        const px = (x / (cfg.cols - 1) - 0.5) * spreadX;
        const py = (y / (cfg.rows - 1) - 0.5) * spreadY;
        const pz = (z / (cfg.depth - 1) - 0.5) * spreadZ - 11;
        base[i * 3] = px; base[i * 3 + 1] = py; base[i * 3 + 2] = pz;
        phase[i] = (x * 0.7 + y * 1.1 + z * 0.5);

        // Colour mixes toward the edges so the centre reads as a hotspot
        const t = Math.min(1, Math.abs(px) / (spreadX * 0.5));
        color.copy(PALETTE[0]).lerp(PALETTE[3], t * 0.75).lerp(PALETTE[4], Math.random() * 0.22);
        lattice.setColorAt(i, color);

        const s = (0.2 + Math.random() * 0.5) * cfg.scale;
        dummy.position.set(px, py, pz);
        dummy.scale.setScalar(s);
        dummy.updateMatrix();
        lattice.setMatrixAt(i, dummy.matrix);
        i++;
      }
    }
  }
  lattice.instanceColor.needsUpdate = true;
  world.add(lattice);

  /* ── Particle field ───────────────────────────────────────────── */
  const pCount = cfg.particles;
  const pGeo = new THREE.BufferGeometry();
  const pPos = new Float32Array(pCount * 3);
  const pCol = new Float32Array(pCount * 3);
  const pSeed = new Float32Array(pCount);
  for (let p = 0; p < pCount; p++) {
    const r = 16 + Math.random() * 40;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(Math.random() * 2 - 1);
    pPos[p * 3] = r * Math.sin(phi) * Math.cos(theta);
    pPos[p * 3 + 1] = (Math.random() - 0.5) * 34;
    pPos[p * 3 + 2] = r * Math.cos(phi) * 0.7 - 10;
    const c = PALETTE[Math.floor(Math.random() * PALETTE.length)];
    pCol[p * 3] = c.r; pCol[p * 3 + 1] = c.g; pCol[p * 3 + 2] = c.b;
    pSeed[p] = Math.random() * Math.PI * 2;
  }
  pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
  pGeo.setAttribute('color', new THREE.BufferAttribute(pCol, 3));
  const points = new THREE.Points(pGeo, new THREE.PointsMaterial({
    size: 0.36,
    map: sprite(),
    vertexColors: true,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    opacity: 0.85,
    sizeAttenuation: true
  }));
  world.add(points);

  /* ── Orbiting core ────────────────────────────────────────────── */
  const core = new THREE.Group();
  if (cfg.core) {
    const shell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(2.2, 1),
      new THREE.MeshBasicMaterial({ color: 0xb69bff, wireframe: true, transparent: true, opacity: 0.26 })
    );
    const inner = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.95, 2),
      new THREE.MeshStandardMaterial({ color: 0x8b5cf6, emissive: 0x7c3aed, emissiveIntensity: 0.85, metalness: 0.6, roughness: 0.25 })
    );
    core.add(shell, inner);
    core.userData.shell = shell;
    core.userData.inner = inner;
    core.position.set(cfg.cols > 10 ? 10.5 : 0, 1.4, -9);
  }
  if (cfg.rings) {
    for (let r = 0; r < 3; r++) {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(3.1 + r * 0.7, 0.012, 6, 160),
        new THREE.MeshBasicMaterial({ color: r === 1 ? 0xf472b6 : 0xa78bfa, transparent: true, opacity: 0.45 })
      );
      ring.rotation.set(Math.PI / 2 + r * 0.34, r * 0.5, r * 0.2);
      core.add(ring);
    }
  }
  if (core.children.length) world.add(core);

  /* ── Data floor ───────────────────────────────────────────────── */
  if (cfg.floor) {
    const grid = new THREE.GridHelper(160, 80, 0x5b3fa8, 0x241a45);
    grid.material.transparent = true;
    grid.material.opacity = 0.2;
    grid.position.y = -18;
    world.add(grid);
  }

  /* ── Bloom (optional) ─────────────────────────────────────────── */
  let composer = null;
  let bloomPass = null;
  let bloomWanted = cfg.bloom;

  async function enableBloom() {
    try {
      const [{ EffectComposer }, { RenderPass }, { UnrealBloomPass }, { OutputPass }] = await Promise.all([
        import('three/addons/postprocessing/EffectComposer.js'),
        import('three/addons/postprocessing/RenderPass.js'),
        import('three/addons/postprocessing/UnrealBloomPass.js'),
        import('three/addons/postprocessing/OutputPass.js'),
      ]);
      composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(scene, camera));
      bloomPass = new UnrealBloomPass(
        new THREE.Vector2(window.innerWidth, window.innerHeight),
        bloomWanted, 0.55, 0.72
      );
      composer.addPass(bloomPass);
      composer.addPass(new OutputPass());
      composer.setSize(window.innerWidth, window.innerHeight);
      composer.setPixelRatio(dpr);
    } catch (e) {
      composer = null;
      body.classList.add('fx-no-bloom');
    }
  }

  /* Building the composer compiles a dozen shader programs — measured at
     ~0.6 s of blocked main thread. That is far too much to spend before
     the hero is on screen, so the stage starts drawing first and the
     bloom is built later, in an idle slot. */
  let bloomStarted = false;
  function startBloom() {
    if (bloomStarted) return;
    bloomStarted = true;
    enableBloom();
  }
  function scheduleBloom() {
    if (window.requestIdleCallback) {
      window.requestIdleCallback(startBloom, { timeout: 2500 });
    } else {
      setTimeout(startBloom, 800);
    }
  }

  /* ── Interaction state ────────────────────────────────────────── */
  const state = { rx: 0, ry: 0, trx: 0, try_: 0, mx: 0, my: 0, tmx: 0, tmy: 0, scroll: 0 };
  const FX = window.FX || { scroll: { progress: 0 }, pointer: { nx: 0, ny: 0 }, on: () => {} };

  window.addEventListener('pointermove', (e) => {
    state.tmx = (e.clientX / window.innerWidth) * 2 - 1;
    state.tmy = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  if (FX.on) {
    FX.on('tick', (s) => {
      state.scroll = s.progress;
      // Camera flies through the lattice as the page deepens
      camera.position.z = cfg.camZ - s.progress * 5.5;
      camera.position.y = 0.6 + s.progress * 3.2;
      camera.position.x = Math.sin(s.progress * Math.PI) * 2.4;
    });
  }

  /* ── Adaptive quality ─────────────────────────────────────────── */
  let frames = 0, sampleStart = performance.now(), degraded = 0, visible = true;
  document.addEventListener('visibilitychange', () => { visible = !document.hidden; });

  function watchdog(now, dt) {
    frames++;
    const span = now - sampleStart;
    if (span < 2600) return;
    const fps = (frames * 1000) / span;
    frames = 0; sampleStart = now;
    if (fps < 42 && degraded === 0 && bloomPass) {
      degraded = 1;
      bloomPass.strength = Math.max(0.25, bloomWanted * 0.45);
      renderer.setPixelRatio(Math.min(dpr, 1.25));
      composer && composer.setPixelRatio(Math.min(dpr, 1.25));
    } else if (fps < 30 && degraded <= 1) {
      degraded = 2;
      composer = null;
      renderer.setPixelRatio(1);
      renderer.setSize(window.innerWidth, window.innerHeight);
      points.visible = pCount > 1200;
      mat.opacity = 0.75;
    }
  }

  /* ── Resize ───────────────────────────────────────────────────── */
  let resizeRaf = 0;
  window.addEventListener('resize', () => {
    if (resizeRaf) return;
    resizeRaf = requestAnimationFrame(() => {
      resizeRaf = 0;
      const w = window.innerWidth, h = window.innerHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      if (composer) composer.setSize(w, h);
    });
  });

  /* ── Context loss ─────────────────────────────────────────────── */
  canvas.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    useFallback('context lost');
  });

  /* ── Render loop ──────────────────────────────────────────────── */
  const dummyFar = new THREE.Object3D();
  let t = 0;
  let lastFrame = 0;
  let live = false;
  let drawn = 0;

  function frame(now) {
    requestAnimationFrame(frame);
    if (!visible) return;
    // Throttle to ~34fps once the lattice scrolls well out of the hero
    const minDelta = state.scroll > 0.18 ? 29 : 0;
    if (now - lastFrame < minDelta) return;
    const dt = Math.min((now - lastFrame) / 1000, 0.05);
    lastFrame = now;
    t += dt;
    if (!live) {
      live = true;
      canvas.classList.add('is-live');
    }
    drawn++;
    if (drawn === 3) scheduleBloom();

    state.tmx = FX.pointer && FX.pointer.nx ? FX.pointer.nx : state.tmx;
    state.tmy = FX.pointer && FX.pointer.ny ? FX.pointer.ny : state.tmy;
    state.mx = state.mx + (state.tmx - state.mx) * 0.06;
    state.my = state.my + (state.tmy - state.my) * 0.06;

    // Wave the lattice
    for (let k = 0; k < count; k++) {
      const px = base[k * 3], py = base[k * 3 + 1], pz = base[k * 3 + 2];
      const ph = phase[k];
      const w = Math.sin(t * 0.75 + ph * 0.55 + px * 0.22) * 0.5 + Math.sin(t * 0.42 + pz * 0.2) * 0.5;
      const lift = w * cfg.wave;
      const s = 0.18 + (0.5 + w * 0.5) * 0.5;
      dummyFar.position.set(px, py + lift, pz + Math.cos(t * 0.3 + ph) * 0.5);
      dummyFar.rotation.set(lift * 0.1, ph * 0.08 + t * 0.06, lift * 0.08);
      dummyFar.scale.setScalar(s * cfg.scale);
      dummyFar.updateMatrix();
      lattice.setMatrixAt(k, dummyFar.matrix);
    }
    lattice.instanceMatrix.needsUpdate = true;

    world.rotation.y = state.mx * 0.22 + t * cfg.spin * 0.35;
    world.rotation.x = -state.my * 0.14 + state.scroll * 0.22;

    points.rotation.y = -t * 0.02 - state.mx * 0.06;
    points.position.y = Math.sin(t * 0.25) * 0.6;

    if (core.children.length) {
      core.rotation.y += 0.0032 + state.mx * 0.0016;
      core.rotation.x = state.my * 0.2;
      core.position.y = 1.2 + Math.sin(t * 0.6) * 0.4;
      if (core.userData.shell) {
        core.userData.shell.rotation.y -= 0.0016;
        core.userData.shell.rotation.z += 0.0009;
      }
      if (core.userData.inner) {
        core.userData.inner.scale.setScalar(1 + Math.sin(t * 1.6) * 0.05);
      }
    }

    key1.intensity = 90 + Math.sin(t * 0.8) * 18;
    key2.intensity = 70 + Math.cos(t * 0.6) * 14;

    if (composer) composer.render();
    else renderer.render(scene, camera);

    watchdog(now, dt);
  }

  /* Diagnostics — the QA harness reads renderer.info.render.calls to
     prove the scene is actually drawing, not just that a canvas exists. */
  if (window.FX) {
    window.FX.webgl = true;
    window.FX.webglRenderer = renderer;
    window.FX.webglScene = scene;
    window.FX.webglCamera = camera;
  }

  /* Start drawing straight away — the bloom is built later, in an idle
     slot, so the hero is on screen before a dozen shaders are compiled. */
  requestAnimationFrame(frame);
}

/* A failure inside the stage must never take the page down: the CSS
   aurora is a complete hero on its own. */
function safeBoot() {
  try {
    boot();
  } catch (err) {
    useFallback('startup error: ' + (err && err.message ? err.message : err));
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', safeBoot);
} else {
  safeBoot();
}

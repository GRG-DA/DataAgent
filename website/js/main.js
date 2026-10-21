/* ============================================================
   LLM Data Analytics Agent — Interactions & Animations
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {
  initNavbar();
  initMobileMenu();
  initScrollAnimations();
  initScrollTop();
  initActiveNav();
  initCountUp();
});

/* ── Navbar Scroll Effect (hide on scroll down, show on up) ── */
function initNavbar() {
  const navbar = document.querySelector('.navbar');
  if (!navbar) return;

  let lastScroll = window.scrollY;
  const menu = document.querySelector('.nav-links');
  window.addEventListener('scroll', () => {
    const scrollY = window.scrollY;
    if (scrollY > 50) {
      navbar.classList.add('scrolled');
    } else {
      navbar.classList.remove('scrolled');
    }
    const menuOpen = menu && menu.classList.contains('open');
    if (scrollY > 160 && scrollY > lastScroll + 4 && !menuOpen) {
      navbar.classList.add('nav-hidden');
    } else if (scrollY < lastScroll - 4 || scrollY <= 160) {
      navbar.classList.remove('nav-hidden');
    }
    lastScroll = scrollY;
  }, { passive: true });
}

/* ── Mobile Menu ───────────────────────────────────────────── */
function initMobileMenu() {
  const toggle = document.querySelector('.nav-mobile-toggle');
  const links = document.querySelector('.nav-links');
  if (!toggle || !links) return;

  toggle.addEventListener('click', () => {
    toggle.classList.toggle('open');
    links.classList.toggle('open');
  });

  links.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', () => {
      toggle.classList.remove('open');
      links.classList.remove('open');
    });
  });

  document.addEventListener('click', (e) => {
    if (!toggle.contains(e.target) && !links.contains(e.target)) {
      toggle.classList.remove('open');
      links.classList.remove('open');
    }
  });
}

/* ── Scroll Animations ─────────────────────────────────────── */
function initScrollAnimations() {
  const elements = document.querySelectorAll('.animate-on-scroll');
  if (!elements.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
  });

  elements.forEach(el => observer.observe(el));

  /* Instant jumps (anchor links, restored scroll position, scripted
     scrollTo) can skip elements past the viewport between IO frames so
     they never intersect and stay hidden. Sweep on scroll: anything whose
     top has passed the fold is revealed, jumping or not. */
  let sweepRaf = 0;
  const sweep = () => {
    sweepRaf = 0;
    const fold = window.innerHeight * 0.95;
    elements.forEach(el => {
      if (el.classList.contains('visible')) return;
      if (el.getBoundingClientRect().top < fold) {
        el.classList.add('visible');
        observer.unobserve(el);
      }
    });
  };
  window.addEventListener('scroll', () => {
    if (!sweepRaf) sweepRaf = requestAnimationFrame(sweep);
  }, { passive: true });
}

/* ── Scroll to Top ─────────────────────────────────────────── */
function initScrollTop() {
  const btn = document.querySelector('.scroll-top');
  if (!btn) return;

  window.addEventListener('scroll', () => {
    if (window.scrollY > 400) {
      btn.classList.add('visible');
    } else {
      btn.classList.remove('visible');
    }
  }, { passive: true });

  btn.addEventListener('click', () => {
    if (window.__daLenis) {
      window.__daLenis.scrollTo(0, { duration: 1.1 });
    } else {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });
}

/* ── Active Nav Link ───────────────────────────────────────── */
function initActiveNav() {
  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-links a').forEach(link => {
    const href = link.getAttribute('href');
    if (href === currentPage || (currentPage === '' && href === 'index.html')) {
      link.classList.add('active');
    }
  });
}

/* ── Count Up Animation (fallback when GSAP motion.js absent) ─ */
function initCountUp() {
  const counters = document.querySelectorAll('[data-count]');
  if (!counters.length) return;
  if (window.gsap && window.ScrollTrigger) return; // motion.js owns counters

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        animateCount(entry.target);
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });

  counters.forEach(el => observer.observe(el));
}

function animateCount(el) {
  const target = parseInt(el.getAttribute('data-count'));
  const suffix = el.getAttribute('data-suffix') || '';
  const prefix = el.getAttribute('data-prefix') || '';
  const duration = 1500;
  const start = performance.now();

  function update(now) {
    const elapsed = now - start;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.floor(eased * target);
    el.textContent = prefix + current.toLocaleString() + suffix;
    if (progress < 1) requestAnimationFrame(update);
  }
  requestAnimationFrame(update);
}

/* ── Copy Code Blocks ──────────────────────────────────────── */
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('code-copy')) {
    const block = e.target.closest('.code-block');
    if (!block) return;
    const code = block.querySelector('code') || block;
    const text = code.textContent.replace('Copy', '').trim();
    navigator.clipboard.writeText(text).then(() => {
      e.target.textContent = 'Copied!';
      setTimeout(() => { e.target.textContent = 'Copy'; }, 2000);
    });
  }
});

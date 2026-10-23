/* ============================================================
   DataAgent — Pricing storefront (static)
   Rails (ADR-016): ONE live rail, chosen on the billing server via
   PAYMENTS_PROVIDER (famgateway | razorpay | off) and reported to this
   page by GET {API}/v1/payments-info so the copy matches reality.

   FamGateway (UPI, one-time payments only — both plans):
     Pro      → POST {API}/create-order → hosted UPI checkout
                (checkout_url redirect) → /famgateway/webhook grants a
                NEW 31-day window per payment; renew by paying again.
     Lifetime → POST {API}/create-order → hosted UPI checkout → one-time
                grant. Keyless: entitlement follows the buyer's email.

   Razorpay (dormant in famgateway deployments, kept intact):
     Pro      → POST {API}/create-subscription → Checkout subscription
                mode → POST {API}/verify-subscription → /success?…
     Lifetime → POST {API}/create-order → Checkout popup → POST
                {API}/verify → /success?order_id=.
   A Razorpay Payment Link (window.RAZORPAY_PAYMENT_LINKS.lifetime)
   remains a no-code fallback for Lifetime only.
   ============================================================ */

(function () {
  'use strict';

  var EXTERNAL = (window.EXTERNAL_BILLING_URL || '').replace(/\/+$/, '');
  var API = (window.BILLING_API_URL || 'http://127.0.0.1:8090').replace(/\/+$/, '');
  // A checkout POST to a loopback address never reaches a real billing
  // server — it just fails in the visitor's browser. Treat an unresolved or
  // local API URL as "checkout not configured" and say so, instead of
  // rendering a button that cannot work.
  var LOCAL_API = /^(https?:\/\/)?(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/|$)/i.test(API);
  var BILLING_READY = !!API && !LOCAL_API;
  var LINKS = Object.assign(
    { lifetime: '' },
    window.RAZORPAY_PAYMENT_LINKS || {}
  );
  /* Which rail is live on THIS billing server (ADR-016). razorpay is the
     pre-famgateway default and the safe fallback when /v1/payments-info is
     unreachable — a dormant Razorpay deployment still needs its copy. */
  var PROVIDER = { loaded: false, mode: 'razorpay' };
  function detectProvider() {
    if (PROVIDER.loaded || !BILLING_READY) {
      return Promise.resolve(PROVIDER);
    }
    return fetch(API + '/v1/payments-info', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (info) {
        if (info && info.provider) PROVIDER.mode = String(info.provider).toLowerCase();
        PROVIDER.loaded = true;
        return PROVIDER;
      })
      .catch(function () { PROVIDER.loaded = true; return PROVIDER; });
  }
  function isFam() { return PROVIDER.mode === 'famgateway'; }
  function isOff() { return PROVIDER.mode === 'off'; }

  /* The plan table mirrors api/subscriptions.py -> PLANS. The site and the
     app must never quote different numbers: Free ₹0 (25 AI requests a day),
     Pro ₹499/mo (50 a day), Lifetime ₹10,000 one-time (100 a day).
     No plan has an unlimited allowance. */
  var plans = [
    {
      code: 'free', name: 'Free', price: '₹0',
      desc: '25 AI requests a day, 3 saved dashboards, 7 days of chat history. ' +
            'Chat, SQL, file upload and the spreadsheet workbench.'
    },
    {
      code: 'pro', name: 'Pro', price: '₹499/mo',
      hint: 'auto-renews · cancel anytime',
      desc: '50 AI requests a day, 50 saved dashboards and a year of history, ' +
            'plus the export, automation, live-connection and governance tools.'
    },
    {
      code: 'lifetime', name: 'Lifetime', price: '₹10,000',
      hint: 'one-time · nothing to renew',
      desc: 'Everything in Pro, 100 AI requests a day, 1000 saved dashboards, ' +
            'and no further payment.',
      recommended: true
    },
  ];

  function famHint(p) {
    if (p.code === 'pro') return '31-day pass · renew by paying again';
    return p.hint || '';
  }
  function planHint(p) {
    if (isOff()) return ''; // no rail: no renewal promise to print
    return isFam() ? famHint(p) : p.hint;
  }

  /* The button says what happens next and what it costs. */
  function payLabel(p) {
    if (isFam()) {
      return p.code === 'pro' ? 'Pay ₹499 for 31 days of Pro (UPI)'
                              : 'Pay ₹10,000 once (UPI)';
    }
    return 'Pay ' + String(p.price).replace(/\/mo$/, '') + ' with Razorpay';
  }

  function hasPayLinks() {
    return !!LINKS.lifetime;
  }

  function toast(msg, ok) {
    var el = document.getElementById('billing-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'billing-toast';
      el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);' +
        'padding:12px 20px;border-radius:10px;font:500 14px Inter,sans-serif;z-index:9999;' +
        'background:var(--surface-deep,#040404);color:var(--text,#f4f4f4);box-shadow:0 8px 30px rgba(0,0,0,.35)';
      document.body.appendChild(el);
    }
    el.style.background = ok ? 'var(--success,#a2c3a8)' : 'var(--danger,#b07474)';
    el.textContent = msg;
    el.style.display = 'block';
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.style.display = 'none'; }, 4000);
  }

  function loadCheckoutJs() {
    return new Promise(function (resolve, reject) {
      if (window.Razorpay) return resolve();
      var s = document.createElement('script');
      s.src = 'https://checkout.razorpay.com/v1/checkout.js';
      s.onload = resolve;
      s.onerror = function () { reject(new Error('Could not load Razorpay Checkout')); };
      document.head.appendChild(s);
    });
  }

  function fetchJson(url, body, failMsg) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || failMsg); });
      return r.json();
    });
  }

  var busy = false;

  function buy(planCode, btn) {
    if (!BILLING_READY) {
      toast('Checkout is not enabled on this deployment — no billing server is configured, so no payment can be taken here. The free tier needs no card: download the app and create your account in it.', false);
      return;
    }
    if (busy) return;
    var email = (document.getElementById('email-' + planCode) || {}).value || '';
    email = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      toast('Enter the email for your account.', false);
      return;
    }
    busy = true;
    if (btn) btn.disabled = true;
    var settle = function () { busy = false; if (btn) btn.disabled = false; };
    toast('Starting checkout…', true);
    startOneTime(planCode, email).catch(function (err) {
      settle();
      toast(err.message || 'Checkout could not start.', false);
    });
  }

  function checkoutRedirect(target) {
    /* Only navigate on REAL https URLs — never javascript:/relative soup.
       The checkout_url is the hosted payment page (FamGateway) or a dev mock. */
    return typeof target === 'string' && /^https:\/\//i.test(target);
  }

  /* ONE entry point for both plans. FamGateway returns a hosted
     checkout_url (redirect); the dormant Razorpay rail answers the Pro
     call with "use /create-subscription", which falls back to the
     subscription flow below. */
  function startOneTime(planCode, email) {
    return fetchJson(API + '/create-order', { plan: planCode, email: email },
                     'Order failed')
      .then(function (order) {
        if (order && order.plan === planCode && checkoutRedirect(order.checkout_url)) {
          window.location.href = order.checkout_url;
          return;
        }
        if (!order || !order.order_id) {
          throw new Error('Checkout could not start.');
        }
        // Razorpay one-time popup (lifetime / non-fam hostgate).
        return loadCheckoutJs().then(function () {
          var rzp = new window.Razorpay({
            key: order.key_id,
            order_id: order.order_id,
            amount: order.amount,
            currency: order.currency,
            name: order.name,
            description: order.description,
            prefill: { email: email },
            theme: { color: '#7b7b7b' },
            handler: function (resp) {
              fetch(API + '/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  order_id: resp.razorpay_order_id,
                  payment_id: resp.razorpay_payment_id,
                  signature: resp.razorpay_signature,
                }),
              }).then(function (r) { return r.json(); }).then(function (v) {
                if (v && v.ok) {
                  window.location.href = API + '/success?order_id=' +
                    encodeURIComponent(resp.razorpay_order_id);
                } else {
                  toast((v && v.detail) || 'Payment verification failed — contact support.', false);
                }
              }).catch(function () { toast('Verification failed — contact support.', false); });
            },
            modal: {
              ondismiss: function () { toast('Checkout closed before payment.', false); },
            },
          });
          rzp.on('payment.failed', function () {
            toast('Payment failed — no money was taken. Try again.', false);
          });
          rzp.open();
        });
      })
      .catch(function (err) {
        // Razorpay-dormant hostgate: monthly Pro is subscription-only there.
        if (planCode === 'pro' && /create-subscription/i.test(String((err && err.message) || ''))) {
          return startSubscription(email).catch(function (e2) {
            toast(e2.message || 'Checkout could not start.', false);
          });
        }
        throw err;
      });
  }

  // Monthly Pro — Razorpay Subscriptions (auto-renewing; keyless).
  function startSubscription(email) {
    return fetchJson(API + '/create-subscription', { email: email },
                     'Subscription checkout failed')
      .then(function (sub) {
        // Dev/server-driven checkout: the server can hand back a hosted
        // checkout URL (mock in dev; real gateways may do the same).
        if (sub && checkoutRedirect(sub.checkout_url)) {
          window.location.href = sub.checkout_url;
          return;
        }
        return loadCheckoutJs().then(function () {
          var rzp = new window.Razorpay({
            key: sub.key_id,
            subscription_id: sub.subscription_id,
            name: sub.name,
            description: sub.description,
            prefill: { email: email },
            theme: { color: '#7b7b7b' },
            handler: function (resp) {
              fetch(API + '/verify-subscription', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  subscription_id: resp.razorpay_subscription_id,
                  payment_id: resp.razorpay_payment_id,
                  signature: resp.razorpay_signature,
                }),
              }).then(function (r) { return r.json(); }).then(function (v) {
                if (v && v.ok) {
                  window.location.href = API + '/success?subscription_id=' +
                    encodeURIComponent(resp.razorpay_subscription_id);
                } else {
                  toast((v && v.detail) || 'Payment verification failed — contact support.', false);
                }
              }).catch(function () { toast('Verification failed — contact support.', false); });
            },
            modal: {
              ondismiss: function () { toast('Checkout closed before payment.', false); },
            },
          });
          rzp.on('payment.failed', function () {
            toast('Payment failed — no money was taken. Try again.', false);
          });
          rzp.open();
        });
      });
  }

  function cta(p) {
    if (p.code === 'free') {
      /* There is no signup page on this site: the download IS the action.
         The free tier needs no card and no checkout. */
      return '<a class="btn btn-secondary" href="download.html">Download the free app</a>';
    }
    if (isOff()) {
      return '<p class="desc">Checkout is disabled on this deployment. ' +
        'The free tier needs no card.</p>';
    }
    // No-code fallback: Razorpay Payment Links (Lifetime only — links cannot
    // create auto-renewing subscriptions).
    var link = p.code === 'lifetime' ? LINKS.lifetime : '';
    if (link && !isFam()) {
      return '<a class="btn btn-primary" href="' + link + '" target="_blank" rel="noopener noreferrer">' +
        payLabel(p) + '</a>' +
        '<input id="email-' + p.code + '" type="email" placeholder="you@example.com"' +
        ' style="margin-top:10px;width:100%;padding:8px 12px;border-radius:8px;' +
        'border:1px solid var(--border,#e4e8ed);font:400 14px Inter,sans-serif"' +
        ' aria-label="Email for your account">';
    }
    var label = payLabel(p);
    return '<input id="email-' + p.code + '" type="email" placeholder="you@example.com"' +
      ' style="margin:10px 0;width:100%;padding:8px 12px;border-radius:8px;' +
      'border:1px solid var(--border,#e4e8ed);font:400 14px Inter,sans-serif"' +
      ' aria-label="Email for your account">' +
      '<button class="btn btn-primary" data-buy="' + p.code + '">' + label + '</button>';
  }

  function providerNotice() {
    if (isOff()) {
      return 'Checkout is disabled on this deployment — no payment can be ' +
        'taken here. The app itself is fully usable: the free tier needs ' +
        'no card. Self-hosting with Docker lets an admin on your own ' +
        'instance grant any plan.';
    }
    if (isFam()) {
      return 'Secure UPI checkout by FamGateway — a third-party payment ' +
        'automation service, not affiliated with (and distinct from) ' +
        'FamPay/FamApp/Trio. Your plan is attached to the email you pay ' +
        'with — no codes to paste; sign in with the same address. ' +
        'Pro is a 31-day pass: renew by paying again.';
    }
    return 'Secure checkout by Razorpay — UPI, cards, netbanking. Your plan ' +
      'is attached to the email you pay with — no codes to paste; sign in ' +
      'with the same address. Pro renews monthly; cancel anytime.';
  }

  function paint() {
    var root = document.getElementById('billing-root') || document.body;
    var notice = isOff()
      ? '<div class="billing-notice">' + providerNotice() + '</div>'
      : (!BILLING_READY && !hasPayLinks()
      ? '<div class="billing-notice">Checkout is not enabled on this deployment — no billing server is configured, so no payment can be taken here. The app itself is fully usable: download it, create your own account, and the free tier needs no card. Self-hosting with Docker removes the checkout entirely; an admin on your own instance can grant any plan.</div>'
      : (hasPayLinks() && !isFam()
        ? '<div class="billing-notice">Secure checkout by Razorpay — pay with UPI, cards, or netbanking. Sign in with the same email after checkout and your plan is attached to that account. Pro renews monthly; cancel anytime.</div>'
        : (EXTERNAL && !isFam()
          ? '<div class="billing-notice">Purchases are completed on our secure billing website.</div>'
          : '<div class="billing-notice">' + providerNotice() + '</div>')));
    root.innerHTML =
      '<h1>Pricing</h1>' + notice +
      '<div class="plans">' +
      plans.map(function (p) {
        var hint = planHint(p);
        return '<div class="plan' + (p.recommended ? ' recommended' : '') + '">' +
          '<h3>' + p.name + '</h3>' +
          '<p class="price">' + p.price + (hint ? '<span class="price-hint">' + hint + '</span>' : '') + '</p>' +
          '<p class="desc">' + p.desc + '</p>' +
          cta(p) +
          '</div>';
      }).join('') +
      '</div>';
    root.querySelectorAll('[data-buy]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        buy(btn.getAttribute('data-buy'), btn);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    detectProvider().then(paint);
  });
})();

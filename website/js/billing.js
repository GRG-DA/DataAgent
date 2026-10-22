/* ============================================================
   DataAgent — Pricing storefront (static)
   Flow (Razorpay Standard Checkout, keys live on the billing server):
     Pro      → POST {API}/create-subscription → Checkout in subscription
                mode (subscription_id) → POST {API}/verify-subscription
                → /success?subscription_id= (keyless: the plan follows the
                buyer's email; renews monthly, cancel anytime).
     Lifetime → POST {API}/create-order → Razorpay Checkout popup
                → POST {API}/verify → /success?order_id= (keyless — no codes
                to paste; entitlement follows the buyer's email).
   A Razorpay Payment Link (window.RAZORPAY_PAYMENT_LINKS.lifetime) remains a
   no-code fallback for Lifetime only — links cannot create subscriptions.
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

  /* The button says what happens next and what it costs. */
  function payLabel(p) {
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

  function buy(planCode) {
    if (!BILLING_READY) {
      toast('Checkout is not enabled on this deployment — no billing server is configured, so no payment can be taken here. The free tier needs no card: download the app and create your account in it.', false);
      return;
    }
    var email = (document.getElementById('email-' + planCode) || {}).value || '';
    email = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      toast('Enter the email for your account.', false);
      return;
    }
    toast('Starting checkout…', true);
    loadCheckoutJs().then(function () {
      return planCode === 'pro' ? startSubscription(email) : startOneTime(planCode, email);
    }).catch(function (err) {
      toast(err.message || 'Checkout could not start.', false);
    });
  }

  // Monthly Pro — Razorpay Subscriptions (auto-renewing; keyless).
  function startSubscription(email) {
    return fetch(API + '/create-subscription', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email }),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || 'Subscription checkout failed'); });
      return r.json();
    }).then(function (sub) {
      // Dev/server-driven checkout: the server can hand back a hosted
      // checkout URL (mock in dev; real gateways may do the same).
      if (sub.checkout_url) {
        window.location.href = sub.checkout_url;
        return;
      }
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
  }

  // Lifetime — one-time Razorpay Order (keyless: entitlement follows email).
  function startOneTime(planCode, email) {
    return fetch(API + '/create-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: planCode, email: email }),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || 'Order failed'); });
      return r.json();
    }).then(function (order) {
      // Dev/server-driven checkout: the server can hand back a hosted
      // checkout URL (mock in dev; real gateways may do the same).
      if (order.checkout_url) {
        window.location.href = order.checkout_url;
        return;
      }
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
  }

  function cta(p) {
    if (p.code === 'free') {
      /* There is no signup page on this site: the download IS the action.
         The free tier needs no card and no checkout. */
      return '<a class="btn btn-secondary" href="download.html">Download the free app</a>';
    }
    // No-code fallback: Razorpay Payment Links (Lifetime only — links cannot
    // create auto-renewing subscriptions).
    var link = p.code === 'lifetime' ? LINKS.lifetime : '';
    if (link) {
      return '<a class="btn btn-primary" href="' + link + '" target="_blank" rel="noopener noreferrer">' +
        payLabel(p) + '</a>' +
        '<input id="email-' + p.code + '" type="email" placeholder="you@example.com"' +
        ' style="margin-top:10px;width:100%;padding:8px 12px;border-radius:8px;' +
        'border:1px solid var(--border,#e4e8ed);font:400 14px Inter,sans-serif"' +
        ' aria-label="Email for your account">';
    }
    if (!BILLING_READY) {
      return '<p style="margin:10px 0 0;font-size:0.85rem;font-weight:500;color:var(--text-secondary,#607493);line-height:1.6;">' +
        'No payment can be taken on this deployment. ' +
        (EXTERNAL
          ? '<a href="' + EXTERNAL + '" target="_blank" rel="noopener noreferrer">Buy on our billing site &rarr;</a>'
          : 'The free tier needs no card: <a href="download.html">download the app &rarr;</a> and create your account in it.') +
        '</p>';
    }
    return '<input id="email-' + p.code + '" type="email" placeholder="you@example.com"' +
      ' style="margin:10px 0;width:100%;padding:8px 12px;border-radius:8px;' +
      'border:1px solid var(--border,#e4e8ed);font:400 14px Inter,sans-serif"' +
      ' aria-label="Email for your account">' +
      '<button class="btn btn-primary" data-buy="' + p.code + '">' + payLabel(p) + '</button>';
  }

  function render() {
    var root = document.getElementById('billing-root') || document.body;
    var notice = (!BILLING_READY && !hasPayLinks())
      ? '<div class="billing-notice">Checkout is not enabled on this deployment — no billing server is configured, so no payment can be taken here. The app itself is fully usable: download it, create your own account, and the free tier needs no card. Self-hosting with Docker removes the checkout entirely; an admin on your own instance can grant any plan.</div>'
      : (hasPayLinks()
        ? '<div class="billing-notice">Secure checkout by Razorpay — pay with UPI, cards, or netbanking. Sign in with the same email after checkout and your plan is attached to that account. Pro renews monthly; cancel anytime.</div>'
        : (EXTERNAL
          ? '<div class="billing-notice">Purchases are completed on our secure billing website.</div>'
          : '<div class="billing-notice">Secure checkout by Razorpay — UPI, cards, netbanking. Your plan is attached to the email you pay with — no codes to paste; sign in with the same address. Pro renews monthly; cancel anytime.</div>'));
    root.innerHTML =
      '<h1>Pricing</h1>' + notice +
      '<div class="plans">' +
      plans.map(function (p) {
        return '<div class="plan' + (p.recommended ? ' recommended' : '') + '">' +
          '<h3>' + p.name + '</h3>' +
          '<p class="price">' + p.price + (p.hint ? '<span class="price-hint">' + p.hint + '</span>' : '') + '</p>' +
          '<p class="desc">' + p.desc + '</p>' +
          cta(p) +
          '</div>';
      }).join('') +
      '</div>';
    root.querySelectorAll('[data-buy]').forEach(function (btn) {
      btn.addEventListener('click', function () { buy(btn.getAttribute('data-buy')); });
    });
  }

  document.addEventListener('DOMContentLoaded', render);
})();

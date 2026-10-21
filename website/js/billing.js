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
  var RAZORPAY_MARK = 'Pay with Razorpay';

  var plans = [
    { code: 'free', name: 'Free', price: '$0', desc: 'Basic chat, 3 dashboards, 7-day history.' },
    { code: 'pro', name: 'Pro', price: '$6/mo', hint: '≈ ₹499/mo · auto-renews · cancel anytime', desc: 'Unlimited chat, 50 dashboards, ad-free.', recommended: true },
    { code: 'lifetime', name: 'Lifetime', price: '$120', hint: '≈ ₹10,000 · one-time', desc: 'Everything in Pro, lifetime access.' },
  ];

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
        'background:#0f172a;color:#fff;box-shadow:0 8px 30px rgba(0,0,0,.35)';
      document.body.appendChild(el);
    }
    el.style.background = ok ? '#059669' : '#dc2626';
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
      toast('Checkout is not enabled on this deployment — no billing server is configured. Self-host DataAgent with Docker to get the full product free forever.', false);
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
        theme: { color: '#6366f1' },
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
        theme: { color: '#6366f1' },
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
      return EXTERNAL
        ? '<a class="btn btn-secondary" href="' + EXTERNAL + '">Get started</a>'
        : '';
    }
    // No-code fallback: Razorpay Payment Links (Lifetime only — links cannot
    // create auto-renewing subscriptions).
    var link = p.code === 'lifetime' ? LINKS.lifetime : '';
    if (link) {
      return '<a class="btn btn-primary" href="' + link + '" target="_blank" rel="noopener noreferrer">' +
        RAZORPAY_MARK + '</a>' +
        '<input id="email-' + p.code + '" type="email" placeholder="you@example.com"' +
        ' style="margin-top:10px;width:100%;padding:8px 12px;border-radius:8px;' +
        'border:1px solid var(--border,#e2e8f0);font:400 14px Inter,sans-serif"' +
        ' aria-label="Email for your account">';
    }
    if (!BILLING_READY) {
      return '<p style="margin:10px 0 0;font-size:0.85rem;font-weight:500;color:var(--text-secondary,#64748b);line-height:1.6;">' +
        'Checkout is not enabled on this deployment — no billing server is configured, so no payment can be taken here. ' +
        (EXTERNAL
          ? '<a href="' + EXTERNAL + '" target="_blank" rel="noopener noreferrer">Buy on our billing site &rarr;</a>'
          : 'Self-host DataAgent with Docker and the whole product is free forever, with no account and no card. ' +
            '<a href="download.html">Download &amp; self-host &rarr;</a>') +
        '</p>';
    }
    return '<input id="email-' + p.code + '" type="email" placeholder="you@example.com"' +
      ' style="margin:10px 0;width:100%;padding:8px 12px;border-radius:8px;' +
      'border:1px solid var(--border,#e2e8f0);font:400 14px Inter,sans-serif"' +
      ' aria-label="Email for your account">' +
      '<button class="btn btn-primary" data-buy="' + p.code + '">' + RAZORPAY_MARK + '</button>';
  }

  function render() {
    var root = document.getElementById('billing-root') || document.body;
    var notice = (!BILLING_READY && !hasPayLinks())
      ? '<div class="billing-notice">Checkout is not enabled on this deployment — no billing server is configured, so no payment can be taken here. The app itself is fully usable: self-host it with Docker and every feature is free forever, with no account and no card.</div>'
      : (hasPayLinks()
        ? '<div class="billing-notice">Secure checkout by Razorpay — pay with UPI, cards, or netbanking. Sign in with the same email after checkout — your plan unlocks on every device. Pro renews monthly; cancel anytime.</div>'
        : (EXTERNAL
          ? '<div class="billing-notice">Purchases are completed on our secure billing website.</div>'
          : '<div class="billing-notice">Secure checkout by Razorpay — UPI, cards, netbanking. Your plan is tied to your email — no codes to paste; sign in with the same address. Pro renews monthly; cancel anytime.</div>'));
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

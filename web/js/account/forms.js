/**
 * Account forms that live inside the login overlay.
 *
 * "Forgot password" asks for an address and posts it to
 * `/auth/forgot-password`. The endpoint always answers the same way, so
 * this form never says whether an account exists — it shows the same
 * generic sentence in both cases.
 */
(function () {
  "use strict";

  var GENERIC = "If that account exists, a reset link has been sent.";

  function esc(value) {
    if (window.App && window.App.escapeHtml) return window.App.escapeHtml(String(value == null ? "" : value));
    return String(value == null ? "" : value);
  }

  // A failed request, phrased for the reader.
  function failure(e, fallback) {
    if (window.AccountViews && window.AccountViews.failure) {
      return window.AccountViews.failure(e, fallback);
    }
    if (e && e.data && (e.data.detail || e.data.error)) {
      return String(e.data.detail || e.data.error);
    }
    if (e && e.message) return String(e.message);
    return fallback || "The request could not be sent.";
  }

  var INPUT =
    "width:100%;padding:11px 14px;border:1px solid #e5e7eb;border-radius:10px;" +
    "font-size:14px;box-sizing:border-box;";

  /**
   * Replace the login form with the "forgot password" form.
   * @param {HTMLElement} overlay  the login overlay
   * @param {Function} onBack      called when the user returns to sign-in
   */
  function showForgotPassword(overlay, onBack) {
    var card = overlay.firstElementChild;
    if (!card) return;
    card.innerHTML =
      '<div style="width:60px;height:60px;margin:0 auto 14px;border-radius:16px;' +
        'background:linear-gradient(135deg,#6366f1,#8b5cf6);display:flex;align-items:center;justify-content:center;">' +
        '<i class="fas fa-key" style="color:#fff;font-size:24px;"></i></div>' +
      '<h1 style="font-size:22px;font-weight:800;color:#111827;margin:0 0 6px;">Reset your password</h1>' +
      '<p style="font-size:14px;color:#6b7280;margin:0 0 20px;">Enter the address on your account and we will send a reset link.</p>' +
      '<input id="forgot-email" type="email" maxlength="254" autocomplete="email" aria-label="E-mail address" ' +
        'placeholder="E-mail address" style="' + INPUT + 'margin-bottom:12px;" />' +
      '<div id="forgot-status" style="display:none;margin-bottom:12px;padding:10px 12px;border-radius:8px;' +
        'background:#ecfdf5;color:#047857;font-size:13px;text-align:left;"></div>' +
      '<div id="forgot-error" style="display:none;margin-bottom:12px;padding:10px 12px;border-radius:8px;' +
        'background:#fef2f2;color:#dc2626;font-size:13px;text-align:left;"></div>' +
      '<button type="button" id="forgot-submit" style="width:100%;padding:11px;border:none;border-radius:10px;' +
        'background:#4f46e5;color:#fff;font-size:14px;font-weight:700;cursor:pointer;">Send the reset link</button>' +
      '<button type="button" id="forgot-back" style="width:100%;margin-top:10px;padding:10px;border:1px solid #e5e7eb;' +
        'border-radius:10px;background:#fff;color:#111827;font-size:14px;font-weight:600;cursor:pointer;">Back to sign in</button>';

    var email = card.querySelector("#forgot-email");
    var statusBox = card.querySelector("#forgot-status");
    var errorBox = card.querySelector("#forgot-error");
    var submit = card.querySelector("#forgot-submit");

    card.querySelector("#forgot-back").addEventListener("click", function () {
      if (typeof onBack === "function") onBack();
    });

    submit.addEventListener("click", function () {
      errorBox.style.display = "none";
      statusBox.style.display = "none";
      var value = (email.value || "").trim();
      if (!value) {
        errorBox.textContent = "Enter the e-mail address on your account.";
        errorBox.style.display = "block";
        return;
      }
      submit.disabled = true;
      submit.style.opacity = "0.6";
      window.API
        .forgotPassword(value)
        .then(function (res) {
          statusBox.textContent = (res && res.message) || GENERIC;
          statusBox.style.display = "block";
          submit.disabled = false;
          submit.style.opacity = "1";
          email.value = "";
        })
        .catch(function (e) {
          var message = failure(e, "The request could not be sent.");
          errorBox.textContent = message;
          errorBox.style.display = "block";
          submit.disabled = false;
          submit.style.opacity = "1";
        });
    });

    if (email) email.focus();
  }

  /**
   * Ask for a fresh confirmation mail. Always reports the same thing.
   * @returns {Promise<string>} the message to show the user
   */
  function resendVerification(email) {
    return window.API
      .resendVerification(email || "")
      .then(function (res) {
        return (res && res.message) || "If that account exists, a confirmation mail has been sent";
      })
      .catch(function (e) {
        throw new Error(failure(e, "The request could not be sent."));
      });
  }

  window.AccountForms = {
    showForgotPassword: showForgotPassword,
    resendVerification: resendVerification,
    failure: failure,
    esc: esc,
  };
})();

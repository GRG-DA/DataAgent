/**
 * Self-serve account deletion.
 *
 * The caller's own account goes away together with everything it owns
 * (dashboards, conversations, uploads, its tenant schema). The request
 * carries the current password *and* an explicit confirmation flag, so it
 * can never happen by accident.
 */
(function () {
  "use strict";

  var WARNING =
    "Your account and everything in it will be deleted: dashboards, " +
    "conversations, query history, API keys and your workspace data. " +
    "This cannot be undone.";

  function _message(e) {
    if (e && e.data && (e.data.detail || e.data.error)) {
      return String(e.data.detail || e.data.error);
    }
    if (e && e.message) return String(e.message);
    return "The account could not be deleted.";
  }

  /**
   * Delete the signed-in account.
   * @param {string} password  the caller's current password
   * @returns {Promise<boolean>} true when the account was deleted
   */
  async function run(password) {
    if (!password) {
      throw new Error("Enter your password to confirm the deletion.");
    }
    if (window.Modal && window.Modal.confirm) {
      var confirmed = await window.Modal.confirm("Delete your account", WARNING, {
        variant: "danger",
        confirmLabel: "Delete permanently",
      });
      if (!confirmed) return false;
    } else if (!window.confirm(WARNING)) {
      return false;
    }
    await window.API.deleteAccount(password);
    // The account is gone: drop the session before anything else can use it.
    try {
      localStorage.removeItem("session");
      localStorage.removeItem("auth_token");
      localStorage.removeItem("plan");
    } catch (e) {
      /* ignore */
    }
    window.location.hash = "#dashboard";
    window.location.reload();
    return true;
  }

  window.AccountDelete = {
    run: run,
    message: _message,
  };
})();

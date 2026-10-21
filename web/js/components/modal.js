/**
 * Modal v2 — Enhanced modal system with sizes, stacking, focus trap, prompt
 *
 * Features:
 * - Sizes: sm, md (default), lg, xl, full
 * - Nested stacking (multiple modals)
 * - Focus trap (Tab cycles through modal elements)
 * - ESC key closes top modal
 * - Backdrop click closes top modal
 * - Auto-focus first input
 * - confirm() and prompt() convenience methods
 * - Returns controller with .close()
 */
(function () {
  "use strict";

  const _stack = [];
  const _focusableSelector =
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function _createModalId() {
    return "app-modal-" + Date.now();
  }

  function _open(title, content, options) {
    options = options || {};
    const size = options.size || "md";
    const wideClass = options.wide ? " wide" : "";

    // Build size class
    const sizeMap = {
      sm: " max-w-sm",
      md: " max-w-md",
      lg: " max-w-lg",
      xl: " max-w-xl",
      full: " max-w-full",
    };
    const sizeClass = sizeMap[size] || " max-w-md";

    const modalId = _createModalId();

    const overlay = document.createElement("div");
    overlay.id = modalId;
    overlay.className = "modal-overlay";
    overlay.style.zIndex = 100 + _stack.length * 2;

    overlay.innerHTML =
      '<div class="modal-content' +
      wideClass +
      sizeClass +
      '">' +
      '<div class="flex justify-between items-center mb-4">' +
      '<h3 class="text-lg font-bold">' +
      _escape(title) +
      "</h3>" +
      '<button class="modal-close text-gray-400 hover:text-gray-600 text-xl">&times;</button>' +
      "</div>" +
      '<div class="modal-body">' +
      content +
      "</div>" +
      (options.footer
        ? '<div class="mt-4 flex justify-end gap-2">' + options.footer + "</div>"
        : "") +
      "</div>";

    document.body.appendChild(overlay);

    // Stack management
    const modalData = { id: modalId, overlay: overlay, options: options };
    _stack.push(modalData);

    // Close handlers
    const closeBtn = overlay.querySelector(".modal-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        _close(modalId);
      });
    }

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) _close(modalId);
    });

    // ESC key
    const escHandler = function (e) {
      if (e.key === "Escape") {
        _close(modalId);
        document.removeEventListener("keydown", escHandler);
      }
    };
    document.addEventListener("keydown", escHandler);

    // Focus trap
    const focusHandler = function (e) {
      if (e.key !== "Tab") return;
      const focusable = overlay.querySelectorAll(_focusableSelector);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    overlay.addEventListener("keydown", focusHandler);

    // Auto-focus first input
    setTimeout(function () {
      const firstInput = overlay.querySelector("input, textarea, select, button");
      if (firstInput) firstInput.focus();
    }, 100);

    return {
      close: function () {
        _close(modalId);
      },
      id: modalId,
    };
  }

  function _close(id) {
    for (var i = 0; i < _stack.length; i++) {
      if (_stack[i].id === id) {
        _stack[i].overlay.remove();
        _stack.splice(i, 1);
        break;
      }
    }
  }

  function _escape(str) {
    if (typeof str !== "string") return String(str);
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  // ── Public API
  window.Modal = {
    /** Show a modal. Returns { close, id } */
    show: function (title, content, options) {
      return _open(title, content, options);
    },

    /** Confirmation dialog. Returns Promise that resolves true/false */
    confirm: function (title, message, options) {
      options = options || {};
      return new Promise(function (resolve) {
        var footer =
          '<button class="modal-confirm-cancel btn btn-secondary btn-sm">' +
          (options.cancelLabel || "Cancel") +
          '</button>' +
          '<button class="modal-confirm-ok btn btn-' +
          (options.variant === "danger" ? "danger" : "primary") +
          " btn-sm" +
          '">' +
          (options.confirmLabel || "Confirm") +
          "</button>";
        var ctrl = _open(title, "<p>" + _escape(message) + "</p>", { footer: footer, size: "sm" });
        var overlay = document.getElementById(ctrl.id);
        if (overlay) {
          overlay.querySelector(".modal-confirm-cancel").addEventListener("click", function () {
            ctrl.close();
            resolve(false);
          });
          overlay.querySelector(".modal-confirm-ok").addEventListener("click", function () {
            ctrl.close();
            resolve(true);
          });
        }
      });
    },

    /** Simple text prompt. Returns Promise<string | null> */
    prompt: function (title, placeholder, options) {
      options = options || {};
      var defaultValue = options.defaultValue || "";
      return new Promise(function (resolve) {
        var footer =
          '<button class="modal-prompt-cancel btn btn-secondary btn-sm">Cancel</button>' +
          '<button class="modal-prompt-ok btn btn-primary btn-sm">OK</button>';
        var ctrl = _open(
          title,
          '<input id="modal-prompt-input" class="form-input" type="text" placeholder="' +
            _escape(placeholder || "") +
            '" value="' +
            _escape(defaultValue) +
            '">',
          { footer: footer, size: "sm" }
        );
        var overlay = document.getElementById(ctrl.id);
        if (overlay) {
          var input = overlay.querySelector("#modal-prompt-input");
          if (input) {
            input.focus();
            input.select();
            input.addEventListener("keydown", function (e) {
              if (e.key === "Enter") {
                ctrl.close();
                resolve(input.value);
              }
              if (e.key === "Escape") {
                ctrl.close();
                resolve(null);
              }
            });
          }
          overlay.querySelector(".modal-prompt-cancel").addEventListener("click", function () {
            ctrl.close();
            resolve(null);
          });
          overlay.querySelector(".modal-prompt-ok").addEventListener("click", function () {
            ctrl.close();
            resolve(input ? input.value : null);
          });
        }
      });
    },

    /** Close the topmost modal */
    close: function () {
      if (_stack.length > 0) {
        _close(_stack[_stack.length - 1].id);
      }
    },

    /** Close all modals */
    closeAll: function () {
      while (_stack.length > 0) {
        _close(_stack[0].id);
      }
    },

    /** Get current stack depth */
    get depth() {
      return _stack.length;
    },
  };
})();

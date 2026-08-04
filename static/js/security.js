/* ============================================================
   SafeExam — security.js
   Browser lock-down for the exam page. Detects and reports
   integrity violations and exposes fullscreen helpers.

   Usage:
     SEBSecurity.init({ onViolation: (type, details) => {...} });
   ============================================================ */
(function (global) {
  "use strict";

  const SEBSecurity = {
    _onViolation: function () {},
    _enabled: false,

    init(opts) {
      opts = opts || {};
      this._onViolation = opts.onViolation || function () {};
      this._enabled = true;
      this._bind();
      this._watchDevTools();
      return this;
    },

    report(type, details) {
      if (this._enabled) this._onViolation(type, details || null);
    },

    /* ---- Fullscreen helpers ---- */
    requestFullscreen() {
      const el = document.documentElement;
      const fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
      if (fn) {
        try { return fn.call(el); } catch (e) { /* ignore */ }
      }
      return Promise.reject();
    },

    isFullscreen() {
      return !!(document.fullscreenElement || document.webkitFullscreenElement);
    },

    /* ---- Event binding ---- */
    _bind() {
      // Tab / window focus loss
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) this.report("Tab Switch");
      });
      window.addEventListener("blur", () => this.report("Window Blur"));

      // Fullscreen exit
      document.addEventListener("fullscreenchange", () => {
        if (!this.isFullscreen()) this.report("Exited Fullscreen");
      });

      // Clipboard
      document.addEventListener("copy", (e) => { e.preventDefault(); this.report("Copy Attempt"); });
      document.addEventListener("cut", (e) => { e.preventDefault(); this.report("Cut Attempt"); });
      document.addEventListener("paste", (e) => { e.preventDefault(); this.report("Paste Attempt"); });

      // Right click + text selection
      document.addEventListener("contextmenu", (e) => { e.preventDefault(); this.report("Right Click Attempt"); });
      document.addEventListener("selectstart", (e) => {
        if (!/^(INPUT|TEXTAREA)$/.test((e.target.tagName || ""))) e.preventDefault();
      });

      // Keyboard shortcuts (copy/paste/cut, view-source, save, print, dev tools)
      document.addEventListener("keydown", (e) => this._onKey(e), true);

      // Window resize (possible attempt to un-maximise / split screen)
      let rt;
      window.addEventListener("resize", () => {
        clearTimeout(rt);
        rt = setTimeout(() => this.report("Window Resized"), 400);
      });
    },

    _onKey(e) {
      const k = (e.key || "").toLowerCase();
      const ctrl = e.ctrlKey || e.metaKey;

      // Block dev tools: F12, Ctrl+Shift+I/J/C, Ctrl+U
      if (
        e.key === "F12" ||
        (ctrl && e.shiftKey && ["i", "j", "c"].includes(k)) ||
        (ctrl && k === "u")
      ) {
        e.preventDefault();
        this.report("DevTools Suspected", "key:" + e.key);
        return;
      }

      // Block clipboard / save / print shortcuts
      if (ctrl && ["c", "v", "x", "s", "p"].includes(k)) {
        e.preventDefault();
        this.report("Keyboard Shortcut Attempt", "ctrl+" + k);
        return;
      }

      // Block Alt+Tab hint (cannot truly trap, but flag)
      if (e.altKey && k === "tab") {
        this.report("Tab Switch", "alt+tab");
      }
    },

    /* ---- Heuristic dev-tools open detection (window size delta) ---- */
    _watchDevTools() {
      const threshold = 170;
      let fired = false;
      setInterval(() => {
        const wDiff = window.outerWidth - window.innerWidth;
        const hDiff = window.outerHeight - window.innerHeight;
        const open = wDiff > threshold || hDiff > threshold;
        if (open && !fired) {
          fired = true;
          this.report("DevTools Suspected", "viewport-delta");
        } else if (!open) {
          fired = false;
        }
      }, 1500);
    },
  };

  global.SEBSecurity = SEBSecurity;
})(window);

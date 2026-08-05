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
      // Entering full screen fires resize/focus events of its own. Without a
      // short settling window those count as violations and can terminate the
      // attempt the instant it opens.
      this._graceUntil = Date.now() + (opts.graceMs || 2500);
      this._fsChangedAt = 0;
      this._bind();
      this._watchDevTools();
      return this;
    },

    report(type, details) {
      if (!this._enabled) return;
      if (Date.now() < this._graceUntil) return;   // start-up noise, not misconduct
      this._onViolation(type, details || null);
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
        this._fsChangedAt = Date.now();
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
        rt = setTimeout(() => {
          // Entering or leaving full screen resizes the window by definition —
          // that resize is not a violation on its own.
          if (Date.now() - this._fsChangedAt < 1500) return;
          this.report("Window Resized");
        }, 400);
      });
    },

    /* ---- Human-readable key names, so the student is told exactly what
            they pressed rather than a generic "shortcut blocked". ---- */
    _keyLabel(e) {
      const k = e.key || "";
      const named = {
        " ": "Spacebar", Escape: "Esc", Tab: "Tab", Enter: "Enter",
        Backspace: "Backspace", Delete: "Delete", CapsLock: "Caps Lock",
        Control: "Ctrl", Alt: "Alt", Shift: "Shift", Meta: "Windows key",
        ArrowUp: "Up arrow", ArrowDown: "Down arrow",
        ArrowLeft: "Left arrow", ArrowRight: "Right arrow",
        Home: "Home", End: "End", PageUp: "Page Up", PageDown: "Page Down",
        PrintScreen: "Print Screen", Insert: "Insert", ContextMenu: "Menu key",
        NumLock: "Num Lock", ScrollLock: "Scroll Lock", Pause: "Pause",
      };
      if (named[k]) return named[k];
      if (/^F\d{1,2}$/.test(k)) return k;
      if (k.length === 1) return k.toUpperCase() + " key";
      return k || "Unknown key";
    },

    _combo(e) {
      const mods = [];
      if (e.ctrlKey) mods.push("Ctrl");
      if (e.altKey) mods.push("Alt");
      if (e.shiftKey) mods.push("Shift");
      if (e.metaKey) mods.push("Windows");
      const isModifier = ["Control", "Alt", "Shift", "Meta"].indexOf(e.key) !== -1;
      if (!isModifier) mods.push(this._keyLabel(e));
      return mods.length ? mods.join(" + ") : this._keyLabel(e);
    },

    /*
      The exam is answered with the mouse only: every key is swallowed and
      reported by name. A few keys are handled by the operating system before
      the page ever sees them and cannot be blocked from a web page —
      Ctrl+Alt+Del, the Windows key, Alt+Tab and Esc (which the browser
      reserves for leaving full screen). Those still get reported: Esc shows up
      as "Exited Fullscreen" and Alt+Tab as a tab switch or window blur.
    */
    _onKey(e) {
      e.preventDefault();
      e.stopPropagation();

      // Holding a key down repeats it dozens of times a second — report once.
      if (e.repeat) return;

      const k = (e.key || "").toLowerCase();
      const ctrl = e.ctrlKey || e.metaKey;
      const combo = this._combo(e);

      // Dev tools: F12, Ctrl+Shift+I/J/C, Ctrl+U
      if (
        e.key === "F12" ||
        (ctrl && e.shiftKey && ["i", "j", "c"].includes(k)) ||
        (ctrl && k === "u")
      ) {
        this.report("DevTools Suspected", combo);
        return;
      }

      // Clipboard / save / print / find / select-all
      if (ctrl && ["c", "v", "x", "s", "p", "a", "f", "r"].includes(k)) {
        this.report("Keyboard Shortcut Attempt", combo);
        return;
      }

      if (e.altKey && k === "tab") {
        this.report("Tab Switch", combo);
        return;
      }

      // Anything else — a plain letter, digit, arrow, function or modifier key.
      this.report("Blocked Key", combo);
    },

    /* ---- Heuristic dev-tools detection ----
       Comparing outerWidth-innerWidth against a fixed threshold produces false
       positives on narrow windows, zoomed pages and browsers with a side panel
       open — a candidate can be disqualified for nothing more than a small
       screen. Instead, take a baseline at start-up and only flag a *growth* in
       the chrome size, which is what actually happens when dev tools open. */
    _watchDevTools() {
      const growth = 140;
      const base = {
        w: window.outerWidth - window.innerWidth,
        h: window.outerHeight - window.innerHeight,
      };
      let fired = false;
      setInterval(() => {
        const wDiff = window.outerWidth - window.innerWidth - base.w;
        const hDiff = window.outerHeight - window.innerHeight - base.h;
        const open = wDiff > growth || hDiff > growth;
        if (open && !fired) {
          fired = true;
          this.report("DevTools Suspected", "panel opened");
        } else if (!open) {
          fired = false;
        }
      }, 1500);
    },
  };

  global.SEBSecurity = SEBSecurity;
})(window);

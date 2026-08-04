/* ============================================================
   SafeExam — timer.js
   Countdown timer for the exam page. The authoritative remaining
   time comes from the server (monitoring.js syncs it); this module
   just renders a smooth local countdown and fires onExpire once.
   ============================================================ */
(function (global) {
  "use strict";

  const SEBTimer = {
    _remaining: 0,
    _interval: null,
    _expired: false,
    _onExpire: null,
    _el: null,

    start(seconds, el, onExpire) {
      this._remaining = Math.max(0, parseInt(seconds, 10) || 0);
      this._el = el;
      this._onExpire = onExpire;
      this._expired = false;
      this.render();
      clearInterval(this._interval);
      this._interval = setInterval(() => this._tick(), 1000);
      return this;
    },

    /* Re-sync to the server's remaining time (avoids client clock drift / tab throttling). */
    sync(seconds) {
      const s = parseInt(seconds, 10);
      if (!isNaN(s)) this._remaining = Math.max(0, s);
      this.render();
    },

    _tick() {
      this._remaining -= 1;
      if (this._remaining <= 0) {
        this._remaining = 0;
        this.render();
        clearInterval(this._interval);
        if (!this._expired) {
          this._expired = true;
          if (typeof this._onExpire === "function") this._onExpire();
        }
        return;
      }
      this.render();
    },

    render() {
      if (!this._el) return;
      const m = Math.floor(this._remaining / 60);
      const s = this._remaining % 60;
      this._el.textContent = m + ":" + (s < 10 ? "0" : "") + s;

      // Visual urgency states on the parent pill.
      const pill = this._el.closest(".pill");
      if (pill) {
        pill.classList.toggle("danger", this._remaining <= 30);
        pill.classList.toggle("warn", this._remaining > 30 && this._remaining <= 120);
      }
    },

    stop() {
      clearInterval(this._interval);
    },
  };

  global.SEBTimer = SEBTimer;
})(window);

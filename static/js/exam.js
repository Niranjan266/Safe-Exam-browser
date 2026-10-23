/* ============================================================
   SafeExam — exam.js
   Orchestrates the locked-down exam page: ties together the timer,
   security lock-down and proctoring modules, manages the violation
   counter, answer autosave and termination.

   Reads configuration from window.SEB (injected by the template).
   ============================================================ */
(function () {
  "use strict";

  const SEB = window.SEB || {};
  const form = document.getElementById("examForm");
  const timerEl = document.getElementById("timer");
  const violEl = document.getElementById("violationCount");
  const warnBox = document.getElementById("warningBox");
  const gate = document.getElementById("startGate");
  const startBtn = document.getElementById("startBtn");

  let violations = 0;                       // weighted severity, server-authoritative
  let limit = SEB.maxViolations || 3;
  let terminated = false;

  function showWarning(msg) {
    if (!warnBox) return;
    warnBox.textContent = msg;
    warnBox.classList.add("show");
    clearTimeout(warnBox._t);
    warnBox._t = setTimeout(() => warnBox.classList.remove("show"), 2200);
  }

  /* ---------------- Termination ---------------- */
  function lockUI() {
    document.querySelectorAll("#examForm input, #examForm button").forEach((el) => (el.disabled = true));
  }

  function goLocked() {
    if (terminated) return;
    terminated = true;
    SEBTimer.stop();
    if (window.SEBMonitoring) SEBMonitoring.stop();
    lockUI();
    window.location = SEB.urls.locked;
  }

  function submitNow() {
    if (terminated) return;
    terminated = true;
    SEBTimer.stop();
    if (window.SEBMonitoring) SEBMonitoring.stop();
    // Submit first: lockUI() disables every form input, including the hidden
    // csrf_token, and disabled controls are left out of the POST (CSRF 400).
    form.submit();
    lockUI();
  }

  function onTerminate(reason, state) {
    if (state && state.completed) {
      window.location = SEB.urls.result;
      return;
    }
    showWarning("⛔ " + (reason || "Attempt ended"));
    setTimeout(goLocked, 800);
  }

  /* ---------------- Violations ---------------- */
  /* Tell the candidate exactly what they just did, not a generic warning. */
  function describe(type, details) {
    switch (type) {
      case "Blocked Key":
        return "Keyboard is disabled — you pressed " + (details || "a key");
      case "Keyboard Shortcut Attempt":
        return "Shortcut blocked — " + (details || "keyboard shortcut");
      case "DevTools Suspected":
        return "Developer tools blocked" + (details ? " — " + details : "");
      case "Right Click Attempt":
        return "Right-click is disabled";
      case "Copy Attempt":
        return "Copying is disabled";
      case "Cut Attempt":
        return "Cutting is disabled";
      case "Paste Attempt":
        return "Pasting is disabled";
      case "Tab Switch":
        return "You switched away from the exam" + (details ? " (" + details + ")" : "");
      case "Window Blur":
        return "The exam window lost focus";
      case "Exited Fullscreen":
        return "You left full screen";
      case "Window Resized":
        return "The exam window was resized";
      default:
        return type + (details ? " — " + details : "");
    }
  }

  function handleViolation(type, details) {
    if (terminated) return;

    // Optimistic bump so the badge reacts instantly; the server's weighted
    // total replaces it as soon as the response lands.
    violations += 1;
    if (violEl) violEl.textContent = Math.min(violations, limit);

    const what = describe(type, details);

    SEBMonitoring.postJSON(SEB.urls.violation, { type: type, details: details }).then((res) => {
      if (!res) return;

      // The server terminates on a WEIGHTED severity total, not on a raw count
      // of events — a dev-tools probe is worth more than a stray keystroke.
      // Showing the local count against that limit made the badge read "10 / 5".
      if (typeof res.limit === "number") limit = res.limit;
      if (typeof res.severity === "number") {
        violations = res.severity;
        if (violEl) violEl.textContent = Math.min(violations, limit);
      }

      const score = " (" + Math.min(violations, limit) + "/" + limit + ")";

      if (res.terminated) {
        if (violEl) violEl.textContent = limit;
        showWarning("⛔ " + what + " — violation limit reached, attempt terminated");
        setTimeout(goLocked, 1400);
      } else if (res.status === "high_risk") {
        showWarning("⚠ " + what + " — flagged as high risk" + score);
      } else {
        showWarning("⚠ " + what + score);
      }
    });
  }

  /* ---------------- Answer autosave + selection ---------------- */
  function wireAnswers() {
    form.querySelectorAll('input[type="radio"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        // highlight
        const name = radio.name;
        form.querySelectorAll('input[name="' + name + '"]').forEach((r) =>
          r.closest(".option-label").classList.toggle("selected", r.checked)
        );
        // autosave
        SEBMonitoring.postJSON(SEB.urls.answer, {
          question_id: parseInt(name, 10),
          selected: radio.value,
        });
      });
    });
  }

  /* ---------------- Start gate ---------------- */
  function gateError(msg) {
    const box = document.getElementById("gateError");
    if (box) {
      box.textContent = msg;
      box.style.display = "block";
    }
  }

  function clearGateError() {
    const box = document.getElementById("gateError");
    if (box) box.style.display = "none";
  }

  /* The gate runs in two steps so nothing that steals focus happens once the
     lock-down is live:
       1. camera permission + screen-share picker (both pop up native dialogs
          that drop full screen and blur the window);
       2. every instruction ticked -> "Start exam" enters full screen from that
          click, and only once full screen is confirmed does SEBSecurity start
          recording and blocking keys. */
  const permBtn = document.getElementById("permBtn");
  const permOk = document.getElementById("permOk");
  const ticks = Array.prototype.slice.call(document.querySelectorAll(".gate-tick"));
  const fsGate = document.getElementById("fsGate");
  const fsBtn = document.getElementById("fsBtn");
  let permsReady = false;
  let started = false;

  function refreshStart() {
    if (startBtn) startBtn.disabled = !(permsReady && ticks.every((t) => t.checked));
  }

  function requestPermissions() {
    clearGateError();
    permBtn.disabled = true;
    const wantCam = SEB.requireWebcam !== false;
    const cam = wantCam ? SEBMonitoring.requestCamera() : Promise.resolve({ ok: true });
    cam.then((c) => {
      if (!c.ok) {
        permBtn.disabled = false;
        gateError(c.reason === "unsupported"
          ? "This browser cannot use a camera. Please use Chrome, Edge or Firefox on a desktop computer."
          : "Camera access was blocked. It is required for this exam — allow it in the address bar and try again.");
        return;
      }
      return SEBMonitoring.requestScreen().then((res) => {
        if (!res.ok) {
          permBtn.disabled = false;
          if (res.reason === "partial") {
            gateError(
              "You shared a single " + (res.surface === "browser" ? "tab" : "window") +
              ". The proctor must be able to see your entire screen — choose the " +
              '"Entire screen" option and share again.'
            );
          } else if (res.reason === "unsupported") {
            gateError("This browser cannot share a screen. Please use Chrome, Edge or Firefox on a desktop computer.");
          } else {
            gateError("Screen sharing was declined. It is required for this exam — try again and choose your entire screen.");
          }
          return;
        }
        permsReady = true;
        permBtn.style.display = "none";
        if (permOk) permOk.style.display = "flex";
        refreshStart();
      });
    });
  }

  function beginExam() {
    if (started || !permsReady || !ticks.every((t) => t.checked)) return;
    clearGateError();
    startBtn.disabled = true;
    // Called straight from the click so the browser accepts the request.
    SEBSecurity.requestFullscreen().then(() => {
      started = true;
      enterExam();
    }).catch(() => {
      gateError("The browser refused full screen. Press Start exam again.");
      refreshStart();
    });
  }

  function watchFullscreen() {
    document.addEventListener("fullscreenchange", () => {
      if (terminated) return;
      if (fsGate) fsGate.style.display = SEBSecurity.isFullscreen() ? "none" : "flex";
    });
    if (fsBtn) fsBtn.addEventListener("click", () => {
      SEBSecurity.requestFullscreen().catch(() => {});
    });
  }

  function enterExam() {
    if (gate) gate.style.display = "none";

    // Full screen is already confirmed; graceMs absorbs the resize/focus
    // events the switch itself produces.
    SEBSecurity.init({ onViolation: handleViolation, graceMs: 2500 });
    watchFullscreen();

    SEBMonitoring.init({
      urls: SEB.urls,
      snapshotInterval: SEB.snapshotInterval,
      // Per-exam webcam switch, set by the admin/teacher on the exam form.
      requireWebcam: SEB.requireWebcam !== false,
      // Null on serverless hosts; monitoring.js then falls back to HTTP and
      // picks a slower capture rate on its own.
      wsUrl: SEB.wsUrl || null,
      onTerminate: onTerminate,
      onCameraDenied: () => handleViolation("No Face Detected", "camera denied"),
      onScreenDenied: () => handleViolation("Screen Share Denied", "getDisplayMedia denied"),
      onScreenEnded: () => handleViolation("Screen Share Stopped", "track ended"),
      onState: (s) => {
        if (typeof s.remaining === "number") SEBTimer.sync(s.remaining);
      },
    });

    SEBTimer.start(SEB.secondsRemaining, timerEl, submitNow);

    wireAnswers();

    form.addEventListener("submit", () => {
      terminated = true;
      SEBTimer.stop();
      if (window.SEBMonitoring) SEBMonitoring.stop();
    });
  }

  if (startBtn) {
    ticks.forEach((t) => t.addEventListener("change", refreshStart));
    if (permBtn) permBtn.addEventListener("click", requestPermissions);
    startBtn.addEventListener("click", beginExam);
  } else {
    // No gate present — begin immediately.
    document.addEventListener("DOMContentLoaded", enterExam);
  }
})();

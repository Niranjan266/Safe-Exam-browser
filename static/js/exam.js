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

  let violations = 0;
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
    lockUI();
    form.submit();
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
  function handleViolation(type, details) {
    if (terminated) return;
    violations += 1;
    if (violEl) violEl.textContent = violations;

    SEBMonitoring.postJSON(SEB.urls.violation, { type: type, details: details }).then((res) => {
      if (!res) return;
      if (res.terminated) {
        showWarning("⛔ Violation limit exceeded — attempt terminated");
        setTimeout(goLocked, 900);
      } else if (res.status === "high_risk") {
        showWarning("⚠ " + type + " — you are being flagged (high risk)");
      } else {
        showWarning("⚠ " + type + " detected (" + violations + "/" + SEB.maxViolations + ")");
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
  function beginExam() {
    if (gate) gate.style.display = "none";

    SEBSecurity.requestFullscreen();

    SEBSecurity.init({ onViolation: handleViolation });

    SEBMonitoring.init({
      urls: SEB.urls,
      snapshotInterval: SEB.snapshotInterval,
      liveInterval: 1500,
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
    startBtn.addEventListener("click", beginExam);
  } else {
    // No gate present — begin immediately.
    document.addEventListener("DOMContentLoaded", beginExam);
  }
})();

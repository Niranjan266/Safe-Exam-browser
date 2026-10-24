/* ============================================================
   SafeExam — facecheck.js
   Local face checks on the candidate's webcam. Frames never leave the
   browser. Uses the native FaceDetector when present, otherwise lazy-loads
   the MediaPipe Tasks Vision face detector from jsDelivr. If neither is
   available it stays silent: a missing detector is never a violation.

     SEBFaceCheck.start(stream, onViolation)   // onViolation(type, details)
   ============================================================ */
(function (global) {
  "use strict";

  const MP_VERSION = "0.10.14";
  const MP_BASE = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@" + MP_VERSION;
  const MP_MODEL = "https://storage.googleapis.com/mediapipe-models/face_detector/" +
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite";

  const TICK_MS = 2000;
  const HOLD_MS = 5000;          // how long a condition must last before it is reported

  function loadDetector() {
    if (global.FaceDetector) {
      try {
        const d = new global.FaceDetector({ fastMode: true, maxDetectedFaces: 4 });
        return Promise.resolve((video) => d.detect(video).then((faces) =>
          faces.map((f) => f.boundingBox)));
      } catch (e) { /* fall through to MediaPipe */ }
    }
    return import(MP_BASE + "/vision_bundle.mjs").then((mod) =>
      mod.FilesetResolver.forVisionTasks(MP_BASE + "/wasm").then((fileset) =>
        mod.FaceDetector.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: MP_MODEL },
          runningMode: "VIDEO",
        })).then((d) => (video) => Promise.resolve(
          d.detectForVideo(video, performance.now()).detections
            .map((x) => x.boundingBox).filter(Boolean))));
  }

  /* Episode rules: each condition is reported once, then re-armed when it clears. */
  function makeTracker(report) {
    const since = { none: 0, away: 0 };
    const sent = { none: false, away: false, multi: false };

    function episode(key, active, now, type, details) {
      if (!active) { since[key] = 0; sent[key] = false; return; }
      if (!since[key]) since[key] = now;
      if (!sent[key] && now - since[key] > HOLD_MS) { sent[key] = true; report(type, details); }
    }

    return function update(boxes, vw, vh, now) {
      const n = boxes.length;
      episode("none", n === 0, now, "No Face Detected", "no face for over 5s");
      if (n >= 2) {
        if (!sent.multi) { sent.multi = true; report("Multiple Faces", n + " faces in frame"); }
      } else {
        sent.multi = false;
      }
      let away = false;
      if (n === 1 && vw && vh) {
        const b = boxes[0];
        const cx = (b.originX + b.width / 2) / vw;
        const cy = (b.originY + b.height / 2) / vh;
        away = cx < 0.15 || cx > 0.85 || cy < 0.1 || cy > 0.9;
      }
      episode("away", away, now, "Looking Away", "face off-centre for over 5s");
    };
  }

  const SEBFaceCheck = {
    _timer: null,

    start(stream, onViolation) {
      if (!stream || this._timer) return;
      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.srcObject = stream;
      video.play().catch(() => {});
      const update = makeTracker(onViolation || function () {});

      loadDetector().then((detect) => {
        let busy = false;
        this._timer = setInterval(() => {
          if (busy || video.readyState < 2) return;
          busy = true;
          detect(video)
            .then((boxes) => update(boxes, video.videoWidth, video.videoHeight, Date.now()))
            .catch(() => {})
            .then(() => { busy = false; });
        }, TICK_MS);
      }).catch(() => { /* detector unavailable: stay silent */ });
    },

    stop() {
      clearInterval(this._timer);
      this._timer = null;
    },
  };

  global.SEBFaceCheck = SEBFaceCheck;
})(window);

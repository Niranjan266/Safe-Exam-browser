/* ============================================================
   SafeExam — monitoring.js
   Client-side proctoring: server heartbeat, authoritative state
   polling, periodic webcam snapshots (kept for the record), and
   LIVE screen + webcam streaming so a proctor can watch in real time.

   Usage:
     SEBMonitoring.init({
       urls: {...}, snapshotInterval: 20, liveInterval: 1500,
       onState, onTerminate, onCameraDenied, onScreenDenied, onScreenEnded
     });
   ============================================================ */
(function (global) {
  "use strict";

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      keepalive: true,
    }).then((r) => r.json()).catch(() => null);
  }

  const SEBMonitoring = {
    _opts: null,
    _liveInterval: 1500,

    init(opts) {
      this._opts = opts || {};
      this._liveInterval = this._opts.liveInterval || 1500;
      this._startHeartbeat();
      this._startStatePoll();
      this._startWebcam();
      this._startScreen();
      window.addEventListener("beforeunload", () => {
        navigator.sendBeacon && this._beaconHeartbeat();
      });
      return this;
    },

    _beaconHeartbeat() {
      try {
        navigator.sendBeacon(this._opts.urls.heartbeat,
          new Blob([JSON.stringify({})], { type: "application/json" }));
      } catch (e) { /* ignore */ }
    },

    _handleState(state) {
      if (!state) return;
      if (this._opts.onState) this._opts.onState(state);
      if (state.disqualified || state.locked || state.completed) {
        const reason = state.reason ||
          (state.disqualified ? "Disqualified" : state.locked ? "Locked by proctor" : "Submitted");
        if (this._opts.onTerminate) this._opts.onTerminate(reason, state);
      }
    },

    _startHeartbeat() {
      const beat = () => postJSON(this._opts.urls.heartbeat, {}).then((s) => this._handleState(s));
      beat();
      this._hb = setInterval(beat, 5000);
    },

    _startStatePoll() {
      const poll = () => fetch(this._opts.urls.state).then((r) => r.json())
        .then((s) => this._handleState(s)).catch(() => {});
      this._sp = setInterval(poll, 3000);
    },

    /* ---- Send one JPEG frame (multipart) to an endpoint ---- */
    _sendFrame(canvas, url, field, kind) {
      canvas.toBlob((blob) => {
        if (!blob) return;
        const fd = new FormData();
        fd.append(field, blob, "f.jpg");
        if (kind) fd.append("kind", kind);
        fetch(url, { method: "POST", body: fd }).catch(() => {});
      }, "image/jpeg", 0.6);
    },

    _grab(video, canvas) {
      if (!video || video.readyState < 2) return false;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      return true;
    },

    /* ---- Webcam: pip + record snapshots + live cam frames ---- */
    _startWebcam() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
      navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 }, audio: false })
        .then((stream) => {
          this._camStream = stream;
          const pip = document.createElement("div");
          pip.className = "proctor-pip";
          pip.innerHTML = '<div class="pip-label"><span class="rec"></span> REC</div><video autoplay playsinline muted></video>';
          document.body.appendChild(pip);
          this._camVideo = pip.querySelector("video");
          this._camVideo.srcObject = stream;
          this._camCanvas = document.createElement("canvas");
          this._camCanvas.width = 320; this._camCanvas.height = 240;

          // Live webcam frames
          if (this._opts.urls.live) {
            this._camLive = setInterval(() => {
              if (this._grab(this._camVideo, this._camCanvas))
                this._sendFrame(this._camCanvas, this._opts.urls.live, "frame", "cam");
            }, this._liveInterval);
          }
          // Recorded snapshots (kept on the server)
          if (this._opts.snapshotInterval > 0) {
            this._snap = setInterval(() => {
              if (this._grab(this._camVideo, this._camCanvas))
                this._sendFrame(this._camCanvas, this._opts.urls.snapshot, "snapshot", null);
            }, this._opts.snapshotInterval * 1000);
          }
        })
        .catch(() => { if (this._opts.onCameraDenied) this._opts.onCameraDenied(); });
    },

    /* ---- Screen share: live screen frames ---- */
    _startScreen() {
      if (!this._opts.urls.live || !navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) return;
      navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 4 }, audio: false })
        .then((stream) => {
          this._screenStream = stream;
          const v = document.createElement("video");
          v.autoplay = true; v.muted = true; v.playsInline = true; v.srcObject = stream;
          this._screenVideo = v;
          this._screenCanvas = document.createElement("canvas");

          const track = stream.getVideoTracks()[0];
          if (track) track.addEventListener("ended", () => {
            clearInterval(this._screenLive);
            if (this._opts.onScreenEnded) this._opts.onScreenEnded();
          });

          v.onloadedmetadata = () => {
            const maxW = 1280;
            const scale = Math.min(1, maxW / (v.videoWidth || maxW));
            this._screenCanvas.width = Math.round((v.videoWidth || maxW) * scale);
            this._screenCanvas.height = Math.round((v.videoHeight || 720) * scale);
          };

          this._screenLive = setInterval(() => {
            if (this._grab(this._screenVideo, this._screenCanvas))
              this._sendFrame(this._screenCanvas, this._opts.urls.live, "frame", "screen");
          }, this._liveInterval);
        })
        .catch(() => { if (this._opts.onScreenDenied) this._opts.onScreenDenied(); });
    },

    stop() {
      clearInterval(this._hb); clearInterval(this._sp);
      clearInterval(this._snap); clearInterval(this._camLive); clearInterval(this._screenLive);
      [this._camStream, this._screenStream].forEach((s) => { if (s) s.getTracks().forEach((t) => t.stop()); });
    },
  };

  SEBMonitoring.postJSON = postJSON;
  global.SEBMonitoring = SEBMonitoring;
})(window);

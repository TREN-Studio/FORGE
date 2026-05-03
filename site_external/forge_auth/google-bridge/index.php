<?php
header('Cache-Control: no-store, no-cache, must-revalidate');
header('Pragma: no-cache');
header('Expires: 0');
header('Referrer-Policy: origin');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header("Content-Security-Policy: default-src 'self'; base-uri 'self'; img-src 'self' data: https://*.gstatic.com https://*.googleusercontent.com; style-src 'self' 'unsafe-inline' https://accounts.google.com; script-src 'self' 'unsafe-inline' https://accounts.google.com https://static.cloudflareinsights.com; connect-src https://accounts.google.com https://oauth2.googleapis.com https://cloudflareinsights.com; frame-src https://accounts.google.com https://*.google.com; form-action https://www.trenstudio.com; object-src 'none'; upgrade-insecure-requests");
?><!-- FORGE-owned Google bridge; keep this flow on trenstudio.com. -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FORGE Google Bridge</title>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <style>
    :root {
      --bg: #070707;
      --panel: rgba(16, 16, 16, 0.92);
      --line: rgba(255,255,255,0.09);
      --text: #f2f0ec;
      --muted: #a6a09a;
      --accent: #FF6B1A;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background:
        radial-gradient(circle at top left, rgba(255,107,26,0.18), transparent 28%),
        linear-gradient(135deg, #070707 0%, #12100f 100%);
      color: var(--text);
      font-family: "Segoe UI", system-ui, sans-serif;
    }
    .panel {
      width: min(100%, 560px);
      padding: 28px;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 28px 70px rgba(0,0,0,0.35);
    }
    .eyebrow {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: var(--accent);
    }
    h1 {
      margin: 14px 0 10px;
      font-size: clamp(28px, 5vw, 40px);
      line-height: 1.05;
    }
    p {
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
    }
    .button-host {
      margin-top: 24px;
      display: flex;
      justify-content: center;
      min-height: 46px;
    }
    .status {
      margin-top: 18px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.03);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .status.error {
      color: #ff8f8f;
      border-color: rgba(255,143,143,0.22);
      background: rgba(255,143,143,0.08);
    }
  </style>
</head>
<body>
  <main class="panel">
    <div class="eyebrow">TREN Studio Auth Bridge</div>
    <h1>Continue to FORGE with Google</h1>
    <p>This page completes Google sign-in on trenstudio.com, then returns you to the FORGE portal with a verified session.</p>
    <div id="google-button-host" class="button-host"></div>
    <div id="status" class="status">Preparing Google sign-in...</div>
  </main>

  <script>
    const allowedReturnTo = "https://www.trenstudio.com/FORGE/portal/api/index.php/auth/google/bridge-complete";
    const params = new URLSearchParams(window.location.search);
    const state = params.get("state") || "";
    const returnTo = params.get("return_to") || "";
    const clientId = params.get("client_id") || "";
    const statusNode = document.getElementById("status");
    const host = document.getElementById("google-button-host");

    function setStatus(message, tone = "") {
      statusNode.textContent = message;
      statusNode.className = `status${tone ? ` ${tone}` : ""}`;
    }

    function submitCredential(credential) {
      const form = document.createElement("form");
      form.method = "POST";
      form.action = returnTo;
      form.style.display = "none";
      const credentialInput = document.createElement("input");
      credentialInput.type = "hidden";
      credentialInput.name = "credential";
      credentialInput.value = credential;
      const stateInput = document.createElement("input");
      stateInput.type = "hidden";
      stateInput.name = "state";
      stateInput.value = state;
      form.appendChild(credentialInput);
      form.appendChild(stateInput);
      document.body.appendChild(form);
      form.submit();
    }

    function boot() {
      if (!state || !returnTo || !clientId) {
        setStatus("Google bridge is missing state, return_to, or client_id.", "error");
        return;
      }
      if (returnTo !== allowedReturnTo) {
        setStatus("Google bridge return target is not allowed.", "error");
        return;
      }
      if (!window.google || !window.google.accounts || !window.google.accounts.id) {
        setStatus("Google Identity Services failed to load on the bridge.", "error");
        return;
      }
      window.google.accounts.id.initialize({
        client_id: clientId,
        auto_select: false,
        cancel_on_tap_outside: true,
        callback: (response) => {
          const credential = (response && response.credential) || "";
          if (!credential) {
            setStatus("Google returned no credential.", "error");
            return;
          }
          setStatus("Google account verified. Returning to FORGE...");
          submitCredential(credential);
        }
      });
      window.google.accounts.id.renderButton(host, {
        theme: "outline",
        size: "large",
        text: "continue_with",
        shape: "pill",
        width: 360
      });
      setStatus("Google bridge is ready.");
    }

    const readyCheck = () => {
      if (window.google && window.google.accounts && window.google.accounts.id) {
        boot();
        return;
      }
      window.setTimeout(readyCheck, 200);
    };
    readyCheck();
  </script>
</body>
</html>

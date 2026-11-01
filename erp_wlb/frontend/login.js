(function () {
  const form = document.getElementById("loginForm");
  const err = document.getElementById("err");
  const btn = document.getElementById("loginBtn");

  // already signed in? skip the form
  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(r => { if (r.ok) location.replace("/app.html"); })
    .catch(() => {});

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    btn.disabled = true;
    btn.textContent = "Signing in…";
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value,
          password: document.getElementById("password").value,
        }),
      });
      if (res.ok) { location.replace("/app.html"); return; }
      let msg = "Sign in failed.";
      try { const d = await res.json(); if (typeof d.detail === "string") msg = d.detail; } catch (_) {}
      err.textContent = msg;
      err.hidden = false;
    } catch (_) {
      err.textContent = "Cannot reach the server. Is it running?";
      err.hidden = false;
    }
    btn.disabled = false;
    btn.textContent = "Sign in";
  });
})();

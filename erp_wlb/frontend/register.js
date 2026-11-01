(function () {
  const form = document.getElementById("regForm");
  const err = document.getElementById("err");
  const btn = document.getElementById("regBtn");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;

    const pw1 = document.getElementById("password").value;
    const pw2 = document.getElementById("password2").value;
    if (pw1 !== pw2) {
      err.textContent = "Passwords do not match.";
      err.hidden = false;
      return;
    }

    btn.disabled = true;
    btn.textContent = "Creating…";

    try {
      const res = await fetch("/api/register", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          full_name:   document.getElementById("full_name").value.trim(),
          username:    document.getElementById("username").value.trim(),
          email:       document.getElementById("email").value.trim() || null,
          department:  document.getElementById("department").value.trim() || "General",
          designation: document.getElementById("designation").value.trim(),
          password:    pw1,
          // no `role` - the server forces "employee"
        }),
      });

      if (res.ok) {
        // sign in with the credentials we just registered with
        const loginRes = await fetch("/api/auth/login", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: document.getElementById("username").value.trim(),
            password: pw1,
          }),
        });
        location.replace(loginRes.ok ? "/app.html" : "/login.html");
        return;
      }

      let msg = "Could not create account.";
      try {
        const d = await res.json();
        if (typeof d.detail === "string") msg = d.detail;
        else if (Array.isArray(d.detail)) msg = d.detail.map((x) => x.msg).join("; ");
      } catch (_) {}
      err.textContent = msg;
      err.hidden = false;
    } catch (_) {
      err.textContent = "Cannot reach the server. Is it running?";
      err.hidden = false;
    }

    btn.disabled = false;
    btn.textContent = "Create account";
  });
})();

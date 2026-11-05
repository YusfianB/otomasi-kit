// Landing page interactions: theme toggle, mobile menu, WhatsApp links/form.
(function () {
  "use strict";

  var root = document.documentElement;
  var waNumber = document.body.getAttribute("data-wa") || "";

  // ---- theme (terang/gelap), remembered in localStorage ----
  function currentTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "gelap" : "terang";
  }
  var themeBtn = document.getElementById("tema-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = currentTheme() === "gelap" ? "terang" : "gelap";
      root.dataset.theme = next;
      try { localStorage.setItem("tema", next); } catch (e) { /* private mode */ }
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", next === "gelap" ? "#0e1513" : "#0f766e");
    });
  }

  // ---- mobile navigation ----
  var nav = document.getElementById("nav");
  var menuBtn = document.getElementById("menu-toggle");
  function setMenu(open) {
    nav.classList.toggle("terbuka", open);
    menuBtn.setAttribute("aria-expanded", String(open));
    menuBtn.setAttribute("aria-label", open ? "Tutup menu" : "Buka menu");
  }
  if (nav && menuBtn) {
    menuBtn.addEventListener("click", function () {
      setMenu(!nav.classList.contains("terbuka"));
    });
    nav.addEventListener("click", function (e) {
      if (e.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("terbuka")) {
        setMenu(false);
        menuBtn.focus();
      }
    });
  }

  // ---- WhatsApp ----
  function waUrl(text) {
    return "https://wa.me/" + waNumber + "?text=" + encodeURIComponent(text);
  }
  document.querySelectorAll("[data-wa-link]").forEach(function (a) {
    if (!waNumber) return;
    a.href = waUrl("Halo, saya ingin bertanya/memesan.");
    a.target = "_blank";
    a.rel = "noopener";
  });

  var form = document.getElementById("form-kontak");
  if (form) {
    var error = document.getElementById("form-error");
    form.addEventListener("input", function () { error.hidden = true; });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var name = form.elements.nama.value.trim();
      var message = form.elements.pesan.value.trim();
      if (!name || !message) {
        error.hidden = false;
        (name ? form.elements.pesan : form.elements.nama).focus();
        return;
      }
      window.open(waUrl("Halo, saya " + name + ". " + message), "_blank", "noopener");
    });
  }

  var year = document.getElementById("tahun");
  if (year) year.textContent = String(new Date().getFullYear());
})();

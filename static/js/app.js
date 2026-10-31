// Small progressive enhancements. Every form still works (and is validated
// on the server) without JavaScript.
(function () {
  "use strict";

  // Live password checklist: <input data-password-rules="#rules-list">
  document.querySelectorAll("[data-password-rules]").forEach(function (input) {
    var list = document.querySelector(input.getAttribute("data-password-rules"));
    if (!list) return;
    var checks = {
      length: function (p) { return p.length > 8; },
      upper: function (p) { return /[A-Z]/.test(p); },
      lower: function (p) { return /[a-z]/.test(p); },
      special: function (p) { return /[^A-Za-z0-9\s]/.test(p); }
    };
    function update() {
      list.querySelectorAll("[data-rule]").forEach(function (li) {
        var ok = checks[li.getAttribute("data-rule")](input.value);
        li.classList.toggle("ok", ok);
      });
    }
    input.addEventListener("input", update);
    update();
  });

  // Ask before destructive actions: <form data-confirm="Cancel this invoice?">
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // Filter dropdowns submit their form on change: <select data-autosubmit>
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  // Footer: show how fast the server answered (Server-Timing header)
  var perf = document.getElementById("perf");
  if (perf && window.performance && performance.getEntriesByType) {
    window.addEventListener("load", function () {
      var nav = performance.getEntriesByType("navigation")[0];
      if (!nav) return;
      var server = (nav.serverTiming || []).find(function (t) { return t.name === "app"; });
      var parts = [];
      if (server) parts.push("Server " + Math.round(server.duration) + " ms");
      parts.push("page ready in " + Math.round(nav.domContentLoadedEventEnd) + " ms");
      perf.textContent = parts.join(", ");
    });
  }
})();

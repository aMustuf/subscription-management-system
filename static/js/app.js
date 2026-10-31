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

  // Show a block only when a select has a given value:
  // <select data-toggle-target="#box" data-toggle-value="products">
  document.querySelectorAll("[data-toggle-target]").forEach(function (select) {
    var target = document.querySelector(select.getAttribute("data-toggle-target"));
    var wanted = select.getAttribute("data-toggle-value");
    function sync() { if (target) target.hidden = select.value !== wanted; }
    select.addEventListener("change", sync);
    sync();
  });

  // Money in Indian format, e.g. 123456.5 -> ₹1,23,456.50
  function inr(n) {
    return "₹" + (Math.round(n * 100) / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  window.brightloopInr = inr;

  // Product lines editor (templates and subscriptions)
  document.querySelectorAll("[data-lines-root]").forEach(function (root) {
    var table = root.querySelector("[data-lines-editor]");
    var tbody = table.querySelector("tbody");
    var template = root.querySelector("template[data-line-template]");
    var withPrices = table.getAttribute("data-with-prices") === "1";
    var form = root.closest("form");

    function rows() { return Array.prototype.slice.call(tbody.querySelectorAll("tr.line-row")); }

    function recalc() {
      var untaxed = 0, tax = 0, qtyTotal = 0;
      rows().forEach(function (row) {
        var qty = parseInt(row.querySelector("[data-line-qty]").value, 10) || 0;
        qtyTotal += row.querySelector("[data-line-item]").value ? qty : 0;
        if (!withPrices) return;
        var price = parseFloat(row.querySelector("[data-line-price]").value) || 0;
        var taxSelect = row.querySelector("[data-line-tax]");
        var rate = parseFloat((taxSelect.selectedOptions[0] || {}).dataset ? taxSelect.selectedOptions[0].dataset.rate : 0) || 0;
        var amount = qty * price;
        row.querySelector("[data-line-amount]").textContent = inr(amount);
        untaxed += amount;
        tax += Math.round(amount * rate) / 100;
      });
      var plan = form && form.querySelector("[data-plan-select]");
      var summary = form && form.querySelector("[data-totals]");
      var minNote = form && form.querySelector("[data-min-qty-note]");
      if (plan) {
        var opt = plan.selectedOptions[0];
        var fee = opt && opt.value ? parseFloat(opt.dataset.price) || 0 : 0;
        var minQty = opt && opt.value ? parseInt(opt.dataset.min, 10) || 1 : 0;
        var feeRate = summary ? parseFloat(summary.dataset.defaultTaxRate) || 0 : 0;
        untaxed += fee;
        tax += Math.round(fee * feeRate) / 100;
        if (summary) {
          summary.querySelector("[data-plan-fee]").textContent = inr(fee);
          summary.querySelector("[data-period]").textContent = opt && opt.value ? opt.dataset.period : "period";
        }
        if (minNote) {
          minNote.hidden = !(minQty && qtyTotal < minQty);
          minNote.textContent = "This plan needs at least " + minQty + " units in total; the lines add up to " + qtyTotal + ".";
        }
      }
      if (summary) {
        summary.querySelector("[data-untaxed]").textContent = inr(untaxed);
        summary.querySelector("[data-tax]").textContent = inr(tax);
        summary.querySelector("[data-total]").textContent = inr(untaxed + tax);
      }
    }

    tbody.addEventListener("change", function (event) {
      var target = event.target;
      if (withPrices && target.matches("[data-line-item]")) {
        var opt = target.selectedOptions[0];
        var row = target.closest("tr");
        if (opt && opt.value) {
          row.querySelector("[data-line-price]").value = opt.dataset.price;
          row.querySelector("[data-line-tax]").value = opt.dataset.tax || "";
        }
      }
      recalc();
    });
    tbody.addEventListener("input", recalc);
    tbody.addEventListener("click", function (event) {
      var button = event.target.closest("[data-remove-line]");
      if (!button) return;
      if (rows().length > 1) {
        button.closest("tr").remove();
      } else {
        button.closest("tr").querySelectorAll("input, select").forEach(function (el) { el.value = el.matches("[data-line-qty]") ? "1" : ""; });
      }
      recalc();
    });
    root.querySelector("[data-add-line]").addEventListener("click", function () {
      var row = template.content.firstElementChild.cloneNode(true);
      tbody.appendChild(row);
      row.querySelector("[data-line-item]").focus();
      recalc();
    });
    if (form) {
      var plan = form.querySelector("[data-plan-select]");
      if (plan) plan.addEventListener("change", recalc);
    }
    recalc();
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

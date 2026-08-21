// app/plugins/autogrid360/static/currency.js
(function () {
  "use strict";

  function parseAmount(rawValue, policy) {
    let text = String(rawValue || "").trim();
    if (!text) {
      return null;
    }

    const symbol = policy.symbol || "";
    if (symbol) {
      if (text.startsWith(symbol)) {
        text = text.slice(symbol.length).trimStart();
      } else if (text.includes(symbol)) {
        return null;
      }
    }

    let sign = "";
    if (text.startsWith("+") || text.startsWith("-")) {
      sign = text[0];
      text = text.slice(1);
    }
    if (!text) {
      return null;
    }

    const decimalSeparator = policy.decimalSeparator;
    const decimalParts = text.split(decimalSeparator);
    if (decimalParts.length > 2) {
      return null;
    }

    let integerText = decimalParts[0];
    const fractionText = decimalParts.length === 2 ? decimalParts[1] : null;
    if (fractionText !== null && (fractionText === "" || !/^\d+$/.test(fractionText))) {
      return null;
    }

    const thousandsSeparator = policy.thousandsSeparator;
    if (thousandsSeparator && integerText.includes(thousandsSeparator)) {
      const groups = integerText.split(thousandsSeparator);
      if (
        !groups[0] ||
        groups[0].length > 3 ||
        !/^\d+$/.test(groups[0]) ||
        groups.slice(1).some(function (group) {
          return group.length !== 3 || !/^\d+$/.test(group);
        })
      ) {
        return null;
      }
      integerText = groups.join("");
    }

    if (!integerText) {
      integerText = "0";
    }
    if (!/^\d+$/.test(integerText)) {
      return null;
    }

    let normalized = sign + integerText;
    if (fractionText !== null && fractionText !== "") {
      normalized += "." + fractionText;
    }

    const amount = Number(normalized);
    return Number.isFinite(amount) ? amount : null;
  }

  function formatAmount(amount, policy) {
    const fixed = amount.toFixed(2);
    let parts = fixed.split(".");
    let integerText = parts[0];
    const fractionText = parts[1];
    let sign = "";

    if (integerText.startsWith("-")) {
      sign = "-";
      integerText = integerText.slice(1);
    }

    if (policy.thousandsSeparator) {
      integerText = integerText.replace(
        /\B(?=(\d{3})+(?!\d))/g,
        policy.thousandsSeparator
      );
    }

    return (
      policy.symbol +
      sign +
      integerText +
      policy.decimalSeparator +
      fractionText
    );
  }

  function initializePreview(input) {
    const form = input.closest("form");
    const outputId = input.dataset.currencyPreview;
    const output = outputId ? document.getElementById(outputId) : null;
    if (!form || !output) {
      return;
    }

    const policy = {
      symbol: form.dataset.currencySymbol || "",
      decimalSeparator: form.dataset.currencyDecimalSeparator || ".",
      thousandsSeparator: form.dataset.currencyThousandsSeparator || "",
    };

    function updatePreview() {
      const amount = parseAmount(input.value, policy);
      output.value = amount === null ? "" : formatAmount(amount, policy);
    }

    input.addEventListener("input", updatePreview);
    updatePreview();
  }

  document
    .querySelectorAll("[data-currency-preview]")
    .forEach(initializePreview);
})();

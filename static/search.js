// app/plugins/autogrid360/static/search.js
(() => {
  "use strict";

  const startYear = document.getElementById("min_year");
  const endYear = document.getElementById("max_year");
  if (startYear && endYear) {
    const endYearOptions = Array.from(endYear.options);

    function updateEndYears() {
      const minimum = Number.parseInt(startYear.value, 10);
      const hasMinimum = Number.isInteger(minimum);
      const selectedEnd = Number.parseInt(endYear.value, 10);

      for (const option of endYearOptions) {
        if (!option.value) {
          option.hidden = false;
          option.disabled = false;
          continue;
        }
        const year = Number.parseInt(option.value, 10);
        const allowed = !hasMinimum || year >= minimum;
        option.hidden = !allowed;
        option.disabled = !allowed;
      }

      if (hasMinimum && Number.isInteger(selectedEnd) && selectedEnd < minimum) {
        endYear.value = "";
      }
    }

    startYear.addEventListener("change", updateEndYears);
    updateEndYears();
  }

  const country = document.getElementById("country_code");
  const zone = document.getElementById("zone_code");
  if (country && zone) {
    const zoneOptions = Array.from(zone.options);

    function updateZones() {
      const countryCode = country.value;
      const selected = zone.selectedOptions[0];
      if (
        selected &&
        selected.dataset.country &&
        countryCode &&
        selected.dataset.country !== countryCode
      ) {
        zone.value = "";
      }

      for (const option of zoneOptions) {
        const optionCountry = option.dataset.country;
        if (!optionCountry) {
          option.hidden = false;
          option.disabled = false;
          continue;
        }
        const matches = !countryCode || optionCountry === countryCode;
        option.hidden = !matches;
        option.disabled = !matches;
      }
    }

    country.addEventListener("change", updateZones);
    updateZones();
  }
})();

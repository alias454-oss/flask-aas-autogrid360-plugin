// app/plugins/autogrid360/static/location.js
(() => {
  "use strict";

  function sameOriginUrl(value) {
    if (!value) {
      return null;
    }

    try {
      const url = new URL(value, window.location.origin);
      if (url.origin !== window.location.origin) {
        return null;
      }
      return url;
    } catch (_error) {
      return null;
    }
  }

  const forms = document.querySelectorAll("[data-autogrid360-location-form]");
  forms.forEach((form) => {
    const country = form.querySelector("[name='country_code']");
    const postal = form.querySelector("[name='postal_code']");
    const city = form.querySelector("[name='city']");
    const zone = form.querySelector("[name='zone_code']");
    if (!country || !postal || !city || !zone) {
      return;
    }

    const lookupUrl = form.dataset.geoLookupUrl;
    let autoCity = false;
    let autoZone = false;

    city.addEventListener("input", () => { autoCity = false; });
    zone.addEventListener("change", () => { autoZone = false; });

    async function loadZones(countryValue, selectedZone = "") {
      const endpoint = zone.dataset.zonesUrl;
      if (!endpoint || !countryValue) {
        zone.replaceChildren(new Option("Select country first", ""));
        zone.disabled = true;
        return;
      }

      zone.replaceChildren(new Option("Loading subdivisions…", ""));
      zone.disabled = true;
      try {
        const url = sameOriginUrl(endpoint);
        if (!url) {
          throw new Error("Cross-origin subdivision lookup blocked");
        }
        url.searchParams.set("country", countryValue);
        const response = await fetch(url, {
          credentials: "same-origin",
          headers: {"Accept": "application/json"},
        });
        if (!response.ok) {
          throw new Error("Subdivision lookup failed");
        }

        const payload = await response.json();
        const zones = Array.isArray(payload.zones) ? payload.zones : [];
        zone.replaceChildren(new Option(
          zones.length ? "Select subdivision" : "No ISO subdivision available",
          "",
        ));
        zones.forEach((item) => zone.add(new Option(item.label, item.code)));
        if (selectedZone && zones.some((item) => item.code === selectedZone)) {
          zone.value = selectedZone;
        }
        zone.disabled = false;
      } catch (_error) {
        zone.replaceChildren(new Option("Subdivision data unavailable", ""));
        zone.disabled = false;
      }
    }

    const profileButton = form.querySelector("[data-use-profile-location]");
    if (profileButton) {
      profileButton.addEventListener("click", async () => {
        const countryValue = profileButton.dataset.countryCode || "";
        country.value = countryValue;
        postal.value = profileButton.dataset.postalCode || "";
        city.value = profileButton.dataset.city || "";
        await loadZones(countryValue, profileButton.dataset.zoneCode || "");
        autoCity = false;
        autoZone = false;
      });
    }

    async function resolvePostalLocation() {
      if (!lookupUrl) {
        return;
      }

      const countryValue = country.value.trim();
      const postalValue = postal.value.trim();
      if (!countryValue || !postalValue) {
        return;
      }

      try {
        const url = sameOriginUrl(lookupUrl);
        if (!url) {
          return;
        }
        url.searchParams.set("country", countryValue);
        url.searchParams.set("postal_code", postalValue);
        const response = await fetch(url, {
          headers: { "Accept": "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) {
          return;
        }
        const data = await response.json();
        if ((!city.value.trim() || autoCity) && data.city) {
          city.value = data.city;
          autoCity = true;
        }
        if ((!zone.value || autoZone) && data.zone_code) {
          const available = Array.from(zone.options).some(
            (option) => option.value === data.zone_code,
          );
          if (available) {
            zone.value = data.zone_code;
            autoZone = true;
          }
        }
      } catch (_error) {
        // Postal lookup is optional progressive enhancement; manual locality remains valid.
      }
    }

    postal.addEventListener("change", resolvePostalLocation);
  });
})();

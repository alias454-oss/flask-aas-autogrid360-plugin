(function () {
  "use strict";

  const OTHER_VALUE = "__other__";

  function bindOtherValue(selectId, inputId) {
    const select = document.getElementById(selectId);
    const input = document.getElementById(inputId);
    if (!select || !input) {
      return;
    }

    const group = input.closest(".form-group");
    if (!group) {
      return;
    }

    function update() {
      const usesOther = select.value === OTHER_VALUE;
      group.hidden = !usesOther;
      input.disabled = !usesOther;
      input.required = usesOther;
    }

    select.addEventListener("change", update);
    update();
  }

  bindOtherValue("year", "year_other");
  bindOtherValue("doors", "doors_other");
})();

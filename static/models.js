(function () {
  "use strict";

  const makeSelect = document.getElementById("make");
  const modelSelect = document.getElementById("model");
  if (!makeSelect || !modelSelect) {
    return;
  }

  const modelOther = document.getElementById("model_other");
  const modelOtherGroup = modelOther ? modelOther.closest(".form-group") : null;
  const modelOptions = Array.from(modelSelect.options);

  function updateOtherModel() {
    if (!modelOther || !modelOtherGroup) {
      return;
    }

    const usesOther = modelSelect.value === "__other__";
    modelOtherGroup.hidden = !usesOther;
    modelOther.disabled = !usesOther;
    modelOther.required = usesOther;
  }

  function updateModels() {
    const makeKey = makeSelect.value;
    const selected = modelSelect.selectedOptions[0];
    if (
      selected &&
      selected.dataset.make &&
      selected.dataset.make !== makeKey
    ) {
      modelSelect.value = "";
    }

    for (const option of modelOptions) {
      const optionMake = option.dataset.make;
      if (!optionMake) {
        option.hidden = false;
        option.disabled = false;
        continue;
      }

      const matches = Boolean(makeKey) && optionMake === makeKey;
      option.hidden = !matches;
      option.disabled = !matches;
    }

    // Public search has a blank make option; listing forms do not. Require the
    // make first there so model identity always remains make-scoped.
    modelSelect.disabled = !makeKey;
    updateOtherModel();
  }

  makeSelect.addEventListener("change", updateModels);
  modelSelect.addEventListener("change", updateOtherModel);
  updateModels();
})();

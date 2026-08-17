(function () {
  "use strict";

  function initializeGallery(root) {
    var mainLink = root.querySelector("[data-autogrid360-gallery-main-link]");
    var mainImage = root.querySelector("[data-autogrid360-gallery-main-image]");
    var section = root.closest("section");
    var lightbox = section && section.querySelector("[data-autogrid360-image-lightbox]");
    var lightboxImage = lightbox && lightbox.querySelector("[data-autogrid360-image-lightbox-image]");
    var thumbnails = section ? section.querySelectorAll("[data-autogrid360-gallery-thumbnail]") : [];

    if (!mainLink || !mainImage) {
      return;
    }

    thumbnails.forEach(function (thumbnail) {
      thumbnail.addEventListener("click", function (event) {
        var displayUrl = thumbnail.dataset.displayUrl || thumbnail.href;
        var imageAlt = thumbnail.dataset.imageAlt || mainImage.alt;

        event.preventDefault();
        mainLink.href = displayUrl;
        mainImage.src = displayUrl;
        mainImage.alt = imageAlt;

        if (lightboxImage) {
          lightboxImage.src = displayUrl;
          lightboxImage.alt = imageAlt;
        }

        thumbnails.forEach(function (candidate) {
          var item = candidate.closest("li");
          if (item) {
            item.removeAttribute("aria-current");
          }
        });

        var selectedItem = thumbnail.closest("li");
        if (selectedItem) {
          selectedItem.setAttribute("aria-current", "true");
        }
      });
    });

    if (!lightbox || !lightboxImage || typeof lightbox.showModal !== "function") {
      return;
    }

    mainLink.addEventListener("click", function (event) {
      event.preventDefault();
      lightboxImage.src = mainLink.href;
      lightboxImage.alt = mainImage.alt;
      lightbox.showModal();
    });

    lightbox.addEventListener("click", function (event) {
      if (event.target === lightbox) {
        lightbox.close();
      }
    });
  }

  document.querySelectorAll("[data-autogrid360-gallery]").forEach(initializeGallery);
})();

import { el } from "../ui/dom.js";

/** Cover Art Archive URL for a release ID */
export function coverUrlForRelease(releaseId, size = 250) {
  if (!releaseId) return "";
  return `https://coverartarchive.org/release/${releaseId}/front-${size}`;
}

/** Cover Art Archive URL for a release-group ID */
export function coverUrlForReleaseGroup(releaseGroupId, size = 250) {
  if (!releaseGroupId) return "";
  return `https://coverartarchive.org/release-group/${releaseGroupId}/front-${size}`;
}

/**
 * “No flicker”: start transparent, fade in on load.
 * We hide on error (and remove src).
 */
export function activateImage(img, src) {
  if (!img) return;

  img.classList.remove("loaded");
  img.removeAttribute("src");

  if (!src) {
    img.style.visibility = "hidden";
    return;
  }

  img.style.visibility = "visible";

  img.onload = () => {
    img.classList.add("loaded");
  };
  img.onerror = () => {
    img.style.visibility = "hidden";
    img.classList.remove("loaded");
    img.removeAttribute("src");
  };

  img.src = src;
}

export function clearCover() {
  el.coverCaption.textContent = "";
  el.detailsTop.style.display = "none";
  el.coverImg.style.visibility = "hidden";
  el.coverImg.classList.remove("loaded");
  el.coverImg.removeAttribute("src");
  el.coverImg.onload = null;
  el.coverImg.onerror = null;
}

export function setCoverEntity(entityType, id, captionText) {
  clearCover();
  if (!id) return;

  el.coverCaption.textContent = captionText || "";
  el.detailsTop.style.display = "flex";

  const src = (entityType === "release-group")
    ? coverUrlForReleaseGroup(id, 500)
    : coverUrlForRelease(id, 500);

  activateImage(el.coverImg, src);

  // If cover fails, hide the whole block
  el.coverImg.onerror = () => {
    clearCover();
  };
}

// Back-compat helper
export function setCover(releaseId, captionText) {
  return setCoverEntity("release", releaseId, captionText);
}

export const el = {
  q: document.getElementById("q"),
  searchBtn: document.getElementById("searchBtn"),
  limit: document.getElementById("limit"),
  prevBtn: document.getElementById("prevBtn"),
  nextBtn: document.getElementById("nextBtn"),
  status: document.getElementById("status"),
  results: document.getElementById("results"),
  count: document.getElementById("count"),
  page: document.getElementById("page"),
  details: document.getElementById("details"),
  links: document.getElementById("links"),

  // Advanced
  advArtist: document.getElementById("advArtist"),
  advTitle: document.getElementById("advTitle"),
  advAlbum: document.getElementById("advAlbum"),
  advYear: document.getElementById("advYear"),
  advDuration: document.getElementById("advDuration"),
  advTitleLabel: document.getElementById("advTitleLabel"),
  advAlbumWrap: document.getElementById("advAlbumWrap"),
  advDurWrap: document.getElementById("advDurWrap"),
  strictMatch: document.getElementById("strictMatch"),
  penalizeAlt: document.getElementById("penalizeAlt"),
  autoFillFromBox: document.getElementById("autoFillFromBox"),

  // Cover
  detailsTop: document.getElementById("detailsTop"),
  coverImg: document.getElementById("coverImg"),
  coverCaption: document.getElementById("coverCaption"),
};

export function selectedType() {
  return document.querySelector('input[name="type"]:checked')?.value || "recording";
}

export function onTypeChange(handler) {
  document.querySelectorAll('input[name="type"]').forEach(r => r.addEventListener("change", handler));
}

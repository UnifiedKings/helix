export const state = {
  type: "recording",
  query: "",
  limit: 25,
  offset: 0,
  lastResults: [],
  lastCount: 0,
  activeId: null,
  settings: {
    search_default_country: "US",
    search_hide_non_official: true,
    search_prefer_original_release: false,
  },
};

export function resetResults() {
  state.lastResults = [];
  state.lastCount = 0;
  state.activeId = null;
}

import { startPlayerPolling } from "../player.js";
import { initTopNav } from "../ui/topnav.js";

export async function init() {
  await initTopNav();
  // Player module will populate Now Playing + Player Bar from backend state.
  startPlayerPolling();
}

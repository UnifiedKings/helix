import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  LobbyMember,
  LobbyPermissions,
  LobbyQueueItem,
  LobbyState,
  SearchMode,
  SearchSong,
  Station,
} from "../api/types";
import { useAuth } from "../auth";
import { Sidebar } from "../components/navigation/Sidebar";
import { ActivityCard, AddMusicCard, GuestPermissionsCard, LobbyHistoryCard, LobbyMiniPlayer, LobbyStationCard, MembersCard, NowPlayingCard, QueueCard, RoomInfoCard } from "../components/lobby";

type AddMode = "search" | "youtube";

const VOLUME_STORAGE_KEY = "helix.volume";

function readStoredVolume() {
  if (typeof window === "undefined") return 0.85;
  const saved = window.localStorage.getItem(VOLUME_STORAGE_KEY);
  const parsed = saved === null ? Number.NaN : Number(saved);
  return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 0.85;
}

function streamUrl(lobbyId: string, itemId: string) {
  return `/api/lobbies/${encodeURIComponent(lobbyId)}/stream/${encodeURIComponent(itemId)}`;
}

function formatTime(ms: number | undefined) {
  const totalSeconds = Math.max(0, Math.floor((ms ?? 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function formatPlayedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function VolumeIcon() {
  return (
    <svg
      className="lobby-volume-icon"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M4 9.5v5h3.2L12 19V5L7.2 9.5H4Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path
        d="M15.2 8.2a5 5 0 0 1 0 7.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <path
        d="M18 5.8a8.7 8.7 0 0 1 0 12.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function formatTotal(ms: number) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0)
    return `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function currentPositionMs(state: LobbyState | null) {
  if (!state) return 0;
  if (!state.is_playing) return state.position_ms || 0;
  const drift = Date.now() - (state.server_time_ms || Date.now());
  return Math.max(0, (state.effective_position_ms || 0) + drift);
}

function activeMembers(members: LobbyMember[]) {
  return members.filter((member) => member.is_active);
}

function memberLabel(member: LobbyMember, selfMemberId: string) {
  return `${member.nickname}${member.id === selfMemberId ? " (You)" : ""}`;
}

function copyToClipboard(value: string) {
  if (!value) return;
  void navigator.clipboard?.writeText(value);
}

export function LobbyPage() {
  const { lobbyId = "" } = useParams();
  const navigate = useNavigate();
  const auth = useAuth();
  const audioARef = useRef<HTMLAudioElement | null>(null);
  const audioBRef = useRef<HTMLAudioElement | null>(null);
  const activeAudioKeyRef = useRef<"a" | "b">("a");
  const lastTrackIdRef = useRef("");
  const preloadedTrackIdRef = useRef("");
  const initialSyncPendingRef = useRef(true);
  const sawEmptyNowPlayingRef = useRef(false);
  const smoothTrackStartTrackIdRef = useRef("");
  const smoothTrackStartUntilRef = useRef(0);
  const lastPlaybackSnapshotRef = useRef<{
    trackId: string;
    positionUpdatedAt: string;
    isPlaying: boolean;
  } | null>(null);
  const [state, setState] = useState<LobbyState | null>(null);
  const [optimisticQueue, setOptimisticQueue] = useState<LobbyQueueItem[] | null>(null);
  const [error, setError] = useState("");
  const [audioError, setAudioError] = useState("");
  const [addMode, setAddMode] = useState<AddMode>("search");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("hybrid");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchSong[]>([]);
  const latestLobbyStateRequestRef = useRef(0);
  const actionInFlightRef = useRef(false);
  const [volume, setVolume] = useState(readStoredVolume);
  const [needsManualSync, setNeedsManualSync] = useState(false);
  const [positionTick, setPositionTick] = useState(0);
  const [editingLobbyName, setEditingLobbyName] = useState(false);
  const [lobbyNameDraft, setLobbyNameDraft] = useState("");
  const [savingLobbyName, setSavingLobbyName] = useState(false);
  const [stations, setStations] = useState<Station[]>([]);
  const [selectedStationId, setSelectedStationId] = useState("");
  const [stationBusy, setStationBusy] = useState(false);

  const isHost = state?.self_role === "host";
  const now = state?.now_playing ?? null;
  const members = activeMembers(state?.members ?? []);
  const hosts = members.filter((member) => member.role === "host");
  const guests = members.filter((member) => member.role !== "host");
  const queue = optimisticQueue ?? state?.queue ?? [];
  const queueTotalMs = queue.reduce(
    (sum, item) => sum + (item.duration_ms || 0),
    0,
  );
  const guestAddedCount = queue.filter(
    (item) =>
      item.added_by_member_id && item.added_by_member_id !== hosts[0]?.id,
  ).length;
  const canAdd = Boolean(state?.self_permissions.can_add_to_queue);
  const canControl = Boolean(state?.self_permissions.can_control_playback);
  const canSkip = Boolean(
    state?.self_permissions.can_skip ||
    state?.self_permissions.can_control_playback,
  );
  const canSeek = Boolean(
    state?.self_permissions.can_seek ||
    state?.self_permissions.can_control_playback,
  );
  const canClearQueue = Boolean(
    state?.self_permissions.can_remove_any_queue_item,
  );
  const currentMember = state?.members.find(
    (member) => member.id === state?.self_member_id,
  );

  async function load() {
    if (!lobbyId || actionInFlightRef.current) return;
    const requestId = ++latestLobbyStateRequestRef.current;
    try {
      const next = await api.lobbyState(lobbyId);
      if (requestId !== latestLobbyStateRequestRef.current || actionInFlightRef.current) return;
      setState(next);
      setOptimisticQueue(null);
      setError("");
    } catch (err) {
      if (requestId !== latestLobbyStateRequestRef.current || actionInFlightRef.current) return;
      setError(err instanceof Error ? err.message : "Could not load lobby");
    }
  }

  useEffect(() => {
    void load();
  }, [lobbyId]);

  useEffect(() => {
    if (!isHost) return;
    let cancelled = false;
    void api.stations().then((items) => {
      if (cancelled) return;
      setStations(items);
      setSelectedStationId((current) => current || state?.active_station_id || items[0]?.id || "");
    }).catch(() => {
      if (!cancelled) setStations([]);
    });
    return () => { cancelled = true; };
  }, [isHost]);

  useEffect(() => {
    if (state?.active_station_id) setSelectedStationId(state.active_station_id);
  }, [state?.active_station_id]);

  useEffect(() => {
    if (!lobbyId) return;
    let socket: WebSocket | null = null;
    let reconnectTimer = 0;
    let pingTimer = 0;
    let fallbackTimer = 0;
    let stopped = false;
    let connected = false;

    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(api.lobbySocketUrl(lobbyId));
      socket.onopen = () => {
        connected = true;
        setError("");
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send("ping");
        }, 20000);
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as { type?: string; state?: LobbyState };
          if (message.type === "lobby.state" && message.state) {
            setState(message.state);
            setOptimisticQueue(null);
            setError("");
          }
        } catch {
          // Ignore malformed realtime messages and keep the fallback path alive.
        }
      };
      socket.onclose = () => {
        connected = false;
        window.clearInterval(pingTimer);
        if (!stopped) reconnectTimer = window.setTimeout(connect, 1500);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    fallbackTimer = window.setInterval(() => {
      if (!connected) void load();
    }, 10000);

    return () => {
      stopped = true;
      window.clearInterval(pingTimer);
      window.clearInterval(fallbackTimer);
      window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [lobbyId]);

  useEffect(() => {
    if (!state?.is_playing || !state?.now_playing?.id) return;
    const interval = window.setInterval(
      () => setPositionTick((tick) => tick + 1),
      250,
    );
    return () => window.clearInterval(interval);
  }, [state?.is_playing, state?.now_playing?.id]);

  useEffect(() => {
    window.localStorage.setItem(VOLUME_STORAGE_KEY, String(volume));
  }, [volume]);

  function activeAudio() {
    return activeAudioKeyRef.current === "a"
      ? audioARef.current
      : audioBRef.current;
  }

  function preloadAudio() {
    return activeAudioKeyRef.current === "a"
      ? audioBRef.current
      : audioARef.current;
  }

  useEffect(() => {
    const active = activeAudio();
    const preload = preloadAudio();
    if (active) active.volume = volume;
    if (preload) preload.volume = volume;
  }, [volume]);

  useEffect(() => {
    if (state && !now) sawEmptyNowPlayingRef.current = true;
  }, [state?.id, state?.now_playing?.id]);

  useEffect(() => {
    const preload = preloadAudio();
    if (!preload || !state || !now) return;

    // If the preloader already contains the new now-playing track, leave it
    // alone so the playback effect can promote it to the active player. Without
    // this guard, the preload effect can overwrite the warmed track before it is
    // used, forcing a cold stream load and making the UI/player drift.
    if (preloadedTrackIdRef.current === now.id) return;

    const nowIndex = queue.findIndex((item) => item.id === now.id);
    const nextItem =
      nowIndex >= 0
        ? queue[nowIndex + 1]
        : queue.find((item) => item.id !== now.id);
    if (!nextItem) {
      preloadedTrackIdRef.current = "";
      preload.pause();
      preload.removeAttribute("src");
      preload.load();
      return;
    }

    if (preloadedTrackIdRef.current !== nextItem.id) {
      preloadedTrackIdRef.current = nextItem.id;
      preload.pause();
      preload.dataset.lobbyItemId = nextItem.id;
      preload.src = streamUrl(state.id, nextItem.id);
      preload.load();
    }
  }, [state?.id, state?.now_playing?.id, queue]);

  useEffect(() => {
    let audio = activeAudio();
    if (!audio || !state) return;

    if (!now) {
      audio.pause();
      audio.removeAttribute("src");
      delete audio.dataset.lobbyItemId;
      audio.load();

      const preload = preloadAudio();
      if (preload) {
        preload.pause();
        preload.removeAttribute("src");
        delete preload.dataset.lobbyItemId;
        preload.load();
      }

      lastTrackIdRef.current = "";
      preloadedTrackIdRef.current = "";
      lastPlaybackSnapshotRef.current = null;
      smoothTrackStartTrackIdRef.current = "";
      smoothTrackStartUntilRef.current = 0;
      setNeedsManualSync(false);
      setPositionTick((tick) => tick + 1);
      return;
    }

    audio.volume = volume;

    const previousSnapshot = lastPlaybackSnapshotRef.current;
    const previousTrackId = lastTrackIdRef.current;
    const trackChanged = previousTrackId !== now.id;
    const positionStampChanged =
      Boolean(previousSnapshot) &&
      previousSnapshot?.trackId === now.id &&
      previousSnapshot?.positionUpdatedAt !== state.position_updated_at;
    const startedNewTrackAfterHostSeek =
      trackChanged &&
      Number.isFinite(state.position_ms) &&
      Number(state.position_ms || 0) > 1500;
    const shouldForcePositionSync =
      positionStampChanged || startedNewTrackAfterHostSeek;

    if (trackChanged) {
      const isTrackAdvance =
        Boolean(previousTrackId) || sawEmptyNowPlayingRef.current;
      const preloaded = preloadAudio();
      const oldActive = audio;
      if (preloaded && preloadedTrackIdRef.current === now.id) {
        oldActive.pause();
        oldActive.removeAttribute("src");
        delete oldActive.dataset.lobbyItemId;
        oldActive.load();
        activeAudioKeyRef.current =
          activeAudioKeyRef.current === "a" ? "b" : "a";
        audio = activeAudio();
        preloadedTrackIdRef.current = "";
        setPositionTick((tick) => tick + 1);
      } else {
        audio.dataset.lobbyItemId = now.id;
        audio.src = streamUrl(state.id, now.id);
        audio.load();
      }
      lastTrackIdRef.current = now.id;
      sawEmptyNowPlayingRef.current = false;
      if (isTrackAdvance && !startedNewTrackAfterHostSeek) {
        smoothTrackStartTrackIdRef.current = now.id;
        smoothTrackStartUntilRef.current = Date.now() + 12000;
      } else if (startedNewTrackAfterHostSeek) {
        smoothTrackStartTrackIdRef.current = "";
        smoothTrackStartUntilRef.current = 0;
      }
    }

    lastPlaybackSnapshotRef.current = {
      trackId: now.id,
      positionUpdatedAt: state.position_updated_at,
      isPlaying: state.is_playing,
    };

    if (!audio) return;

    if (!state.is_playing) {
      audio.pause();
      setNeedsManualSync(false);
      return;
    }

    const syncWhenReady = () => {
      void syncLocalAudio({ automatic: true, forceSeek: shouldForcePositionSync });
    };
    if (audio.readyState >= HTMLMediaElement.HAVE_METADATA) {
      syncWhenReady();
      return;
    }

    audio.addEventListener("loadedmetadata", syncWhenReady, { once: true });
    audio.addEventListener("canplay", syncWhenReady, { once: true });
    return () => {
      audio.removeEventListener("loadedmetadata", syncWhenReady);
      audio.removeEventListener("canplay", syncWhenReady);
    };
  }, [
    state?.id,
    state?.now_playing?.id,
    state?.is_playing,
    state?.effective_position_ms,
    state?.server_time_ms,
    state?.position_updated_at,
    volume,
  ]);

  async function run(action: () => Promise<LobbyState>) {
    const requestId = ++latestLobbyStateRequestRef.current;
    actionInFlightRef.current = true;
    try {
      const next = await action();
      latestLobbyStateRequestRef.current = requestId;
      setState(next);
      setOptimisticQueue(null);
      setError("");
      window.setTimeout(() => {
        void load();
      }, 150);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lobby action failed");
    } finally {
      actionInFlightRef.current = false;
    }
  }

  async function reorderQueue(itemIds: string[]) {
    if (!lobbyId) return;
    const byId = new Map(queue.map((item) => [item.id, item]));
    const nextQueue = itemIds
      .map((itemId) => byId.get(itemId))
      .filter((item): item is LobbyQueueItem => Boolean(item))
      .map((item, index) => ({ ...item, position: index }));
    const missing = queue.filter((item) => !itemIds.includes(item.id));
    setOptimisticQueue([...nextQueue, ...missing.map((item, offset) => ({ ...item, position: nextQueue.length + offset }))]);
    await run(() => api.lobbyReorderQueue(lobbyId, itemIds));
  }

  async function addYoutube(event: FormEvent) {
    event.preventDefault();
    if (!lobbyId || !youtubeUrl.trim()) return;
    await run(() => api.lobbyAddYoutubeUrl(lobbyId, youtubeUrl.trim()));
    setYoutubeUrl("");
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!searchQuery.trim()) return;
    setSearching(true);
    setError("");
    try {
      const response = await api.lobbySearch(
        lobbyId,
        searchQuery.trim(),
        searchMode,
      );
      setSearchResults(response.songs ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }

  async function syncLocalAudio(options?: { automatic?: boolean; forceSeek?: boolean }) {
    const audio = activeAudio();
    if (!audio || !state || !now) return;
    const desiredSeconds = currentPositionMs(state) / 1000;
    try {
      audio.volume = volume;
      const currentSeconds = audio.currentTime || 0;
      const driftSeconds = desiredSeconds - currentSeconds;
      const isSmoothTrackStart =
        options?.automatic &&
        !options?.forceSeek &&
        smoothTrackStartTrackIdRef.current === now.id &&
        Date.now() < smoothTrackStartUntilRef.current;
      const driftThresholdSeconds = options?.automatic ? 6 : 0.75;
      const shouldSeek =
        Number.isFinite(desiredSeconds) &&
        !isSmoothTrackStart &&
        (options?.forceSeek ||
          initialSyncPendingRef.current ||
          !options?.automatic ||
          Math.abs(driftSeconds) > driftThresholdSeconds);

      if (shouldSeek) {
        audio.currentTime = desiredSeconds;
        if (options?.forceSeek) {
          smoothTrackStartTrackIdRef.current = "";
          smoothTrackStartUntilRef.current = 0;
        }
      }
      if (state.is_playing) {
        await audio.play();
      } else {
        audio.pause();
      }
      initialSyncPendingRef.current = false;
      setNeedsManualSync(false);
      setAudioError("");
    } catch (err) {
      setNeedsManualSync(true);
      setAudioError(
        options?.automatic
          ? "Your browser blocked automatic audio after refresh. Tap Sync audio once to resume this lobby."
          : err instanceof Error
            ? err.message
            : "Could not start lobby audio",
      );
    }
  }

  function beginLobbyRename() {
    if (!state || !isHost) return;
    setLobbyNameDraft(state.name ?? "");
    setEditingLobbyName(true);
  }

  function cancelLobbyRename() {
    setEditingLobbyName(false);
    setLobbyNameDraft("");
  }

  async function saveLobbyName(event?: FormEvent) {
    event?.preventDefault();
    if (!state || !isHost || savingLobbyName) return;
    const name = lobbyNameDraft.trim();
    if (!name) {
      setError("Lobby name cannot be empty");
      return;
    }
    if (name === state.name) {
      cancelLobbyRename();
      return;
    }

    setSavingLobbyName(true);
    try {
      await run(() => api.updateLobby(state.id, { name }));
      setEditingLobbyName(false);
      setLobbyNameDraft("");
    } finally {
      setSavingLobbyName(false);
    }
  }

  async function updateGuestPermissions(nextPermissions: LobbyPermissions) {
    if (!state || !isHost) return;
    await run(() =>
      api.updateLobby(state.id, { guest_permissions: nextPermissions }),
    );
  }

  async function toggleGuestPermission(key: keyof LobbyPermissions) {
    if (!state) return;
    const nextValue = !state.guest_permissions[key];
    const nextPermissions = {
      ...state.guest_permissions,
      [key]: nextValue,
    };

    if (key === "can_remove_own_queue_items" && !nextValue) {
      nextPermissions.can_remove_any_queue_item = false;
    }

    if (key === "can_remove_any_queue_item" && nextValue) {
      nextPermissions.can_remove_own_queue_items = true;
    }

    await updateGuestPermissions(nextPermissions);
  }

  async function setLobbyOpen(nextOpen: boolean) {
    if (!state || !isHost) return;
    await run(() => api.updateLobby(state.id, { is_open: nextOpen }));
  }

  async function setGuestQueueLimit(limit: number) {
    if (!state || !isHost) return;
    const normalized = Math.max(0, Math.min(100, Math.trunc(limit || 0)));
    await run(() =>
      api.updateLobby(state.id, { guest_queue_limit: normalized }),
    );
  }

  async function setCleanupAfterDays(days: number) {
    if (!state || !isHost) return;
    const normalized = [0, 1, 7, 30].includes(days) ? days : 0;
    await run(() =>
      api.updateLobby(state.id, { cleanup_after_days: normalized }),
    );
  }

  async function setLobbyPassword(password: string | null) {
    if (!state || !isHost) return;
    await run(() => api.updateLobby(state.id, { password }));
  }

  async function regenerateInvite() {
    if (!state || !isHost) return;
    if (!window.confirm("Regenerate this lobby code? The old code and join link will stop accepting new guests.")) return;
    await run(() => api.regenerateLobbyInvite(state.id));
  }

  async function renameSelf(nickname: string) {
    if (!state) return;
    const cleaned = nickname.trim();
    if (!cleaned) {
      setError("Nickname cannot be empty");
      return;
    }
    await run(() => api.lobbyUpdateSelf(state.id, { nickname: cleaned }));
  }

  async function updateLobbyMember(
    member: LobbyMember,
    payload: { nickname?: string; permissions?: LobbyPermissions },
  ) {
    if (!state || !isHost || member.role === "host") return;
    await run(() => api.lobbyUpdateMember(state.id, member.id, payload));
  }

  async function startLobbyStation() {
    if (!state || !selectedStationId || stationBusy) return;
    setStationBusy(true);
    try {
      await run(() => api.playLobbyStation(state.id, selectedStationId));
    } finally {
      setStationBusy(false);
    }
  }

  async function stopLobbyStation() {
    if (!state || stationBusy) return;
    setStationBusy(true);
    try {
      await run(() => api.stopLobbyStation(state.id));
    } finally {
      setStationBusy(false);
    }
  }

  async function clearQueue() {
    if (!state || !window.confirm("Clear every track from this lobby queue?"))
      return;

    // Clear should feel instant. Do not wait for the next poll to visually
    // remove the rows, and do not let a stale action response briefly restore
    // the old queue.
    setOptimisticQueue([]);
    await run(() => api.lobbyClearQueue(state.id));
    setOptimisticQueue(null);
    setState((current) =>
      current
        ? {
            ...current,
            queue: [],
            now_playing: null,
            current_index: 0,
            is_playing: false,
            position_ms: 0,
            effective_position_ms: 0,
          }
        : current,
    );
  }

  async function kickMember(member: LobbyMember) {
    if (!state || member.role === "host") return;
    const label = member.nickname || "this guest";
    if (!window.confirm(`Kick ${label} from this lobby?`)) return;
    await run(() => api.lobbyKickMember(state.id, member.id));
  }

  async function closeLobby() {
    if (!state || !window.confirm("End this lobby for everyone?")) return;
    try {
      await api.deleteLobby(state.id);
      navigate("/lobbies", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not end lobby");
    }
  }

  async function leaveLobby() {
    if (!state) return;
    try {
      await api.lobbyLeave(state.id);
      navigate(auth.status === "authenticated" ? "/lobbies" : "/login", {
        replace: true,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not leave lobby");
    }
  }

  async function logout() {
    await auth.logout();
    navigate("/login", { replace: true });
  }

  const inviteLink = useMemo(() => {
    if (!state?.invite_code) return "";
    return `${window.location.origin}/join/${encodeURIComponent(state.invite_code)}`;
  }, [state?.invite_code]);

  if (!lobbyId)
    return (
      <main className="login-page">
        <div className="login-card">Missing lobby id.</div>
      </main>
    );

  const position = useMemo(() => currentPositionMs(state), [state, positionTick]);
  const displayName =
    auth.status === "authenticated"
      ? (auth.user?.username ?? "Helix")
      : (currentMember?.nickname ?? "Guest");

  return (
    <div className={`lobby-dashboard-shell ${isHost ? "owner" : "guest"}`}>
      {isHost ? (
        <div className="lobby-dashboard-sidebar">
          <Sidebar user={auth.user} onLogout={() => void logout()} />
        </div>
      ) : null}

      <div className="lobby-dashboard-frame">
        <main className="lobby-dashboard-main">
          <section className="lobby-dashboard-hero">
            <div className="lobby-hero-copy">
              <span className="eyebrow">
                {isHost ? "♛ Owner control" : "Joined as guest"}
              </span>
              <div className="lobby-dashboard-title-row">
                {editingLobbyName && isHost ? (
                  <form
                    onSubmit={(event) => void saveLobbyName(event)}
                    style={{ display: "flex", alignItems: "center", gap: "0.55rem", flexWrap: "wrap" }}
                  >
                    <input
                      autoFocus
                      value={lobbyNameDraft}
                      onChange={(event) => setLobbyNameDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape") cancelLobbyRename();
                      }}
                      maxLength={120}
                      aria-label="Lobby name"
                      disabled={savingLobbyName}
                      style={{
                        fontSize: "clamp(1.65rem, 3vw, 3rem)",
                        fontWeight: 900,
                        minWidth: "min(28rem, 72vw)",
                      }}
                    />
                    <button type="submit" disabled={savingLobbyName || !lobbyNameDraft.trim()}>
                      {savingLobbyName ? "Saving…" : "Save"}
                    </button>
                    <button type="button" className="secondary" onClick={cancelLobbyRename} disabled={savingLobbyName}>
                      Cancel
                    </button>
                  </form>
                ) : (
                  <>
                    <h1>{state?.name ?? "Shared Lobby"}</h1>
                    {isHost ? (
                      <button
                        type="button"
                        className="secondary"
                        onClick={beginLobbyRename}
                        title="Rename lobby"
                        aria-label="Rename lobby"
                        style={{ paddingInline: "0.7rem" }}
                      >
                        ✎ Rename
                      </button>
                    ) : null}
                  </>
                )}
                <span className="lobby-role-pill">
                  {isHost ? "Host" : "Guest"}
                </span>
              </div>
              <p className="muted">
                {isHost
                  ? "Manage playback, queue, members, and basic guest permissions."
                  : `Listening with ${hosts[0]?.nickname ?? "the host"}. Add tracks when the host allows it.`}
              </p>
              <div className="lobby-meta-row" aria-label="Lobby status">
                <span className="lobby-meta-chip">
                  <span aria-hidden="true">👥</span>
                  <strong>{members.length}</strong>
                  member{members.length === 1 ? "" : "s"}
                </span>
                <span className="lobby-meta-chip">
                  <span aria-hidden="true">♬</span>
                  <strong>{queue.length}</strong>
                  in queue
                </span>
                {state?.invite_code ? (
                  <span className="lobby-meta-chip lobby-code-chip" title="5-letter join code">
                    <span aria-hidden="true">{state.has_password ? "🔒" : "#"}</span>
                    <strong>{state.invite_code}</strong>
                  </span>
                ) : null}
                <span className="lobby-meta-chip">
                  <span
                    className={`lobby-status-dot ${state?.is_open ? "active" : ""}`}
                    aria-hidden="true"
                  />
                  {state?.is_open ? "Lobby open" : "Lobby closed"}
                </span>
              </div>
            </div>
            <div className="lobby-hero-actions">
              {inviteLink ? (
                <button
                  type="button"
                  onClick={() => copyToClipboard(inviteLink)}
                >
                  Copy join link
                </button>
              ) : null}
              {isHost && inviteLink ? (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => void regenerateInvite()}
                >
                  Regenerate code
                </button>
              ) : null}
              {isHost ? (
                <button
                  type="button"
                  onClick={() =>
                    window.open(
                      `/join/${state?.invite_code ?? ""}`,
                      "_blank",
                      "noopener,noreferrer",
                    )
                  }
                >
                  Open guest view
                </button>
              ) : null}
              {isHost ? (
                <button
                  className="danger"
                  type="button"
                  onClick={() => void closeLobby()}
                >
                  End lobby
                </button>
              ) : (
                <button
                  className="danger lobby-leave-button"
                  type="button"
                  onClick={() => void leaveLobby()}
                >
                  Leave lobby
                </button>
              )}
            </div>
          </section>

          {error ? <div className="error-banner">{error}</div> : null}
          {audioError ? <div className="info-banner">{audioError}</div> : null}

          <section className="lobby-dashboard-grid">
            <div className="lobby-left-stack">
              <NowPlayingCard
                state={state}
                now={now}
                position={position}
                volume={volume}
                setVolume={setVolume}
                canControl={canControl}
                canSkip={canSkip}
                canSeek={canSeek}
                syncLocalAudio={syncLocalAudio}
                needsManualSync={needsManualSync}
                run={run}
                lobbyId={lobbyId}
              />
              <AddMusicCard
                addMode={addMode}
                setAddMode={setAddMode}
                canAdd={canAdd}
                searchMode={searchMode}
                setSearchMode={setSearchMode}
                searchQuery={searchQuery}
                setSearchQuery={setSearchQuery}
                searching={searching}
                searchResults={searchResults}
                search={search}
                addYoutube={addYoutube}
                youtubeUrl={youtubeUrl}
                setYoutubeUrl={setYoutubeUrl}
                addSearchSong={(song) =>
                  run(() => api.lobbyAddQueueItem(lobbyId, song))
                }
              />
              {isHost ? (
                <LobbyStationCard
                  stations={stations}
                  selectedStationId={selectedStationId}
                  setSelectedStationId={setSelectedStationId}
                  activeStationId={state?.active_station_id ?? ""}
                  activeStationName={state?.active_station_name ?? ""}
                  busy={stationBusy}
                  onPlay={() => void startLobbyStation()}
                  onStop={() => void stopLobbyStation()}
                />
              ) : null}
            </div>

            <div className="lobby-center-stack">
              <QueueCard
                title={isHost ? "Queue Management" : "Queue"}
                state={state}
                queue={queue}
                canClearQueue={canClearQueue}
                canJump={canSkip || canControl}
                canReorder={isHost}
                isHost={isHost}
                onClearQueue={clearQueue}
                onJump={(item) =>
                  run(() => api.lobbyJumpToQueueItem(lobbyId, item.id))
                }
                onReorder={reorderQueue}
                onRemove={(item) =>
                  run(() => api.lobbyRemoveQueueItem(lobbyId, item.id))
                }
              />
              {isHost ? (
                <GuestPermissionsCard
                  state={state}
                  onToggle={(key) => void toggleGuestPermission(key)}
                  onSetQueueLimit={(limit) => void setGuestQueueLimit(limit)}
                  onSetLobbyOpen={(open) => void setLobbyOpen(open)}
                  onSetCleanupAfterDays={(days) => void setCleanupAfterDays(days)}
                  onSetPassword={(password) => void setLobbyPassword(password)}
                />
              ) : (
                <RoomInfoCard state={state} queueTotalMs={queueTotalMs} />
              )}
            </div>

            <aside className="lobby-right-stack">
              <MembersCard
                state={state}
                hosts={hosts}
                guests={guests}
                isHost={isHost}
                onKickMember={(member) => void kickMember(member)}
                onRenameSelf={(nickname) => void renameSelf(nickname)}
                onUpdateMember={(member, payload) =>
                  void updateLobbyMember(member, payload)
                }
              />
              <LobbyHistoryCard state={state} />
              {isHost ? (
                <RoomInfoCard
                  state={state}
                  queueTotalMs={queueTotalMs}
                  guestAddedCount={guestAddedCount}
                />
              ) : (
                <ActivityCard state={state} />
              )}
            </aside>
          </section>
        </main>
      </div>

      <LobbyMiniPlayer
        now={now}
        state={state}
        position={position}
        volume={volume}
        setVolume={setVolume}
        canControl={canControl}
        canSkip={canSkip}
        syncLocalAudio={syncLocalAudio}
        run={run}
        lobbyId={lobbyId}
      />

      <audio
        ref={audioARef}
        preload="auto"
        onPlay={() => setAudioError("")}
        onError={() => {
          if (activeAudioKeyRef.current === "a")
            setAudioError("Audio stream failed for this lobby track");
        }}
        onEnded={(event) => {
          const itemId = event.currentTarget.dataset.lobbyItemId || "";
          if (activeAudioKeyRef.current === "a" && itemId)
            void run(() => api.lobbyEnded(lobbyId, itemId));
        }}
      />
      <audio
        ref={audioBRef}
        preload="auto"
        onPlay={() => setAudioError("")}
        onError={() => {
          if (activeAudioKeyRef.current === "b")
            setAudioError("Audio stream failed for this lobby track");
        }}
        onEnded={(event) => {
          const itemId = event.currentTarget.dataset.lobbyItemId || "";
          if (activeAudioKeyRef.current === "b" && itemId)
            void run(() => api.lobbyEnded(lobbyId, itemId));
        }}
      />
    </div>
  );
}

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  LobbyMember,
  LobbyPermissions,
  LobbyQueueItem,
  LobbyState,
  SearchMode,
  SearchSong,
} from "../api/types";
import { Artwork } from "../components/Artwork";
import { useAuth } from "../auth";

const SEARCH_MODES: Array<{ id: SearchMode; label: string }> = [
  { id: "hybrid", label: "All" },
  { id: "subsonic", label: "Library" },
  { id: "ytmusic", label: "YTMusic" },
];

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

function SidebarLink({
  to,
  label,
  icon,
}: {
  to: string;
  label: string;
  icon: string;
}) {
  return (
    <NavLink to={to} className="side-link">
      <span className="side-icon" aria-hidden="true">
        {icon}
      </span>
      <span>{label}</span>
    </NavLink>
  );
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
    const interval = window.setInterval(() => void load(), 900);
    return () => window.clearInterval(interval);
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
      preload.src = streamUrl(state.id, nextItem.id);
      preload.load();
    }
  }, [state?.id, state?.now_playing?.id, queue]);

  useEffect(() => {
    let audio = activeAudio();
    if (!audio || !state || !now) return;
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
        oldActive.load();
        activeAudioKeyRef.current =
          activeAudioKeyRef.current === "a" ? "b" : "a";
        audio = activeAudio();
        preloadedTrackIdRef.current = "";
        setPositionTick((tick) => tick + 1);
      } else {
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
      <aside className="app-sidebar lobby-dashboard-sidebar">
        <NavLink
          to={auth.status === "authenticated" ? "/" : "/login"}
          className="sidebar-brand"
          aria-label="Helix home"
        >
          <img src="/helix-logo.png" alt="" />
          <span>Helix</span>
        </NavLink>
        <nav className="side-nav" aria-label="Main navigation">
          <SidebarLink to="/" label="Home" icon="⌂" />
          <SidebarLink to="/search" label="Search" icon="⌕" />
          <SidebarLink to="/stations" label="Stations" icon="◉" />
          <SidebarLink to="/playlists" label="Playlists" icon="♫" />
          <SidebarLink to="/history" label="History" icon="◷" />
          <SidebarLink to="/lobbies" label="Lobbies" icon="◎" />
          <SidebarLink to="/settings" label="Settings" icon="⚙" />
        </nav>

        <div className="sidebar-account-panel">
          <div className="sidebar-account-card">
            <button
              className="profile-placeholder sidebar-profile-avatar"
              type="button"
              title="Profile"
              aria-label="Profile"
            >
              <span aria-hidden="true">
                {(displayName || "H").slice(0, 1).toUpperCase()}
              </span>
            </button>
            <div className="sidebar-account-copy">
              <strong>{displayName}</strong>
              <span>{isHost ? "Host" : auth.status === "authenticated" ? "User" : "Guest"}</span>
            </div>
          </div>
          {auth.status === "authenticated" ? (
            <button className="sidebar-logout-button" type="button" onClick={() => void logout()}>
              <span aria-hidden="true">↪</span>
              Log out
            </button>
          ) : (
            <button className="sidebar-logout-button" type="button" onClick={() => void leaveLobby()}>
              <span aria-hidden="true">↩</span>
              Leave lobby
            </button>
          )}
        </div>
      </aside>

      <div className="lobby-dashboard-frame">
        <main className="lobby-dashboard-main">
          <section className="lobby-dashboard-hero">
            <div className="lobby-hero-copy">
              <span className="eyebrow">
                {isHost ? "♛ Owner control" : "Joined as guest"}
              </span>
              <div className="lobby-dashboard-title-row">
                <h1>{state?.name ?? "Shared Lobby"}</h1>
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
                  Copy invite
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
              />
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
        onEnded={() => {
          if (activeAudioKeyRef.current === "a" && canSkip)
            void run(() => api.lobbyNext(lobbyId));
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
        onEnded={() => {
          if (activeAudioKeyRef.current === "b" && canSkip)
            void run(() => api.lobbyNext(lobbyId));
        }}
      />
    </div>
  );
}

function NowPlayingCard({
  state,
  now,
  position,
  volume,
  setVolume,
  canControl,
  canSkip,
  canSeek,
  syncLocalAudio,
  needsManualSync,
  run,
  lobbyId,
}: {
  state: LobbyState | null;
  now: LobbyQueueItem | null;
  position: number;
  volume: number;
  setVolume: (volume: number) => void;
  canControl: boolean;
  canSkip: boolean;
  canSeek: boolean;
  syncLocalAudio: (options?: { automatic?: boolean; forceSeek?: boolean }) => Promise<void>;
  needsManualSync: boolean;
  run: (action: () => Promise<LobbyState>) => Promise<void>;
  lobbyId: string;
}) {
  return (
    <section className="panel lobby-control-card lobby-now-dashboard-card">
      <div className="section-heading">
        <h2>Now Playing</h2>
        <span className="status-pill good">Synced playback</span>
      </div>
      <div className="lobby-now-dashboard-body">
        <Artwork src={now?.art_url} alt={now?.title ?? "No track"} size="md" />
        <div className="lobby-now-dashboard-copy">
          <strong>{now?.title ?? "Nothing queued"}</strong>
          <span className="muted">
            {now
              ? `${now.artist}${now.album ? ` • ${now.album}` : ""}`
              : "Add something to start the room."}
          </span>
          <span className="muted">
            {formatTime(position)} / {formatTime(now?.duration_ms)}
          </span>
        </div>
      </div>
      <div className="lobby-inline-controls">
        <button
          type="button"
          className="icon-button"
          disabled={!canSkip || !state}
          onClick={() => run(() => api.lobbyPrevious(lobbyId))}
        >
          ‹
        </button>
        {state?.is_playing ? (
          <button
            type="button"
            className="primary round-control"
            disabled={!canControl || !state || !now}
            onClick={() => run(() => api.lobbyPause(lobbyId))}
          >
            Ⅱ
          </button>
        ) : (
          <button
            type="button"
            className="primary round-control"
            disabled={!canControl || !state || !now}
            onClick={() => run(() => api.lobbyPlay(lobbyId))}
          >
            ▶
          </button>
        )}
        <button
          type="button"
          className="icon-button"
          disabled={!canSkip || !state}
          onClick={() => run(() => api.lobbyNext(lobbyId))}
        >
          ›
        </button>
      </div>
      {needsManualSync && state?.is_playing && now ? (
        <button
          type="button"
          className="subtle-button lobby-sync-audio-button"
          onClick={() => void syncLocalAudio()}
        >
          Sync audio
        </button>
      ) : null}
      <div className="lobby-progress-row compact">
        <input
          type="range"
          min="0"
          max={now?.duration_ms || 0}
          value={Math.min(position, now?.duration_ms || position)}
          disabled={!canSeek || !now?.duration_ms}
          onChange={(event) =>
            run(() => api.lobbySeek(lobbyId, Number(event.target.value)))
          }
        />
      </div>
      <div className="lobby-volume-row">
        <VolumeIcon />
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={volume}
          onChange={(event) => setVolume(Number(event.target.value))}
        />
      </div>
    </section>
  );
}

function AddMusicCard({
  addMode,
  setAddMode,
  canAdd,
  searchMode,
  setSearchMode,
  searchQuery,
  setSearchQuery,
  searching,
  searchResults,
  search,
  addYoutube,
  youtubeUrl,
  setYoutubeUrl,
  addSearchSong,
}: {
  addMode: AddMode;
  setAddMode: (mode: AddMode) => void;
  canAdd: boolean;
  searchMode: SearchMode;
  setSearchMode: (mode: SearchMode) => void;
  searchQuery: string;
  setSearchQuery: (value: string) => void;
  searching: boolean;
  searchResults: SearchSong[];
  search: (event: FormEvent) => Promise<void>;
  addYoutube: (event: FormEvent) => Promise<void>;
  youtubeUrl: string;
  setYoutubeUrl: (value: string) => void;
  addSearchSong: (song: SearchSong) => Promise<void>;
}) {
  return (
    <section className="panel lobby-control-card lobby-add-panel-redesign">
      <h2>Add Music</h2>
      <div
        className="lobby-add-tabs"
        role="tablist"
        aria-label="Add music method"
      >
        <button
          type="button"
          className={addMode === "search" ? "active" : ""}
          onClick={() => setAddMode("search")}
        >
          Helix Search
        </button>
        <button
          type="button"
          className={addMode === "youtube" ? "active" : ""}
          onClick={() => setAddMode("youtube")}
        >
          YT Link
        </button>
      </div>
      {!canAdd ? (
        <p className="muted">The host has paused guest additions.</p>
      ) : null}
      {addMode === "search" ? (
        <>
          <div className="search-tabs compact-tabs">
            {SEARCH_MODES.map((mode) => (
              <button
                key={mode.id}
                type="button"
                className={`tab-button ${searchMode === mode.id ? "active" : ""}`}
                onClick={() => setSearchMode(mode.id)}
              >
                {mode.label}
              </button>
            ))}
          </div>
          <form
            className="lobby-search-form"
            onSubmit={(event) => void search(event)}
          >
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search for songs, artists, albums…"
              disabled={!canAdd}
            />
            <button disabled={!canAdd || searching || !searchQuery.trim()}>
              {searching ? "Searching…" : "Search"}
            </button>
          </form>
          <div className="lobby-search-results compact-results">
            {searchResults.map((song, index) => (
              <button
                key={`${song.source}-${song.title}-${song.artist}-${index}`}
                type="button"
                disabled={!canAdd}
                onClick={() => void addSearchSong(song)}
              >
                <img
                  className="lobby-search-result-art"
                  src={song.art_url || song.thumbnail_url || "/helix-subsonic-mark.svg"}
                  alt=""
                  loading="lazy"
                />
                <span className="lobby-search-result-copy">
                  <strong>{song.title}</strong>
                  <span>
                    {song.artist}
                    {song.album ? ` • ${song.album}` : ""}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </>
      ) : (
        <form
          className="lobby-youtube-form"
          onSubmit={(event) => void addYoutube(event)}
        >
          <label>
            <span>YouTube / YouTube Music URL, playlist, or album</span>
            <input
              value={youtubeUrl}
              onChange={(event) => setYoutubeUrl(event.target.value)}
              placeholder="Track, playlist, or album URL…"
              disabled={!canAdd}
            />
          </label>
          <button className="primary" disabled={!canAdd || !youtubeUrl.trim()}>
            Add YT link
          </button>
          <p className="muted">
            Paste a link to add it to the shared queue for everyone.
          </p>
        </form>
      )}
    </section>
  );
}

function QueueCard({
  title,
  state,
  queue,
  canClearQueue,
  canJump,
  canReorder,
  isHost,
  onClearQueue,
  onJump,
  onReorder,
  onRemove,
}: {
  title: string;
  state: LobbyState | null;
  queue: LobbyQueueItem[];
  canClearQueue: boolean;
  canJump: boolean;
  canReorder: boolean;
  isHost: boolean;
  onClearQueue: () => Promise<void>;
  onJump: (item: LobbyQueueItem) => Promise<void>;
  onReorder: (itemIds: string[]) => Promise<void>;
  onRemove: (item: LobbyQueueItem) => Promise<void>;
}) {
  const [draggingItemId, setDraggingItemId] = useState("");
  const [dragOverItemId, setDragOverItemId] = useState("");

  function reorderAroundTarget(targetItemId: string) {
    if (!draggingItemId || draggingItemId === targetItemId) {
      setDraggingItemId("");
      setDragOverItemId("");
      return;
    }

    const itemIds = queue.map((item) => item.id);
    const fromIndex = itemIds.indexOf(draggingItemId);
    const toIndex = itemIds.indexOf(targetItemId);
    if (fromIndex < 0 || toIndex < 0) {
      setDraggingItemId("");
      setDragOverItemId("");
      return;
    }

    const [moved] = itemIds.splice(fromIndex, 1);
    itemIds.splice(toIndex, 0, moved);
    setDraggingItemId("");
    setDragOverItemId("");
    void onReorder(itemIds);
  }

  return (
    <section className="panel lobby-control-card lobby-queue-dashboard-card">
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          <span className="muted">
            {queue.length} song{queue.length === 1 ? "" : "s"} •{" "}
            {formatTotal(
              queue.reduce((sum, item) => sum + (item.duration_ms || 0), 0),
            )}{" "}
            total
          </span>
        </div>
        {canClearQueue ? (
          <button type="button" onClick={() => void onClearQueue()}>
            Clear queue
          </button>
        ) : null}
      </div>
      <div className="lobby-queue-table" role="table" aria-label="Lobby queue">
        <div className="lobby-queue-table-head" role="row">
          <span>#</span>
          <span>Track</span>
          <span>Added by</span>
          <span>Duration</span>
          <span />
        </div>
        <div
          className="lobby-queue-scroll"
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              setDragOverItemId("");
            }
          }}
        >
          {queue.length ? (
            queue.map((item, index) => {
              const canRemove = Boolean(
                state?.self_permissions.can_remove_any_queue_item ||
                (state?.self_permissions.can_remove_own_queue_items &&
                  item.added_by_member_id === state?.self_member_id),
              );
              return (
                <QueueTableRow
                  key={item.id}
                  item={item}
                  index={index}
                  active={item.id === state?.now_playing?.id}
                  isHost={isHost}
                  canJump={canJump}
                  canReorder={canReorder}
                  canRemove={canRemove}
                  isDragging={draggingItemId === item.id}
                  isDragOver={dragOverItemId === item.id && draggingItemId !== item.id}
                  onJump={() => onJump(item)}
                  onDragStart={() => setDraggingItemId(item.id)}
                  onDragOver={() => setDragOverItemId(item.id)}
                  onDragEnd={() => {
                    setDraggingItemId("");
                    setDragOverItemId("");
                  }}
                  onDrop={() => reorderAroundTarget(item.id)}
                  onRemove={() => onRemove(item)}
                />
              );
            })
          ) : (
            <p className="muted lobby-empty-state">
              The queue is empty. Add a song from search or paste a YouTube
              Music link.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function QueueTableRow({
  item,
  index,
  active,
  isHost,
  canJump,
  canReorder,
  canRemove,
  isDragging,
  isDragOver,
  onJump,
  onDragStart,
  onDragOver,
  onDragEnd,
  onDrop,
  onRemove,
}: {
  item: LobbyQueueItem;
  index: number;
  active: boolean;
  isHost: boolean;
  canJump: boolean;
  canReorder: boolean;
  canRemove: boolean;
  isDragging: boolean;
  isDragOver: boolean;
  onJump: () => Promise<void>;
  onDragStart: () => void;
  onDragOver: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
  onRemove: () => Promise<void>;
}) {
  return (
    <div
      className={`lobby-queue-table-row ${active ? "active" : ""} ${canJump ? "interactive" : ""} ${isDragging ? "dragging" : ""} ${isDragOver ? "drag-over" : ""}`}
      role={canJump ? "button" : "row"}
      tabIndex={canJump ? 0 : undefined}
      title={canJump ? `Skip to ${item.title}` : undefined}
      onClick={() => {
        if (canJump) void onJump();
      }}
      onKeyDown={(event) => {
        if (!canJump) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          void onJump();
        }
      }}
      onDragOver={(event) => {
        if (!canReorder) return;
        event.preventDefault();
        onDragOver();
      }}
      onDrop={(event) => {
        if (!canReorder) return;
        event.preventDefault();
        onDrop();
      }}
    >
      <span className="queue-index">{index + 1}</span>
      <span className="queue-track-cell">
        {isHost ? (
          <span
            className="queue-grip"
            aria-label={`Drag ${item.title} to reorder`}
            draggable={canReorder}
            role="button"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
            onDragStart={(event) => {
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", item.id);
              onDragStart();
            }}
            onDragEnd={onDragEnd}
          >
            ⋮⋮
          </span>
        ) : null}
        <Artwork src={item.art_url} alt={item.title} size="sm" />
        <span className="queue-track-copy">
          <strong>{item.title}</strong>
          <span className="muted">
            {item.artist}
            {item.album ? ` • ${item.album}` : ""}
          </span>
        </span>
      </span>
      <span className="queue-added-by">
        {item.added_by_nickname ? `@${item.added_by_nickname}` : "—"}
      </span>
      <span className="queue-duration">{formatTime(item.duration_ms)}</span>
      <span className="queue-row-action">
        {canRemove ? (
          <button
            className="danger subtle-button"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              void onRemove();
            }}
          >
            ×
          </button>
        ) : null}
      </span>
    </div>
  );
}

function GuestPermissionsCard({
  state,
  onToggle,
}: {
  state: LobbyState | null;
  onToggle: (key: keyof LobbyPermissions) => void;
}) {
  const perms = state?.guest_permissions;
  return (
    <section className="panel lobby-control-card lobby-permissions-card">
      <h2>Guest Permissions</h2>
      <PermissionToggle
        label="Guests can add to queue"
        description="Allow friends to add music."
        checked={Boolean(perms?.can_add_to_queue)}
        onClick={() => onToggle("can_add_to_queue")}
      />
      <PermissionToggle
        label="Guests can remove own songs"
        description="Let guests clean up tracks they added."
        checked={Boolean(perms?.can_remove_own_queue_items)}
        onClick={() => onToggle("can_remove_own_queue_items")}
      />
      {perms?.can_remove_own_queue_items ? (
        <div className="permission-subsetting">
          <PermissionToggle
            label="Guests can remove any song"
            description="Let guests remove tracks added by anyone."
            checked={Boolean(perms?.can_remove_any_queue_item)}
            onClick={() => onToggle("can_remove_any_queue_item")}
          />
        </div>
      ) : null}
      <PermissionToggle
        label="Guests can control playback"
        description="Allow play and pause from guest screens."
        checked={Boolean(perms?.can_control_playback)}
        onClick={() => onToggle("can_control_playback")}
      />
      <PermissionToggle
        label="Guests can skip"
        description="Allow previous and next controls."
        checked={Boolean(perms?.can_skip)}
        onClick={() => onToggle("can_skip")}
      />
      <PermissionToggle
        label="Guests can seek"
        description="Allow scrubbing through the current track."
        checked={Boolean(perms?.can_seek)}
        onClick={() => onToggle("can_seek")}
      />
    </section>
  );
}

function PermissionToggle({
  label,
  description,
  checked,
  onClick,
}: {
  label: string;
  description: string;
  checked: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className="permission-row" onClick={onClick}>
      <span>
        <strong>{label}</strong>
        <span>{description}</span>
      </span>
      <span className={`toggle-pill ${checked ? "on" : ""}`} aria-hidden="true">
        <span />
      </span>
    </button>
  );
}

function MembersCard({
  state,
  hosts,
  guests,
  isHost,
  onKickMember,
}: {
  state: LobbyState | null;
  hosts: LobbyMember[];
  guests: LobbyMember[];
  isHost: boolean;
  onKickMember: (member: LobbyMember) => void;
}) {
  return (
    <section className="panel lobby-control-card lobby-members-dashboard-card">
      <div className="section-heading">
        <h2>
          {isHost ? "Members" : "Members in Lobby"} (
          {hosts.length + guests.length})
        </h2>
      </div>
      <div className="member-section">
        <span className="member-section-title">Host</span>
        {hosts.map((member) => (
          <MemberRow
            key={member.id}
            member={member}
            selfMemberId={state?.self_member_id ?? ""}
          />
        ))}
      </div>
      <div className="member-section">
        <span className="member-section-title">Guests ({guests.length})</span>
        {guests.length ? (
          guests.map((member) => (
            <MemberRow
              key={member.id}
              member={member}
              selfMemberId={state?.self_member_id ?? ""}
              onKick={isHost ? () => onKickMember(member) : undefined}
            />
          ))
        ) : (
          <p className="muted">No guests yet.</p>
        )}
      </div>
    </section>
  );
}

function MemberRow({
  member,
  selfMemberId,
  onKick,
}: {
  member: LobbyMember;
  selfMemberId: string;
  onKick?: () => void;
}) {
  const canKick = Boolean(onKick && member.role !== "host" && member.id !== selfMemberId);
  return (
    <div className="member-dashboard-row">
      <span className="member-avatar">
        {member.nickname.slice(0, 1).toUpperCase()}
      </span>
      <span className="member-copy">
        <strong>{memberLabel(member, selfMemberId)}</strong>
        <span>{member.role === "host" ? "Host" : "Guest"}</span>
      </span>
      <span
        className={`lobby-member-dot ${member.is_active ? "active" : ""}`}
      />
      {canKick ? (
        <button
          className="member-kick-button danger"
          type="button"
          onClick={onKick}
          title={`Kick ${member.nickname} from this lobby`}
        >
          Kick
        </button>
      ) : null}
    </div>
  );
}

function RoomInfoCard({
  state,
  queueTotalMs,
  guestAddedCount,
}: {
  state: LobbyState | null;
  queueTotalMs: number;
  guestAddedCount?: number;
}) {
  return (
    <section className="panel lobby-control-card lobby-room-info-card">
      <h2>Room Info</h2>
      <div className="room-stat-grid">
        <span>
          <strong>{state?.queue.length ?? 0}</strong>
          <small>Songs in Queue</small>
        </span>
        <span>
          <strong>{formatTotal(queueTotalMs)}</strong>
          <small>Queue Length</small>
        </span>
        <span>
          <strong>{activeMembers(state?.members ?? []).length}</strong>
          <small>Members</small>
        </span>
        {guestAddedCount !== undefined ? (
          <span>
            <strong>{guestAddedCount}</strong>
            <small>Queued by Guests</small>
          </span>
        ) : null}
      </div>
    </section>
  );
}

function ActivityCard({ state }: { state: LobbyState | null }) {
  const latest = (state?.queue ?? []).slice(-3).reverse();
  return (
    <section className="panel lobby-control-card lobby-activity-card">
      <h2>Recent Adds</h2>
      {latest.length ? (
        latest.map((item) => (
          <p key={item.id}>
            <strong>{item.added_by_nickname || "Someone"}</strong> added{" "}
            {item.title}
          </p>
        ))
      ) : (
        <p className="muted">No recent queue additions.</p>
      )}
    </section>
  );
}

function LobbyMiniPlayer({
  now,
  state,
  position,
  volume,
  setVolume,
  canControl,
  canSkip,
  syncLocalAudio,
  run,
  lobbyId,
}: {
  now: LobbyQueueItem | null;
  state: LobbyState | null;
  position: number;
  volume: number;
  setVolume: (volume: number) => void;
  canControl: boolean;
  canSkip: boolean;
  syncLocalAudio: (options?: { automatic?: boolean; forceSeek?: boolean }) => Promise<void>;
  run: (action: () => Promise<LobbyState>) => Promise<void>;
  lobbyId: string;
}) {
  return (
    <footer className="lobby-mini-player">
      <div className="lobby-mini-now">
        <Artwork src={now?.art_url} alt={now?.title ?? "No track"} size="sm" />
        <div>
          <strong>{now?.title ?? "Nothing playing"}</strong>
          <span>{now?.artist ?? "Shared lobby"}</span>
        </div>
      </div>
      <div className="lobby-mini-transport">
        <button
          type="button"
          className="icon-button"
          disabled={!canSkip || !state}
          onClick={() => run(() => api.lobbyPrevious(lobbyId))}
        >
          ‹
        </button>
        {state?.is_playing ? (
          <button
            type="button"
            className="primary round-control small"
            disabled={!canControl || !state || !now}
            onClick={() => run(() => api.lobbyPause(lobbyId))}
          >
            Ⅱ
          </button>
        ) : (
          <button
            type="button"
            className="primary round-control small"
            disabled={!canControl || !state || !now}
            onClick={() => run(() => api.lobbyPlay(lobbyId))}
          >
            ▶
          </button>
        )}
        <button
          type="button"
          className="icon-button"
          disabled={!canSkip || !state}
          onClick={() => run(() => api.lobbyNext(lobbyId))}
        >
          ›
        </button>
      </div>
      <div className="lobby-mini-progress">
        <span>{formatTime(position)}</span>
        <progress
          value={Math.min(position, now?.duration_ms || position)}
          max={now?.duration_ms || 1}
        />
        <span>{formatTime(now?.duration_ms)}</span>
      </div>
      <div className="lobby-mini-volume">
        <VolumeIcon />
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={volume}
          onChange={(event) => setVolume(Number(event.target.value))}
        />
      </div>
    </footer>
  );
}

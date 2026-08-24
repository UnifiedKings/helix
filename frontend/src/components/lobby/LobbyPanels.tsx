import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api/client'
import type { LobbyMember, LobbyPermissions, LobbyQueueItem, LobbyState, SearchMode, SearchSong, Station } from '../../api/types'
import { Artwork } from '../Artwork'

type AddMode = "search" | "youtube";

const SEARCH_MODES: Array<{ id: SearchMode; label: string }> = [
  { id: "hybrid", label: "All" },
  { id: "subsonic", label: "Library" },
  { id: "ytmusic", label: "YTMusic" },
]

function formatTime(ms: number | undefined) {
  const totalSeconds = Math.max(0, Math.floor((ms ?? 0) / 1000)); const minutes = Math.floor(totalSeconds / 60); const seconds = totalSeconds % 60; return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}
function formatPlayedAt(value: string) { const date = new Date(value); if (Number.isNaN(date.getTime())) return ""; return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
function formatTotal(ms: number) { const totalSeconds = Math.max(0, Math.floor(ms / 1000)); const hours = Math.floor(totalSeconds / 3600); const minutes = Math.floor((totalSeconds % 3600) / 60); const seconds = totalSeconds % 60; return hours > 0 ? `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}` : `${minutes}:${seconds.toString().padStart(2, "0")}`; }
function VolumeIcon() { return <svg className="lobby-volume-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M4 9.5v5h3.2L12 19V5L7.2 9.5H4Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/><path d="M15.2 8.2a5 5 0 0 1 0 7.6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/><path d="M18 5.8a8.7 8.7 0 0 1 0 12.4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg> }
function activeMembers(members: LobbyMember[]) { return members.filter((member) => member.is_active) }
function memberLabel(member: LobbyMember, selfMemberId: string) { return `${member.nickname}${member.id === selfMemberId ? " (You)" : ""}` }

export function NowPlayingCard({
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

export function AddMusicCard({
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

export function LobbyStationCard({
  stations,
  selectedStationId,
  setSelectedStationId,
  activeStationId,
  activeStationName,
  busy,
  onPlay,
  onStop,
}: {
  stations: Station[];
  selectedStationId: string;
  setSelectedStationId: (id: string) => void;
  activeStationId: string;
  activeStationName: string;
  busy: boolean;
  onPlay: () => void;
  onStop: () => void;
}) {
  return (
    <section className="panel lobby-station-card">
      <div className="section-heading-row">
        <div>
          <p className="eyebrow">AUTOPLAY</p>
          <h2>Lobby Station</h2>
        </div>
        {activeStationId ? <span className="lobby-role-pill">Active</span> : null}
      </div>
      <p className="muted lobby-station-description">Use one of your saved stations to keep the lobby queue filled.</p>
      {activeStationId ? (
        <div className="info-banner lobby-station-active-banner">Playing station: <strong>{activeStationName || "Station"}</strong></div>
      ) : null}
      <div className="lobby-station-controls">
        <select value={selectedStationId} onChange={(event) => setSelectedStationId(event.target.value)} disabled={busy || stations.length === 0}>
          {stations.length === 0 ? <option value="">No saved stations</option> : null}
          {stations.map((station) => <option key={station.id} value={station.id}>{station.name}</option>)}
        </select>
        <button className="lobby-station-play-button" type="button" onClick={onPlay} disabled={busy || !selectedStationId}>
          <span aria-hidden="true">▶</span>
          {busy ? "Working…" : activeStationId ? "Change station" : "Play station"}
        </button>
        {activeStationId ? <button type="button" className="secondary lobby-station-stop-button" onClick={onStop} disabled={busy}>Stop station</button> : null}
      </div>
    </section>
  );
}

export function QueueCard({
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

export function QueueTableRow({
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

export function GuestPermissionsCard({
  state,
  onToggle,
  onSetQueueLimit,
  onSetLobbyOpen,
  onSetCleanupAfterDays,
}: {
  state: LobbyState | null;
  onToggle: (key: keyof LobbyPermissions) => void;
  onSetQueueLimit: (limit: number) => void;
  onSetLobbyOpen: (open: boolean) => void;
  onSetCleanupAfterDays: (days: number) => void;
}) {
  const perms = state?.guest_permissions;
  const [queueLimitDraft, setQueueLimitDraft] = useState(
    String(state?.guest_queue_limit ?? 0),
  );

  useEffect(() => {
    setQueueLimitDraft(String(state?.guest_queue_limit ?? 0));
  }, [state?.guest_queue_limit]);

  const parsedQueueLimit = Math.max(
    0,
    Math.min(100, Number.parseInt(queueLimitDraft || "0", 10) || 0),
  );
  const queueLimitChanged =
    parsedQueueLimit !== Math.max(0, state?.guest_queue_limit ?? 0);

  return (
    <section className="panel lobby-control-card lobby-permissions-card">
      <h2>Guest Settings</h2>

      <div className="lobby-setting-block">
        <div>
          <strong>Pending tracks per guest</strong>
          <span>
            Limits how many waiting songs each guest may have. The currently
            playing track does not count. Use 0 for unlimited.
          </span>
        </div>
        <div className="lobby-queue-limit-control">
          <div className="lobby-queue-limit-stepper" aria-label="Pending tracks allowed per guest">
            <button
              type="button"
              className="lobby-queue-limit-arrow"
              aria-label="Decrease pending tracks per guest"
              onClick={() => setQueueLimitDraft(String(Math.max(0, parsedQueueLimit - 1)))}
            >
              −
            </button>
            <input
              type="number"
              min={0}
              max={100}
              value={queueLimitDraft}
              onChange={(event) => setQueueLimitDraft(event.target.value)}
              aria-label="Pending tracks allowed per guest"
            />
            <button
              type="button"
              className="lobby-queue-limit-arrow"
              aria-label="Increase pending tracks per guest"
              onClick={() => setQueueLimitDraft(String(Math.min(100, parsedQueueLimit + 1)))}
            >
              +
            </button>
          </div>
          <button
            type="button"
            className="secondary"
            disabled={!queueLimitChanged}
            onClick={() => onSetQueueLimit(parsedQueueLimit)}
          >
            Save
          </button>
        </div>
      </div>

      <div className="lobby-setting-block">
        <div>
          <strong>Automatic cleanup</strong>
          <span>Delete this lobby after it has been inactive for the selected amount of time.</span>
        </div>
        <select
          value={state?.cleanup_after_days ?? 0}
          onChange={(event) => onSetCleanupAfterDays(Number(event.target.value))}
          aria-label="Automatic lobby cleanup"
        >
          <option value={0}>Never</option>
          <option value={1}>After 1 day</option>
          <option value={7}>After 7 days</option>
          <option value={30}>After 30 days</option>
        </select>
      </div>

      <PermissionToggle
        label="Allow new guests to join"
        description={
          state?.is_open
            ? "New invite-link joins are currently allowed."
            : "Lobby is locked. Existing members can still reconnect."
        }
        checked={Boolean(state?.is_open)}
        onClick={() => onSetLobbyOpen(!state?.is_open)}
      />

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

export function PermissionToggle({
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
        <span className="toggle-thumb" />
      </span>
    </button>
  );
}

export function MembersCard({
  state,
  hosts,
  guests,
  isHost,
  onKickMember,
  onRenameSelf,
  onUpdateMember,
}: {
  state: LobbyState | null;
  hosts: LobbyMember[];
  guests: LobbyMember[];
  isHost: boolean;
  onKickMember: (member: LobbyMember) => void;
  onRenameSelf: (nickname: string) => void;
  onUpdateMember: (
    member: LobbyMember,
    payload: { nickname?: string; permissions?: LobbyPermissions },
  ) => void;
}) {
  const pendingCount = (memberId: string) =>
    (state?.queue ?? []).filter(
      (item) =>
        item.added_by_member_id === memberId &&
        Number(item.position || 0) > Number(state?.current_index || 0),
    ).length;

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
            pendingCount={pendingCount(member.id)}
            queueLimit={0}
            onRenameSelf={
              member.id === state?.self_member_id ? onRenameSelf : undefined
            }
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
              pendingCount={pendingCount(member.id)}
              queueLimit={state?.guest_queue_limit ?? 0}
              onRenameSelf={
                member.id === state?.self_member_id ? onRenameSelf : undefined
              }
              onKick={isHost ? () => onKickMember(member) : undefined}
              onUpdate={
                isHost
                  ? (payload) => onUpdateMember(member, payload)
                  : undefined
              }
            />
          ))
        ) : (
          <p className="muted">No guests yet.</p>
        )}
      </div>
    </section>
  );
}

export function MemberRow({
  member,
  selfMemberId,
  pendingCount,
  queueLimit,
  onKick,
  onRenameSelf,
  onUpdate,
}: {
  member: LobbyMember;
  selfMemberId: string;
  pendingCount: number;
  queueLimit: number;
  onKick?: () => void;
  onRenameSelf?: (nickname: string) => void;
  onUpdate?: (payload: {
    nickname?: string;
    permissions?: LobbyPermissions;
  }) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [nicknameDraft, setNicknameDraft] = useState(member.nickname);

  useEffect(() => {
    if (!editing) setNicknameDraft(member.nickname);
  }, [member.nickname, editing]);

  const isSelf = member.id === selfMemberId;
  const canKick = Boolean(onKick && member.role !== "host" && !isSelf);
  const canManage = Boolean(onUpdate && member.role !== "host");
  const canRename = Boolean(onRenameSelf || canManage);
  const queueLabel =
    member.role === "guest"
      ? queueLimit > 0
        ? `${pendingCount}/${queueLimit} pending`
        : `${pendingCount} pending · unlimited`
      : "Host";

  const saveNickname = () => {
    const next = nicknameDraft.trim();
    if (!next || next === member.nickname) {
      setNicknameDraft(member.nickname);
      return;
    }
    if (isSelf && onRenameSelf) {
      onRenameSelf(next);
    } else if (onUpdate) {
      onUpdate({ nickname: next });
    }
  };

  const updatePermission = (key: keyof LobbyPermissions) => {
    if (!onUpdate) return;
    const next = {
      ...member.permissions,
      [key]: !member.permissions[key],
    };
    if (key === "can_remove_own_queue_items" && !next.can_remove_own_queue_items) {
      next.can_remove_any_queue_item = false;
    }
    if (key === "can_remove_any_queue_item" && next.can_remove_any_queue_item) {
      next.can_remove_own_queue_items = true;
    }
    onUpdate({ permissions: next });
  };

  return (
    <div className={`member-dashboard-entry ${editing ? "editing" : ""}`}>
      <div className="member-dashboard-row">
        <span className="member-avatar">
          {member.nickname.slice(0, 1).toUpperCase()}
        </span>
        <span className="member-copy">
          <strong>{memberLabel(member, selfMemberId)}</strong>
          <span>{queueLabel}</span>
        </span>
        <span
          className={`lobby-member-dot ${member.is_active ? "active" : ""}`}
        />
        <div className="member-row-actions">
          {canRename ? (
            <button
              className="secondary member-manage-button"
              type="button"
              onClick={() => setEditing((value) => !value)}
            >
              {editing ? "Done" : canManage ? "Manage" : "Rename"}
            </button>
          ) : null}
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
      </div>

      {editing ? (
        <div className="member-editor">
          <div className="member-nickname-editor">
            <label>
              <span>Nickname</span>
              <input
                value={nicknameDraft}
                maxLength={80}
                onChange={(event) => setNicknameDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    saveNickname();
                  }
                  if (event.key === "Escape") {
                    setNicknameDraft(member.nickname);
                    setEditing(false);
                  }
                }}
              />
            </label>
            <button
              type="button"
              className="secondary"
              disabled={!nicknameDraft.trim() || nicknameDraft.trim() === member.nickname}
              onClick={saveNickname}
            >
              Save name
            </button>
          </div>

          {canManage ? (
            <div className="member-permission-editor">
              <span className="member-section-title">Individual permissions</span>
              <PermissionToggle
                label="Add to queue"
                description="Allow this guest to add music."
                checked={member.permissions.can_add_to_queue}
                onClick={() => updatePermission("can_add_to_queue")}
              />
              <PermissionToggle
                label="Remove own songs"
                description="Allow removal of tracks they added."
                checked={member.permissions.can_remove_own_queue_items}
                onClick={() => updatePermission("can_remove_own_queue_items")}
              />
              <PermissionToggle
                label="Remove any song"
                description="Allow removal of anyone's queued tracks."
                checked={member.permissions.can_remove_any_queue_item}
                onClick={() => updatePermission("can_remove_any_queue_item")}
              />
              <PermissionToggle
                label="Control playback"
                description="Allow play and pause."
                checked={member.permissions.can_control_playback}
                onClick={() => updatePermission("can_control_playback")}
              />
              <PermissionToggle
                label="Skip"
                description="Allow previous and next."
                checked={member.permissions.can_skip}
                onClick={() => updatePermission("can_skip")}
              />
              <PermissionToggle
                label="Seek"
                description="Allow scrubbing through the current track."
                checked={member.permissions.can_seek}
                onClick={() => updatePermission("can_seek")}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function RoomInfoCard({
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

export function LobbyHistoryCard({ state }: { state: LobbyState | null }) {
  const history = state?.history ?? [];
  return (
    <section className="panel lobby-control-card lobby-history-card">
      <h2>Recently Played</h2>
      {history.length ? (
        <div className="lobby-history-list">
          {history.slice(0, 10).map((item) => (
            <div className="lobby-history-row" key={item.id}>
              <Artwork src={item.art_url} alt={item.title} size="sm" />
              <div className="lobby-history-copy">
                <strong>{item.title}</strong>
                <span>{item.artist}</span>
                <small>
                  {item.added_by_nickname ? `Added by ${item.added_by_nickname} · ` : ""}
                  {formatPlayedAt(item.played_at)}
                </small>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">Nothing has played in this lobby yet.</p>
      )}
    </section>
  );
}

export function ActivityCard({ state }: { state: LobbyState | null }) {
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

export function LobbyMiniPlayer({
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

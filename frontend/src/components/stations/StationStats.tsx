export function StationStat({ icon, value, label }: { icon: string; value: string | number; label: string }) {
  return <div className="station-stat-card"><span className="station-stat-icon" aria-hidden="true">{icon}</span><strong>{value}</strong><span>{label}</span></div>
}

export type IconName = 'home' | 'search' | 'stations' | 'playlists' | 'history' | 'lobbies' | 'settings'

export function NavIcon({ name }: { name: IconName }) {
  const common = {
    width: 20,
    height: 20,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.9,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }

  switch (name) {
    case 'home': return <svg {...common}><path d="M3.5 10.8 12 3.8l8.5 7v8.7a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5Z"/><path d="M9.2 21v-6.2h5.6V21"/></svg>
    case 'search': return <svg {...common}><circle cx="10.8" cy="10.8" r="6.2"/><path d="m15.4 15.4 4.3 4.3"/></svg>
    case 'stations': return <svg {...common}><circle cx="12" cy="12" r="2.4"/><circle cx="12" cy="12" r="6.2"/><circle cx="12" cy="12" r="9" opacity=".55"/></svg>
    case 'playlists': return <svg {...common}><path d="M9 5v12.2"/><path d="m9 6 9-2v11"/><ellipse cx="6.5" cy="18.2" rx="2.5" ry="1.9"/><ellipse cx="15.5" cy="16.2" rx="2.5" ry="1.9"/></svg>
    case 'history': return <svg {...common}><circle cx="12" cy="12" r="8.5"/><path d="M12 7.4v5l3.3 2"/></svg>
    case 'lobbies': return <svg {...common}><circle cx="12" cy="12" r="7.7"/><circle cx="12" cy="12" r="3.8" opacity=".75"/></svg>
    case 'settings': return <svg {...common}><circle cx="12" cy="12" r="2.8"/><path d="M19.1 13.6a7.5 7.5 0 0 0 0-3.2l2-1.5-2-3.4-2.5 1a7.4 7.4 0 0 0-2.7-1.6L13.6 2h-4l-.4 2.9a7.4 7.4 0 0 0-2.7 1.6l-2.5-1-2 3.4 2 1.5a7.5 7.5 0 0 0 0 3.2l-2 1.5 2 3.4 2.5-1a7.4 7.4 0 0 0 2.7 1.6l.4 2.9h4l.4-2.9a7.4 7.4 0 0 0 2.7-1.6l2.5 1 2-3.4Z"/></svg>
  }
}

export type IconName =
  | 'home'
  | 'search'
  | 'stations'
  | 'playlists'
  | 'history'
  | 'lobbies'
  | 'settings'
  | 'quality'
  | 'admin'

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
    case 'home':
      return (
        <svg {...common}>
          <path d="M3.5 10.8 12 3.8l8.5 7v8.7a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5Z" />
          <path d="M9.2 21v-6.2h5.6V21" />
        </svg>
      )

    case 'search':
      return (
        <svg {...common}>
          <circle cx="10.8" cy="10.8" r="6.2" />
          <path d="m15.4 15.4 4.3 4.3" />
        </svg>
      )

    case 'stations':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="1.8" />
          <path d="M8.3 8.3a5.2 5.2 0 0 0 0 7.4" />
          <path d="M15.7 8.3a5.2 5.2 0 0 1 0 7.4" />
          <path d="M5.2 5.2a9.6 9.6 0 0 0 0 13.6" />
          <path d="M18.8 5.2a9.6 9.6 0 0 1 0 13.6" />
        </svg>
      )

    case 'playlists':
      return (
        <svg {...common}>
          <path d="M4 6h8" />
          <path d="M4 10h8" />
          <path d="M4 14h5" />
          <path d="M15 5v11.7" />
          <path d="m15 6 5-1.2v10.4" />
          <ellipse cx="12.5" cy="18.1" rx="2.5" ry="1.9" />
          <ellipse cx="17.5" cy="16.8" rx="2.5" ry="1.9" />
        </svg>
      )

    case 'history':
      return (
        <svg {...common}>
          <path d="M3.8 8.2V4.5l3.2 2" />
          <path d="M4.4 8.2A8.5 8.5 0 1 1 4 15" />
          <path d="M12 7.3v5l3.4 2" />
        </svg>
      )

    case 'lobbies':
      return (
        <svg {...common}>
          <circle cx="9" cy="8.3" r="3" />
          <circle cx="17.2" cy="9.4" r="2.3" />
          <path d="M3.5 19.5v-1.1c0-3.1 2.4-5.4 5.5-5.4s5.5 2.3 5.5 5.4v1.1" />
          <path d="M14.1 14.1c.9-.6 2-.9 3.2-.9 2.3 0 4.2 1.7 4.2 4v.8" />
        </svg>
      )

    case 'settings':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.1 13.6a7.5 7.5 0 0 0 0-3.2l2-1.5-2-3.4-2.5 1a7.4 7.4 0 0 0-2.7-1.6L13.6 2h-3.2l-.4 2.9a7.4 7.4 0 0 0-2.7 1.6l-2.5-1-2 3.4 2 1.5a7.5 7.5 0 0 0 0 3.2l-2 1.5 2 3.4 2.5-1a7.4 7.4 0 0 0 2.7 1.6l.4 2.9h3.2l.4-2.9a7.4 7.4 0 0 0 2.7-1.6l2.5 1 2-3.4Z" />
        </svg>
      )

    case 'quality':
      return (
        <svg {...common}>
          <path d="m12 2 1.15 3.35L16.5 6.5l-3.35 1.15L12 11l-1.15-3.35L7.5 6.5l3.35-1.15Z" />
          <path d="m6.3 10.5 1.45 4.15 4.15 1.45-4.15 1.45L6.3 21.7l-1.45-4.15L.7 16.1l4.15-1.45Z" />
          <path d="m18.3 12.1.8 2.3 2.3.8-2.3.8-.8 2.3-.8-2.3-2.3-.8 2.3-.8Z" />
        </svg>
      )

    case 'admin':
      return (
        <svg {...common}>
          <circle cx="8.2" cy="7.2" r="3" />
          <path d="M2.8 18.8v-1c0-3 2.3-5.3 5.4-5.3 1.6 0 3 .6 4 1.6" />
          <circle cx="16.8" cy="16.7" r="2.2" />
          <path d="M16.8 12.4v1.2" />
          <path d="M16.8 19.8V21" />
          <path d="m13.1 14.6 1 .6" />
          <path d="m19.5 18.2 1 .6" />
          <path d="m13.1 18.8 1-.6" />
          <path d="m19.5 15.2 1-.6" />
        </svg>
      )
  }
}

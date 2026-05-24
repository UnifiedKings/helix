type Props = { src?: string; alt: string; size?: 'sm' | 'md' | 'lg' }

export function Artwork({ src, alt, size = 'md' }: Props) {
  return src ? <img className={`artwork artwork-${size}`} src={src} alt={alt} /> : <div className={`artwork artwork-${size} artwork-empty`}>♪</div>
}

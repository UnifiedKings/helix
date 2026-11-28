import sharp from 'sharp'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const publicDir = fileURLToPath(new URL('../public/', import.meta.url))
const source = join(publicDir, 'helix-logo.png')
const background = '#111111'

for (const size of [180, 192, 512]) {
  // Keep the existing Helix artwork intact and give it a square canvas.
  await sharp(source)
    .resize(size, size, { fit: 'contain', background })
    .flatten({ background })
    .png()
    .toFile(join(publicDir, `pwa-icon-${size}.png`))
}

// Maskable icons keep the artwork within the central safe zone.
const maskSize = 512
const insetSize = 360
const inset = await sharp(source)
  .resize(insetSize, insetSize, { fit: 'contain', background })
  .flatten({ background })
  .png().toBuffer()
await sharp({ create: { width: maskSize, height: maskSize, channels: 3, background } })
  .composite([{ input: inset, gravity: 'centre' }])
  .png().toFile(join(publicDir, 'pwa-icon-maskable-512.png'))

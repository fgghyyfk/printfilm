import { fileURLToPath } from 'node:url'
import { rolldown } from 'rolldown'

// Execute the actual source graph, including its locale modules, with the existing test toolchain.
export async function loadSource(relativePath) {
  const bundle = await rolldown({ input: fileURLToPath(new URL(relativePath, import.meta.url)) })
  try {
    const { output } = await bundle.generate({ format: 'esm' })
    return await import(`data:text/javascript;base64,${Buffer.from(output[0].code).toString('base64')}`)
  } finally {
    await bundle.close()
  }
}

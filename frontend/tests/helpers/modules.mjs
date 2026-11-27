// Compile the real frontend modules in memory, without adding test dependencies.
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const cache = new Map()

export async function importSource(relativePath) {
  return import(await moduleUrl(new URL(relativePath, import.meta.url)))
}

async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href)
  let source = await readFile(url, 'utf8')
  source = source.replaceAll('import.meta.env.VITE_API_URL', "'https://backend.example.test/'")
  source = source.replace(/^import\s+['"][^'"]+\.css['"]\s*$/gm, '')
  let { outputText } = ts.transpileModule(source, {
    fileName: fileURLToPath(url),
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
  })
  const imports = [...outputText.matchAll(/from\s+(['"])([^'"]+)\1/g)]
  for (const match of imports) {
    const specifier = match[2]
    let target
    if (specifier.startsWith('.')) {
      const imported = new URL(specifier, url)
      for (const extension of ['.ts', '.tsx']) {
        try {
          target = await moduleUrl(new URL(imported.href + extension))
          break
        } catch (error) {
          if (error.code !== 'ENOENT') throw error
        }
      }
      if (!target) throw new Error('Missing frontend module: ' + specifier)
    } else {
      target = pathToFileURL(require.resolve(specifier)).href
    }
    outputText = outputText.replaceAll(match[0], 'from ' + JSON.stringify(target))
  }
  const result = 'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
  cache.set(url.href, result)
  return result
}

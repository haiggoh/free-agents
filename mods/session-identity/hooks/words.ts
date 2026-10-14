// Pure word logic for the session-identity mod, kept apart from the hooks so the tests can
// drive it without an engine.

export const DEFAULT_PCT = 60

/** The model-word share, 0-100. Anything that is not a plain integer in range falls back. */
export function parsePct(raw: string | undefined): number {
  if (raw === undefined || !/^\d{1,3}$/.test(raw)) return DEFAULT_PCT
  const n = Number(raw)
  return n <= 100 ? n : DEFAULT_PCT
}

/**
 * A short display name from a provider-qualified id:
 *   "nvidia/nemotron-3-ultra-550b-a55b" -> "Nemotron-3-Ultra"
 *   "qwen3.8-35b-a3b-mlx-4bit"          -> "Qwen3.8"
 * Size, quantisation and packaging suffixes are dropped: they are noise in a spinner.
 */
export function shortName(actualModel: string): string {
  const base = actualModel.split('/').pop() ?? actualModel
  const kept: string[] = []
  for (const part of base.split(/[-_]/)) {
    if (/^(a?\d+(\.\d+)?[bm]|\d+bit|mlx|gguf|instruct|it|chat|optiq.*|dev|fp\d+|q\d.*)$/i.test(part)) break
    kept.push(part.charAt(0).toUpperCase() + part.slice(1))
  }
  return kept.length ? kept.join('-') : base
}

/**
 * The words that name the model: the launcher's curated pool for the model family first,
 * then two built from the model's own name, so even an uncurated model is named.
 */
export function deriveModelWords(actualModel: string, provided: readonly string[]): string[] {
  const words = provided.filter(w => typeof w === 'string' && w.length > 0 && w.length <= 40)
  if (actualModel && actualModel !== 'unknown') {
    const name = shortName(actualModel)
    words.push(`Running on ${name}`, `${name} is thinking`)
  }
  return [...new Set(words)]
}

/** FNV-1a: stable across reloads and frames, so one turn always draws the same word. */
function hash(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h
}

/**
 * The word to show for a turn the engine sampled `vanillaWord` for.
 *
 * Decided from the sampled word alone, never at random: the spinner may be drawn many times
 * a turn, and a fresh draw each time would make the word flicker.
 */
export function pickWord(vanillaWord: string, modelWords: readonly string[], pct: number): string {
  if (modelWords.length === 0 || pct <= 0) return vanillaWord
  const h = hash(vanillaWord)
  return h % 100 < pct ? (modelWords[(h >>> 8) % modelWords.length] ?? vanillaWord) : vanillaWord
}

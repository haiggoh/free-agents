import { expect, mock, test } from 'claude-code/testing'

// One spinner as the engine raises it; `word` is what the engine sampled for the turn.
const spinner = (word: string, message: string | null = null) => ({
  surface: 'terminal' as const,
  component: 'Spinner' as const,
  requestId: 'main',
  props: { word, message, suffix: '…', mode: 'responding' as const },
})

// The word the engine is handed once every plugin has had its say: the test's own hook sits
// beneath the plugin and records the props that reach it. Registered once, before the test's
// first $ call, as the kit requires.
function recorder(on: any) {
  const seen: { word?: string } = {}
  on('ui.render', { component: 'Spinner' }, (_$: any, e: any) => {
    seen.word = e.props.word
    return { type: 'engine', ref: 0 }
  })
  return async ($: any, word: string, message: string | null = null) => {
    seen.word = undefined
    await $.ui.render(spinner(word, message))
    return seen.word
  }
}

const SAMPLED = Array.from({ length: 40 }, (_, i) => `Verb${i}ing`)

test('a gateway session (no LA_SESSION_KIND) keeps every vanilla word', async ($, on) => {
  // Everything else a free session has, so only the session-kind gate can keep words vanilla.
  mock.env(on, { LA_ACTUAL_MODEL: 'qwen3.8-35b-a3b', LA_MODEL_SPINNER_WORDS: '["Qwening"]', LA_SPINNER_MODEL_PCT: '100' })
  const draw = recorder(on)
  let changed = 0
  for (const w of SAMPLED) {
    const fresh = await draw($, w)
    if (fresh !== w) changed++
  }
  expect(changed).toBe(0)
})

test('a local session names its real model on some turns and keeps vanilla on others', async ($, on) => {
  mock.env(on, {
    LA_SESSION_KIND: 'local',
    LA_ACTUAL_MODEL: 'qwen3.8-35b-a3b',
    LA_MODEL_SPINNER_WORDS: '["Qwening","Qwen38ing"]',
  })
  const draw = recorder(on)
  const drawn: string[] = []
  for (const w of SAMPLED) drawn.push((await draw($, w)) ?? '')
  expect(drawn.some(w => w.startsWith('Qwen') || w.includes('Qwen3.8'))).toBe(true)
  expect(drawn.some((w, i) => w === SAMPLED[i])).toBe(true)
})

test('a share of 0 keeps every vanilla word even in a free session', async ($, on) => {
  mock.env(on, { LA_SESSION_KIND: 'free_api', LA_ACTUAL_MODEL: 'nvidia/nemotron-3-ultra', LA_SPINNER_MODEL_PCT: '0' })
  const draw = recorder(on)
  for (const w of SAMPLED.slice(0, 10)) expect(await draw($, w)).toBe(w)
})

test('a state message is never touched', async ($, on) => {
  mock.env(on, { LA_SESSION_KIND: 'local', LA_ACTUAL_MODEL: 'qwen3.8', LA_SPINNER_MODEL_PCT: '100' })
  const draw = recorder(on)
  expect(await draw($, 'Sauteing', 'Retrying in 5s')).toBe('Sauteing')
})

test('a malformed word pool does not break the spinner', async ($, on) => {
  mock.env(on, { LA_SESSION_KIND: 'local', LA_ACTUAL_MODEL: 'qwen3.8', LA_MODEL_SPINNER_WORDS: '{not json', LA_SPINNER_MODEL_PCT: '100' })
  const draw = recorder(on)
  const w = await draw($, 'Sauteing')
  expect(w).toContain('Qwen3.8')
})

import type { Register } from 'claude-code'
import { deriveModelWords, parsePct, pickWord } from './words'

// Active only in a session a free-agents launcher started: LA_SESSION_KIND is exported by
// launch-claude-agent.sh (local) and remote-session.sh (free_api). The model comes from
// LA_ACTUAL_MODEL, never from $.session.model(): a free session reports a spoofed Claude id
// to the engine, so that would put "Sonnet" words on a Qwen session.
type Config = { words: string[]; pct: number }

export const register: Register = on => {
  // undefined: not read yet; null: not a free session, so every hook passes through.
  let config: Config | null | undefined

  on('ui.render', { component: 'Spinner' }, async ($, e, next) => {
    if (config === undefined) {
      const kind = await $.env.get('LA_SESSION_KIND')
      if (kind !== 'local' && kind !== 'free_api') {
        config = null
      } else {
        let provided: string[] = []
        try {
          const parsed: unknown = JSON.parse((await $.env.get('LA_MODEL_SPINNER_WORDS')) ?? '[]')
          if (Array.isArray(parsed)) provided = parsed.filter((w): w is string => typeof w === 'string')
        } catch {
          provided = []
        }
        config = {
          words: deriveModelWords((await $.env.get('LA_ACTUAL_MODEL')) ?? '', provided),
          pct: parsePct(await $.env.get('LA_SPINNER_MODEL_PCT')),
        }
      }
    }
    // A state message (e.g. a retry notice) stands in for the word: leave it alone.
    if (config === null || e.props.message !== null) return next(e)
    return next({ ...e, props: { ...e.props, word: pickWord(e.props.word, config.words, config.pct) } })
  }).catch(($, e, next) => next(e))
}

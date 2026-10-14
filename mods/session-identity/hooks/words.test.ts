import { describe, expect, test } from 'claude-code/testing'
import { deriveModelWords, parsePct, pickWord, shortName } from './words'

describe('parsePct', () => {
  test('falls back to 60 on anything that is not a plain 0-100 integer', () => {
    for (const raw of [undefined, '', 'abc', '150', '-5', '12.5', ' 40']) expect(parsePct(raw)).toBe(60)
    expect(parsePct('0')).toBe(0)
    expect(parsePct('100')).toBe(100)
    expect(parsePct('75')).toBe(75)
  })
})

describe('model names', () => {
  test('short names drop size, quantisation and packaging', () => {
    expect(shortName('nvidia/nemotron-3-ultra-550b-a55b')).toBe('Nemotron-3-Ultra')
    expect(shortName('qwen3.8-35b-a3b-mlx-4bit')).toBe('Qwen3.8')
    expect(shortName('mistral/devstral-small-2')).toBe('Devstral-Small-2')
  })

  test('the real model is named, never a spoofed Claude id', () => {
    const words = deriveModelWords('nvidia/nemotron-3-ultra-550b-a55b', [])
    expect(words.some(w => w.includes('Nemotron'))).toBe(true)
    expect(words.some(w => /claude|sonnet|opus/i.test(w))).toBe(false)
  })

  test('the curated pool comes first and a name word is always added', () => {
    const words = deriveModelWords('qwen3.8-35b-a3b', ['Qwening', 'Qwenching'])
    expect(words[0]).toBe('Qwening')
    expect(words.some(w => w.includes('Qwen3.8'))).toBe(true)
  })

  test('an unknown model leaves only the curated pool', () => {
    expect(deriveModelWords('unknown', ['Qwening'])).toEqual(['Qwening'])
    expect(deriveModelWords('', [])).toEqual([])
  })
})

describe('pickWord', () => {
  const vanilla = Array.from({ length: 2000 }, (_, i) => `Verb${i}ing`)

  test('is a function of the sampled word, so the spinner never flickers', () => {
    for (const v of vanilla.slice(0, 50)) expect(pickWord(v, ['A', 'B'], 60)).toBe(pickWord(v, ['A', 'B'], 60))
  })

  test('names the model on about the configured share of turns', () => {
    for (const pct of [30, 60, 90]) {
      const hits = vanilla.filter(v => pickWord(v, ['Qwening'], pct) === 'Qwening').length / vanilla.length
      expect(Math.abs(hits - pct / 100)).toBeLessThan(0.05)
    }
  })

  test('uses every word in the pool', () => {
    const seen = new Set(vanilla.map(v => pickWord(v, ['A', 'B', 'C'], 100)))
    expect([...seen].sort()).toEqual(['A', 'B', 'C'])
  })

  test('passes the vanilla word through at 0 % and with an empty pool', () => {
    expect(pickWord('Sauteing', ['Qwening'], 0)).toBe('Sauteing')
    expect(pickWord('Sauteing', [], 100)).toBe('Sauteing')
  })
})

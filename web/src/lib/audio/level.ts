/**
 * How loud a track is right now, as one number.
 *
 * Root mean square rather than peak: peak jumps on a single sample and reads as
 * noise, while RMS is what a level meter shows. Pure maths and no policy - the
 * floor a caller treats as silence and the curve it maps onto a bar or a bloom
 * are the caller's, because a waveform and a corona want different responses to
 * the same voice.
 *
 * One implementation, two callers (`session/room-signals.ts` measures the
 * watcher's levels, `talk/levels.ts` measures a call the visitor is in), so the
 * measurement cannot drift between the two surfaces while the policies differ.
 */
export function rms(analyser: AnalyserNode): number {
  const samples = new Float32Array(analyser.fftSize)
  analyser.getFloatTimeDomainData(samples)
  let sum = 0
  for (const sample of samples) sum += sample * sample
  return Math.sqrt(sum / samples.length)
}

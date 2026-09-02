import 'server-only'

import { hosted } from '@/lib/hosted'
import { agentDir } from '@/lib/textmode/process'

/**
 * Whether text mode should refuse rather than replay.
 *
 * Three states, not two, and the middle one is the one worth naming:
 *
 *   real     `AMBASSADOR_AGENT_DIR` names an agent, so the turn runs through
 *            the actual Python core. Works on a laptop, hosted or not.
 *   replay   no agent, and nobody is claiming otherwise: the laptop's labelled
 *            fallback for a room with bad audio, which is what text mode was
 *            built to be.
 *   refused  no agent AND this is the public service. A client typing their own
 *            questions into a scripted replay is being misled by a label they
 *            have no reason to read (docs/09-), so the honest answer is to
 *            refuse and say why.
 *
 * Both conditions are required. A hosted deployment that somehow does have the
 * agent beside it keeps working, and a laptop with no agent keeps its replay.
 */
export type TextModeAvailability = 'real' | 'replay' | 'refused'

export function textModeAvailability(): TextModeAvailability {
  if (agentDir() !== null) return 'real'
  return hosted() ? 'refused' : 'replay'
}

export function textModeRefused(): boolean {
  return textModeAvailability() === 'refused'
}

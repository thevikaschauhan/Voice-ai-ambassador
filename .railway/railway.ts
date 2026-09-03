/**
 * The Railway project, in code. This file is the whole environment: both
 * services, their build and deploy settings, and the NAMES of the variables
 * they carry.
 *
 * WHY THIS FILE REPLACED TWO railway.json FILES. Railway deprecated config as
 * code. It is not a soft preference: the API refuses to set a service's config
 * file path at all ("Config as Code (railway.json / railway.toml) is
 * deprecated. Use Infrastructure as Code (.railway/railway.ts) instead"), new
 * services cannot opt in, and existing files stop being read on 2026-12-01.
 * Both of this project's services were created after that change, so neither
 * railway.json was ever read: the live instances showed builder RAILPACK,
 * empty watch patterns and no healthcheck, while the files in the repository
 * said DOCKERFILE with patterns and a healthcheck. The agent image built from
 * a Dockerfile anyway, but only because Railway auto-detects a root
 * `Dockerfile` - not because the file below it was obeyed. Two files that look
 * like configuration and are not is worse than no configuration at all, which
 * is why they are gone rather than kept as documentation.
 *
 * HOW IT IS APPLIED. `railway config plan` reads Railway and prints the diff;
 * `railway config apply` asks before writing. Both walk up from the working
 * directory to find this file, so either runs from the repository root. Omit
 * means delete, so this file has to describe the whole project: dropping a
 * service or a variable name here is a request to remove it from Railway.
 */
import { defineRailway, github, preserve, project, service } from "railway/iac";

const REPO = "thevikaschauhan/Voice-ai-ambassador";

export default defineRailway(() => {
  const web = service("web", {
    // rootDirectory is deliberately null, and it is the one setting on this
    // service that has already broken a deploy. The app does not fit inside
    // `web/`: its dynamic routes read `join(process.cwd(), '..', 'data')` at
    // request time and `web/Dockerfile` copies `data` to `/srv/data` beside
    // the app. With the root set to `/web`, the build context starts inside
    // `web/` and the image build fails on `"/data": not found`
    // (docs/09-deploy.md, "The layout is part of the contract").
    source: github(REPO, { branch: "main", rootDirectory: null, checkSuites: false }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "web/Dockerfile",
      // `data/**` is in both services' patterns on purpose: an inventory edit
      // is a deploy for the worker as well, because it reads the same files.
      watchPatterns: ["web/**", "data/**"],
    },
    deploy: {
      // `/` is a dynamic route that reads `../data`, so a 200 here proves the
      // image is layered correctly. It proves nothing about the LiveKit
      // variables - docs/09-deploy.md, "Verifying a deploy", says what does.
      healthcheckPath: "/",
      healthcheckTimeout: 60,
      // No restartPolicyType. Railway's default already IS on-failure with a
      // maximum of ten restarts (docs/deployments/restart-policy), and the
      // platform does not STORE a setting that equals its default: it reads
      // back as null. Declaring it therefore protects nothing and makes every
      // future `railway config plan` show a pending change that can never be
      // applied away - which is how a plan stops being read. Measured, not
      // assumed: after the 13:31Z apply the read-back showed
      // restartPolicyType null on both services. Only the value that DIFFERS
      // from the default is worth declaring, so this stays:
      restartPolicyMaxRetries: 3,
    },
    replicas: { ams: 1 },
    // Five variables, and the short list is the point. This service was
    // provisioned with the agent's whole environment, so a public-facing Node
    // container was holding DEEPGRAM_API_KEY and OPENROUTER_API_KEY - two
    // metered provider credentials it has no code path to use. `web` reads
    // exactly the three LIVEKIT_ values (`lib/livekit/config.ts`) plus the two
    // gates below; every other name was removed from the service rather than
    // left listed here, because a credential that cannot be reached from a
    // container cannot leak from it.
    //
    // The three LIVEKIT_ROOM / AMBASSADOR_* names stay deliberately ABSENT
    // rather than listed: docs/09-'s web contract sets them unset on purpose,
    // and naming them here would be the first step towards someone setting
    // them.
    variables: {
      // The talk page's two gates (#73). Listed because omit means delete:
      // once the human sets DEMO_ACCESS_CODE in the dashboard, an apply from a
      // file that did not name it would take it away again. DEMO_MAX_ROOMS
      // defaults to 2 in the route when unset, so it is named for the same
      // reason rather than because it has to be set.
      DEMO_ACCESS_CODE: preserve(),
      // Which languages the talk page offers (#87 follow-on). Set to `en` on
      // the hosted service until the Arabic and Hindi packets come back; unset
      // means all three, which is what a laptop wants. Named for the same
      // reason as the two around it: omit means delete, and an apply from a
      // file that did not name it would silently re-open two languages whose
      // copy nobody has authored, on a public URL.
      DEMO_LANGUAGES: preserve(),
      DEMO_MAX_ROOMS: preserve(),
      LIVEKIT_API_KEY: preserve(),
      LIVEKIT_API_SECRET: preserve(),
      LIVEKIT_URL: preserve(),
    },
  });

  const agentWorker = service("Voice-ai-ambassador", {
    // The service name is Railway's, not ours: renaming it is a separate
    // decision, and a rename here would read as delete-and-create.
    // No commitSha. The pulled state had one, which would pin the service to a
    // single commit and stop main from deploying.
    source: github(REPO, { branch: "main", upstreamUrl: `https://github.com/${REPO}` }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
      // Without patterns every push redeploys the worker, including web-only
      // ones, which drains a live call for a change the image does not carry.
      // `.dockerignore` is here because it decides the build context, so a
      // change to it changes the image.
      watchPatterns: ["agent/**", "data/**", "Dockerfile", ".dockerignore"],
    },
    deploy: {
      // No startCommand. The image's CMD carries `--drain-timeout 600`, and a
      // start command set here would silently replace it and take the drain
      // with it.
      //
      // No restart policy either, and for this service BOTH halves are the
      // platform default: on-failure, ten restarts. See the note on `web`
      // above for why a declared default is worse than an undeclared one. The
      // behaviour docs/09 relies on - a non-zero exit crash-looping instead of
      // stopping quietly - is unchanged, because that behaviour IS the default.
      // The drain below is declared because 600 is not a default; the default
      // is 0.
      // The platform half of that drain, and it was missing entirely. This is
      // the SIGTERM-to-SIGKILL window, and Railway's default is 0 seconds: the
      // worker asked for 600 to finish a call and was being killed
      // immediately. Matched to the CMD so the two numbers cannot disagree.
      drainingSeconds: 600,
    },
    replicas: { "europe-west4-drams3a": 1 },
    networking: { privateNetworkEndpoint: "voice-ai-ambassador" },
    // Names only. Values stay on Railway: `preserve()` means "keep what is
    // already set", so this file can be read by anyone without leaking a
    // credential, and applying it never rewrites a secret.
    variables: {
      ALLOW_UNCERTIFIED_LANGUAGE: preserve(),
      BRIEF_MODEL: preserve(),
      DEEPGRAM_API_KEY: preserve(),
      DEEPGRAM_MODEL: preserve(),
      // Toby's per-call duration cap (#77), set to 600 on the hosted service.
      // Named here for the reason the whole list exists: omit means delete, and
      // an apply from a file that did not name it would take the cap away and
      // leave a public URL with uncapped calls on metered providers.
      DEMO_MAX_CALL_SECONDS: preserve(),
      DEMO_MODE: preserve(),
      FISH_API_KEY: preserve(),
      FISH_TTS_MODEL: preserve(),
      GUARDRAIL_MODE: preserve(),
      LIVEKIT_API_KEY: preserve(),
      LIVEKIT_API_SECRET: preserve(),
      LIVEKIT_URL: preserve(),
      LLM_BASE_URL: preserve(),
      LLM_MODEL: preserve(),
      LLM_THINKING: preserve(),
      OPENROUTER_API_KEY: preserve(),
      PROMPT_MODE: preserve(),
      STT_ENABLED: preserve(),
      STT_MODEL_DEFAULT: preserve(),
      STT_PROVIDER: preserve(),
      TTS_PROVIDER: preserve(),
      TTS_VOICE_ID_AR: preserve(),
      TTS_VOICE_ID_EN: preserve(),
      TTS_VOICE_ID_HI: preserve(),
    },
  });

  return project("voice-ai-binghatti", {
    resources: [web, agentWorker],
  });
});

/**
 * The Railway project, in code. This file is the whole environment: all three
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
    source: github(REPO, { branch: "main", rootDirectory: null }),
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
      // Phase 2, the browser-facing half of the admin surface. `web` is the
      // ONLY service that carries these three, and it deliberately never
      // carries DATABASE_URL: its server routes call the admin API's private
      // address and add the bearer server-side, so a mistake in the web tier
      // cannot become a mistake against the database (docs/10-admin.md).
      ADMIN_ACCESS_CODE: preserve(),
      // Shared with admin-api and nowhere else. Never browser-visible.
      ADMIN_API_TOKEN: preserve(),
      // `http://admin-api.railway.internal:8080` - the private endpoint
      // declared below, and the port the start command binds. Set by hand
      // rather than by `ref()`: RAILWAY_PRIVATE_DOMAIN is referencable, but it
      // yields the bare host, and the contract in docs/01 defines this as a
      // URL. A reference cannot add the scheme and port, and a literal
      // carrying Railway's own `${...}` syntax could not be verified without
      // an apply, which this card is not allowed to do.
      ADMIN_API_URL: preserve(),
      ADMIN_SESSION_SECRET: preserve(),
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
      // Supabase Postgres, via the Supavisor pooler in SESSION mode. One
      // variable and not two: the usual pooled/direct pair (DATABASE_URL plus
      // DIRECT_URL) exists so migrations can bypass the pooler, and the direct
      // host is reachable only over IPv6 on a free Supabase project while
      // Railway leaves outbound IPv6 off per service. So there is no direct
      // route to name, and migrations take the same session-mode URL the app
      // uses. Named here before it exists because omit means delete: an apply
      // from a file that did not name it would remove the database credential
      // from a running worker.
      DATABASE_URL: preserve(),
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
      // Phase 2, shared with admin-api and with no other service: the worker
      // writes buyer payloads and the admin API reads them back, so both need
      // the same envelope key and the same contact fingerprint key. Neither
      // ever reaches `web`.
      PII_ENCRYPTION_KEY: preserve(),
      PII_HASH_KEY: preserve(),
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

  // The Phase 2 admin API: FastAPI out of the SAME Python image as the worker,
  // started differently. docs/10-admin.md is the surface contract; docs/01
  // ADR-018..021 are the decisions.
  const adminApi = service("admin-api", {
    source: github(REPO, { branch: "main" }),
    build: {
      builder: "DOCKERFILE",
      // The worker's Dockerfile, unchanged and unforked. One image, two
      // processes: the difference is the start command below, not a second
      // build. So the watch patterns have to match the worker's exactly, or
      // an `agent/**` change would deploy one of the two services that run it.
      dockerfilePath: "Dockerfile",
      watchPatterns: ["agent/**", "data/**", "Dockerfile", ".dockerignore"],
    },
    deploy: {
      // Unlike the worker, this service DOES need a start command: the image's
      // CMD runs the LiveKit worker, and this process is uvicorn.
      //
      // `--host ::` and not 127.0.0.1 or 0.0.0.0. Railway's private network is
      // IPv6, and internal DNS resolves to IPv6 only in environments created
      // before 2025-10-16; `::` serves both, since a dual-stack listener also
      // accepts IPv4-mapped connections. A process bound to 127.0.0.1 is
      // reachable from nothing at all.
      //
      // The port is fixed rather than read from PORT, because nothing
      // publishes this service: there is no domain and no edge proxy to hand
      // it one. `web` reaches it at admin-api.railway.internal:8080, which is
      // the value of ADMIN_API_URL above, so the two numbers have to agree.
      startCommand: "uv run --no-sync uvicorn adapter.admin_api:app --host :: --port 8080",
      // Migrations run here and nowhere else: once, after the build, before
      // this deployment takes traffic, and never at ordinary startup
      // (docs/10-admin.md). A failed pre-deploy stops the deployment and
      // leaves the previous version serving, which is the behaviour a schema
      // change wants.
      //
      // RECONCILE AT dwight/task-p2-migrations-repo: he owns the runner, the
      // module below does not exist yet, and docs/10 specifies the phase
      // rather than the invocation. A pre-deploy command that names a missing
      // module fails the deployment, so this string is the one thing in this
      // file that must be checked against his merge before anyone applies it.
      preDeployCommand: ["uv run --no-sync python -m adapter.migrations up"],
      // Railway's default is NO time limit, and the failure mode of that
      // default is the quiet one: a migration blocked on a lock holds the
      // deployment "in progress" indefinitely rather than failing it. Same
      // shape as the worker's drain, where the default 0 was also wrong for
      // us. 300s is ten times a normal migration on a Nano instance and well
      // inside the platform's 3600s ceiling.
      preDeployTimeoutSeconds: 300,
      // No healthcheckPath, deliberately. docs/10 gives this service a
      // `/ready` that reports NOT ready while the database is unreachable, by
      // design, so that a Supabase pause degrades the admin surface instead of
      // taking the deploy down - and a platform healthcheck pointed at it
      // would convert exactly that state into a failed deployment. `/health`
      // proves process liveness only and would be safe, but it is dwight's
      // route to confirm and an undeclared healthcheck blocks nothing.
      //
      // No restart policy: on-failure with ten retries is the platform
      // default, and a declared default is worse than an undeclared one.
      // No drainingSeconds: this process serves bounded HTTP requests rather
      // than holding a call open, so the platform default of 0 is right here
      // and wrong for the worker.
    },
    // Amsterdam, beside the other two. `ams` is the same region the worker
    // spells `europe-west4-drams3a`: Railway stores whichever form is written,
    // and both plans read clean, so the two spellings in this file are not
    // drift.
    replicas: { ams: 1 },
    // No public domain, and none is declared: every route but `/health` is
    // bearer-protected, and the way to keep a private API private is to give
    // it no ingress rather than to guard one.
    //
    // No `privateNetworkEndpoint` either, though ADMIN_API_URL's host is
    // `admin-api.railway.internal`. That endpoint is the platform default -
    // the service name - and Railway does not store a setting equal to its
    // default, so after the apply the stored value here is null and declaring
    // "admin-api" left every later plan reading `1 to change` forever. That
    // is the `checkSuites` shape #99 removed from `web`, and the cost is the
    // same: a permanently dirty plan is one that can no longer show drift.
    // Measured after the apply, not assumed - declaring a DIFFERENT name
    // plans the same single change, so the field is read rather than ignored;
    // omitting it plans clean. The worker keeps its own line because there
    // the stored value differs from the service name by case, and omitting
    // that one plans a real change.
    // Names only, as everywhere in this file.
    variables: {
      // Shared with `web`, which sends it; this service verifies it.
      ADMIN_API_TOKEN: preserve(),
      // The Supabase session-pooler URL, the same value the worker carries.
      // Present on the two Python services and on no others.
      DATABASE_URL: preserve(),
      PII_ENCRYPTION_KEY: preserve(),
      PII_HASH_KEY: preserve(),
    },
  });

  return project("voice-ai-binghatti", {
    resources: [web, agentWorker, adminApi],
  });
});

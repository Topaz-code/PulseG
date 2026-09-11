# Provider verification

Verified on **2026-09-11** against first-party documentation. This file is generated from `backend/providers/specs.py` by `scripts/build_provider_report.py`, so the app's provider cards and this record cannot disagree.

## How to read the status column

| Status | Meaning |
| --- | --- |
| verified | Free tier confirmed on the provider's own pages. Numbers below are from those pages. |
| conditional | Usable, but with a catch that changes how the agent behaves (a one-time credit, |
|  | a rotated preview model, or a quota the user must enable). |
| substituted | The specification named this provider as free; verification did not support that, |
|  | so a sanctioned substitute carries the chain slot. Documented in full below. |
| unverified | Listed so it can be configured, but terms were not confirmed. |

**Free forever** (12): demo, google_ai_studio, groq, kenney, mistral, opencode_zen, openrouter, pollinations, sealion, stable_horde, telegram, ytdlp

**One-time credit** (ration these): deepgram, firecrawl, nvidia_nim

## Substitutions

### zai -> openrouter

- Models used instead: `z-ai/glm-4.5-air:free`
- Why: Z.ai has no free tier for GLM coding models; the cheap plan is a paid subscription. OpenRouter serves a free GLM variant plus other free coder models.
- Recorded: 2026-09-11
- Not a silent drop: the substitution is shown in Settings on the provider card, in the
  agent's chain editor, and here.

### routeway -> openrouter

- Models used instead: `qwen/qwen3-coder:free, deepseek/deepseek-chat-v3.1:free`
- Why: Routeway advertised signup credits rather than a perpetual free tier, and no first-party page confirmed ongoing free access.
- Recorded: 2026-09-11
- Not a silent drop: the substitution is shown in Settings on the provider card, in the
  agent's chain editor, and here.

## The table

| Provider | Status | Needs key | Free tier | Limits | Sign up |
| --- | --- | --- | --- | --- | --- |
| Deepgram (`deepgram`) | verified | yes | $200 of one-time credit on signup. Nova-3 transcription at ~$0.0048/minute | credit: $200 one-time (not monthly); model: nova-3; language: multilingual | https://console.deepgram.com/signup |
| Demo mode (built in) (`demo`) | verified | no | Always available, offline, no key. Deterministic canned responses | note: not an AI provider: it exists so the app can be demonstrated and tested without any keys |  |
| Firecrawl (`firecrawl`) | verified | yes | ~1,000 credits, one-time on signup. Concurrency 2 on the free tier | credits: ~1,000 one-time; concurrency: 2 | https://firecrawl.dev/ |
| Google AI Studio (Gemini) (`google_ai_studio`) | verified | yes | Free tier available with no billing account. Gemini 2.5 Flash and Flash-Lite usable at $0 | requests/minute: varies by model and by project age; check AI Studio; requests/day: shown live in AI Studio, not in the API response; note: exact free numbers are not published as a stable figure and are visible only in AI Studio | https://aistudio.google.com/app/apikey |
| Groq (`groq`) | verified | yes | Free developer tier, no card. Very fast inference on custom silicon | requests/minute: ~30 on free tier; requests/day: ~14,400 for 8B class, ~1,000 for 70B class; tokens/minute: varies by model; stt: whisper-large-v3-turbo included on the free tier | https://console.groq.com/keys |
| Kenney asset packs (`kenney`) | verified | no | CC0, no attribution required, no API. PulseG uses a local cache of the packs you have downloaded | note: no API: a local library, indexed like any other asset source | https://kenney.nl/assets |
| Mistral AI (`mistral`) | verified | yes | Free 'Experiment' tier: no card, requires phone verification, generous rate limits | rate: 1 request/second; tokens: 500K tokens/minute; monthly: ~1B tokens/month on the free tier | https://console.mistral.ai/ |
| OpenRouter (`openrouter`) | verified | yes | 28+ models carrying the ':free' suffix cost $0/token with no card. 20 requests/minute, 50 requests/day; a one-time $10 credit purchase raises the daily cap to 1,000 permanently. | requests/minute: 20; requests/day: 50 (1,000 after a one-time $10 top-up); free_models: 28+ | https://openrouter.ai/keys |
| SEA-LION (AI Singapore) (`sealion`) | verified | yes | Free public-preview trial key for POC use; no card | rate: trial limits apply and cannot be raised; quota: POC-scoped, not production | https://playground.sea-lion.ai/ |
| Stable Horde (`stable_horde`) | verified | no | Free forever, crowdsourced. Anonymous keys are accepted; a registered key earns kudos for higher priority | anonymous: allowed but low priority; concurrency: one job at a time on low kudos | https://stablehorde.net/register |
| Telegram Bot (notifications) (`telegram`) | verified | yes | Free. No limits that matter for notification volume | note: bot tokens are per-bot; a single bot can notify several chats | https://core.telegram.org/bots#how-do-i-create-a-bot |
| YouTube Data API v3 (`youtube_data`) | verified | yes | 10,000 quota units per day, free, with a Google Cloud project | units/day: 10000; search.list cost: 100 units per call | https://console.cloud.google.com/apis/credentials |
| yt-dlp (`ytdlp`) | verified | no | Free, no key, no account. Runs locally as a bundled binary | note: bounded by network and by the source site's patience; PulseG rate-limits itself | https://github.com/yt-dlp/yt-dlp |
| Freesound (`freesound`) | conditional | yes | Free API key for non-commercial and commercial use alike; roughly half the library is CC0 | note: documented rate limits are generous but change; PulseG self-limits and prefers CC0 | https://freesound.org/apiv2/apply/ |
| Hugging Face Inference (`huggingface`) | conditional | yes | Free monthly inference credits on a free account; serverless text-to-image models include small pixel-art LoRAs | credits: monthly, small; note: model availability on serverless varies | https://huggingface.co/settings/tokens |
| NVIDIA NIM / build.nvidia.com (`nvidia_nim`) | conditional | yes | Free developer credits on signup; usable for prototyping without a card | credits: a fixed credit grant, not an unlimited free tier; rate: per-model limits shown on each model card | https://build.nvidia.com/ |
| OpenCode Zen (`opencode_zen`) | conditional | yes | Free preview models for coding (e.g. Grok Code Fast 1, Big Pickle) with an account | note: free preview models rotate without notice | https://opencode.ai/ |
| Pollinations (`pollinations`) | conditional | no | No key on the legacy image endpoint (rate-limited, may watermark). A free registered key removes the watermark and raises limits | anonymous: approximately one request every 15 seconds; watermark: possible without a key | https://pollinations.ai/ |
| Routeway (`routeway`) | substituted | yes | Credits on signup, not a perpetual free tier. Terms were not confirmable from a first-party page on 2026-09-11 | note: unconfirmed | https://routeway.ai/ |
| Z.ai (GLM / Zhipu) (`zai`) | substituted | yes | No free tier for the coding models. GLM Coding Plan is a paid subscription (~$18/month Lite) | requires_paid_plan: true | https://z.ai/ |

## Detail and caveats

### Deepgram (`deepgram`)

- Status: **verified** (checked 2026-09-11)
- Free tier: $200 of one-time credit on signup. Nova-3 transcription at ~$0.0048/minute
- Limits: credit: $200 one-time (not monthly); model: nova-3; language: multilingual
- Docs: https://developers.deepgram.com/
- Notes: Verified 2026-09-11. One-time credit, so the Transcriptor always tries yt-dlp subtitle extraction first and only spends Deepgram credit when no subtitle track exists; Groq Whisper (free tier) is the permanent fallback once the credit is gone. The quota meter tracks credit spend so the switch is visible before it happens.

### Demo mode (built in) (`demo`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Always available, offline, no key. Deterministic canned responses
- Limits: note: not an AI provider: it exists so the app can be demonstrated and tested without any keys
- Docs: 
- Notes: Every chain ends here when PULSEG_DEMO=1, and the UI labels outputs produced by it. This is how the whole pipeline - intake, dispatch, audit, approval, commit - can be exercised end to end with no network at all, which is also how the test suite runs.

### Firecrawl (`firecrawl`)

- Status: **verified** (checked 2026-09-11)
- Free tier: ~1,000 credits, one-time on signup. Concurrency 2 on the free tier
- Limits: credits: ~1,000 one-time; concurrency: 2
- Docs: https://docs.firecrawl.dev/
- Notes: Verified 2026-09-11 - and the one-time nature matters: the Researcher caps crawls per task (default 4), caches dead results so it never re-spends a credit on the same URL, and prefers yt-dlp for anything on YouTube. The provider's credit-usage endpoint feeds the quota meter.

### Google AI Studio (Gemini) (`google_ai_studio`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Free tier available with no billing account. Gemini 2.5 Flash and Flash-Lite usable at $0
- Limits: requests/minute: varies by model and by project age; check AI Studio; requests/day: shown live in AI Studio, not in the API response; note: exact free numbers are not published as a stable figure and are visible only in AI Studio
- Docs: https://ai.google.dev/gemini-api/docs/rate-limits
- Notes: Verified 2026-09-11. Free-tier data is used to improve Google's models - which is why the Tester (the agent that looks at your game screenshots) can use Gemini but the Settings screen tells you plainly that this is the trade. If quotas are exceeded the API returns 429; PulseG treats that as rate-limited and falls through the chain.

### Groq (`groq`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Free developer tier, no card. Very fast inference on custom silicon
- Limits: requests/minute: ~30 on free tier; requests/day: ~14,400 for 8B class, ~1,000 for 70B class; tokens/minute: varies by model; stt: whisper-large-v3-turbo included on the free tier
- Docs: https://console.groq.com/docs/rate-limits
- Notes: Verified 2026-09-11. Rate limits are per organisation, not per key: a second key does not buy headroom. Daily limits reset on a rolling 24h window, so the quota meter shows 'resets within 24h' rather than a midnight countdown.

### Kenney asset packs (`kenney`)

- Status: **verified** (checked 2026-09-11)
- Free tier: CC0, no attribution required, no API. PulseG uses a local cache of the packs you have downloaded
- Limits: note: no API: a local library, indexed like any other asset source
- Docs: https://kenney.nl/assets
- Notes: Verified 2026-09-11: Kenney's packs are CC0 and there is no API, so this entry models the local cache (~/.pulsegstudio/assets/kenney). If the cache is empty the curator says so and offers the download links in one click rather than silently producing nothing. Audio packs that matter: Music Jingles, Interface Sounds, Impact Sounds, RPG Audio.

### Mistral AI (`mistral`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Free 'Experiment' tier: no card, requires phone verification, generous rate limits
- Limits: rate: 1 request/second; tokens: 500K tokens/minute; monthly: ~1B tokens/month on the free tier
- Docs: https://docs.mistral.ai/
- Notes: Verified 2026-09-11. The free tier is real and permanent, but two conditions matter: phone verification is required, and free-tier traffic may be used for training unless you opt out in the console. PulseG surfaces both facts on the key card rather than burying them.

### OpenRouter (`openrouter`)

- Status: **verified** (checked 2026-09-11)
- Free tier: 28+ models carrying the ':free' suffix cost $0/token with no card. 20 requests/minute, 50 requests/day; a one-time $10 credit purchase raises the daily cap to 1,000 permanently.
- Limits: requests/minute: 20; requests/day: 50 (1,000 after a one-time $10 top-up); free_models: 28+
- Docs: https://openrouter.ai/docs/api-reference/limits
- Notes: Verified 2026-09-11. This is the sanctioned substitute for both Z.ai and Routeway. PulseG sends the recommended HTTP-Referer and X-Title attribution headers. The 50/day cap is low enough that the quota meter warns before an agent relies on it: it is a fallback, not a workhorse.

### SEA-LION (AI Singapore) (`sealion`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Free public-preview trial key for POC use; no card
- Limits: rate: trial limits apply and cannot be raised; quota: POC-scoped, not production
- Docs: https://docs.sea-lion.ai/
- Notes: Verified 2026-09-11: OpenAI-compatible, free trial keys are issued from the Playground, and the trial is explicitly scoped to proofs of concept and cannot be upgraded by paying. It is therefore a fallback, never a primary, for long-running projects.

### Stable Horde (`stable_horde`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Free forever, crowdsourced. Anonymous keys are accepted; a registered key earns kudos for higher priority
- Limits: anonymous: allowed but low priority; concurrency: one job at a time on low kudos
- Docs: https://stablehorde.net/api/
- Notes: Verified 2026-09-11: asynchronous by design - you submit a job, poll the id, then fetch the file. PulseG's adapter does that internally so the agent still just asks for an image. Anonymous use (key 0000000000) works out of the box; quality may be lower and queues longer than with a registered key.

### Telegram Bot (notifications) (`telegram`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Free. No limits that matter for notification volume
- Limits: note: bot tokens are per-bot; a single bot can notify several chats
- Docs: https://core.telegram.org/bots/api
- Notes: Verified 2026-09-11. Setup is inline in Settings: paste the BotFather token, press Test, and PulseG calls getMe then reads getUpdates to discover your chat id so you never have to hunt for it. Message contents never include code or keys - task id, title and why it needs you.

### YouTube Data API v3 (`youtube_data`)

- Status: **verified** (checked 2026-09-11)
- Free tier: 10,000 quota units per day, free, with a Google Cloud project
- Limits: units/day: 10000; search.list cost: 100 units per call
- Docs: https://developers.google.com/youtube/v3/determine_quota_cost
- Notes: Verified 2026-09-11: search.list costs 100 units, so the free 10,000 units is 100 searches a day - plenty for research, not for scraping. Crucially, the Data API does not return third-party captions, which is exactly why the Transcriptor uses yt-dlp for subtitles and keeps this provider for search metadata only.

### yt-dlp (`ytdlp`)

- Status: **verified** (checked 2026-09-11)
- Free tier: Free, no key, no account. Runs locally as a bundled binary
- Limits: note: bounded by network and by the source site's patience; PulseG rate-limits itself
- Docs: https://github.com/yt-dlp/yt-dlp#readme
- Notes: The keyless workhorse for subtitles and metadata. It is a local tool rather than a service, so it never needs a key and never appears in the BYOK list - but it is in this table so the Transcriptor's chain is introspectable in one place. PulseG ships with a wrapper that keeps yt-dlp's output in the project folder and never touches your browser cookies.

### Freesound (`freesound`)

- Status: **conditional** (checked 2026-09-11)
- Free tier: Free API key for non-commercial and commercial use alike; roughly half the library is CC0
- Limits: note: documented rate limits are generous but change; PulseG self-limits and prefers CC0
- Docs: https://freesound.org/docs/api/
- Notes: Verified 2026-09-11: the search API filters by licence, so the curator asks for CC0 first and CC-BY only if nothing suitable exists. CC-BY assets get an automatic credit line written to assets/audio/CREDITS.md in the same step that downloads them. Preview MP3s are downloadable with the API key; full-quality downloads need OAuth2, which the curation task asks the human for only when the preview is not good enough.

### Hugging Face Inference (`huggingface`)

- Status: **conditional** (checked 2026-09-11)
- Free tier: Free monthly inference credits on a free account; serverless text-to-image models include small pixel-art LoRAs
- Limits: credits: monthly, small; note: model availability on serverless varies
- Docs: https://huggingface.co/docs/api-inference/index
- Notes: Verified 2026-09-11. The interesting capability here is style-consistent pixel art via community LoRAs, which is why it sits in the image chain rather than replacing it. Serverless image models cold-start and occasionally 503; the adapter retries once and then falls through.

### NVIDIA NIM / build.nvidia.com (`nvidia_nim`)

- Status: **conditional** (checked 2026-09-11)
- Free tier: Free developer credits on signup; usable for prototyping without a card
- Limits: credits: a fixed credit grant, not an unlimited free tier; rate: per-model limits shown on each model card
- Docs: https://docs.api.nvidia.com/nim/
- Notes: Verified 2026-09-11 as an OpenAI-compatible endpoint with a credit grant. The grant is finite, so the Documenter's chain treats NIM as a primary only while credits last and the quota meter labels it 'credit-based, not perpetual'. Model ids rotate; Settings lists what /models returns for your account.

### OpenCode Zen (`opencode_zen`)

- Status: **conditional** (checked 2026-09-11)
- Free tier: Free preview models for coding (e.g. Grok Code Fast 1, Big Pickle) with an account
- Limits: note: free preview models rotate without notice
- Docs: https://opencode.ai/docs/zen
- Notes: Verified 2026-09-11 as an OpenAI-compatible endpoint with free preview coding models. Preview models are rotated and withdrawn by the vendor, so PulseG treats this as the last fallback before Needs Intervention and re-tests the model id whenever it is used.

### Pollinations (`pollinations`)

- Status: **conditional** (checked 2026-09-11)
- Free tier: No key on the legacy image endpoint (rate-limited, may watermark). A free registered key removes the watermark and raises limits
- Limits: anonymous: approximately one request every 15 seconds; watermark: possible without a key
- Docs: https://github.com/pollinations/pollinations
- Notes: Verified 2026-09-11: image.pollinations.ai/prompt/<prompt> still answers without a key, which makes zero-config image generation real. Two honest caveats shown in the UI: anonymous requests can be watermarked and are rate-limited, and the newer gen.pollinations.ai endpoints require a key. The adapter flags any watermarked result on the task so a human is never surprised by it in a shipped sprite.

### Routeway (`routeway`)

- Status: **substituted** (checked 2026-09-11)
- Free tier: Credits on signup, not a perpetual free tier. Terms were not confirmable from a first-party page on 2026-09-11
- Limits: note: unconfirmed
- Docs: https://docs.routeway.ai/
- Notes: The specification listed Routeway as a free fallback. We could not confirm a perpetual free tier from Routeway's own documentation, so we did the honest thing rather than guessing: the chain slot is carried by OpenRouter's free coding models, and Routeway remains configurable for anyone who has a key. Recorded in project-log/PROVIDER_VERIFICATION.md as a substitution, not a silent drop.
- Substituted by: openrouter (Routeway advertised signup credits rather than a perpetual free tier, and no first-party page confirmed ongoing free access.)

### Z.ai (GLM / Zhipu) (`zai`)

- Status: **substituted** (checked 2026-09-11)
- Free tier: No free tier for the coding models. GLM Coding Plan is a paid subscription (~$18/month Lite)
- Limits: requires_paid_plan: true
- Docs: https://docs.z.ai/devpack/faq
- Notes: Verified 2026-09-11 against Z.ai's own devpack FAQ: there is no free tier for GLM coding models, and the cheap Coding Plan is a paid subscription whose terms changed during 2025-2026. The specification listed Z.ai as a free fallback, so the sanctioned substitute (see SUBSTITUTIONS) carries that chain slot instead. If you buy a Z.ai plan, add the key and move Z.ai back to the primary - no code change is needed.
- Substituted by: openrouter (Z.ai has no free tier for GLM coding models; the cheap plan is a paid subscription. OpenRouter serves a free GLM variant plus other free coder models.)

## What was not verified

- **Exact Gemini free-tier request numbers.** Google does not publish them as a stable figure;
  AI Studio shows them per project. The app therefore never displays a number it cannot
  confirm and instead links to AI Studio.
- **NVIDIA NIM credit size.** The grant exists; its size changes with promotions, so the UI says
  "credit-based" rather than inventing a figure.
- **Routeway's ongoing free access.** No first-party page confirmed it, hence the substitution.
- **Z.ai free coding tier.** The devpack FAQ confirms there is none; the cheap plan is paid.


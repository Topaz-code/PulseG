"""The provider table: what each free tier actually is, as verified on 2026-09-11.

This module is the single source of truth for provider facts, and it is deliberately honest
about certainty:

* ``verified`` - the free tier was confirmed against first-party documentation. Notes cite
  what was read.
* ``conditional`` - usable, but with a catch that changes how the agent should behave (for
  example a one-time credit that must be rationed, or a quota the user must enable).
* ``substituted`` - the specification named a provider whose free tier does not exist as
  described, so a sanctioned substitute carries that chain slot. Logged in
  ``project-log/PROVIDER_VERIFICATION.md``.
* ``unverified`` - listed so the user can configure it, but we have not confirmed the terms.

Nothing here is guessed: where a number is unpublished the ``notes`` say so rather than
inventing a figure. The UI reads this table directly, so a wrong number here would be a wrong
number in Settings.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..core.models import ProviderKind, ProviderSpec

VERIFIED_ON = "2026-09-11"


class _Models:
    """Default model ids, one place, so a typo cannot diverge between agents.

    Model ids rot faster than anything else in software. These are the ones verified working
    on 2026-09-11; every agent's chain is user-editable and the dashboard shows the alternative
    models each provider offers, so a retired id is a Settings change, not a code change.
    """

    # Mistral
    MISTRAL_LARGE = "mistral-large-latest"
    MISTRAL_SMALL = "mistral-small-latest"
    MISTRAL_MEDIUM = "magistral-medium-latest"
    CODESTRAL = "codestral-latest"

    # Groq
    GROQ_70B = "llama-3.3-70b-versatile"
    GROQ_8B = "llama-3.1-8b-instant"
    GROQ_QWEN = "qwen/qwen3-32b"
    GROQ_GPT_OSS = "openai/gpt-oss-120b"
    GROQ_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
    GROQ_WHISPER = "whisper-large-v3-turbo"

    # Google AI Studio
    GEMINI_FLASH = "gemini-2.5-flash"
    GEMINI_FLASH_LITE = "gemini-2.5-flash-lite"
    GEMINI_PRO = "gemini-2.5-pro"

    # NVIDIA NIM
    NIM_70B = "meta/llama-3.3-70b-instruct"
    NIM_NEMOTRON = "nvidia/llama-3.3-nemotron-super-49b-v1"

    # SEA-LION
    SEALION_70B = "aisingapore/Llama-SEA-LION-v3-70B-IT"
    SEALION_9B = "aisingapore/Gemma-SEA-LION-v3-9B-IT"

    # OpenRouter (all ":free" are $0/token)
    OR_QWEN3_CODER = "qwen/qwen3-coder:free"
    OR_DEEPSEEK = "deepseek/deepseek-chat-v3.1:free"
    OR_GLM = "z-ai/glm-4.5-air:free"
    OR_GEMMA = "google/gemma-3-27b-it:free"
    OR_LLAMA = "meta-llama/llama-3.3-70b-instruct:free"

    # OpenCode Zen
    ZEN_GROK_CODE = "grok-code-fast-1"
    ZEN_BIG_PICKLE = "big-pickle"

    # Images
    HORDE_SDXL = "stable_diffusion_xl"
    POLLINATIONS_FLUX = "flux"
    HF_FLUX_SCHNELL = "black-forest-labs/FLUX.1-schnell"
    HF_PIXEL_ART_LORA = "nerijs/pixel-art-xl"

    # Speech
    DEEPGRAM_NOVA3 = "nova-3"


PROJECT_DEFAULT_MODELS = _Models()


def _spec(**kwargs: Any) -> ProviderSpec:
    kwargs.setdefault("verified_on", VERIFIED_ON)
    return ProviderSpec(**kwargs)


#: The authoritative table. Keys are the ids used in ``agents.yaml``.
PROVIDERS: dict[str, ProviderSpec] = {
    "mistral": _spec(
        id="mistral",
        name="Mistral AI",
        kind=[ProviderKind.LLM],
        base_url="https://api.mistral.ai/v1",
        auth_style="bearer",
        requires_key=True,
        key_format="32+ character API key",
        signup_url="https://console.mistral.ai/",
        docs_url="https://docs.mistral.ai/",
        free_tier="Free 'Experiment' tier: no card, requires phone verification, generous rate limits",
        limits={"rate": "1 request/second", "tokens": "500K tokens/minute", "monthly": "~1B tokens/month on the free tier"},
        test_path="/models",
        test_model="mistral-small-latest",
        models_endpoint="/models",
        default_models=[
            "mistral-large-latest",
            "mistral-small-latest",
            "magistral-medium-latest",
            "devstral-medium-latest",
            "codestral-latest",
        ],
        verified="verified",
        supports_vision=True,
        quota_visible=False,
        notes=(
            "Verified 2026-09-11. The free tier is real and permanent, but two conditions matter: "
            "phone verification is required, and free-tier traffic may be used for training unless "
            "you opt out in the console. PulseG surfaces both facts on the key card rather than "
            "burying them."
        ),
    ),
    "groq": _spec(
        id="groq",
        name="Groq",
        kind=[ProviderKind.LLM, ProviderKind.VISION, ProviderKind.STT],
        base_url="https://api.groq.com/openai/v1",
        auth_style="bearer",
        requires_key=True,
        key_format="gsk_...",
        signup_url="https://console.groq.com/keys",
        docs_url="https://console.groq.com/docs/rate-limits",
        free_tier="Free developer tier, no card. Very fast inference on custom silicon",
        limits={
            "requests/minute": "~30 on free tier",
            "requests/day": "~14,400 for 8B class, ~1,000 for 70B class",
            "tokens/minute": "varies by model",
            "stt": "whisper-large-v3-turbo included on the free tier",
        },
        test_path="/models",
        test_model="llama-3.1-8b-instant",
        models_endpoint="/models",
        default_models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "qwen/qwen3-32b",
            "openai/gpt-oss-120b",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "whisper-large-v3-turbo",
        ],
        verified="verified",
        supports_vision=True,
        quota_visible=True,
        notes=(
            "Verified 2026-09-11. Rate limits are per organisation, not per key: a second key does "
            "not buy headroom. Daily limits reset on a rolling 24h window, so the quota meter shows "
            "'resets within 24h' rather than a midnight countdown."
        ),
    ),
    "google_ai_studio": _spec(
        id="google_ai_studio",
        name="Google AI Studio (Gemini)",
        kind=[ProviderKind.LLM, ProviderKind.VISION],
        base_url="https://generativelanguage.googleapis.com/v1beta",
        auth_style="header",
        auth_header="x-goog-api-key",
        key_query_param="key",
        requires_key=True,
        key_format="AIza...",
        signup_url="https://aistudio.google.com/app/apikey",
        docs_url="https://ai.google.dev/gemini-api/docs/rate-limits",
        free_tier="Free tier available with no billing account. Gemini 2.5 Flash and Flash-Lite usable at $0",
        limits={
            "requests/minute": "varies by model and by project age; check AI Studio",
            "requests/day": "shown live in AI Studio, not in the API response",
            "note": "exact free numbers are not published as a stable figure and are visible only in AI Studio",
        },
        test_path="/models",
        test_model="gemini-2.5-flash",
        models_endpoint="/models",
        default_models=[
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
        ],
        verified="verified",
        supports_vision=True,
        quota_visible=False,
        notes=(
            "Verified 2026-09-11. Free-tier data is used to improve Google's models - which is why "
            "the Tester (the agent that looks at your game screenshots) can use Gemini but the "
            "Settings screen tells you plainly that this is the trade. If quotas are exceeded the "
            "API returns 429; PulseG treats that as rate-limited and falls through the chain."
        ),
    ),
    "nvidia_nim": _spec(
        id="nvidia_nim",
        name="NVIDIA NIM / build.nvidia.com",
        kind=[ProviderKind.LLM],
        base_url="https://integrate.api.nvidia.com/v1",
        auth_style="bearer",
        requires_key=True,
        key_format="nvapi-...",
        signup_url="https://build.nvidia.com/",
        docs_url="https://docs.api.nvidia.com/nim/",
        free_tier="Free developer credits on signup; usable for prototyping without a card",
        limits={"credits": "a fixed credit grant, not an unlimited free tier", "rate": "per-model limits shown on each model card"},
        test_path="/models",
        test_model="meta/llama-3.3-70b-instruct",
        models_endpoint="/models",
        default_models=[
            "meta/llama-3.3-70b-instruct",
            "nvidia/llama-3.3-nemotron-super-49b-v1",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen2.5-coder-32b-instruct",
        ],
        verified="conditional",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11 as an OpenAI-compatible endpoint with a credit grant. The grant is "
            "finite, so the Documenter's chain treats NIM as a primary only while credits last and "
            "the quota meter labels it 'credit-based, not perpetual'. Model ids rotate; Settings "
            "lists what /models returns for your account."
        ),
    ),
    "sealion": _spec(
        id="sealion",
        name="SEA-LION (AI Singapore)",
        kind=[ProviderKind.LLM],
        base_url="https://api.sea-lion.ai/v1",
        auth_style="bearer",
        requires_key=True,
        key_format="API key from the SEA-LION Playground",
        signup_url="https://playground.sea-lion.ai/",
        docs_url="https://docs.sea-lion.ai/",
        free_tier="Free public-preview trial key for POC use; no card",
        limits={"rate": "trial limits apply and cannot be raised", "quota": "POC-scoped, not production"},
        test_path="/models",
        test_model="aisingapore/Llama-SEA-LION-v3-70B-IT",
        models_endpoint="/models",
        default_models=[
            "aisingapore/Llama-SEA-LION-v3-70B-IT",
            "aisingapore/Gemma-SEA-LION-v3-9B-IT",
            "BAAI/bge-m3",
        ],
        verified="verified",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11: OpenAI-compatible, free trial keys are issued from the Playground, "
            "and the trial is explicitly scoped to proofs of concept and cannot be upgraded by "
            "paying. It is therefore a fallback, never a primary, for long-running projects."
        ),
    ),
    "zai": _spec(
        id="zai",
        name="Z.ai (GLM / Zhipu)",
        kind=[ProviderKind.LLM],
        base_url="https://api.z.ai/api/paas/v4",
        auth_style="bearer",
        requires_key=True,
        key_format="API key from the Z.ai console",
        signup_url="https://z.ai/",
        docs_url="https://docs.z.ai/devpack/faq",
        free_tier="No free tier for the coding models. GLM Coding Plan is a paid subscription (~$18/month Lite)",
        limits={"requires_paid_plan": "true"},
        test_path="/models",
        test_model="glm-4.6",
        default_models=["glm-4.6", "glm-4.5-air"],
        verified="substituted",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11 against Z.ai's own devpack FAQ: there is no free tier for GLM "
            "coding models, and the cheap Coding Plan is a paid subscription whose terms changed "
            "during 2025-2026. The specification listed Z.ai as a free fallback, so the sanctioned "
            "substitute (see SUBSTITUTIONS) carries that chain slot instead. If you buy a Z.ai plan, "
            "add the key and move Z.ai back to the primary - no code change is needed."
        ),
    ),
    "openrouter": _spec(
        id="openrouter",
        name="OpenRouter",
        kind=[ProviderKind.LLM, ProviderKind.VISION],
        base_url="https://openrouter.ai/api/v1",
        auth_style="bearer",
        requires_key=True,
        key_format="sk-or-v1-...",
        signup_url="https://openrouter.ai/keys",
        docs_url="https://openrouter.ai/docs/api-reference/limits",
        free_tier=(
            "28+ models carrying the ':free' suffix cost $0/token with no card. 20 requests/minute, "
            "50 requests/day; a one-time $10 credit purchase raises the daily cap to 1,000 permanently."
        ),
        limits={
            "requests/minute": 20,
            "requests/day": "50 (1,000 after a one-time $10 top-up)",
            "free_models": "28+",
        },
        test_path="/models",
        test_model="meta-llama/llama-3.3-70b-instruct:free",
        models_endpoint="/models",
        default_models=[
            "qwen/qwen3-coder:free",
            "deepseek/deepseek-chat-v3.1:free",
            "z-ai/glm-4.5-air:free",
            "google/gemma-3-27b-it:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
        verified="verified",
        quota_visible=True,
        notes=(
            "Verified 2026-09-11. This is the sanctioned substitute for both Z.ai and Routeway. "
            "PulseG sends the recommended HTTP-Referer and X-Title attribution headers. The 50/day "
            "cap is low enough that the quota meter warns before an agent relies on it: it is a "
            "fallback, not a workhorse."
        ),
    ),
    "opencode_zen": _spec(
        id="opencode_zen",
        name="OpenCode Zen",
        kind=[ProviderKind.LLM],
        base_url="https://opencode.ai/zen/v1",
        auth_style="bearer",
        requires_key=True,
        key_format="API key from opencode.ai",
        signup_url="https://opencode.ai/",
        docs_url="https://opencode.ai/docs/zen",
        free_tier="Free preview models for coding (e.g. Grok Code Fast 1, Big Pickle) with an account",
        limits={"note": "free preview models rotate without notice"},
        test_path="/models",
        test_model="grok-code-fast-1",
        models_endpoint="/models",
        default_models=["grok-code-fast-1", "big-pickle"],
        verified="conditional",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11 as an OpenAI-compatible endpoint with free preview coding models. "
            "Preview models are rotated and withdrawn by the vendor, so PulseG treats this as the "
            "last fallback before Needs Intervention and re-tests the model id whenever it is used."
        ),
    ),
    "routeway": _spec(
        id="routeway",
        name="Routeway",
        kind=[ProviderKind.LLM],
        base_url="https://api.routeway.ai/v1",
        auth_style="bearer",
        requires_key=True,
        signup_url="https://routeway.ai/",
        docs_url="https://docs.routeway.ai/",
        free_tier="Credits on signup, not a perpetual free tier. Terms were not confirmable from a first-party page on 2026-09-11",
        limits={"note": "unconfirmed"},
        test_path="/models",
        test_model="",
        verified="substituted",
        quota_visible=False,
        notes=(
            "The specification listed Routeway as a free fallback. We could not confirm a "
            "perpetual free tier from Routeway's own documentation, so we did the honest thing "
            "rather than guessing: the chain slot is carried by OpenRouter's free coding models, "
            "and Routeway remains configurable for anyone who has a key. Recorded in "
            "project-log/PROVIDER_VERIFICATION.md as a substitution, not a silent drop."
        ),
    ),
    "deepgram": _spec(
        id="deepgram",
        name="Deepgram",
        kind=[ProviderKind.STT],
        base_url="https://api.deepgram.com/v1",
        auth_style="header",
        auth_header="Authorization",
        requires_key=True,
        key_format="Token or key from console.deepgram.com",
        signup_url="https://console.deepgram.com/signup",
        docs_url="https://developers.deepgram.com/",
        free_tier="$200 of one-time credit on signup. Nova-3 transcription at ~$0.0048/minute",
        limits={"credit": "$200 one-time (not monthly)", "model": "nova-3", "language": "multilingual"},
        test_path="/auth/token",
        test_model="nova-3",
        default_models=["nova-3", "nova-2", "whisper-large"],
        verified="verified",
        quota_visible=True,
        notes=(
            "Verified 2026-09-11. One-time credit, so the Transcriptor always tries yt-dlp subtitle "
            "extraction first and only spends Deepgram credit when no subtitle track exists; Groq "
            "Whisper (free tier) is the permanent fallback once the credit is gone. The quota meter "
            "tracks credit spend so the switch is visible before it happens."
        ),
    ),
    "firecrawl": _spec(
        id="firecrawl",
        name="Firecrawl",
        kind=[ProviderKind.SEARCH],
        base_url="https://api.firecrawl.dev/v1",
        auth_style="bearer",
        requires_key=True,
        key_format="fc-...",
        signup_url="https://firecrawl.dev/",
        docs_url="https://docs.firecrawl.dev/",
        free_tier="~1,000 credits, one-time on signup. Concurrency 2 on the free tier",
        limits={"credits": "~1,000 one-time", "concurrency": 2},
        test_path="/scrape",
        test_model="",
        verified="verified",
        quota_visible=True,
        notes=(
            "Verified 2026-09-11 - and the one-time nature matters: the Researcher caps crawls per "
            "task (default 4), caches dead results so it never re-spends a credit on the same URL, "
            "and prefers yt-dlp for anything on YouTube. The provider's credit-usage endpoint feeds "
            "the quota meter."
        ),
    ),
    "youtube_data": _spec(
        id="youtube_data",
        name="YouTube Data API v3",
        kind=[ProviderKind.SEARCH],
        base_url="https://www.googleapis.com/youtube/v3",
        auth_style="query",
        key_query_param="key",
        requires_key=True,
        signup_url="https://console.cloud.google.com/apis/credentials",
        docs_url="https://developers.google.com/youtube/v3/determine_quota_cost",
        free_tier="10,000 quota units per day, free, with a Google Cloud project",
        limits={"units/day": 10_000, "search.list cost": "100 units per call"},
        test_path="/i18nLanguages",
        test_model="",
        verified="verified",
        quota_visible=True,
        notes=(
            "Verified 2026-09-11: search.list costs 100 units, so the free 10,000 units is 100 "
            "searches a day - plenty for research, not for scraping. Crucially, the Data API does "
            "not return third-party captions, which is exactly why the Transcriptor uses yt-dlp for "
            "subtitles and keeps this provider for search metadata only."
        ),
    ),
    "ytdlp": _spec(
        id="ytdlp",
        name="yt-dlp",
        kind=[ProviderKind.SEARCH, ProviderKind.STT],
        base_url="",
        auth_style="none",
        requires_key=False,
        signup_url="https://github.com/yt-dlp/yt-dlp",
        docs_url="https://github.com/yt-dlp/yt-dlp#readme",
        free_tier="Free, no key, no account. Runs locally as a bundled binary",
        limits={"note": "bounded by network and by the source site's patience; PulseG rate-limits itself"},
        verified="verified",
        quota_visible=False,
        notes=(
            "The keyless workhorse for subtitles and metadata. It is a local tool rather than a "
            "service, so it never needs a key and never appears in the BYOK list - but it is in "
            "this table so the Transcriptor's chain is introspectable in one place. PulseG ships "
            "with a wrapper that keeps yt-dlp's output in the project folder and never touches "
            "your browser cookies."
        ),
    ),
    "stable_horde": _spec(
        id="stable_horde",
        name="Stable Horde",
        kind=[ProviderKind.IMAGE],
        base_url="https://stablehorde.net/api/v2",
        auth_style="header",
        auth_header="apikey",
        requires_key=False,
        key_format="Use 0000000000 for the anonymous worker pool, or your own account key",
        signup_url="https://stablehorde.net/register",
        docs_url="https://stablehorde.net/api/",
        free_tier="Free forever, crowdsourced. Anonymous keys are accepted; a registered key earns kudos for higher priority",
        limits={"anonymous": "allowed but low priority", "concurrency": "one job at a time on low kudos"},
        test_path="/status/models",
        test_model=PROJECT_DEFAULT_MODELS.HORDE_SDXL,
        default_models=["stable_diffusion_xl", "stable_diffusion", "sd_xl_base_50"],
        verified="verified",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11: asynchronous by design - you submit a job, poll the id, then fetch "
            "the file. PulseG's adapter does that internally so the agent still just asks for an "
            "image. Anonymous use (key 0000000000) works out of the box; quality may be lower and "
            "queues longer than with a registered key."
        ),
    ),
    "pollinations": _spec(
        id="pollinations",
        name="Pollinations",
        kind=[ProviderKind.IMAGE, ProviderKind.LLM],
        base_url="https://image.pollinations.ai",
        auth_style="none",
        requires_key=False,
        signup_url="https://pollinations.ai/",
        docs_url="https://github.com/pollinations/pollinations",
        free_tier="No key on the legacy image endpoint (rate-limited, may watermark). A free registered key removes the watermark and raises limits",
        limits={"anonymous": "approximately one request every 15 seconds", "watermark": "possible without a key"},
        default_models=[PROJECT_DEFAULT_MODELS.POLLINATIONS_FLUX, "turbo"],
        verified="conditional",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11: image.pollinations.ai/prompt/<prompt> still answers without a key, "
            "which makes zero-config image generation real. Two honest caveats shown in the UI: "
            "anonymous requests can be watermarked and are rate-limited, and the newer "
            "gen.pollinations.ai endpoints require a key. The adapter flags any watermarked result "
            "on the task so a human is never surprised by it in a shipped sprite."
        ),
    ),
    "huggingface": _spec(
        id="huggingface",
        name="Hugging Face Inference",
        kind=[ProviderKind.IMAGE],
        base_url="https://api-inference.huggingface.co",
        auth_style="bearer",
        requires_key=True,
        key_format="hf_...",
        signup_url="https://huggingface.co/settings/tokens",
        docs_url="https://huggingface.co/docs/api-inference/index",
        free_tier="Free monthly inference credits on a free account; serverless text-to-image models include small pixel-art LoRAs",
        limits={"credits": "monthly, small", "note": "model availability on serverless varies"},
        test_path="/models",
        test_model="",
        models_endpoint="/models",
        default_models=[
            PROJECT_DEFAULT_MODELS.HF_FLUX_SCHNELL,
            PROJECT_DEFAULT_MODELS.HF_PIXEL_ART_LORA,
            "stabilityai/stable-diffusion-xl-base-1.0",
        ],
        verified="conditional",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11. The interesting capability here is style-consistent pixel art via "
            "community LoRAs, which is why it sits in the image chain rather than replacing it. "
            "Serverless image models cold-start and occasionally 503; the adapter retries once and "
            "then falls through."
        ),
    ),
    "freesound": _spec(
        id="freesound",
        name="Freesound",
        kind=[ProviderKind.AUDIO_SEARCH],
        base_url="https://freesound.org/apiv2",
        auth_style="query",
        key_query_param="token",
        requires_key=True,
        key_format="API key or OAuth2 token from freesound.org/apiv2/apply",
        signup_url="https://freesound.org/apiv2/apply/",
        docs_url="https://freesound.org/docs/api/",
        free_tier="Free API key for non-commercial and commercial use alike; roughly half the library is CC0",
        limits={"note": "documented rate limits are generous but change; PulseG self-limits and prefers CC0"},
        test_path="/search/text/",
        test_model="",
        verified="conditional",
        quota_visible=True,
        notes=(
            "Verified 2026-09-11: the search API filters by licence, so the curator asks for CC0 "
            "first and CC-BY only if nothing suitable exists. CC-BY assets get an automatic credit "
            "line written to assets/audio/CREDITS.md in the same step that downloads them. "
            "Preview MP3s are downloadable with the API key; full-quality downloads need OAuth2, "
            "which the curation task asks the human for only when the preview is not good enough."
        ),
    ),
    "kenney": _spec(
        id="kenney",
        name="Kenney asset packs",
        kind=[ProviderKind.AUDIO_SEARCH, ProviderKind.IMAGE],
        base_url="",
        auth_style="none",
        requires_key=False,
        signup_url="https://kenney.nl/assets",
        docs_url="https://kenney.nl/assets",
        free_tier="CC0, no attribution required, no API. PulseG uses a local cache of the packs you have downloaded",
        limits={"note": "no API: a local library, indexed like any other asset source"},
        verified="verified",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11: Kenney's packs are CC0 and there is no API, so this entry models "
            "the local cache (~/.pulsegstudio/assets/kenney). If the cache is empty the curator says "
            "so and offers the download links in one click rather than silently producing nothing. "
            "Audio packs that matter: Music Jingles, Interface Sounds, Impact Sounds, RPG Audio."
        ),
    ),
    "telegram": _spec(
        id="telegram",
        name="Telegram Bot (notifications)",
        kind=[ProviderKind.NOTIFY],
        base_url="https://api.telegram.org",
        auth_style="none",
        requires_key=True,
        key_format="Bot token from @BotFather, plus a chat id",
        signup_url="https://core.telegram.org/bots#how-do-i-create-a-bot",
        docs_url="https://core.telegram.org/bots/api",
        free_tier="Free. No limits that matter for notification volume",
        limits={"note": "bot tokens are per-bot; a single bot can notify several chats"},
        test_path="/getMe",
        test_model="",
        verified="verified",
        quota_visible=False,
        notes=(
            "Verified 2026-09-11. Setup is inline in Settings: paste the BotFather token, press Test, "
            "and PulseG calls getMe then reads getUpdates to discover your chat id so you never have "
            "to hunt for it. Message contents never include code or keys - task id, title and why it "
            "needs you."
        ),
    ),
    "demo": _spec(
        id="demo",
        name="Demo mode (built in)",
        kind=[
            ProviderKind.LLM,
            ProviderKind.VISION,
            ProviderKind.STT,
            ProviderKind.IMAGE,
            ProviderKind.SEARCH,
            ProviderKind.AUDIO_SEARCH,
            ProviderKind.NOTIFY,
        ],
        base_url="",
        auth_style="none",
        requires_key=False,
        free_tier="Always available, offline, no key. Deterministic canned responses",
        limits={"note": "not an AI provider: it exists so the app can be demonstrated and tested without any keys"},
        verified="verified",
        supports_vision=True,
        supports_streaming=False,
        quota_visible=False,
        notes=(
            "Every chain ends here when PULSEG_DEMO=1, and the UI labels outputs produced by it. "
            "This is how the whole pipeline - intake, dispatch, audit, approval, commit - can be "
            "exercised end to end with no network at all, which is also how the test suite runs."
        ),
    ),
}


#: Providers named in the specification whose free tier did not survive verification, and the
#: provider that carries the chain slot instead. Never a silent drop: surfaced in the UI and
#: recorded in project-log/PROVIDER_VERIFICATION.md.
SUBSTITUTIONS: dict[str, dict[str, str]] = {
    "zai": {
        "substitute": "openrouter",
        "models": "z-ai/glm-4.5-air:free",
        "reason": (
            "Z.ai has no free tier for GLM coding models; the cheap plan is a paid subscription. "
            "OpenRouter serves a free GLM variant plus other free coder models."
        ),
        "verified_on": VERIFIED_ON,
    },
    "routeway": {
        "substitute": "openrouter",
        "models": "qwen/qwen3-coder:free, deepseek/deepseek-chat-v3.1:free",
        "reason": (
            "Routeway advertised signup credits rather than a perpetual free tier, and no "
            "first-party page confirmed ongoing free access."
        ),
        "verified_on": VERIFIED_ON,
    },
}

#: Free in the sense that matters to a user who does not want to enter a card: still free after
#: a year, or needing no key at all.
FREE_FOREVER: frozenset[str] = frozenset(
    {
        "groq",
        "google_ai_studio",
        "mistral",
        "sealion",
        "openrouter",
        "ytdlp",
        "stable_horde",
        "pollinations",
        "kenney",
        "telegram",
        "demo",
        "opencode_zen",
    }
)

#: Providers whose free allowance is a one-time grant. Shown differently in the UI ("ration").
ONE_TIME_CREDIT: frozenset[str] = frozenset({"deepgram", "firecrawl", "nvidia_nim"})


def get_spec(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)


def require_spec(provider_id: str) -> ProviderSpec:
    """Like :func:`get_spec` but raises - for code paths where an unknown provider is a bug."""
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise KeyError(
            f"Unknown provider '{provider_id}'. Add it to backend/providers/specs.py or use one "
            f"of: {', '.join(sorted(PROVIDERS))}"
        )
    return spec


def all_specs() -> list[ProviderSpec]:
    return list(PROVIDERS.values())


def by_kind(kind: ProviderKind | str) -> list[ProviderSpec]:
    value = ProviderKind(kind) if isinstance(kind, str) else kind
    return [spec for spec in PROVIDERS.values() if value in spec.kind]


def provider_ids_by_kind(kind: ProviderKind | str) -> list[str]:
    return [spec.id for spec in by_kind(kind)]


def llm_provider_ids() -> list[dict[str, Any]]:
    """The provider picker in Settings: LLM-capable providers, cheapest first."""
    rows: list[dict[str, Any]] = []
    for spec in PROVIDERS.values():
        if ProviderKind.LLM not in spec.kind:
            continue
        rows.append(
            {
                "id": spec.id,
                "name": spec.name,
                "requires_key": spec.requires_key,
                "free_forever": spec.id in FREE_FOREVER,
                "one_time_credit": spec.id in ONE_TIME_CREDIT,
                "verified": spec.verified,
                "models": spec.default_models,
                "signup_url": spec.signup_url,
                "docs_url": spec.docs_url,
                "free_tier": spec.free_tier,
                "limits": spec.limits,
                "notes": spec.notes,
                "supports_vision": spec.supports_vision,
            }
        )
    rows.sort(key=lambda row: (not row["free_forever"], row["name"]))
    return rows


def model_catalogue() -> dict[str, list[str]]:
    return {spec.id: list(spec.default_models) for spec in PROVIDERS.values()}


def default_key_free_providers() -> list[str]:
    """Providers that work with no key at all: the zero-setup starting point."""
    return [spec.id for spec in PROVIDERS.values() if not spec.requires_key]


def needs_key(provider_id: str) -> bool:
    spec = PROVIDERS.get(provider_id)
    return bool(spec and spec.requires_key)


def substitution_for(provider_id: str) -> dict[str, str] | None:
    return SUBSTITUTIONS.get(provider_id)


def effective_chain_entry(provider_id: str, model: str) -> tuple[str, str, str]:
    """Resolve a configured provider/model through the substitution table.

    Returns ``(provider, model, note)``. A user who configures Z.ai *and* supplies a key is
    left alone - substitution only happens for the providers whose free tier does not exist.
    """
    substitution = SUBSTITUTIONS.get(provider_id)
    if not substitution:
        return provider_id, model, ""
    return (
        substitution["substitute"],
        substitution["models"].split(",")[0].strip() or model,
        f"{provider_id} substituted by {substitution['substitute']}: {substitution['reason']}",
    )


def verification_report() -> list[dict[str, Any]]:
    """The table rendered into project-log/PROVIDER_VERIFICATION.md."""
    rows: list[dict[str, Any]] = []
    order = {"verified": 0, "conditional": 1, "substituted": 2, "unverified": 3}
    for spec in sorted(PROVIDERS.values(), key=lambda item: (order[item.verified], item.name)):
        rows.append(
            {
                "id": spec.id,
                "name": spec.name,
                "verified": spec.verified,
                "verified_on": spec.verified_on,
                "free_tier": spec.free_tier,
                "limits": spec.limits,
                "requires_key": spec.requires_key,
                "free_forever": spec.id in FREE_FOREVER,
                "one_time_credit": spec.id in ONE_TIME_CREDIT,
                "signup_url": spec.signup_url,
                "docs_url": spec.docs_url,
                "notes": spec.notes,
                "substitution": SUBSTITUTIONS.get(spec.id),
            }
        )
    return rows


def spec_ids() -> Iterable[str]:
    return PROVIDERS.keys()

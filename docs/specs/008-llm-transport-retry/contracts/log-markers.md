# Marker contract: LLM transport retry

## Required markers

- `[LLM_TRANSPORT_RETRY] provider=<name> attempt=<n> reason=<type>`
- `[LLM_TRANSPORT_FAILED] provider=<name> attempts=<n> reason=<type>`
- `[LLM_DEBUG_CAPTURE] stage=<recommender> artifact=<request|response>`

## Forbidden behavior

- Never log API keys, cookies, proxy URLs, or session UUIDs.
- Never retry indefinitely.
- Never emit a successful response artifact when transport returned no response.
- Never use `LLM_JSON_INVALID` for a transport-only failure.

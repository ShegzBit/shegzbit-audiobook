---
name: external_apis
description: "Access external APIs through Replit-managed billing"
---

# External APIs

This skill provides access to external APIs through Replit-managed
passthrough billing. Requests are proxied through OpenInt with
managed credentials.

## Recommended workflow

1. Open the connector reference for request and response details.
2. Call `externalApi__<connector_name>` from `code_execution`.
3. Use `query` for URL parameters and parse `result.body`.
4. For media URLs, save files under `attached_assets/` and present them.

## Available APIs

- [Brave](references/brave.md) - Search real web image results through Brave passthrough billing.
- [Browserbase](references/browserbase.md)
- [ElevenLabs](references/elevenlabs.md) - Text-to-speech, music, and audio tools through ElevenLabs passthrough billing.
- [Exa](references/exa.md) - Semantic web search through Exa passthrough billing.
- [fal.ai](references/falai.md) - Bria RMBG background removal through fal.ai passthrough billing.
- [Firecrawl](references/firecrawl.md) - Scrape, crawl, and search the web through Firecrawl passthrough billing.
- [LlamaIndex](references/llamaindex.md)
- [Mindee](references/mindee.md)
- [Quiver AI](references/quiver_ai.md)
- [Shotstack](references/shotstack.md)
- [Tripo3D](references/tripo3d.md)
- [X (Twitter)](references/x.md) - Read-only X API v2 access through passthrough billing.

# Model providers

TATC AI works with ASU Voyager and CreateAI, your own API keys, or a ChatGPT plan on a local install. You do not need ASU credentials to use the other providers.

Every model in the picker names who runs it and who pays. Each request goes only to the provider you selected. If that provider fails, you see its error, and TATC AI never switches to another provider.

| Picker group | Credential | Who pays | Available on |
| --- | --- | --- | --- |
| ASU Voyager, ASU CreateAI | Keys set by whoever runs the install | ASU | Local and deployed |
| OpenAI, Anthropic, Google, and OpenRouter API key | Your own API key | Your account with that provider | Local and deployed |
| ChatGPT plan (local only) | Sign in with ChatGPT | Your plan limits | Local only |

## Run locally without ASU credentials

```bash
cp apps/librechat/.env.example apps/librechat/.env
chmod 600 apps/librechat/.env
docker compose -f docker-compose.dev.yml up --build
```

Leave `VOYAGER_API_KEY` and `CREATEAI_API_KEY` as `replace-me`. ASU models stay in the picker and return an authentication error if you select them, so choose a model from another group. Open `http://localhost:3080` and create an account.

## Use your own API key

1. Open **Settings**, then **Data & Privacy**, then **Provider API keys**, and select **Manage**.
2. Select **Update** next to the provider and paste your key.
3. Choose how long to keep it. The default is 12 hours. When a key expires, chat shows an error until you add it again.
4. Choose a model from that provider's group.

LibreChat encrypts each key and keeps it with your account, so other accounts cannot use it. LibreChat 0.8.7 shows "Your key will never expire" even when no key is saved. If no key is saved, sending a message shows "No key found".

The model lists are short and chosen by hand. To offer another model, add it to `modelSpecs` in both LibreChat files. For OpenAI, Anthropic, and Google, also add it to the `*_MODELS` settings in both compose files. For OpenRouter, add it to that endpoint's `models` list. Set `maxContextTokens` if LibreChat does not know the model, because the TATC tool definitions alone are too large for its small default.

Claude Haiku 5.5 is not offered because LibreChat 0.8.7 sends it a thinking setting that Anthropic's newer models do not accept.

## Use a ChatGPT plan

This uses OpenAI's [Sign in with ChatGPT](https://developers.openai.com/siwc) for locally hosted open-source apps. It is available to eligible ChatGPT Plus and Pro subscribers. It does not read your Codex CLI login.

1. Start the development stack and open `http://127.0.0.1:8788`.
2. Select **Continue with ChatGPT** and approve access in your browser.
3. Copy the connection key from that page.
4. In TATC AI, add it as the **ChatGPT Plan** key under **Provider API keys**.
5. Choose a model from **ChatGPT plan (local only)**.

Usage counts toward your existing plan limits. When a limit is reached, chat shows an error with a link to [Manage usage](https://chatgpt.com/settings/usage). In that page, the setting "Allow other apps to use credits after reaching your usage limit" decides whether connected apps can spend credits, so check it if you do not want that.

### Limits

- **Local only.** OpenAI requires an [interest form](https://developers.openai.com/siwc/token-sharing-open-source) for remotely hosted apps, so `docker-compose.deploy.yml` does not include this service.
- **One ChatGPT account per install.** The `chatgpt-bridge` service holds one sign-in. Anyone with the connection key can use that plan, so keep the key private. To use a different account, disconnect first.
- **Text, images, and function tools only.** OpenAI's plan route does not accept other input or hosted tools, and it [rejects settings](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) such as temperature and maximum output tokens, so the service does not send them.

### How it works

`apps/chatgpt-bridge` runs the sign-in page, keeps the OAuth tokens in an owner-only file in the `chatgpt-bridge-data` volume, and translates LibreChat's Chat Completions requests into streaming Responses requests. LibreChat only holds the connection key, never the OAuth tokens. The port is published on `127.0.0.1` only.

To run its tests:

```bash
pip install -r apps/chatgpt-bridge/requirements.txt pytest
cd apps/chatgpt-bridge && PYTHONPATH=. python -m pytest tests
```

## VPN

ASU documents the VPN as a requirement for the Voyager account portal, where keys are managed. Using an existing key does not need it: on 2026-11-04, listing models, chat, and a tool call all worked through a local install with no VPN connected. The other providers do not involve ASU or its VPN.

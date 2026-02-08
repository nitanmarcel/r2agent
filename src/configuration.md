# Configuration

Configuration file: `~/.config/r2agent/config.yaml`
  * `C:\Users\<YourUsername>\AppData\Roaming\r2agent\config.yaml` (windows)

## Fields

Mandatory fields:
  * `default_provider` - must match one of the provider names you've defined in the `providers` section.
  * `providers` -> `<provider_name>` -> `model` - the model identifier in [LiteLLM format](https://docs.litellm.ai/docs/providers).

Optional fields:
  * `providers` -> `<provider_name>` -> `api_key` - your API key for authentication.
  * `providers` -> `<provider_name>` -> `base_url` - custom base URL for the provider.
  * `providers` -> `<provider_name>` -> `extra_headers` - additional headers to send with requests.
  * `allow_r2cmd` - allow the AI to execute radare2 commands directly.


# r2agent

AI-powered reverse engineering assistant for radare2.

> **Note:** Currently only Linux and macOS are supported. Windows support is planned for a future release.

## Installation

### 1. Install r2agent

```bash
pipx install git+https://github.com/nitanmarcel/r2agent.git
```

### 2. Install the radare2 plugin

```bash
curl -o ~/.local/share/radare2/plugins/r2plugin.py \
  https://raw.githubusercontent.com/nitanmarcel/r2agent/refs/heads/main/r2plugin.py
```

## Configuration

Configuration file: `~/.config/r2agent/config.yaml`

- `default_provider` - Provider to use from the providers list (default: `"default"`)
- `allow_r2cmd` - Allow AI to execute radare2 commands (default: `false`)
  - might cause token limit issues. (todo: premade plugins to control the output lenght).
- `providers` - Map of provider configurations
  - `model` - Model in [LiteLLM](https://docs.litellm.ai/docs/providers) format (required)
  - `api_key` - API key for the provider (default: `null`)
  - `base_url` - Custom base URL (default: `https://api.openai.com/v1`)
- `server`
  - `socket_path` - Custom Unix socket path (default: `/tmp/r2agent-{uid}.sock`)

Example:

```yaml
default_provider: default
allow_r2cmd: true
providers:
  default:
    model: openai/gpt-4o-mini
    api_key: your-api-key
```

## Usage

Inside radare2:

```
[0x00001000]> r2a find all string references
```

### Commands

- `r2a <prompt>` - Ask the AI a question
- `r2a?` - Show help
- `r2av` - Show version info
- `r2as` - Check server status
- `r2aS` - Start server
- `r2a-` - Stop server

Press `Ctrl+C` to cancel a streaming response.

## License

MIT

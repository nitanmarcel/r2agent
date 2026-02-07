# r2agent

AI-powered reverse engineering assistant for radare2.

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
- `providers` - Map of provider configurations
  - `model` - Model in [LiteLLM](https://docs.litellm.ai/docs/providers) format (required)
  - `api_key` - API key for the provider (default: `null`)
  - `base_url` - Custom base URL (default: `https://api.openai.com/v1`)
  - `extra_headers` - Extra headers to send with requests (default: null)

Example:

```yaml
default_provider: copilot
providers:
  copilot:
    model: github_copilot/gpt-4
    extra_headers:
      Editor-Version: "vscode/1.109.0"
      Copilot-Integration-Id: "vscode-chat"
      x-github-api-version: "2025-05-01"
      
allow_r2cmd: true
```

## Usage

Inside radare2:

```
export R2AGENT_AUTOSTART=1 # optional, set to 1 to enable agent autostart when r2 starts
$ r2 /bin/ls
[0x00001000]> r2a find all string references
```

### Commands

- `r2a <prompt>` - Ask the AI a question
- `r2a?` - Show help
- `r2av` - Show version info
- `r2as` - List sessions for current binary
- `r2as*` - List all sessions
- `r2as <id>` - Switch to session
- `r2aS` - Create new session
- `r2as- <id>` - Delete session
- `r2as?` - Show current session
Press `Ctrl+C` to cancel a streaming response.

## License

MIT

# Radare2 plugin

## Environment variables

* `R2AGENT_AUTOSTART` - auto start r2agent when the radare2 session opens.

## Usage

0. Open a binary with radare2 `r2 /bin/ls`
1. Use `r2` followed by your question `[0x00001000]> r2a find all string references`
    * Press `Ctrl+C` to cancel a streaming response.

## Commands
- [Session Management](./commands/sessions.md)


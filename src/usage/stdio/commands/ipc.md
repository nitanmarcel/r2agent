# Tool Calls (IPC)

The server may request the client to execute radare2 commands.

**Server → Client (notification):**
```json
{
  jsonrpc: 2.0,
  method: tool_call,
  params: {
    id: 1,
    name: r2cmd,
    args: {command: pdf @ main}
  }
}
```
**Client → Server (notification):**
```json
{
  jsonrpc: 2.0,
  method: tool_result,
  params: {
    id: 1,
    result: ... disassembly output ...
  }
}
```

# Management

## Initialize
Handshake to establish connection and exchange version info.

**Request**:

```json
{
  jsonrpc: 2.0,
  method: initialize,
  params: {
    client_version: 0.3.2,
    binary_name: example.bin
  },
  id: 1
}
```

**Response**:

```json
{
  jsonrpc: 2.0,
  result: {
    server_version: 0.3.2,
    protocol_version: 1.0
  },
  id: 1
}
```

## ping
Health check.

**Request**:

```json
{
  jsonrpc: 2.0,
  method: ping,
  id: 2
}
```

**Response**:

```json
{
  jsonrpc: 2.0,
  result: pong,
  id: 2
}
```

## ask
Send a prompt to the AI agent. The server streams responses via notifications before sending the final result.

**Request**:
```json
{
  jsonrpc: 2.0,
  method: ask,
  params: {prompt: What does the main function do?},
  id: 3
}
```
**Stream notifications (server → client):**

```json
{jsonrpc: 2.0, method: stream, params: {type: agent_start, data: {name: RAgent}}}
{jsonrpc: 2.0, method: stream, params: {type: text_delta, data: {delta: The main }}}
{jsonrpc: 2.0, method: stream, params: {type: text_delta, data: {delta: function...}}}
{jsonrpc: 2.0, method: stream, params: {type: tool_call, data: {name: decompile, args: {...}}}}
```

**Final response**:
```json
{jsonrpc: 2.0, result: The main function..., id: 3}
```

## cancel
Cancel the current ask request. This is a notification (no response).
  * The pending ask will return with result "[cancelled]".

```json
{jsonrpc: 2.0, method: cancel}
```

## shutdown
Gracefully stop the server.

**Request**:
```json
{jsonrpc: 2.0, method: shutdown, id: 4}
```
**Response**:
```json
{jsonrpc: 2.0, result: ok, id: 4}
```

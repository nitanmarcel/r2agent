# Stdio Protocol

## Usage 

```bash
r2a stdio
```

## Message Format

Each message follows the JSON-RPC 2.0 (https://www.jsonrpc.org/specification) specification.

**Request**:
```json
{jsonrpc:2.0,method:<method>,params:{...},id:<number>}
```
**Response**:
```json
{jsonrpc:2.0,result:{...},id:<number>}
```
**Error**:
```json
{jsonrpc:2.0,error:{code:<number>,message:...},id:<number>}
```
**Notification**:
  * no response expected
```json
{jsonrpc:2.0,method:<method>,params:{...}}
```

## Error codes

- `-32700` Parse Error
- `-32600` Invalid Request
- `-32601` Method Not Found
- `-32602` Invalid Params
- `-32603` Internal Error
- `-32000` Timeout Error
- `-32001` Cancelled Error
- `-32002` Version Mismatch


## Commands

- [Management](./commands/management.md)
- [Session Management](./commands/sessions.md)

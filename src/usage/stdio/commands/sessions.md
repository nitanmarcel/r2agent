# Session Management

## session_list
List available sessions.

**Request:**
```json
{
  jsonrpc: 2.0,
  method: session_list,
  params: {filter_binary: true},
  id: 5
}
```
**Response:**
```json
{
  jsonrpc: 2.0,
  result: [
    {
      session_id: example_20240101_120000_abc123,
      binary_name: example.bin,
      created_at: 2024-01-01T12:00:00,
      last_accessed: 2024-01-01T12:30:00,
      is_current: true
    }
  ],
  id: 5
}
```

## session_new
Create a new session.

**Request:**
```json
{
  jsonrpc: 2.0,
  method: session_new,
  params: {},
  id: 6
}
```
**Response:**
```json
{
  jsonrpc: 2.0,
  result: {session_id: ..., message: Created new session},
  id: 6
}
```
## session_switch
Switch to an existing session.

**Request:**
```json
{
  jsonrpc: 2.0,
  method: session_switch,
  params: {session_id: <session_id>},
  id: 7
}
```
**Response:**
```json
{
  jsonrpc: 2.0,
  result: {message: Switched to session <session_id>},
  id: 7
}
```
## session_delete
Delete a session.

**Request:**
```json
{
  jsonrpc: 2.0,
  method: session_delete,
  params: {session_id: <session_id>},
  id: 8
}
```
**Response:**
```json
{
  jsonrpc: 2.0,
  result: {message: Deleted session <session_id>},
  id: 8
}
```
## session_current
Get the current session info.

**Request:**
```json
{
  jsonrpc: 2.0,
  method: session_current,
  params: {},
  id: 9
}
```
**Response:**
```json
{
  jsonrpc: 2.0,
  result: {session_id: ..., binary_name: example.bin},
  id: 9
}
```

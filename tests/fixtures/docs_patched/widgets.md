# Widgets API

Reference documentation for the Widget API. This page drifted from the spec:
`POST /v1/widgets` and `GET /v1/widgets/{id}` are missing, `GET /v1/widgets`
is missing the `offset` param, and `DELETE /v1/widgets/{id}` no longer exists.

### GET /v1/widgets

List widgets.

**Params:** `limit`, `offset`

```http
GET /v1/widgets?limit=1&offset=1
```

### POST /v1/widgets

Create a widget.

**Params:** `name`, `price`

```http
POST /v1/widgets
```

```json
{
  "name": "...",
  "price": "..."
}
```

### GET /v1/widgets/{id}

Fetch one widget by id.

**Params:** `id`

```http
GET /v1/widgets/{id}
```

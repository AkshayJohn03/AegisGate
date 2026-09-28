# Widgets API

Reference documentation for the Widget API. This page drifted from the spec:
`POST /v1/widgets` and `GET /v1/widgets/{id}` are missing, `GET /v1/widgets`
is missing the `offset` param, and `DELETE /v1/widgets/{id}` no longer exists.

### GET /v1/widgets

List widgets.

**Params:** `limit`

```http
GET /v1/widgets?limit=10
```

### DELETE /v1/widgets/{id}

Delete a widget.

```http
DELETE /v1/widgets/{id}
```

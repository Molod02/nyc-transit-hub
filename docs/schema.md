# Database schema (Firebase Firestore)

Live MTA data is **not** stored in the database. The backend fetches it and
caches it in memory. The database holds only what belongs to users.

## Collections

### `users/{uid}`
| Field | Type | Notes |
|---|---|---|
| `displayName` | string | |
| `email` | string | from Firebase Auth |
| `language` | string | `en`, `es`, ... (multilingual support, Week 7) |
| `createdAt` | timestamp | |

### `users/{uid}/favorites/{favoriteId}`
| Field | Type | Notes |
|---|---|---|
| `type` | string | `line` or `station` |
| `refId` | string | line id (`A`) or station id (`A27`) |
| `name` | string | display name, copied at save time |
| `createdAt` | timestamp | |

### `users/{uid}/alertPreferences/{prefId}`
| Field | Type | Notes |
|---|---|---|
| `line` | string | line to watch, for example `L` |
| `channel` | string | `inapp` or `email` |
| `enabled` | boolean | |
| `createdAt` | timestamp | |

## Relationships

```
users/{uid}
  ├── favorites/{favoriteId}         one user has many favorites
  └── alertPreferences/{prefId}      one user has many alert preferences
```

Each subcollection lives under its user, so security rules can say
"a user may read and write only their own `users/{uid}/**`".

## Backend-owned data (not in Firestore)

| Data | Where it lives | Refresh |
|---|---|---|
| Line status and alerts | in-memory cache in the FastAPI backend | every `CACHE_TTL` seconds (default 30) |
| Station and line index (for routes) | `backend/data/index.json`, built from static GTFS | rebuilt when the file is deleted |

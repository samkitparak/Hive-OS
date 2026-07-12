# HIVE OS Dashboard

React dashboard for the HIVE OS FastAPI service.

## Development

```bash
npm ci
npm run dev
```

Vite serves the dashboard on `http://localhost:5173` and proxies `/api` to the
backend on `http://localhost:8000`.

## Production build

```bash
npm run build
```

FastAPI serves `dashboard/dist` directly in production, so a separate Node.js
process is not required after the build completes.

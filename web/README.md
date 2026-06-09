# Aurora Digest Web

AstroPaper-based static frontend for Aurora Digest.

## Commands

```bash
npm --prefix web ci
npm --prefix web run build
npm --prefix web run dev -- --host 127.0.0.1 --port 4321
```

Aurora writes generated digest posts to `web/src/content/posts/`. GitHub
Actions builds `web/dist/` and publishes that directory to `gh-pages`.

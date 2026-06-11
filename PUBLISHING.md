# Publishing — marb.cadclaw.io

The public benchmark site is deployed **only** from [`publishing/`](publishing/)
in this project. That folder is the complete site root (HTML, CSS, fonts,
media) and is the single source of truth for what is public. It is gitignored
here only because it is media-heavy (~34 MB); treat it as published content.

Deploy (Cloudflare Pages, project `marb`, production branch `production`):

```bash
npx wrangler pages deploy publishing --project-name=marb --branch=production --commit-dirty=true
```

Custom domain `marb.cadclaw.io` is bound to the `marb` project. Note the
production branch name differs per project: `marb` uses `production`, the
`cadclaw` project uses `main` — deploying `cadclaw` with `--branch=production`
silently lands a preview.

The old MARB mirror at `cadclaw.io/benchmark/*` is retired: those paths now
301-redirect to `marb.cadclaw.io` (see `Publications/sites/cadclaw.io/docs/_redirects`).

Rules:

1. Nothing goes into `publishing/` before the pre-publication pass:
   professionalism, brevity, and the security sweep (no equipment names,
   hostnames, IPs, local paths, or personal emails — `info@sunn3d.com` only).
2. Drafts and internal notes live in `../MARB-private/`, never here.
3. Figures are built in `results/figures/` by `grader/build_*.py` and copied
   into `publishing/media/` — keep both in sync when regenerating.

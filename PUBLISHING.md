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

## Answer keys — born-gated flow

Answer keys are the one asset class that must never appear in this repo's
history. Canonical key storage lives in the private companion repo; the
gitignored `tasks/**/` key paths in this working tree are disposable synced
copies that exist only so the graders' default paths resolve.

Every **new** task key (task 2 and onward) is born gated:

1. **Author privately.** Create the key in the private companion repo's
   `keys/<task>/` and commit it there. It never touches this working tree
   except through the sync script, which refuses any destination this repo
   does not gitignore.
2. **Upload straight to the gated HF dataset** (a new revision of
   `SunnydayTech/marb-m3-crete-answer-key`, or the task's own gated dataset),
   uploading from the private storage — not from a checkout of this repo.
3. **Publish only the hash.** Add the key files' sha256 digests (and byte
   sizes) to `tasks/<task>/ANSWER_KEY.md` here. That is the entire public
   footprint of a key: third parties can verify the graders ran against the
   canonical revision without ever seeing it.
4. **Revisions repeat the loop.** New key revision → new private commit → new
   gated-dataset revision → updated hashes in the same public commit. Never
   edit a key in place silently.

Enforcement (all three fire on the same patterns —
`scripts/check_no_answer_keys.sh` is the single source of truth):

- `.gitignore` blocks the key paths, including generalized `*reference*`
  patterns for future tasks;
- the pre-push hook (`scripts/hooks/pre-push`, install with
  `cp scripts/hooks/pre-push .git/hooks/pre-push`) rejects any push whose
  commits touch a key path, even a forced `git add -f`;
- the `key-guard` GitHub Actions workflow re-runs the same check server-side
  on every push and PR, so an uninstalled hook is not a bypass.

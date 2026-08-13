# Deployment Checklist — first time going live

Do these **in this exact order**. Steps 1–3 exist because of one fact: this
app ships with no login screen by default, and every visitor is treated as
a full admin until you turn that off. Doing them out of order (especially
exposing the site before step 3) means a stranger could become the
permanent admin instead of you.

## 1. Turn authentication ON before anyone else can reach the site

There is **no button in the app's Settings page for this** — it can only be
set by editing a config file, and it only takes effect after a restart.
Confirmed by reading the actual code, not assumed.

- Start the app once, privately (not yet pointed at by a public domain /
  not yet announced to anyone). This creates
  `data/user/settings/auth.json` with its defaults.
- Edit that file and change `"enabled": false` to `"enabled": true`.
- Restart the app (the setting is only read at startup).
- Confirm it worked: the site should now show a login page instead of
  going straight in.

```json
{
  "version": 1,
  "enabled": true,
  "username": "admin",
  "password_hash": "",
  "token_expire_hours": 24,
  "cookie_secure": false
}
```

(If you don't have direct file access on the host, the same edit can be
done via `docker exec` into the running container instead.)

## 2. Only now, make the site actually reachable

Point your domain at it / open the port / whatever "going live" means for
your setup — **after** step 1, not before.

## 3. Immediately claim the first admin account — do this yourself, first

The very first account ever created on the app becomes the permanent
admin. After that, the "create an account" page closes itself forever —
everyone after you needs an admin (you) to create their account for them.

- Go to the site yourself, right away, and register.
- Don't share the URL with anyone — student, colleague, tester — until
  you've done this.

## 4. Turn on cookie security (same file, while you're in there)

`"cookie_secure": false` is fine for local testing over plain HTTP. Once
the site is reachable over HTTPS (which any real deployment should be),
set it to `true` in the same `auth.json` file, same restart-required rule
as step 1.

## 5. Point the backup script at production, and put it on a schedule

`scripts/backup.py` and `scripts/restore_backup.py` exist and are tested
(see `devin-handoff/DEVIN_LOG.md`, 2026-08-13 entry) — but nothing runs
them automatically yet. On whatever host you land on:

- Set `BACKUP_DIR` to somewhere that survives the app being redeployed
  (not inside a container that gets torn down).
- Schedule `python scripts/backup.py` to run daily (cron on Linux, a
  systemd timer, or your host's equivalent — Windows Task Scheduler only
  makes sense if this ever runs on Windows in production, which is
  unlikely).
- Actually watch the first few scheduled runs succeed, don't just assume.

## 6. Set your real domain in CORS / API base settings

Once you're logged in as the admin (after step 3), go to
**Settings → Network** in the app and set the public API base / allowed
origins to your real domain. Skipping this can make the frontend unable
to reach the backend once things aren't all on `localhost` anymore.

## 7. Known, accepted gaps — not blockers, just don't forget they exist

- **Student code execution is off by default** (issue #7) — deliberate,
  not a bug. Revisit only if students actually need to run code.
- **No database connection timeout on startup** (issue #77) — parked
  until you know your production host; the startup migration retry loop
  could hang longer than ideal if the database is slow to come up.
- **4 reopened partial fixes** (#61 term validation, #35 admin
  "change role" action, #42 remaining pagination, #58 two book block
  types) — none are safety-critical, all are on the GitHub issue tracker.

---

Once steps 1–3 are done, everything else on this list can happen in any
order, at your own pace.

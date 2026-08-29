# LinkedIn Profile API

Give it a LinkedIn profile URL, get the profile back as structured JSON.

```bash
curl "http://localhost:8000/api/v1/profile?url=https://www.linkedin.com/in/williamhgates/"
```

```json
{
  "full_name": "Bill Gates",
  "headline": "Chair, Gates Foundation and Founder, Breakthrough Energy",
  "about": "Chair of the Gates Foundation. Founder of Breakthrough Energy…",
  "location": { "text": "Seattle, Washington, United States" },
  "follower_count": 40603838,
  "profile_picture": { "url": "https://media.licdn.com/dms/image/…" },
  "experience": [{ "company": "Gates Foundation" }],
  "education": [{ "school": "Harvard University" }],
  "meta": { "source": "merged", "partial_sections": ["skills", "dates"] }
}
```

---

## Run it

Python 3.11+ (3.12 recommended — the Docker image pins it).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.api:app --reload
```

Open <http://localhost:8000/docs> for interactive API docs.

**No LinkedIn account needed to try it** — set `DEMO_MODE=true` in `.env` and
every request returns a sample profile, so you can see the schema without
credentials.

### With Docker

```bash
docker build -t linkedin-profile-api .
docker run -p 8000:8000 --env-file .env linkedin-profile-api
```

---

## Getting a session cookie

For live data the service needs a logged-in LinkedIn session. It never
handles a password — you sign in normally in a browser and copy the cookie.

1. Log in to <https://www.linkedin.com> in Chrome
2. DevTools → **Network** → click any request → **Copy as cURL**
3. Copy the value after `-b '…'` into `LINKEDIN_COOKIE` in `.env`

Paste the **whole** cookie string. A browser sends about 25 cookies; sending
only `li_at` is a fingerprint that gets the session blocked within a few
requests. Only the cookies that matter are forwarded upstream.

> **Use a throwaway account.** Automated access violates LinkedIn's User
> Agreement, and the account holding this cookie is the one that gets
> restricted. See [Legal](#legal).

The cookie works like a password — anyone holding it is signed in as you. It
is read only from `.env` (which is gitignored) and never appears in any API
response.

---

## API

### `GET /api/v1/profile`

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Profile URL or bare public id |
| `refresh` | boolean | no | Skip the cache and refetch |

### `POST /api/v1/profile`

```bash
curl -X POST http://localhost:8000/api/v1/profile \
  -H 'Content-Type: application/json' \
  -d '{"url": "williamhgates"}'
```

### `GET /health`

Reports which sources are active and whether a session is configured. Never
exposes the cookie.

### Accepted URLs

All equivalent: full URLs, locale subdomains (`in.linkedin.com`), query
strings, deep links (`/in/foo/detail/experience/`), and the bare id
(`williamhgates`). Company and school pages are rejected with `422`.

### Authentication

Set `API_KEYS` (comma-separated) and every request needs a matching
`X-API-Key` header. Empty disables the check — fine locally, but **always set
it when deployed**, since each call spends your LinkedIn session.

### Errors

```json
{ "error": "linkedin_session_expired",
  "detail": "LinkedIn rejected the session cookie (HTTP 401).",
  "hint": "Copy a fresh LINKEDIN_COOKIE from a logged-in browser." }
```

| Status | Error | Meaning |
| --- | --- | --- |
| 401 | — | Missing or invalid `X-API-Key` |
| 404 | `profile_not_found` | No visible profile there |
| 422 | `invalid_profile_url` | Not a member profile URL |
| 429 | `linkedin_rate_limited` | Slow down |
| 502 | `linkedin_endpoint_retired` | LinkedIn removed an endpoint |
| 503 | `linkedin_session_expired` | Cookie is dead — replace it |
| 503 | `linkedin_session_challenged` | Account soft-blocked; clear it in a browser |

### Where the data came from

Every response carries `meta.source`, and `meta.partial_sections` lists what
could not be read — so you can always tell "this person has no
certifications" from "we couldn't see certifications".

| `meta.source` | Meaning |
| --- | --- |
| `linkedin_api` | LinkedIn's own JSON API |
| `logged_in_page` | The profile page while signed in |
| `public_page` | The profile page while signed out |
| `merged` | Several of the above combined — the normal case |
| `sample_file` | Demo mode |
| `cache` | A stored earlier response |

---

## Approach

**The obvious approach no longer works.** LinkedIn's internal JSON API
(`/voyager/api/identity/profiles/{id}/profileView`) now returns **410 Gone**.
The same session still gets `200` from `/voyager/api/me`, so this is endpoint
removal, not an auth problem.

**And nothing replaced it.** Capturing a real profile page load in Chrome
shows the page makes *no profile API call at all* — the only requests are
messaging polls. LinkedIn moved the profile to **server-driven UI**: the data
is rendered server-side and shipped inside the HTML, in
`<script id="rehydrate-data">` as a React Server Components stream.

So the service reads profiles from **two pages instead of one API**, because
neither alone is complete:

- **Signed in** ([from_logged_in_page.py](app/profile_sources/from_logged_in_page.py))
  — the real headline, follower count, location, current employer
- **Signed out** ([from_public_page.py](app/profile_sources/from_public_page.py))
  — the about text, profile photo and schools, which the signed-in page omits
  for people outside your network. Its headline is masked as `********`,
  which the parser drops rather than passing through as if it were data.

Both are parsed into the same shape and combined, preferring the signed-in
values. The retired JSON API is still tried first, so the service picks it up
again automatically if LinkedIn restores it.

Row ids and CSS class names in LinkedIn's HTML regenerate on every build, so
the parser anchors only on things that hold: `<title>` is always
`"<Name> | LinkedIn"`, and the headline is the first `<span>` after the name.

### Layout

```
app/
  api.py                    the web endpoints people call
  settings.py               configuration read from .env
  response_models.py        the shape of the JSON we send back
  auth.py                   API keys and rate limiting
  cache.py                  remembering recent results
  profile_sources/          every way we can read a profile
    source.py               what every source must provide
    fetch_profile.py        runs the sources, combines what they return
    merge.py                combining results from several sources
    browser_headers.py      making requests look like a real browser
    errors.py               what can go wrong, and its HTTP status
    profile_urls.py         turning a LinkedIn URL into a profile id
    from_linkedin_api.py    source: LinkedIn's own JSON API (retired, kept)
    parse_api_json.py       turning that API's JSON into our format
    from_logged_in_page.py  source: the profile page while signed in
    from_public_page.py     source: the profile page while signed out
    from_sample_file.py     source: a saved example, for demo mode
```

Adding a source means writing one class with a `fetch()` method and listing
it — nothing else changes.

### Not hammering LinkedIn

Results are cached for an hour, at most one upstream call runs at a time
however many callers the API is serving, retries back off with jitter, and
our own endpoint is rate limited.

---

## Testing

```bash
pytest -q     # 69 passed
```

No network or credentials needed — the suite runs against saved fixtures.

---

## Deployment

Must be served over HTTPS. Any Docker host works; the image runs as an
unprivileged user and reads `$PORT` if the platform sets one.

**Koyeb** — free and does not sleep. New Service → GitHub → pick this repo →
it detects the `Dockerfile` → set `LINKEDIN_COOKIE` → deploy.

**Render** — `render.yaml` is committed, so: New → Blueprint → pick this repo,
then set `LINKEDIN_COOKIE` and `API_KEYS` when prompted. They are marked
`sync: false` so secrets are never read from the repository. Note the free
tier sleeps after 15 minutes of inactivity, and the first request after that
takes ~50s.

Secrets go in the platform's environment settings, never in the image.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LINKEDIN_COOKIE` | — | Browser cookie header |
| `DEMO_MODE` | `false` | Serve a sample; no LinkedIn calls |
| `API_KEYS` | *empty* | Comma-separated; empty disables auth |
| `CACHE_TTL_SECONDS` | `3600` | How long results are kept |
| `RATE_LIMIT_PER_MINUTE` | `20` | Per-caller limit |
| `MAX_UPSTREAM_CONCURRENCY` | `1` | Concurrent calls to LinkedIn |

---

## Known limitations

**What you actually get.** Reliably: name, headline, about, location,
follower count, current company, current school, profile photo. Experience
and education come back as organisation names without titles or dates.
Skills, certifications and languages are not available.

**Why.** This is a limit of the account, not the parsing. An account with no
connections sees every profile as 3rd-degree, and LinkedIn renders a stripped
page for those. An account with real network proximity would fill in most of
the gaps with no code change.

**The approach is fragile by nature.** LinkedIn's internal structures have no
compatibility guarantee and change without notice — the 410 above is that
fragility, observed. Parsing is defensive, but a large enough change means
missing fields until the parser is updated.

**Bot detection is real.** Sustained automated traffic triggers a challenge
(`linkedin_session_challenged`). Caching and rate limits reduce this; they do
not eliminate it. Opening linkedin.com in the browser that owns the cookie
usually clears it.

**Deliberately not included.** Contact details, email and phone — they sit
behind a separate gate and are the fields most likely to count as personal
data under GDPR and India's DPDP Act. There is also no proxy rotation or
CAPTCHA solving; that is evasion infrastructure rather than a scraper.

**One session, one identity.** No cookie pooling. The cache is in memory, so
behind more than one instance you would swap it for Redis — `get`/`set` is
the whole interface.

---

## Legal

Automated collection of LinkedIn data is prohibited by the
[LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement) §8.2.
**Using this risks restriction of the account whose cookie it holds.**

*hiQ Labs v. LinkedIn* is often cited as permitting this. It is narrower than
that: it concerned **logged-out, public** data, and hiQ ultimately lost on
breach of contract — the User Agreement bound it regardless of the CFAA
question. This service reads data behind an authenticated session, which is a
different position.

Retrieved profiles contain personal data. Storing or processing it needs a
lawful basis under GDPR and the DPDP Act, independent of whether it was
technically reachable.

Built as a hiring-challenge exercise. Not intended for bulk collection,
resale, or any use at a scale that burdens LinkedIn.

---

## License

MIT — see [LICENSE](LICENSE).

# C1 Imports

This container mirrors files from an SFTP server on a schedule and serves them to staff through a web UI. People in one Entra ID security group can sign in, browse and search the files, download them, read sync logs, and start a sync. The SFTP mirror is what adds and removes files.

## Entra ID app registration

Create this in the Microsoft Entra admin center before starting the container.

1. App registrations → New registration.
   - Name: `C1 Imports`
   - Supported account types: this organizational directory only
   - Redirect URI: platform **Web**, value `{BASE_URL}/auth/callback` (for example `https://c1.example.com/auth/callback`)
2. Certificates & secrets → New client secret. Copy the secret value.
3. Manifest → set `"groupMembershipClaims": "SecurityGroup"`, so the ID token includes security group object IDs.
4. Create a security group and add the staff who need access. Copy the group's object ID.
5. Enterprise applications → this app → Users and groups → add that group.

If someone belongs to so many groups that Entra omits the `groups` claim, change the app's groups optional claim to **Groups assigned to the application** (ID token). The token then includes the one group assigned in step 5.

Copy the tenant ID, application (client) ID, client secret, and group object ID into `.env`.

## Configuration

```bash
cp .env.example .env
```

Fill in `.env`:

- `SFTP_HOST`, `SFTP_USER`, `SFTP_PASS`, and optionally `SFTP_PORT` and `SFTP_REMOTE_DIR`
- `BASE_URL` — the https URL of the site, as users and Entra see it
- `SESSION_SECRET` — `openssl rand -hex 32`
- `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`, `ENTRA_GROUP_ID`
- Cron fields `CRON_MINUTE`, `CRON_HOUR`, `CRON_DOM`, `CRON_MONTH`, `CRON_DOW` (default 06:00 every day)

The app listens on port 8000 without TLS. Put it behind the reverse proxy and forward `X-Forwarded-Proto` and `X-Forwarded-For`. `BASE_URL` must be the external origin so the Entra redirect matches.

## Run

```bash
docker compose up -d --build
```

Open `BASE_URL`. A session lasts 12 hours.

Mirrored files are stored in the `data` volume (`/app/files`). Download history is a SQLite database in the `state` volume (`/app/state/downloads.db`).

## Using the site

- **Files** lists the mirrored folders. Search matches file names in every folder. Downloading a file records your name and the time; the file list shows the last person who downloaded it.
- **Logs** shows each sync log, newest first.
- **Run sync** starts the SFTP mirror if one is not already running. The status line polls every couple of seconds.

There is no delete and no preview. Files leave the list when the SFTP mirror no longer has them.

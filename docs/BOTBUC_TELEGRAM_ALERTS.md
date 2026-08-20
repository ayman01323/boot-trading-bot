# BotBuc Telegram alerts

The BotBuc backup monitor sends operational backup status messages only to active users whose role is `MASTER` in `CSVbot/users.csv`.

- Success: once per MASTER ID for each date after the Google Drive verification marker confirms the remote `ndrive:BotBuc/YYYY-MM-DD.zip` copy.
- Failure/not verified: first alert from 04:00 server local time if that day's Drive verification is still absent, then no more than once per hour while the backup remains unverified.
- Failure alerts state whether the local ZIP is present and retained. The existing backup worker continues its independent hourly upload retry and never deletes an unverified local ZIP.
- Telegram delivery markers are stored as root-only files inside `/root/BotBuc` so service restarts do not spam duplicate success messages or reset the hourly failure interval.
- The Telegram bot token is read from the existing application configuration and is never written to an alert marker.

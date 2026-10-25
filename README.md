# YUTorah → Google Drive archiver

This small project checks one YUTorah teacher's current audio RSS feed each day and copies shiurim that are not already in a Google Drive folder. New uploads go into `Iyun/` or `Other/` beneath that folder. Google Drive itself is the persistent state: a shiur is considered archived when the destination or any subfolder contains a filename beginning with `<shiur_id> -`.

Files are named `<shiur_id> - <title>.mp3`. Hebrew and other Unicode text is preserved; only characters that are unsafe in filenames are replaced. The feed's MP3 enclosure is preferred, with [`torah-dl`](https://github.com/SoferAi/torah-dl) as a fallback.

## What you need

- A GitHub account
- A Google account with enough Drive storage
- The YUTorah teacher ID for your rebbe
- `rclone` installed temporarily on your computer for the initial Google Drive login

The teacher ID is the number used by the teacher's YUTorah RSS URL:

```text
https://www.yutorah.org/rss/RssAudioOnly/teacher/<TEACHER_ID>
```

Opening that URL in a browser is a useful way to confirm the ID before setup.

## Setup after forking

### 1. Fork this repository

Use GitHub's **Fork** button to create a copy under your account. On the fork, open the **Actions** tab and enable workflows if GitHub asks you to.

### 2. Configure Google Drive with rclone

Install [rclone](https://rclone.org/install/) on your own computer, then run:

```shell
rclone config
```

Create a new remote. A straightforward setup is:

1. Name the remote `gdrive`.
2. Choose **Google Drive** as the storage type.
3. Enter your own Google OAuth client ID and client secret. Current rclone releases warn that the old shared Google Drive client is being retired during 2026; follow rclone's [Google Drive client ID instructions](https://rclone.org/drive/#making-your-own-client-id) for a durable scheduled setup. Leaving these fields blank may still work temporarily, but is no longer suitable for a new long-lived archive.
4. Choose the access scope appropriate for the Drive account (full Drive access is the simplest for a dedicated archive account).
5. Leave the service-account file blank.
6. Complete the browser login and approve access.
7. Unless you specifically use a Shared Drive, answer **No** when asked whether this is a Shared Drive.

Create the destination folder in Drive, then verify the exact rclone destination from your computer. For example:

```shell
rclone lsf "gdrive:YUTorah/Rabbi Example"
```

The configured destination can be the Drive root (`gdrive:`) or any folder below it (`gdrive:YUTorah/Rabbi Example`). Spaces are fine.

### 3. Encode the rclone configuration

Find the configuration file:

```shell
rclone config file
```

Copy the path printed by that command. Encode that file as one Base64 line.

On macOS:

```shell
base64 < /path/to/rclone.conf | tr -d '\n'
```

On Linux:

```shell
base64 -w 0 /path/to/rclone.conf
```

Copy the resulting text. Treat it as a password: the configuration normally contains a Google OAuth refresh token. Never add `rclone.conf` or the encoded value to the repository.

### 4. Add the GitHub configuration

In your fork, go to **Settings → Secrets and variables → Actions**.

Under **Variables**, add these three repository variables:

| Name | Example |
| --- | --- |
| `YU_TORAH_TEACHER_ID` | `80153` |
| `YU_TORAH_TEACHER_NAME` | `Rabbi Hershel Schachter` |
| `GDRIVE_DESTINATION` | `gdrive:YUTorah/Rabbi Hershel Schachter` |

Under **Secrets**, add:

| Name | Value |
| --- | --- |
| `RCLONE_CONFIG_B64` | The single-line Base64 text from the previous step |
| `NTFY_TOPIC` (optional) | A long, unguessable [ntfy](https://ntfy.sh/) topic name for phone push notifications |

The remote name at the start of `GDRIVE_DESTINATION` must match the name inside your rclone configuration (`gdrive` in the example).

If `NTFY_TOPIC` is configured, install the ntfy app on your phone and subscribe to that exact topic. Every workflow run sends a success or failure notification with the found, skipped, uploaded, and failed counts. Topic names on the public ntfy service are public to anyone who knows them, so use a long random value and keep it in a GitHub secret.

### 5. Run it once

Open **Actions → Archive YUTorah shiurim → Run workflow**. Watch the run and confirm that files appear in the configured Drive folder.

After that, GitHub Actions runs automatically every day at approximately 06:17 UTC. Scheduled workflows can start a little later when GitHub is busy. The workflow prevents two archive runs from overlapping.

## How each run behaves

1. Fetch and parse every item currently exposed by the teacher's audio RSS feed.
2. List the destination and all subfolders with `rclone lsf --files-only --recursive`.
3. Skip any shiur whose ID already begins a filename in Drive.
4. Download each missing MP3 into the runner's temporary directory.
5. Choose `Iyun` or `Other` using the teacher's rules in `categories.json`, log that category, upload with `rclone copyto`, and delete the temporary local copy.

One broken item does not stop later items from being attempted. If any item fails, the workflow is marked failed after all other items have been processed; because no Drive filename was created for the failed shiur, the next daily run retries it.

No audio or state is committed to Git. The RSS feed only exposes a current window of items, so a new fork archives the items presently in that feed and then keeps up with new additions; it does not discover a teacher's entire historical catalog.

## Category routing

Edit `categories.json` to configure recognized Iyun series for each teacher. Rabbi Elchanan Adler (`80002`) starts with `Shabbos`: only numbered titles beginning with `Shabbos #20`, for example, match. A title merely mentioning Shabbos is not enough. Unrecognized titles, Parsha, and Machshava go to `Other`. Teachers without rules also default to `Other`.

Per-shiur overrides take precedence over series matching. For example (IDs below are illustrative):

```json
{
  "80002": {
    "iyun_series": ["Shabbos"],
    "overrides": {"1234567": "Iyun", "1234568": "Other"}
  }
}
```

Rules are validated before any downloads or uploads; invalid categories or misspelled settings fail the run. Filenames and shiur IDs remain unchanged. The current RSS does not provide reliable series/category metadata, so this initial classifier uses explicit title patterns and ID overrides. Zman assignment is separate.

### Preview organization of existing files

The daily sync leaves existing files where they are. Generate a separate read-only move proposal using the complete archive listing (including shiurim no longer present in RSS):

```shell
python organize.py --teacher-id 80002 --destination "gdrive:YUTorah/Rabbi Adler" > organization-plan.json
```

Or preview a previously saved inventory without connecting to Drive:

```shell
rclone lsf "gdrive:YUTorah/Rabbi Adler" --files-only --recursive > inventory.txt
python organize.py --teacher-id 80002 --inventory inventory.txt > organization-plan.json
```

The JSON report contains proposed `moves`, `skipped` files, and `conflicts`. Duplicate shiur IDs require manual review and cause a nonzero exit code. Only the flat archive and the two category folders are considered; unrelated folders and unrecognized filenames are left alone. Existing filenames are preserved, including underscores introduced when unsafe punctuation was removed.

This command has no apply mode. Review the proposed moves and adjust overrides first. Any later migration should use Drive moves, preserving the original file IDs and sharing links, rather than downloading and re-uploading. Do not run a migration concurrently with the daily archive job.

## Local development

Python 3.12 is used in GitHub Actions. To run the tests locally:

```shell
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

To perform a local sync, make sure `rclone` can access the destination and set the same three non-secret values:

```shell
export YU_TORAH_TEACHER_ID="80153"
export YU_TORAH_TEACHER_NAME="Rabbi Hershel Schachter"
export GDRIVE_DESTINATION="gdrive:YUTorah/Rabbi Hershel Schachter"
python sync.py
```

Only archive material you are permitted to download and store, and follow YUTorah's terms and the rights of the content owners.

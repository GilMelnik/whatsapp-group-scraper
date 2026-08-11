# WhatsApp crypt15 group exporter

Export the **full local history** of a WhatsApp group by decrypting a copied
`msgstore.db.crypt15` backup and reading the group's messages into a single
`chat_android.json`.

This tool is **file-only**: you copy the encrypted backup off the phone yourself
(no ADB, no root, no WhatsApp Web). It supports **crypt15** end-to-end encrypted
backups only.

## What you get

A single JSON array where each message looks like:

```json
{
  "id": "3A10A4FC4745FA7F1EE1",
  "date": "08/10/2022",
  "time": "20:49",
  "sender": {"user_name": "Alice", "phone": null},
  "text": "…",
  "is_outgoing": false,
  "quote": null,
  "reactions": [{"emoji": "👍", "count": 1, "senders": [{"user_name": "Bob", "phone": null}]}],
  "media_type": null,
  "is_forwarded": false,
  "is_deleted": false,
  "link_preview": null
}
```

## Install

```bash
pip install -r requirements.txt
```

Requires Python 3.10+.

## Step 1 — make an encrypted backup on the phone

1. Open WhatsApp on the Android phone.
2. Go to **Settings → Chats → Chat backup**.
3. Tap **End-to-end encrypted backup** and **turn it on**. Choose to use a
   **64-digit key** (not a password). WhatsApp shows you a 64-character
   hexadecimal key — **write it down / save it**; you need it to decrypt.
   - Enabling this is what makes the local backup file `msgstore.db.crypt15`
     (older backups are `.crypt14`/`.crypt12` and are **not** supported here).
4. Back on the Chat backup screen, tap **Back up** and wait until it finishes.

## Step 2 — copy the files off the phone

Connect the phone by USB (enable file transfer / MTP) or use a file manager, and
copy from:

```
Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15
```

Optionally also copy `wa.db` (from `Android/media/com.whatsapp/WhatsApp/` or the
Databases folder) — it maps phone numbers to **contact names**, so senders show
as names instead of raw JIDs. Without it the export still works.

> Tip: to confirm the backup is crypt15, check the file extension is
> `.crypt15`. If you only see `.crypt14`/`.crypt12`, enable the end-to-end
> encrypted backup (Step 1) and back up again.

## Step 3 — export the group

```bash
python whatsapp_android_export.py \
  --db path/to/msgstore.db.crypt15 \
  --wa-db path/to/wa.db \
  --group "My Group" \
  --backup-key <64-hex-key> \
  --output chat_android.json
```

- `--db` (required): the copied `msgstore.db.crypt15` (or a folder that contains
  it — the newest `msgstore-*.db.crypt15` is picked).
- `--group` (required): partial, case-insensitive match of the group name. If it
  is ambiguous, the tool lists the matches so you can be more specific.
- `--wa-db` (optional): `wa.db` for readable contact names.
- `--backup-key` (optional): the 64-character hex key. If a `key` /
  `encrypted_backup.key` file sits next to the backup it is picked up
  automatically; otherwise you are prompted.
- `--output` (optional): output path (default `chat_android.json`).
- `--from-date` / `--to-date` (optional): date window (`dd/mm/yyyy` or
  `yyyy-mm-dd`).

## Using the output

The resulting `chat_android.json` is directly consumable by the
`wiki-from-whatsapp` pipeline: copy it into that project's `data/` directory
(as `data/chat_android.json`) and run step 1.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Decryption failed` | Double-check the 64-char key and that the backup is the newest crypt15 file. |
| Senders show as `…@s.whatsapp.net` | Pass `--wa-db wa.db` so contact names can be resolved. |
| `Group 'X' not found` | Run again with a more specific `--group`; the error lists available groups. |
| Only `.crypt14`/`.crypt12` on the phone | Enable **End-to-end encrypted backup** in WhatsApp, back up again, then re-copy. |
| Fewer messages than expected | The backup only contains history stored on **this** phone. |

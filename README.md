# WhatsApp crypt15 group exporter

Export the **full local history** of a WhatsApp group by decrypting a copied
`msgstore.db.crypt15` backup and reading the group's messages into a single
`chat_android.json`.

This tool is **file-only**: you copy the encrypted backup off the phone yourself
(no ADB, no root, no WhatsApp Web). It supports **crypt15** end-to-end encrypted
backups only.

## What you get

A single JSON array, in the order the phone's database holds the messages
(by send time, then by arrival). Each message looks like:

```json
{
  "id": "3A10A4FC4745FA7F1EE1",
  "date": "08/10/2022",
  "time": "20:49:13",
  "sender": {"user_name": "15550000001@s.whatsapp.net", "phone": null},
  "text": "Thanks @15550000002",
  "is_outgoing": false,
  "quote": null,
  "reactions": [{"emoji": "👍", "count": 1, "senders": [{"user_name": "15550000002@s.whatsapp.net", "phone": null}]}],
  "media_type": null,
  "is_forwarded": false,
  "is_deleted": false,
  "link_preview": null,
  "mentions": [{"tag": "15550000002", "member": "15550000002@s.whatsapp.net"}]
}
```

- **`time`** has seconds. WhatsApp stores whole seconds for almost every message,
  so two messages of the same second are told apart only by their place in the
  array: keep the array's order.
- **`sender.user_name`** is a **member id**: one id per member for the whole
  history. WhatsApp stores a sender under a phone id (`…@s.whatsapp.net`) in
  older messages and under a "lid" id (`…@lid`) in newer ones; the tool joins the
  two (and a member's change of number) and writes the phone id when the database
  knows it, otherwise the lid id. The senders of a quote and of a reaction, and
  the members in `mentions`, carry the same ids. Your own messages, quotes and
  reactions are `"You"`. Contact names are not exported.
- **`mentions`** lists the members tagged in the text, one entry per tag, in the
  order of the text: `tag` is the digits as they stand after the `@`, `member` is
  the tagged member's id. The text itself is not changed. The database does not
  say which id is your own, so a tag of you carries an ordinary member id.

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

## Step 2 — copy the file off the phone

Connect the phone by USB (enable file transfer / MTP) or use a file manager, and
copy from:

```
Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15
```

`wa.db` is not needed any more: everything the export holds comes from
`msgstore.db`.

> Tip: to confirm the backup is crypt15, check the file extension is
> `.crypt15`. If you only see `.crypt14`/`.crypt12`, enable the end-to-end
> encrypted backup (Step 1) and back up again.

## Step 3 — export the group

```bash
python whatsapp_android_export.py \
  --db path/to/msgstore.db.crypt15 \
  --group "My Group" \
  --backup-key <64-hex-key> \
  --members members.json \
  --output chat_android.json
```

- `--db` (required): the copied `msgstore.db.crypt15` (or a folder that contains
  it — the newest `msgstore-*.db.crypt15` is picked). An already decrypted
  `msgstore.db` works too. The database is opened read-only and only the group's
  rows are read.
- `--group` (required): partial, case-insensitive match of the group name. If it
  is ambiguous, the tool lists the matches so you can be more specific.
- `--backup-key` (optional): the 64-character hex key. If a `key` /
  `encrypted_backup.key` file sits next to the backup it is picked up
  automatically; otherwise you are prompted.
- `--members` (optional, recommended): the members file. It is read when it
  exists and written back extended, so every member keeps the same id from one
  backup to the next. Without it the ids come from this backup alone.
- `--output` (optional): output path (default `chat_android.json`).
- `--from-date` / `--to-date` (optional): date window (`dd/mm/yyyy` or
  `yyyy-mm-dd`).
- `--wa-db`: accepted so that older command lines still run; it is ignored.

## The members file

```json
{"members": [{"id": "15550000001@s.whatsapp.net", "aliases": ["15550000001@s.whatsapp.net", "900000000001@lid"]}]}
```

It holds ids only, no names. Pass the same file to every export of the same
group:

- an id that already belongs to a member keeps that member for ever;
- a new id joins the member the database links it to;
- when a later backup links two members that both exist in the file already,
  both keep their ids and the tool only reports how many such links it found: it
  never merges two members on its own.

## Using the output

The `chat_android.json` and the members file are read by the
`wiki-from-whatsapp` pipeline. For a first run, copy the export into that
project's `data/` directory (as `data/chat_android.json`) and run step 1. For a
later export of the same group, give it to that project's update step, and keep
using the same members file.

## Tests

```bash
python -m unittest discover -s tests
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Decryption failed` | Double-check the 64-char key and that the backup is the newest crypt15 file. |
| `Group 'X' not found` | Run again with a more specific `--group`; the error lists available groups. |
| Only `.crypt14`/`.crypt12` on the phone | Enable **End-to-end encrypted backup** in WhatsApp, back up again, then re-copy. |
| Fewer messages than expected | The backup only contains history stored on **this** phone. |
| A member shows under a new id after a later backup | Export with the same `--members` file as before. |

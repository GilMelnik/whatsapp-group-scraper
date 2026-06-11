# Android WhatsApp Group Export

Export a WhatsApp group chat with **full local history** by reading the phone's
`msgstore.db` over ADB. This bypasses WhatsApp Web's linked-device sync cap.

Output JSON matches the Web scraper (`whatsapp_scraper.py`) — same fields, shard
files, and merge workflow.

## Prerequisites

1. **ADB** — install [Android platform-tools](https://developer.android.com/studio/releases/platform-tools) and ensure `adb` is on your `PATH`.
2. **USB debugging** — on the phone: Settings → Developer options → USB debugging.
3. **Python deps**:
   ```bash
   pip install pycryptodome wa-crypt-tools protobuf javaobj-py3
   ```
4. **WhatsApp local backup** — Settings → Chats → Chat backup → **Back up**.
   Wait until a backup file appears before exporting.

## Quick start

### Option A — copy backup files from the phone (no ADB)

1. On the phone, open the WhatsApp backup folder (via Files app or copy to PC):
   - `Android/media/com.whatsapp/WhatsApp/Databases/`
2. Copy to your PC:
   - `msgstore.db.crypt14` (or `.crypt15`)
   - `key` — **required for crypt14** (small file, ~131 bytes)
   - `wa.db` — optional, improves contact/group names
3. Run:

```bash
python whatsapp_android_export.py \
  --db outputs/from_phone/msgstore.db.crypt14 \
  --wa-db outputs/from_phone/wa.db \
  --group "My Group" \
  --output outputs/my_group/chat.json \
  --merge-after
```

If `key` sits in the same folder as the backup, it is picked up automatically.

### Option B — ADB pull from a connected phone

Connect the phone, unlock the screen, then:

```bash
python whatsapp_android_export.py --group "My Group" --output outputs/my_group/chat.json
```

With merge into a single file:

```bash
python whatsapp_android_export.py --group "My Group" --output outputs/my_group/chat.json --merge-after
```

## Encrypted backups

### crypt14 (`msgstore.db.crypt14`)

Needs the **`key`** file from the same phone (not a password you type in).
Copy it next to the backup file. Typical sources:

- Same `WhatsApp/Databases/` folder on the phone (if present)
- Extracted via a backup tool from `/data/data/com.whatsapp/files/key`

```bash
python whatsapp_android_export.py \
  --db outputs/from_phone/msgstore.db.crypt14 \
  --backup-key outputs/from_phone/key \
  --group "My Group" --output outputs/my_group/chat.json
```

### crypt15 (`msgstore.db.crypt15`)

If your backup is end-to-end encrypted (`msgstore.db.crypt15`), you need the
64-character hex key from:

**WhatsApp → Settings → Chats → Chat backup → End-to-end encrypted backup**

Pass it on the command line (or you will be prompted):

```bash
python whatsapp_android_export.py --group "My Group" --backup-key <64-hex-chars>
```

## Extraction methods (automatic)

The tool tries methods in order of invasiveness:

| Priority | Method | When |
|----------|--------|------|
| 1 | Pull local backup from `/sdcard/.../WhatsApp/Databases/` | Default for non-root |
| 2 | Decrypt crypt15 with backup key | Encrypted backup pulled |
| 3 | Use plaintext `msgstore.db` | Already decrypted / older backup |
| 4 | Root fast-path | `su` works — pulls live DB from `/data/data/com.whatsapp/` |
| 5 | `--legacy-backup` | Android ≤13 only; risky; off by default |

## Useful flags

| Flag | Purpose |
|------|---------|
| `--device SERIAL` | Pick a device when multiple are connected |
| `--pull-only` | Copy backup files to `~/.wa_android_cache/` without exporting |
| `--db PATH` | Use an already-pulled `msgstore.db` or `.crypt15` file |
| `--wa-db PATH` | Contacts database for display names |
| `--from-date` / `--to-date` | Date window filter (`dd/mm/yyyy` or `yyyy-mm-dd`) |
| `--merge-only` | Merge existing shards (same as Web scraper) |
| `--legacy-backup` | **Dangerous** adb-backup flow for Android ≤13 |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `adb not found` | Install platform-tools; verify with `adb version` |
| `No Android device detected` | Check USB cable, authorize debugging prompt on phone |
| `No WhatsApp backup found` | Run a local backup in WhatsApp and retry `--pull-only` |
| `Decryption failed` | Verify the 64-char backup key; ensure backup file is newest |
| Android 14+ legacy backup fails | Expected — use default pull + crypt15 key instead |
| Missing contact names | Ensure `wa.db` was pulled; pass `--wa-db` explicitly |
| Fewer messages than expected | DB only contains history stored on **this** phone |

## Message ID differences

Android exports use WhatsApp `key_id` values. Web scraper exports use DOM
`data-id` strings. Content and ordering should match; ids will not.

## Comparison with Web scraper

| | Web scraper | Android exporter |
|--|-------------|------------------|
| History depth | Capped by linked-device sync | Full local database |
| Speed | Slow (scroll + reactions UI) | Fast (SQL read) |
| Setup | QR login + Chrome | ADB + backup key |
| Reactions | Opens reactions drawer | Read from DB tables |

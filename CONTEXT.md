# Project Context — saveRestrictedMessagesBot

## Current project goal

Build a private Telegram bot that accepts a Telegram message link and saves the
referenced message/media to the owner's Saved Messages without routing the
large file through the owner's phone.

The source may be a private group/channel where "Restrict Saving Content" is
enabled. The dedicated second Telegram account is intentionally used as the
reader account and must already be a member of the source chat.

## Architecture decision

Use one Python source file for the bot implementation:

- bot.py — the only bot code file
- No application code is split into multiple Python modules.

The program uses two Telegram sessions:

1. Bot session — created from BOT_TOKEN and used only as the user-facing bot.
2. Reader user session — a Telethon MTProto StringSession belonging to the
   user's second Telegram account. This account is used to read source chats.

Secrets are supplied through environment variables and are never committed:

- API_ID
- API_HASH
- BOT_TOKEN
- USER_SESSION
- OWNER_ID

The reader session can be generated locally with:

    API_ID=... API_HASH=... python bot.py --login

The login command prints a Telethon StringSession. The session must be stored
as an environment/hosting secret and must not be placed in GitHub source files.

## Transfer strategy

The bot uses two paths:

### Path 1 — Native Telegram media reuse

The reader account first attempts to send the existing media object directly to
its Saved Messages. Telegram documents that non-protected media can be resent
without re-uploading the file.

### Path 2 — Remote relay fallback

When native reuse is rejected (for example because the source content is
protected), bot.py downloads the media to a temporary file on the hosting
server and immediately uploads it to Saved Messages using the reader account.
The temporary directory is deleted after the upload.

This means:

- The owner's phone does not download and re-upload the source file.
- The hosting server DOES consume bandwidth for the fallback path.
- If the user runs bot.py on their own computer, that computer's Internet
  connection is used.
- No permanent media archive is intentionally kept by this bot.

## Link support

Current parser supports:

- https://t.me/c/<internal_chat_id>/<message_id>
- https://t.me/<public_username>/<message_id>
- https://telegram.me/... equivalents

For private /c/ links, the reader account must have access to the referenced
chat.

## Security decisions

- Bot is owner-only via OWNER_ID.
- USER_SESSION is never written to the repository.
- Login is deliberately an interactive local terminal operation rather than
  requesting a phone number/login code inside a third-party bot chat.
- The repository must never contain BOT_TOKEN, API_HASH, API_ID secrets,
  phone login codes, or StringSession values.

## Current repository state

Repository: CybersecurityRahbar/saveRestrictedMessagesBot
Primary branch: main

Initial repository contained only README.md.

First implementation commit:
2c12a841a135259acdc08cab880e508ea7e15cf7

bot.py blob after creation:
32e54058b9c7e5bf9c34c9cbb925998aeb2ec531

## Important Telegram API findings verified during development

- Telegram's official API documentation says non-protected media can be
  resent without re-uploading by using an existing photo/document media
  constructor and its file reference.
- Telegram's content-protection documentation states that forwarding protected
  messages raises CHAT_FORWARDS_RESTRICTED.
- Telegram's official Bot API currently documents a 50 MB limit for sending
  files through the standard Bot API, while a local Bot API server can upload
  up to 2000 MB. The current implementation therefore saves to Saved Messages
  through the reader user session rather than depending on the standard Bot API
  upload limit.
- Telegram documents larger file support for user accounts separately; exact
  limits depend on account/Premium/app configuration and must not be hard-coded
  as an unlimited guarantee.

## Reference research

The open-source SaveAny-Bot project explicitly advertises support for
"restrict saving content" media and "streaming transfer", confirming that the
general remote-server architecture is used by real Telegram tooling. This
project is being implemented independently and kept intentionally smaller.

## Next engineering steps

1. Validate bot.py syntax and Telethon compatibility.
2. Run a real test with a small unprotected message.
3. Test a protected video from a private group where the reader account is a
   member.
4. Measure behavior for large files and confirm that the owner's phone does
   not receive the source file until it is already saved in Telegram.
5. Improve the fallback to chunked streaming without a full temporary file if
   a reliable implementation is needed.
6. Add optional destination modes after the Saved Messages path is proven.

## Session history

2026-09-30:
- Confirmed the actual user requirement: avoid large source-file transfer
  through the user's phone; a second Telegram account is available and can be
  made a member of the source groups.
- The user explicitly requested a single-file bot implementation for easy
  deployment.
- Implemented the first single-file Python bot in bot.py.

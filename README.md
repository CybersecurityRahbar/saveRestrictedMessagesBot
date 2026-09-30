# saveRestrictedMessagesBot

Private Telegram bot for saving message content from chats the dedicated
reader account can access.

## Design

The bot code is intentionally contained in one file:

- `bot.py` — complete bot implementation

The bot uses two Telegram identities:

1. Bot account — `BOT_TOKEN`
2. Dedicated second Telegram user account — `USER_SESSION`

The second account must be a member of a private group/channel when the source
message is private.

## What it does

Send the bot a Telegram message link such as:

`https://t.me/c/123456789/42`

The bot:

1. Resolves the source message with the second account.
2. Tries Telegram-native media reuse first.
3. If Telegram rejects that path (for example, protected content), it uses a
   temporary remote relay on the hosting server.
4. Saves the result to the second account's Saved Messages.

The source file is therefore not downloaded to the owner's phone as part of
the relay process.

Important: the fallback relay consumes the hosting server's Internet
bandwidth. Running the bot on your own PC means the PC's connection is used.

## Installation

Only one Python dependency is required:

```bash
pip install -U telethon
```

Optional acceleration:

```bash
pip install -U cryptg
```

## First-time reader account login

Get `API_ID` and `API_HASH` from Telegram's official API development page.

Then run locally:

```bash
API_ID=YOUR_API_ID API_HASH=YOUR_API_HASH python bot.py --login
```

The program asks for the second account's phone number, Telegram login code,
and two-step password when required. It prints a `USER_SESSION` StringSession.

Store that session as a private environment/hosting secret. Do not put it in
GitHub.

## Run the bot

Set these environment variables:

```text
API_ID=...
API_HASH=...
BOT_TOKEN=...
USER_SESSION=...
OWNER_ID=...
```

Then:

```bash
python bot.py
```

Only `OWNER_ID` is allowed to use the bot.

## Commands

- `/start`
- `/help`
- `/status`
- `/id`

The bot accepts one Telegram message link at a time.

## Security

Never commit:

- `BOT_TOKEN`
- `API_HASH`
- `USER_SESSION`
- login codes
- two-step passwords

The reader account should be a dedicated account, not the main account, as
planned for this project.

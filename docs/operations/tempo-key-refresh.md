# Tempo Key Refresh (X Article Ingestion)

The Tempo wallet access key used for Xquik micropayments expires roughly every
40 days (a key created on 2026-09-09 expires 2026-10-09). When it expires, X
article submissions fail with:

> I couldn't save that article: Tempo MPP could not complete the Xquik Article request.

Everything else (Medium/Substack/Web ingestion, bot, RAG) keeps working.

## Renewal: one command, one click

```shell
docker compose exec worker tempo-wallet refresh
```

It prints an `Auth URL` and keeps polling (an image-baked no-op `xdg-open`
shim prevents the usual browser-spawn crash). Open the URL, approve in the
Tempo wallet, and the command finishes on its own.

Verify:

```shell
docker compose exec worker tempo-wallet keys   # status should be active, new expires_at
```

## If articles failed while the key was expired

Failed jobs are terminal. Re-submit the URL to Telegram again (or insert a new
job), e.g. via a one-off Python snippet using
`PostgresIngestionRepository.submit` with `SourceClassifier().classify(url)`.

## Notes

- The wallet secret lives in the `tempo-wallet` docker volume. Do not install
  a separate tempo CLI on the host for this — the container's key is what
  signs payments, and a host login would create an unrelated key.
- The balance is on the wallet itself, not the key; refresh does not touch
  funds. Check with `tempo-wallet whoami`.

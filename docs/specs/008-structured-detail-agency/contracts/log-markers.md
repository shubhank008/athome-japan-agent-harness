# T30 marker contract

## Success markers

- `[T30] structured detail extracted`
- `[T30] agency persisted and linked`
- `[T30] rich detail round-trip preserved`

## Failure patterns

These markers must never be emitted by the T30 path:

- `[T31] recommendation card ingested`
- `[T32] detail hydration queued`
- `[T33] detail worker started`
- `[T34] live cache refreshed`

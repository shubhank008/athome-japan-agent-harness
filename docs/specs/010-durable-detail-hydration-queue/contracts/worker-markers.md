# T33 / G5 worker marker contract

## Required markers

- `HYDRATION_SUCCEEDED`: full detail was validated, persisted, and acknowledged.
- `HYDRATION_FAILURE`: a claimed job recorded a categorized failure.
- `HYDRATION_CHALLENGE_STOP`: challenge detection stopped the current run.
- `HYDRATION_BLOCK_STOP`: block detection stopped the current run.
- `HYDRATION_CANCELLED_UNAVAILABLE`: an explicit unavailable selector marker cancelled a job.
- `HYDRATION_WORKER_REPORT`: bounded run totals, without source URLs or credentials.

## Safety contract

- Logged source values use scheme, host, and path only. Query strings, credentials,
  cookies, proxy URLs, and response bodies are never reported.
- Challenge and block failures cool down by stopping the current run. They are not
  aggressively retried and are never solved or bypassed by the worker.
- Deletion/cancellation is allowed only for the explicit unavailable markers
  `掲載終了`, `募集終了`, or `この物件は削除されました`.
- `HYDRATION_PRIORITY`, `HYDRATION_CACHE_LIVE`, and challenge-solving markers are
  forbidden. T33 does not implement live cache integration or priority scheduling.

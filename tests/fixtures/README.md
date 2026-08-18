# Live-captured AtHome fixtures (T14)

These fixtures are genuine public AtHome.co.jp pages captured during M3 T14 with the
project's configured HTTP fetch path. They were validated with
`_detect_athome_challenge` before being saved; no challenge page is stored here.

Capture policy notes:

- Captured through the existing `HttpDomAdapter`/`BaseScraper` and configured proxy
  path only. AtHome anti-bot puzzles were never solved, automated, or circumvented.
- Every body was checked for the `[ATHOME_CHALLENGE]` markers (`Click to verify`,
  `認証にご協力ください`, and the cookies/JavaScript interstitial) before saving.
- No credentials, proxy credentials, cookies, client IP addresses, or unnecessary
  personal data are stored in these files or this document.

## osaka_rental_list.html

- Source: AtHome Osaka rental (賃貸) list results page (大阪府の賃貸物件).
- Captured: 2026-08-18 UTC.
- Validated with `_detect_athome_challenge`: no challenge, `valid=True`.
- Content: 30 building blocks (`.p-property--building`), each with a heading and
  one or more unit sub-blocks (`.p-property__room--detailbox`). 460 unit boxes in
  total, so multi-unit buildings yield multiple summaries sharing building identity.
- Edge cases present:
  - Multi-unit building with many rooms (e.g. 175 unit boxes in one building).
  - Disabled-facility markers `.p-property__information-facility_disabled-list`
    (probable negatives) alongside enabled facility items (USP tags).
- No detached-house (room-number-less) unit was present on this particular page;
  that optional-field path is covered by a hand-built minimal DOM in the parser
  unit tests, not by this capture.

## detail_1101570928.html

- Source: public AtHome rental detail page for listing key `1101570928`
  (「Ｆ＋ｓｔｙｌｅ東大阪本庄１号館 １０２ １Ｋ」).
- Captured: 2026-08-18 UTC.
- Validated with `_detect_athome_challenge`: no challenge, `valid=True`.
- Content: full detail page with price table, property data table, photo set,
  floor-plan image, USP point list, and facility feature table.
- This particular detail page has no disabled-facility markers; `probable_negatives`
  resolves to an empty list for it. The disabled-facility edge case is covered by
  the list fixture above.

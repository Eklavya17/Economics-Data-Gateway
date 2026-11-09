# Area IDs

Each source uses its own geographic codes. The gateway uses one `area_id` for all of them, so a place found once works with every place-based tool.

| area_id | Meaning | OEWS code | QCEW code | LAUS series prefix |
|---|---|---|---|---|
| `us` | United States | national file | `US000` | national CPS series (`LNS...`) |
| `state:48` | State by FIPS code (48 = Texas) | `48` | `48000` | `LASST48...` |
| `metro:12420` | Metro area by CBSA code (12420 = Austin) | `12420` | `C1242` | `LAUMT4812420...` |
| `county:48453` | County by FIPS code (48453 = Travis County, TX) | not published | `48453` | `LAUCN48453...` |

- Get IDs from `find_area`, which also returns the place's unemployment series IDs. Never build them by hand.
- Metro area names can differ slightly between OEWS and QCEW, which follow their own vintages of the official metro definitions.
- OEWS has no county estimates. For county pay use `get_area_employment` (QCEW average weekly wage), or the county's metro area for occupational wages.

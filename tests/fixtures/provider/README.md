# Small, real Defra response fixtures

These are byte-for-byte copies of four original 2 x 2 km WCS responses from
`artifacts/source_pilot_v1`, acquired on 22 September 2026. They have not been
cropped, resampled or re-encoded. Each is 200 x 200 native 10 m cells.
The accompanying metadata HTML, DescribeCoverage XML and HTTP capture records
are also unchanged. `road-hydrated.html` and its HTTP record come from the later
`source_regions_v1` acquisition and exercise Defra's client-rendered metadata.

`fixture.json` is an extracted test index, not a complete release manifest. It
records the originating release IDs and original pilot manifest checksum, the
selected records' original QA, provider identities and checksums of every capture.
Product descriptions retain references to files outside this small subset; it is
not an independently runnable explorer bundle. To recreate it, select the four
record IDs listed in the index, copy their TIFFs, their products' metadata and
metric descriptions plus each `.http.json` sidecar, then add the regional road
metadata capture. Copy bytes, never rewrite the captured text or TIFFs.

| Capture | Regression covered |
|---|---|
| Heathrow road Lden | WCS 1.0, reported values and numeric zeros |
| Chilterns road Lnight | WCS 2.0 native subset, values down to 35 dB |
| Heathrow rail Lden | Entire response contains numeric zeros, no TIFF nodata |
| Heathrow aircraft Lnight | WCS 2.0, reported values and TIFF nodata |

The tests check parser compatibility, request identity, grid alignment, encoding
and byte integrity offline. They do not establish the scientific meaning of all
missing values, acoustic accuracy, or independent agreement between WCS versions.
In particular, the aircraft extract's observed minimum is not a censoring bound.
Synthetic fixtures elsewhere continue to test malformed and adversarial inputs.

Source datasets:

- [Defra road noise](https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517)
- [Defra rail noise](https://environment.data.gov.uk/dataset/3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947)
- [Defra airport noise](https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8)

© Department for Environment, Food & Rural Affairs copyright and/or database
right 2023 (road/rail), 2024 (airport). Contains public sector information licensed
under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

# Draft provider clarification — not sent

To: `open@defra.gov.uk` (public contact listed in all three captured dataset records)

Subject: England Round 4 noise rasters: zero/nodata semantics, calculation domains and WCS behaviour

We are building a traceable map and analytical access to the Defra England Round
4 all-source road, rail and airport rasters. Before interpreting missing samples
as exposure bounds, could you provide a published specification or a citable
technical clarification for these datasets?

- Road: `562c9d56-7c2d-4d42-83bb-578d6e97a517`
- Rail: `3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947`
- Airport: `dac9cba4-abe7-43bd-b8e9-8a83da52edd8`

1. What does an unmasked numeric `0` mean in each road/rail Lden, Lday and Lnight
   GeoTIFF? Is it exclusively a computed receptor below the reporting threshold,
   or can it include uncalculated receptors, building interiors, omitted tiles,
   missing inputs or other exclusions? Please distinguish it from TIFF nodata
   `-96` and state whether the rule applies uniformly across each national mosaic.
2. Is there a versioned mask or geometry identifying valid calculation receptors
   independently of reporting thresholds? How are England boundaries, water,
   buildings and source-distance/calculation limits represented? A WCS bounding
   rectangle alone includes places that return nodata.
3. Does censoring occur before or after rounding? Are the cutoffs strict `<40`
   and `<35`, or inclusive bounds, and do any exceptions vary by metric, tile,
   processing stage or dataset revision? Could you supply a few example cells
   with known below-cutoff versus excluded-domain status for validation?
4. For the airport mosaic, can you provide an airport-to-raster crosswalk giving
   each airport's calculation domain, actual threshold per metric, exposure year,
   contributing product/version and the meaning of its nodata sentinel
   (approximately `3.4e38`)? How are overlaps and airports without supplied grids
   handled? We are not treating a local observed minimum as an airport threshold.
5. Which native extraction path should clients use? On 25 September 2026, road
   Lnight's WCS 1.0 advertised `wksp…` identifier was rejected. Aircraft WCS 1.0
   described native `GeoTIFF` but rejected that format and both TIFF MIME types
   advertised by WCS 2.0. Is an independently downloadable native tile/product
   available for cross-checking the WCS 2.0 result?
6. Requests crossing the road/rail rectangle's west edge were clipped: WCS 1.0
   kept requested width and changed horizontal resolution, whereas WCS 2.0
   returned fewer native cells. Some WCS range metadata also labels acoustic
   bands `W.m-2.Sr-1`. Can you confirm the intended extraction and unit metadata,
   and whether corrections are planned?

We have retained small native requests, full response bytes and checksums and
can supply reproducible examples. Until the encoding and domain rules are
established, we retain numeric zero and TIFF nodata as distinct unknown states,
with no inferred quietness bound. A technical specification or a response that
can be cited with its applicable dataset version would resolve this ambiguity.

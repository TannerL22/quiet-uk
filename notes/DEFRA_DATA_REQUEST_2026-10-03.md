# Defra unthresholded data request — 3 October 2026

Status: **sent on 3 October 2026** through the connected Gmail account; Gmail
returned a message/thread ID and the `SENT` label. The sent message was read
back: recipient, subject and HTML body match the saved outbound request. Its
timestamp is **09:29 BST on 3 October**. Provider response and data
availability are pending. The owner approved step 1: pursue unthresholded Defra
outputs and calculation-domain/encoding definitions.

**Owner-reported update, 3 October:** an acknowledgement says a response may take
a few days. This is recorded from the owner's message, not independently verified
as a received email or treated as a technical answer. The substantive data and
encoding questions remain open. Independent work continues in the
[aircraft and combined-transport readiness report](AIRCRAFT_AND_TRANSPORT_READINESS_2026-10-03.md).

To: `open@defra.gov.uk`

Subject: England Round 4 noise data: unthresholded outputs and calculation-domain definitions

The recipient was verified against the live road dataset's `publicContact` on
3 October 2026. A narrow Gmail sent-mail search found no existing matching noise
request to this recipient. No attachments, copied recipients or private project
files are included.

## Exact message

Hello Defra Open Data team,

I am developing Quiet UK, a map explorer using the original England Round 4
10 m noise grids. I would like to obtain official model outputs for places where
the published rasters have zero or nodata values, and establish how to interpret
those cells without incorrectly treating missing data as quiet.

Your published guidance describes 40 dB reporting cutoffs for most road/rail
indicators and 35 dB for Lnight. Could you help with the following, or forward
this enquiry to the relevant noise-modelling/data team?

1. **Unthresholded outputs:** Are the existing all-road and all-rail Lden, Lday
   and Lnight receiver results retained before the publication cutoffs? If so,
   could you provide access to the original native-grid values, including
   computed values below those cutoffs, with their version, reference period
   and reuse licence? A small representative extract within our Oxford–London
   pilot would be a useful first delivery if national transfer is impractical.
   This is a request for existing outputs, not new modelling or measurements.
2. **Calculation domain:** Is there a versioned receptor mask, geometry or
   cell-status layer distinguishing computed receptors from uncalculated or
   excluded locations? We need to separate below-threshold results from building
   interiors, source-distance limits, water/boundary exclusions, missing inputs
   and other omissions where applicable.
3. **Zero/nodata definitions:** In the published road/rail GeoTIFFs, what exactly
   do unmasked numeric zero and nodata `-96` mean? Does zero exclusively mean a
   calculated result below the cutoff? Are cutoffs applied before or after
   rounding, and are the bounds strict or inclusive? Please identify any
   product/metric/revision exceptions. A citable specification and a few example
   below-cutoff versus excluded-domain cells would be particularly helpful.
4. **Combined transport product:** The live road metadata links to “Combined
   Transport Noise - All Metrics - England Round 4”, catalogue ID
   `112ffd42-a276-4810-8fd1-2adf0a20b5ff`, but its public page reports that the
   entry is not authorised for viewing. Is this product available for public
   access/reuse? If available, which sources and assessment periods does it
   combine, is the combination calculated before source-specific reporting
   cutoffs, and how are missing contributions and its calculation domain handled?

The source records are:

- Road: https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517
- Rail: https://environment.data.gov.uk/dataset/3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947
- Linked combined product: https://environment.data.gov.uk/dataset/112ffd42-a276-4810-8fd1-2adf0a20b5ff

If unthresholded outputs or domain masks cannot be supplied, please indicate
whether they are not retained, are restricted, or have another access route.
Is a versioned public input/configuration package available to reproduce the
below-cutoff results? A referral to the correct data owner would also help.

We retain original GeoTIFFs, request/response records and checksums and can
provide small reproducible examples if useful. Until the definitions are
established, zero and nodata remain separate unknown states in our explorer.

Thank you,
Tanner Looney

## Evidence and response handling

Live public contact, the unchanged published cutoffs, and the inaccessible linked
combined entry were checked using Firecrawl's secondary connection. Retained
metadata/contact checks, send receipt and the exact outbound MIME body are in
`artifacts/provider_request_2026-10-03/`, outside Git. The linked entry is a lead,
not a verified usable dataset. No access restriction was bypassed.

When a reply arrives, preserve the original email and attachment bytes with
checksums. Check dataset/version/metric/period scope, licence, native grid and
cell-status definitions before deriving a new evidence interpretation or
installing data. A general statement must not silently establish every tile's
domain or identify a specific cell as below threshold.

The original broader extraction/airport questions remain in
[the previous draft](PROVIDER_CLARIFICATION_DRAFT.md). This focused enquiry
supersedes that draft as the initial data-access request.

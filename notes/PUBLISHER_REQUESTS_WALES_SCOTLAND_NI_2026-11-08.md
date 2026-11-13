# Wales, Scotland and Northern Ireland data requests — 8 November 2026

Status: **drafts, not sent.** The owner sends these from their own email. When a
reply arrives, keep the original email, and record its answers here as was done
for [Defra's reply](DEFRA_DATA_REQUEST_2026-10-03.md#provider-reply) before
any data from that nation is shown on the map.

These follow step 1 of the research report "Wales Scotland NI noise data"
(8 November 2026). Every fact in the emails comes from the research notes behind
that report. All of it was read from the publishers' public services and
metadata on 8 November 2026. No data file has been downloaded.

Recipients were checked against the live records on 8 November 2026:

| Nation | Address | Where it is listed |
|---|---|---|
| Wales | `data@gov.wales` | Contact on the DataMapWales layer group "Environmental Noise Mapping 2022" |
| Scotland | `environment.protection.team@gov.scot` | "Noise Policy Officer, Scottish Government Environment Protection Team", the point of contact on spatialdata.gov.scot record `6ad962f6-e2f0-4c94-850e-fd69f91984ff` |
| Northern Ireland | `OpenDataEnquiries@daera-ni.gov.uk` | "DAERA Open Data Enquiries", the contact on the OpenDataNI record `noisemapsround4-gdb`. DAERA's noise pages give no separate noise team address |

Each email body below is plain text, ready to copy. Nothing in them is private
to the project, and they include no attachments.

---

## 1. Welsh Government

To: `data@gov.wales`

Subject: Environmental Noise Mapping 2022 (Round 4): reference year, zero values and attribution

Hello,

I am developing Quiet UK, a map that shows official transport noise levels at 10 m resolution. It currently uses Defra's Round 4 strategic noise maps for England. I would like to add the Welsh Government's "Environmental Noise Mapping 2022" road and rail rasters from DataMapWales. Before I do, could you help with the questions below, or pass this to the team responsible for the noise mapping?

1. Reference year. Which calendar year do the traffic and railway inputs describe? Defra's England maps describe 1 January to 31 December 2021. Is a technical report or method note available? It would help to know the inputs used, the distance from each road or railway that was calculated, and whether roads and railways in England near the border were included.

2. Zero values. Inside Wales, does a 0 mean only "calculated, but below the reporting cutoff" (40 dB for Lden and LAeq,16h, 35 dB for Lnight)? Our spot checks suggest the cutoffs are inclusive, so levels of exactly 40 and 35 dB are kept. The rasters also return 0 outside Wales, for example at Hereford (351000, 240000) and in Cardigan Bay (230000, 270000), and the WCS declares no no-data value. Is there an official boundary or calculation-area mask we should use to tell "below the cutoff" apart from "not modelled"?

3. File details. The values are whole decibels. Were the levels rounded or truncated, and was that done before or after the cutoff was applied? What data type and no-data setting do the GeoTIFFs use? If unrounded results are held, could they be made available? Also, the grid's upper-left corner is 146597.1001, 395998.4007 (British National Grid), which puts each cell about 2.1 m east and 3.4 m north of Defra's England grid. Is that origin exact?

4. Other indicators. DataMapWales publishes Lden, Lnight and LAeq,16h. Were Lday (07:00 to 19:00) and Levening also calculated, and could they be published?

5. Licence and attribution. The layer group states the Open Government Licence v3, but each individual layer record says "Not Specified". Could you confirm that OGL v3 applies to the six road and rail rasters, and tell me the attribution wording you would like?

Everything above comes from the public DataMapWales services and metadata, checked on 8 November 2026. I have not yet downloaded the files.

Thank you,
Tanner Looney

---

## 2. Scottish Government

To: `environment.protection.team@gov.scot`

Subject: Round 4 strategic noise maps: zero and no-data values, and airport-only grids

Hello,

I am developing Quiet UK, a map that shows official transport noise levels at 10 m resolution. It currently uses Defra's Round 4 strategic noise maps for England. I would like to add Scotland's Round 4 road and rail "ALL" layers from SEPA, whose grid lines up exactly with England's. Could you help with the questions below, or pass this to the right team?

1. Zero and -96. In the road and rail ALL GeoTIFFs, does 0 mean only "calculated, but below the reporting cutoff", and does -96 mean no data? For England, Defra confirmed in writing that a zero is a calculated level below the cutoff, that the cutoffs are inclusive (levels of exactly 40 dB, or 35 dB for Lnight, are still published), and that values are not rounded. Do the same rules apply to Scotland? It would also help to know how far from each road or railway levels were calculated, and whether results were interpolated across buildings. For England, Defra told us roads were calculated to 3 km and railways to 2 km.

2. Values just under 40 dB. The histogram from the Noise_Road_LDEN_ALL image service shows a small group of cells just below 40 dB, in the bin that starts at 39.51 dB (261,365 cells, against 4.42 million in the next bin). Is the cutoff applied slightly differently from 40.0 dB, and how should these values be read?

3. Negative values. The colour settings of the Scotland's Noise web map record a minimum value of -46.01. Do any layers hold negative values other than -96, and if so, what do they mean?

4. Airport-only grids. Aircraft noise appears only inside the Consolidated layers. Could the separate airport grids for Aberdeen, Dundee, Edinburgh and Glasgow (Lden, Lday and Lnight) be made available under the same licence, with their resolution and lowest calculated level? We would rather not estimate aircraft levels by subtracting road and rail from the combined map.

5. Versions. Noise_Rail_LDEN_ALL.zip was last modified on 13 January 2025, later than the July 2024 date of the other files. Was this layer reissued, and if so, what changed? Two small things for information: metadata record 67d7a1c7-b27d-4d93-86cb-dcb20cf8d364 links to Noise_Road_LEVG_ALL.zip, which returns "not found" (Noise_Road_LEVE_ALL.zip works), and on the Scotland's Noise downloads page the Road Leve ALL row points to the rail Leve image service.

I plan to use the attribution given in the metadata: "© Scottish Government, contains Ordnance Survey data © Crown copyright and database right 2024." Please let me know if you would prefer different wording.

Everything above comes from SEPA's public services and the Scottish Government's metadata, checked on 8 November 2026. I have not yet downloaded the files.

Thank you,
Tanner Looney

---

## 3. DAERA (Northern Ireland)

To: `OpenDataEnquiries@daera-ni.gov.uk`

Subject: Round 4 noise maps: zero values, calculation areas and a possible Lnight issue

Hello,

I am developing Quiet UK, a map that shows official transport noise levels at 10 m resolution. It currently uses Defra's Round 4 strategic noise maps for England. I would like to add DAERA's Round 4 maps for Northern Ireland. Could you help with the questions below, or forward this to the noise mapping team?

1. Zero values. On the Major_Roads layers, some points far from any major road read 0, for example in the Sperrins (260000, 390000, Irish Grid) and the middle of Lough Neagh (302000, 375000). Points in County Donegal (200000, 440000) and the North Channel (362000, 420000) read NoData. Outside the Belfast agglomeration, can a 0 mean "not calculated" (more than 3 km from a major road) as well as "calculated, but below the cutoff"? Inside the agglomeration and on the airport layers, does 0 mean only "below the cutoff"? And are the 40 dB and 35 dB cutoffs inclusive, so that levels of exactly 40 and 35 dB are kept?

2. Calculation areas. Could you provide the calculation areas as GIS polygons: the 3 km corridors around major roads, the Belfast agglomeration boundary used, the railway calculation area and the airport calculation areas? Are any of these in the NoiseMapsRound4_gdb download?

3. A possible Lnight issue. At 340500, 373500 (Irish Grid, east Belfast), the Agglomeration_Roads layers read Lden 50.80, Lday 50.63 and Levening 47.62, but Lnight 0. At the same point, Agglomeration_Consolidated Lnight reads 39.84 and Major_Roads Lnight reads 37.01. It looks as if night levels between 35 and 40 dB may have been set to 0 in the all-roads Lnight layer. We have only checked this one point, so could you take a look?

4. File details. In the File Geodatabase rasters, which value marks no data, what is the pixel type, and are the rasters on the same grid as the map services? Were the map grids calculated at 4 m above ground?

5. Licence and downloads. OpenDataNI lists the data under the Open Government Licence v3, while the ArcGIS items link to a general Northern Ireland government terms page. Could you confirm that OGL v3 applies, and give the attribution wording you would like, including for any OSNI-derived inputs? For information, the shapefile and GeoJSON exports for these layers on the DAERA open data hub return a server error (HTTP 500), although the reply to request DAERA/26-166 refers to them.

Everything above comes from DAERA's public map services and open data records, checked on 8 November 2026. I have not yet downloaded the File Geodatabase.

Thank you,
Tanner Looney

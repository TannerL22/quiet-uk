# Regional explorer: formative usability check

Status: **ready to run; no participant sessions completed**. This is a short
protocol for five people unfamiliar with Quiet UK. Browser regression checks are
not participant observations. The product remains a general-purpose noise map;
participants can bring their own reason for comparing places.

## Setup

Use the current source and interpretation release `pilot-70542da395300f52dd27`.
Record source commit, release ID, browser and viewport for each session. Include
desktop and phone-sized use, and a keyboard-only pass. Start with an empty saved
comparison in a separate test browser profile, preserving personal saved places.
Allow approximately 15–20 minutes. Ask participants to explain what they think
the map means; let them attempt tasks without coaching. Record intervention when
help becomes necessary, then continue to understand the difficulty.

## Tasks and observations

| Task, given without the success criteria | What to observe |
|---|---|
| Find where this map has detailed data, then explore Oxford. | Discovers coverage outlines/area selector; understands only the selected area is coloured. |
| Find `55, -3` and explain what the result tells you. | Recognises outside coverage; does not infer silence, quietness or low exposure. |
| Inspect `51.464852, -0.447622` with Road and Night selected. What can you conclude about sound here? | Recognises road is one source; notices separately reported aircraft. Does not treat road level as total noise. |
| At the same point, select Rail. Explain the missing value and what further information you would want. | Unknown is not zero dB, a certified bound or proof of quiet. |
| Compare this place with Oxford (`51.752, -1.2577`) for a purpose you choose. | Saves/names places, finds the comparison, distinguishes source/time indicators and supported differences. |
| Copy your view link and reopen it in another tab. | Source, metric, location and camera are restored; recognises this local link requires the app on the same computer. |
| If the historical overview is installed, visit it and return to your detailed view. | Notices the change of construction/metric and restored detailed selection. |

After the unaided tasks, ask whether these are current measurements, whether the
map reveals individual loud events or quiet intervals, and what they would
improve first. Avoid introducing house-search or hiking as the only intended use.

## Record and decide

Use anonymous IDs P1–P5. For each task record completion without help, time,
interpretation in the participant's own words, and any assistance. Do not invent
participant results from automated checks.

The formative gate is at least four of five completing the core tasks without
coaching, with **none** interpreting unknown/outside coverage as silence and none
missing aircraft relevance in the Heathrow task. Report individual failures;
correct the relevant UI and repeat those tasks. Five people provide formative
feedback, not a population-wide usability claim or acoustic validation.

## Implementation checks already performed

- Regional-only server starts with no national dependency; tests exercise an
  extracted evidence bundle and prohibit national construction/access.
- Regional home and APIs work; unavailable overview routes return clear 404s.
- Live browser: coordinate navigation changes regions, outside coverage remains
  explicit, keyboard centre inspection works, and the selected source/time is
  retained in comparison and saved views.
- Main launcher starts the updated local app; explicit overview round-trip
  restores a Road/Night point. An early-click state-loss defect was found and
  fixed by exposing the overview link only after the detailed view is ready.
- At 390 px, point evidence and the Heathrow aircraft warning fit without page
  horizontal overflow. The detailed table is folded; its data remain available.
- Numerical seam regressions compare adjacent historical tiles against a single
  continuous rendering canvas. Scientific source values and release IDs are unchanged.

Remaining serving work includes immutable reader snapshots, avoiding repeated
whole-file hashing, and bounded/prebuilt evidence downloads. This increment does
not claim those performance or deployment gates are complete.

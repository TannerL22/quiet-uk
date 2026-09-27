# P1: partial usability observations, 27 September 2026

Status: session stopped at the owner's request; partial findings retained. These are human
observations relayed by the project owner, not direct observation by the agent.
No overall usability gate result is assigned.

## Context

- Device: project owner's Windows laptop; browser: Firefox.
- Browser version, viewport, prior familiarity, assistance and task time: not reported.
- Intended baseline: source `01e7ab806c7df9c72f3a03d5dfada49efea799c1`, release
  `pilot-be13e2d6e9145df58aa6`. The participant's Firefox URL/release has not been
  independently checked; the supplied in-app browser state is a different browser.
- UI unchanged during this session; automated browser interaction paused.

## Reported observations

For the first task (find detailed coverage, explore Oxford, explain colours and
uncoloured areas), the owner reported:

- The participant easily found covered space because it was the only coloured area.
- They easily understood red as louder.
- They were somewhat confused by the road/rail/aircraft choices. They partly
  understood them but wanted an answer about sound at a place regardless of source.
- They noticed that many locations returned Unknown.

These are paraphrases of the owner's account, not verbatim participant quotes.
The account's phrase "how long is it going to be" is ambiguous; the source-agnostic
intent is clear from the surrounding report, but no duration requirement is inferred.
Oxford navigation completion and the interpretation of uncoloured/unknown areas
were not established. Ease of finding colour does not establish recognition of
all available areas, including the unselected Heathrow area.

In the Unknown follow-up, the owner reported that the participant assumed there
was no data for that place, so its sound level could not be known. This supports
understanding Unknown as unavailable information rather than silence or a quiet
bound. It does not establish understanding of the underlying zero/nodata encoding,
outside coverage, or every source. Whether they would visit and what they would
do next were not reported. The repeated word "long" is not interpreted as a
separate duration requirement.

For the `55, -3` task, the owner reported that the result said Outside coverage
and the participant interpreted this as data not having been gathered for that
location. No inference of silence or quietness was reported. This supports the
intended product-level meaning: this release has no available detailed evidence
there. It does not establish that no provider data exists elsewhere. Search
completion time and assistance were not reported.

For Heathrow Road/Night, the owner relayed: "the sound is 37.4 dba, which means
at night the roads make this much noise". The participant correctly attributed
the reported value to roads at night. Their response did not mention aircraft,
annual averaging or modelling. Those omissions do not by themselves establish a
misunderstanding or failure to notice the aircraft cue. Aircraft relevance and
temporal interpretation remain unconfirmed. No task time or assistance was reported.

After the neutral follow-up (what else the screen tells them), the owner relayed:
"well the color shows me the area is quiet (atleast in the light blue part) for
cars/raods". This prompted elaboration continues to distinguish road noise from
overall sound. Aircraft was not mentioned in either response: the task's aircraft
relevance criterion has not been demonstrated. This is a discoverability concern,
not proof that the participant ignored a visible cue or believes aircraft absent;
their Firefox viewport and cue visibility were not observed. The participant's
word "quiet" is their interpretation of the colour, not a validated quietness
classification or evidence of below-cutoff ranking.

For Rail/Night at the same point, the owner relayed: "tells me unknown, meaning
no data. I would just want to know the actual data, seems pretty obvious". This
again supports understanding the missing result as unavailable information, with
an unmet need for the noise data itself. No claim of silence or a numerical bound
was reported. The account does not distinguish participant wording from owner
commentary. Further repetitive missing-label probes are unnecessary for this
session; move on to the comparison workflow. Completion time and assistance were
not reported.

## Interpretation and candidate changes (not yet validated)

1. Coverage and relative loudness colours offered useful starting cues.
2. A source-first interface may not match the participant's place-first question.
   A candidate is an initial place summary showing available source values together,
   with layer controls for exploration and explicit missing evidence. This is a
   design hypothesis, not permission to present a combined total: current sources
   do not establish overall ambient sound or moment-to-moment experience.
   The Heathrow follow-up strengthens the case for testing the prominence of
   other-source evidence: road scope was understood, but aircraft relevance was
   not expressed even after a general prompt. Keep that prompt-assisted result
   distinct from unaided discovery in the session assessment.
3. Unknown results affect usefulness, not just wording. The follow-up indicates
   the participant understood the absence of a supported answer; no quietness
   misinterpretation was reported. Better explanations alone cannot fill evidence
   gaps; missing data must not become a quietness score.

## Session end and delivery decision

The owner stopped the questions and prioritised data/coverage expansion. The
comparison task was offered but no result was received. View sharing, historical
navigation and the closing questions were not completed. Do not resume recruitment
or treat a five-person study as a prerequisite for expansion unless requested.

The partial session supports comprehension of source-specific road results,
Unknown and outside coverage. It identifies unmet demand for more available data
and a source-independent place answer. Aircraft discoverability remains unresolved.
These findings do not constitute passing the original five-person gate. The app
was not changed during the session. Coverage expansion is the next delivery
priority; retain the source warnings and evidence checks while doing it.

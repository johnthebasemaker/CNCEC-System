---
format: 1920x1080
duration: 30s
message: "GI Hub ERP v1.2.0 makes multi-site inventory, tooling and access provably correct"
arc: Promise → Proof → Proof → Proof → Lockup
audience: General Industries management and internal users
mode: autonomous
music: none
---

## Frame 1 — Title card

- scene: "GI HUB" lands heavy, "ERP" slides in beside it, a gold v1.2.0 pill drops under, a hairline draws across
- duration: 5s
- poster: 3.4s
- transition_in: cut
- status: animated
- blueprint: kinetic-type-beats (Reproduce)
- asset_candidates: none
- focal: the GI HUB wordmark
- roles: n/a — no captured candidates; every element is drawn type or rule
- src: compositions/frames/01-title.html

Open cold on the name. No preamble and no category explanation — the audience
already knows what a warehouse system is. The thesis line underneath is the
whole promise the next three frames pay off: one source of truth across every
site. The hairline that draws left-to-right at the end is the film's carrier
element; it reappears at every frame boundary and closes the piece.

Reproduce: the statement builds across full-screen beats, each its own move,
onto a spring-pop payoff — the blueprint's signature. The payoff object is the
gold version pill.

Scene 1 (0.0–1.3s): dark ground alone, then **GI HUB** slams in centred, heavy
and tight-tracked, one word arriving per beat — `kinetic-beat-slam`. Centred
template, ~55% of frame width, single depth layer. Nothing else on screen.
Scene 2 (1.3–2.4s): **ERP** enters from the right on the same baseline and
settles hard against the wordmark, closing the lockup — `kinetic-beat-slam`
carried at reduced travel. A faint grid field fades up behind at low opacity as
the ground. Centred, still ~55%.
Scene 3 (2.4–3.4s): the gold **v1.2.0** pill spring-pops beneath the lockup —
`spring-pop-entrance`, the payoff. Centred stack, 2 depth layers.
Scene 4 (3.4–5.0s): the thesis line *"Multi-site warehouse inventory. One
source of truth."* mask-reveals from below word by word — `waterfall-entry` —
and the gold hairline draws left-to-right underneath it via `svg-path-draw`.
The frame then holds STILL on the completed lockup; no drift, no breathing.

handoff_out: gold hairline — full frame width at y≈72%, x centred, scale 1,
opacity 1, static at the cut (it has finished drawing and is not moving).

## Frame 2 — Smart Material Estimator

- scene: Two stock tiers enter from opposite wings and hold apart — gold "available now", amber "on order" — never merging
- duration: 6s
- poster: 4.2s
- transition_in: wipe
- status: animated
- blueprint: comparison-split (Adapt)
- asset_candidates: none
- focal: the paired stock-tier columns
- roles: n/a — no captured candidates; the tier columns are drawn elements
- src: compositions/frames/02-estimator.html

The first proof beat. The estimator's whole argument is a separation: stock
physically on the shelf answers "can we build it today", and stock still on
order answers a different question entirely. The two tiers must therefore
arrive as two objects and stay two objects — if they ever visually merge into
one bar, the frame has told the opposite of the truth. The 1,313-comparison
parity figure lands last, as the evidence that both engines agree.

Adapt: keep the mirrored book-open entry from opposite wings and the inner-edge
badge spring-pop — the signature. What changes is what the pair is: not two
features, but two stock tiers that must be read as separate. A visible gutter
of dark ground runs between them for the whole shot and never closes.

Scene 1 (0.0–1.2s): the label chip **01 — SMART MATERIAL ESTIMATOR** waterfalls
in top-left in mono caps, and the headline *"Know what you can build today."*
mask-reveals beneath it — `waterfall-entry`. Left-weighted asymmetric 55/45,
right half still empty. The carried gold hairline sits above, static.
Scene 2 (1.2–2.6s): the two tier columns enter from opposite wings with
mirrored 3D book-open tilts and settle side by side in the right half, a hard
dark gutter between them — `split-tilt-cards`. Gold column labelled AVAILABLE
NOW, amber column labelled ON ORDER. 3 depth layers.
Scene 3 (2.6–3.8s): both columns fill upward from their baselines to different
heights — `stat-bars-and-fills`. They fill independently and at no point touch;
the gutter holds. First supporting line reveals left: *"Turns a work scope into
an exact material demand."*
Scene 4 (3.8–5.0s): an inner-edge pill badge spring-pops on each column —
`spring-pop-entrance` — reading NOW and PENDING. Second supporting line reveals
left: *"Never mixes the two."*
Scene 5 (5.0–6.0s): **1,313** counts up in gold beneath the columns —
`counting-dynamic-scale` — under the words *"checks, both engines agree."* The
frame holds still on the two separated tiers.

handoff_in: gold hairline — full frame width at y≈72%, x centred, scale 1,
opacity 1, static on arrival; it rises to y≈12% over Scene 1 and stays there.
handoff_out: gold hairline — full frame width at y≈12%, x centred, scale 1,
opacity 1, static at the cut.

## Frame 3 — Asset & Tool Geolocation

- scene: A rack grid self-assembles cell by cell, a scan line sweeps it, one cell lights and a location pin pulses
- duration: 7s
- poster: 4.8s
- transition_in: wipe
- status: animated
- blueprint: grid-card-assemble (Adapt)
- asset_candidates: none
- focal: the rack grid, and the single lit cell within it
- roles: n/a — no captured candidates; the grid, sweep and pin are drawn elements
- src: compositions/frames/03-assets.html

The second proof beat, and the most physical one. The grid assembling is the
warehouse itself coming into view; the sweep is the Locator working; the single
lit cell is the answer it gives back. The reverse direction — scan a rack, get
the checklist of what should be on it — is the line that earns the beat, because
it is the one that changes somebody's morning.

Adapt: keep the staggered self-assembling cascade into a grid — the signature.
What changes is that the assembled grid is not a feature wall but a rack
elevation, and the payoff is one cell resolving rather than the array itself.

Scene 1 (0.0–1.2s): the label chip **02 — ASSET & TOOL GEOLOCATION** waterfalls
in top-left, headline *"Every tool, tracked to the shelf."* mask-reveals under
it — `waterfall-entry`. Left-weighted asymmetric 45/55; right half empty.
Scene 2 (1.2–2.8s): the rack grid self-assembles cell by cell in a staggered
cascade across the right half, cells arriving in reading order —
`depth-scatter-assemble`. Thin hairline cell borders, no fills. 3 depth layers.
Scene 3 (2.8–4.0s): a cyan scan line sweeps the grid left to right —
`gradient-text-sweep` applied as a surface sweep — while the first supporting
line reveals left: *"Serialised assets, full custody history."*
Scene 4 (4.0–5.4s): the sweep passes and ONE cell latches gold; a location pin
spring-pops above it and pulses twice — `spring-pop-entrance` then
`ambient-glow-bloom`. Second line reveals left: *"GPS-tagged the moment a tool
moves."*
Scene 5 (5.4–7.0s): three short checklist rows waterfall into the lit cell's
right margin, each ticking as it lands — `waterfall-entry` — under the closing
line *"Scan a rack, see everything that should be on it."* The grid holds still
with the one cell lit.

handoff_in: gold hairline — full frame width at y≈12%, x centred, scale 1,
opacity 1, static on arrival; it stays at y≈12% for the whole frame.
handoff_out: gold hairline — full frame width at y≈12%, x centred, scale 1,
opacity 1, static at the cut.

## Frame 4 — Enforced role-based access

- scene: Thin lines draw a shield; 126 counts up inside it against a static 143
- duration: 6s
- poster: 4.4s
- transition_in: wipe
- status: animated
- blueprint: dataviz-countup (Adapt)
- asset_candidates: none
- focal: the 126 / 143 hero number
- roles: n/a — no captured candidates; the shield is a drawn SVG outline
- src: compositions/frames/04-access.html

The third proof beat. This is the least visual claim and the most important
one, so the number does the work: 126 of 143 counts up and stops, and the
closing line explains why the shape matters — a check written centrally is
closed by default, so a feature added next year is locked down the day it is
written rather than the day somebody remembers it.

Adapt: keep the count-up as hero and the camera landing on one metric — the
signature. What changes is the container: not a progress ring but a shield that
draws itself from thin strokes, and the count-up runs inside it against a fixed
denominator so the ratio, not the number, is the claim.

Scene 1 (0.0–1.1s): the label chip **03 — ENFORCED ROLE-BASED ACCESS**
waterfalls in top-left; headline *"Permission by design, not by memory."*
mask-reveals beneath — `waterfall-entry`. Left-weighted asymmetric 50/50; right
half empty.
Scene 2 (1.1–2.4s): the shield outline draws itself stroke by stroke in the
right half — `svg-path-draw`, starting at the apex and closing at the point.
Hairline weight, no fill. 2 depth layers.
Scene 3 (2.4–4.0s): **126** counts up from 0 inside the shield, scaling
slightly as it climbs — `counting-dynamic-scale` — and stops hard on 126. The
static **/ 143** sits beside it in muted ink and never animates, so the ratio
reads. First supporting line reveals left: *"Enforced centrally — never screen
by screen."*
Scene 4 (4.0–5.0s): a soft gold bloom rises behind the shield and settles —
`ambient-glow-bloom`. Second line reveals left: *"Data-changing actions blocked
for view-only accounts."*
Scene 5 (5.0–6.0s): the closing line waterfalls in beneath: *"A feature added
next year is locked down the day it is written."* The frame holds STILL — the
number does not re-animate and the shield does not breathe.

handoff_in: gold hairline — full frame width at y≈12%, x centred, scale 1,
opacity 1, static on arrival; it stays at y≈12% for the whole frame.
handoff_out: gold hairline — full frame width at y≈12%, x centred, scale 1,
opacity 1, static at the cut.

## Frame 5 — Lockup

- scene: Everything clears to black; GI HUB ERP resolves centre-screen over the gold hairline with the version line under it
- duration: 6s
- poster: 4.5s
- transition_in: cut
- status: animated
- blueprint: logo-assemble-lockup (Reproduce)
- asset_candidates: none
- focal: the GI HUB ERP lockup
- roles: n/a — no captured candidates; the lockup is set type over the carried rule
- src: compositions/frames/05-lockup.html

The close. The hairline that has carried through every seam finally settles
under the wordmark and stops moving. "Available now" is the only claim, and the
General Industries line grounds it. Stillness is the payload here — after four
frames of continuous motion, the held lockup is what makes the piece feel
finished rather than merely ended.

Reproduce: the mark comes to exist on a cleared stage and resolves into a
centred lockup extended to the CTA line — the blueprint's signature, taken in
its "already assembled, settling as satellites clear" form.

Scene 1 (0.0–1.0s): the hard cut does the clearing — the frame opens on the
dark ground with nothing on it but the carried gold hairline, which glides from
y≈12% to centre while the grid recedes behind it. Reconstructing the previous
frame's furniture only to throw it away was tried and cut: it could not be made
to match Frame 4 exactly at the seam, and a near-miss reads worse than a clean
cut.
Scene 2 (1.0–2.2s): **GI HUB ERP** mask-reveals word by word directly above the
settled hairline — `waterfall-entry`. Centred, ~50% of frame, 1 depth layer.
Scene 3 (2.2–3.4s): the gold line *"v1.2.0 — Available now."* spring-pops
beneath the rule — `spring-pop-entrance`.
Scene 4 (3.4–4.4s): the muted line *"General Industries · Multi-site inventory,
procurement and planning."* fades up under it — no travel, opacity only.
Scene 5 (4.4–6.0s): everything holds absolutely still. A single slow bloom
behind the lockup settles to nothing — `ambient-glow-bloom` — and the frame
rests. This is the only true exit in the piece.

handoff_in: gold hairline — full frame width at y≈12%, x centred, scale 1,
opacity 1, static on arrival; it travels to y≈50% during Scene 1 and stops.

## Video direction

**One continuous camera, five stations.** The film reads as a single move
across a dark field, not five slides. The **gold hairline is the carrier
element**: it draws itself at the end of Frame 1, rides to the top of frame for
the three proof beats, and comes back to centre to become the rule under the
closing lockup. It is never re-drawn and never dropped — every seam hands it
over at a stated position, so the cut is the only thing that changes.

**Direction of travel is left-to-right throughout.** Every label chip and
headline lives on the left; every piece of evidence — tier columns, rack grid,
shield — resolves on the right. The eye therefore always moves the same way,
and the three proof beats read as one sweep rather than three restarts.

**Reveal discipline.** No frame shows more than its opening line at t=0. Each
supporting line arrives on its own beat, and every frame places a genuine
reveal in its back half — the 1,313 count on Frame 2, the checklist rows on
Frame 3, the closing line on Frame 4. Nothing is dumped at entrance.

**Held beats, placed deliberately.** Frames 2, 3 and 4 each end on a still
read of roughly one second, and Frame 5 holds for a full 1.6s. After four
frames of continuous motion the final stillness is the payload — there is no
back-half drift, no breathing, and no idle wobble anywhere in the piece.

**Palette discipline.** Gold is the only accent that carries meaning: it marks
the product, physical stock, the located cell, and the closing claim. Cyan
appears exactly once, as the scan sweep on Frame 3, so it reads as an action
rather than a second brand colour. Amber appears exactly once, as the on-order
tier, so it reads as "not yet here".

**Silent by design.** No narration, no music, no sound effects. The reveal
pacing therefore has to carry the rhythm on its own, which is why every frame
is cued in windows rather than run as one continuous build.

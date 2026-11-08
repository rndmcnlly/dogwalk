# Sound design notes: earcons after TNG

Research and rationale for the prototype earcon vocabulary in `earcon_lab.py`. Nothing here is deployed; `cf_worker/src/audio.ts` remains the source of truth. Disciplines in play: film and television sound history, signal analysis, synthesis (FM, additive, amplitude modulation), psychoacoustics (roughness, residue pitch, fatigue), telephony (PSTN band, G.711), and auditory display design (earcons as a learnable code).

Open the lab with `uv run --script earcon_lab.py`. `--render DIR` writes WAVs of both sides and prints their levels; `--check` syntax-checks the page script with Node.

## 1. How the TNG computer sounds were made

### What the sources establish

- **Who.** Jim Wolvington designed the sound effects for The Next Generation, Deep Space Nine, and Voyager, and supervised sound on *Generations* and *First Contact*. Bill Wistrom was the series' supervising sound editor, covering "everything but the effects." The TNG crew won the sound Emmy for its first four seasons. Wolvington trained in musique concrète at the University of Utah and describes himself as listening to "difficult" music. (Kenny; Buskin; Memory Alpha.)
- **What instrument.** TNG effects were built and *performed* on a New England Digital Synclavier. Wistrom: "We started with Synclaviers." Wolvington's 1990s rig was "a Synclavier, Tascam DA-88s, a little mixer and a patchbay," mirrored at Modern Sound in LA so he could send sequences over a modem from Vermont. For *Generations*: "a basic Synclavier setup (20 MB RAM, 32 voices) ... performed on the Synclavier, then laid back to 24 tracks." "The Synclavier allows me to audition effects and resample back into poly-memory ... That way you can get the layers. But you can also go too far." (Buskin; Kenny.)
- **What a Synclavier is.** A Dartmouth-born digital workstation: a 16-bit ABLE minicomputer driving 8-bit voice cards that do additive synthesis, two-operator FM, and "Timbre Frame" resynthesis, plus sampling ("poly-memory") and a 16-track "Memory Recorder" sequencer. Each voice stacks up to four *Partial Timbres*, each a 24-harmonic additive carrier with its own FM modulator and envelopes (US Patent 4,554,855). The Arturia Synclavier V manual credits Wolvington among the instrument's notable users. (Arturia.)
- **Lineage, not invention from scratch.** Wolvington is explicit that he built on sounds made by Alan Howarth, Frank Serafine, Doug Grindstaff (the original series), Mark Mangini, and Richard Anderson. The TNG transporter is the original series' chord with "a series of tri-tones, performed on the Synclavier" added at Roddenberry's suggestion of "a sense of mystery." Howarth's Enterprise rumble for *The Motion Picture* came from the white-noise generator of a Prophet-10 plus recorded fans and machinery, varispeeded on a TEAC 8-track ("That was my sampler"). (Kenny.)
- **Restraint was a production value.** Effects mixer Adam Jenkins on the "telemetry tracks [the computer noises and electronic sounds that accompany high-tech-looking equipment]": "High-tech is boring ... fatiguing on the brain, which is why we would consistently pull back ... Otherwise, it would begin to sound like a phone is ringing through the entire scene." Executive producer Rick Berman had button sounds pulled down: "Can you imagine anything more annoying in the 23rd century than if every time you pushed a button, it went 'boodle-oop?'" Wolvington himself felt some TNG updates of original-series sounds came out "incredibly bland by comparison." (Kenny; Buskin.)

### What is speculation

No source found gives patch-level detail for the LCARS panel beeps, the acknowledgement chirps, or the "working" burbles. The claims below are inferences from the instrument's architecture and from the spectra in section 2, not documentation:

- That the beeps were synthesized on the Synclavier's FM/additive partials rather than sampled from elsewhere. Plausible (the spectra are clean harmonic stacks with formant peaks and narrow sidebands, which a 24-harmonic additive carrier with light FM produces naturally), but the Synclavier was also a sampler, and Wolvington layered and resampled.
- That "random" burbles used sample-and-hold. The two burbles measured below are *looped fixed sequences* drawn from a small pitch set, which points to the Memory Recorder or looped Timbre Frames rather than a random source. Sample-and-hold pitch patterns are the standard way synth tutorials recreate the effect (for example the Vi-Control thread below), which is a different claim from how the show did it.
- A widely shared Science Fiction Stack Exchange answer says the evidence "strongly suggests all the sound effects were created with a Synclavier," mainly by inference from the Synclavier's Wikipedia page. The trade-press interviews support "built and performed on a Synclavier," not "synthesized from scratch on one."

### Sources

1. Tom Kenny, *Sound for Picture: The Art of Sound Design in Film and Television* (Mix Books), chapter on *Star Trek Generations* with sidebar "Remembering 1979." Wolvington, Howarth, Adam Jenkins, and Berman quotes. A PDF circulates at <https://library.compasscollege.art/images/Books/Tom%20Kenny%20Sound%20for%20the%20image.%20The%20art%20of%20sound%20design%20in%20film%20and%20television.pdf>. Most useful single source.
2. Richard Buskin, "Sound Design of Star Trek," *Studio Sound* (c. August 1997), reproduced at <http://www.filmsound.org/studiosound/pp_startrek.html>. Wolvington's Synclavier workstation, Wistrom's workflow, the "bland by comparison" remark.
3. Arturia, *Synclavier V User Manual*, <https://downloads.arturia.net/products/synclavier-v/manual/synclavier-v2_Manual_2_0_EN.pdf>. Partial Timbre architecture, FM ratio controls, Timbre Frames, credits Wolvington.
4. Memory Alpha: [Bill Wistrom](https://memory-alpha.fandom.com/wiki/Bill_Wistrom), [Jim Wolvington](https://memory-alpha.fandom.com/wiki/Jim_Wolvington). Credits and tenure.
5. "How were Star Trek: The Next Generation computer beeps/bloops sound effects created?", Science Fiction Stack Exchange, <https://scifi.stackexchange.com/questions/192630>. Fan reasoning; returns 403 to scripted fetches, cited from search snippets.
6. Alan Howarth interview, FACT, <https://www.factmag.com/2015/10/28/alan-howarth-interview-escape-from-new-york/>, and his site <https://alanhowarth.com/>. Film-side lineage only; found in search, not read in depth (Kenny covers the same ground).
7. Vi-Control, "Synth to make completely random notes / bleeps," <https://vi-control.net/community/threads/synth-to-make-completely-random-notes-bleeps.146640/>. The generic sample-and-hold recipe, for contrast.

**Fan archives hosting the effects** (copyrighted show audio; used here for analysis only, never copied into this repo): TrekCore, <https://www.trekcore.com/audio/> (its mod_security rejects plain `curl`; a browser user agent and referer work), and Star Trek Dimension, <https://www.stdimension.org/MediaLib/computere.htm>.

## 2. Spectrogram findings

Sixteen TrekCore clips were downloaded to `/tmp/tng_analysis/downloads`, converted with `afconvert`, and analyzed with scripts kept in a separate directory (`python -I`, paths as arguments): spectrograms, onset segmentation, envelope autocorrelation for gating rates, per-pulse dominant pitch, and 2^18-point spectra for sideband spacing. Most source files are 48 kHz MP3s, so values within about 10 Hz are approximate.

| Clip (TrekCore name) | Structure | Numbers |
|---|---|---|
| Computer Beep 4 (two-blip acknowledgement) | two identical blips | 74 ms each, 116 ms onset interval (8.6/s). Harmonics of 1035 Hz with a formant on h3: h1 -29 dB, h2 -8, **h3 0**, h4 -20, h5 -24. Sidebands near ±35 and ±87 Hz at -15 to -23 dB (slow AM/FM). |
| Computer Beep 10 | a run of ~20 ms steps (7.6, 4.2, 1.4, 6.3 kHz), then a held tone | Held tone: 4189, 4450, 4712, 4974, 5236, 5498 Hz within 4 dB of each other = **harmonics 16 to 21 of 262 Hz (middle C)**. Either FM with c:m near 16:1 at high index or a bright pulse through a resonant band-pass. ±26 Hz micro-sidebands. |
| Computer Beep 1 | one tone, a 150 ms break, then held | 1334 Hz with sidebands at **±110 Hz** (1224 at -4 dB, 1443 at -19, 1115 at -24); envelope period 9 ms confirms a ~111 Hz modulator, c:m about 12:1, index about 1. Asymmetric sidebands imply mixed AM and FM. |
| Processing 1 (burble) | gated pulse train | First half: 16-17 ms pulses every **26 ms (38 Hz)**; second half: 35 ms pulses every **46 ms (22 Hz)**. Pulse pitches come from a fixed set {2862, 3639, 4506, 5479, 6759 Hz}; the subsequence 6759/3639, 6759/2862, 6759/4506 x5 recurs exactly at 86 ms and 376 ms: **a loop, not randomness**. Timbre is a 1:2:3:4 stack on ~1830 Hz. |
| Processing 3 | sustained warble with interjected blips | 1900 Hz carrier with sidebands on a **100 Hz grid** (1800 to 2100, 3600 to 4300): c:m 19:1. Blips at 2.7 and 3.1 kHz; closes with a 5.2 kHz gated burst and a 1 to 1.5 kHz figure. |
| Computer Beep 40 (long burble) | notes over a gated bed, closing run | 44-48 ms notes at 3790 Hz paired with 2527 Hz (ratio 1.50, **a fifth**). Ends with a **falling run 3793, 1758, 888 Hz** in about 130 ms (two octaves, ~45 ms steps): a closing gesture. |
| Input Failed, Deny Beep 1 | fast rising staircase, then a buzz | Staircase of 25-50 ms blips from ~2 kHz up to ~6 kHz. The buzz's partials all sit on a **74.6 Hz grid** (603 = h8, 676 = h9, 1352/1425/1499 = h18-20): a very low, rough fundamental with formants near 600 Hz and 1.4 kHz, re-struck every 110-125 ms, four times. |
| Hail Beep 1 | three pulses | 432 Hz, 66-70 ms pulses, sidebands every 28 Hz (a purr). |
| Computer Work Beep | ticks over a bed | 3378 Hz, 5-12 ms ticks, ±24 Hz sidebands, over a 983 Hz tone. |
| TNG Chirp 1 (communicator) | click plus glide | Broadband click attacks and a glide falling from ~5.6 to ~3.6 kHz; almost all energy above 3.4 kHz. |

Patterns across clips:

1. **Formant on an upper harmonic.** The beeps put their energy on h3 (or on h16-21 of a low fundamental) rather than on the fundamental. That is what makes them glassy and small.
2. **Element lengths of 16 to 75 ms**, gated at 20 to 40 Hz for "working," with re-strikes every 110 to 125 ms for alerts.
3. **Roughness for refusal.** Denials are built on a ~75 Hz periodicity, squarely in the 30-150 Hz range where amplitude fluctuation is heard as roughness.
4. **Consonant intervals in routine figures** (the 3:2 pair), descending runs for closing gestures, rising staircases as a lead-in to alerts.
5. **Too bright for a phone.** Spectral centroids run 1.1 to 6.4 kHz; the chirp and much of the burble sparkle lie above the PSTN band. A phone-line vocabulary has to keep the formant low (the lab's sounds put it between about 0.4 and 2.6 kHz) and let residue pitch carry the fundamental.

## 3. The lab's vocabulary

### A semantic code instead of per-sound taste

Each earcon carries a code in `CODES` (one table, easy to revise); `MAP` turns each axis value into synthesis parameters; `design()` composes them. The synthesis is fully deterministic: no random, no seeded noise. TNG's random-sounding burbles turned out to be fixed loops anyway, and where the lab uses a pattern, its shape encodes what is happening.

| Axis | Values | Carried by | Parameters |
|---|---|---|---|
| source | system, walker, dog, caller | timbre family | FM c:m ratio, index envelope, attack, decay, glide, home pitch |
| polarity | onset, offset, state | contour | rise from home by the interval, fall from home by it, or level |
| layer | fg, mid, bg | loudness, length, brightness | gain 0.28 / 0.22 / 0.14; note lengths 100+190 / 90+150 / 40+70 ms; FM index x1.0 / 0.75 / 0.45 |
| normality | routine, abnormal | harmonicity | routine: scope's just interval, integer c:m, a fifth as dyad partner. abnormal: tritone (sqrt 2), modulator x sqrt 2 (inharmonic), a minor second as dyad partner, 75 Hz amplitude roughness at depth 0.5 |
| demand | none, optional, required | repetition | 1, 2, or 3 statements of the gesture |
| initiator | caller, news | texture | caller's own action: monophonic, 0.8x length. news: last note becomes a dyad |
| scope (lab addition) | call, session, turn, step, follow, take | interval size | octave for call and session, fifth for turn, major third for step, major sixth for follow (kept from the current table), fourth for take |
| activity (lab addition) | read, edit, execute, execute_ongoing, speak | rhythmic shape, replacing contour | read: two pips at 1.5x home (a glance). edit: low-high-low (back and forth). execute: three 16 ms pulses on a 26 ms period (the measured TNG processing gate). execute_ongoing: the same gate for eight pulses. speak: a rise and fall like a phrase |

### Families (source)

All four homes spell one A minor seventh chord (A4 440, E5 659, G5 784, C6 1047), so overlapping earcons stay consonant unless they are coded abnormal.

- **system**, home G5: 1:1 FM, index 2.6 decaying to 0.3 over 25 ms, 1 ms attack, an 8 ms upward pitch flick from 0.92x. The LCARS panel chirp: bright attack that mellows.
- **walker**, home E5: 1:2 FM (odd harmonics, hollow), index 1.4 to 0.2 over 60 ms, a soft 12 ms attack, level 0.7. The Voice Agent's own attention, breathier than anything a dog makes.
- **dog**, home A4: 3:1 FM, index 1.6 to 0.6, 1.5 ms attack. Energy sits on the third harmonic like the measured Computer Beep 4, while residue pitch keeps the perceived note at home. The home moved from C5 to A4 during tuning so rising gestures keep the formant below 3.4 kHz.
- **caller**, home C6: nearly pure (index 0.25), flat envelope, level 0.3 to match the others' RMS. Plain, because the content is the caller's own voice.

### Where the code had to be overridden (`OVERRIDES`)

- **turn_truncated** has the same code as turn_failed. Running out of room is a cutoff, not a breakage: the falling tritone stops at 35% of its length, without roughness.
- **turn_refused** has the same code as turn_failed. A refusal is a flat "no": it keeps the roughness but drops to a level pair an octave below home, after the TNG deny buzz.
- **progress_tiring**: weariness reads as a slow 6 Hz wobble rather than 75 Hz grit, and needs 300 ms to read as a state rather than an event.
- The two lab axes are themselves overrides of a kind: without **scope**, session_created collides with turn_started and session_closed with turn_cancelled; without **activity**, five Progress Beats share one code.

### Classifications that felt ambiguous

- **turn_stopped** (and **progress_plan_done**): domain polarity says offset (the Prompt Turn ends), the fiction says arrival ("dog back"), and the current table rises. The lab follows the code: a falling fifth with a dyad, which sounds like a cadence. This is the decision most worth Adam's ear.
- **recording_discarded** is coded abnormal only so it differs from recording_saved; discarding is deliberate, not a fault. A valence axis (kept vs dropped) might be the honest fix.
- **Initiator** for follow_started/stopped, session_created/closed, and call_started: usually the caller's request, but the Voice Agent may follow or open sessions on its own, and call_started is also the system greeting.
- **permission_requested** source: the dog asks, but the gate is the User's authority (DOMAIN.md: Permission Requests authorize actions). Coded dog, routine, required.
- **pull** layer: fg (it must cut through) versus mid (it is optional by definition).
- **progress_failed** polarity: a tool call ending badly (offset) versus a state of trouble.

### Levels and the phone line

`--render` prints per-clip levels after a 300-3400 Hz band-pass with G.711 simulation. Foreground and midground new earcons land at phone-band RMS 0.028 to 0.045 (current: 0.033 to 0.042); Progress Beats at 0.015 to 0.025 (current: 0.020 to 0.028). Durations: 68 to 290 ms, with permission_requested at 420 ms (three statements). Tuning caught two problems the code did not predict: the flat caller family came out three times louder than the rest (now trimmed with a family level), and the first progress_looking at 2x home put most of its energy above 3.4 kHz (now 1.5x).

### Porting to audio.ts

`design()` and `render()` in `SYNTH_JS` use only `Math` and `Float32Array`; port them with the `MAP`, `CODES`, and `OVERRIDES` tables as typed constants, convert to Int16 at the end as `synthesize()` does, and build all 26 clips at module load (about 130k samples total, two FM voices at most per note). `phoneLine()` is lab-only.

## 4. Open questions

1. Should Dogwalk's completions (turn_stopped, plan_done) rise as arrivals or fall as offsets?
2. Accept scope and activity as code axes, or fold them into the canonical code in some other way? If accepted, should they go into DOMAIN.md's Presentation Fiction section, or stay presentation-only in audio.ts?
3. Add a valence axis (kept vs dropped) for recording_discarded and possibly turn_cancelled?
4. A live call over real PSTN and G.711 is the actual test; the browser simulation does not model codec packet loss, AGC, or GPT-Live's own resampling.

## The opening phrase (chosen 2026-11-08)

`call_started` and `dogs_ready` are heard first and most often, so they were auditioned as a pair in `opening_audition.py` (half cadence, suspension, ladder, brightening, question and answer). Adam chose **Brightening**: progress carried by timbre rather than pitch. The line opens on C to G with a nearly pure, dim tone (FM index about 0.5), ending on the dominant so the wait feels unresolved; the dogs waking answers G to high C with the full bright system chirp (index about 3.5) and a fifth beneath the tonic. A third earcon, `call_started_ready`, plays the whole brightening climb at once when the Sandbox is already awake, because then `dogs_ready` never follows and a lone antecedent would hang forever. These three are `phrase` overrides in `earcon_lab.py`: the opening is deliberately one phrase across two events, not two independent codes.

## Shipping

`uv run --script earcon_lab.py --export` writes `cf_worker/src/earcon_designs.json`; `audio.ts` renders it at module load with a port of the lab's `render()`. The caller's Earcon Style (System Menu four: default, tones, spoken words) selects it; `default` maps to `DEFAULT_EARCON_STYLE` in `voice_session.ts`, now `fm`.

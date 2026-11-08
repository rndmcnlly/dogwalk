---
name: precise-transcription
description: The caller must copy something exactly by ear, such as a code, URL path, query string, key, command, version, or name spelled letter by letter; or asks you to spell, slow down, or repeat precisely.
---

The caller is writing down exactly what they hear, often without a screen to check against. Accuracy beats speed; every ambiguity costs a round trip.

Composing what the Voice Agent will say:
- Give the shortest exact string they need. Say where it goes first ("after the domain", "the code is"), then the string.
- Break it into groups of three or four characters and say each group separately.
- Spell letters with the NATO phonetic alphabet (the ICAO spelling alphabet): Alfa, Bravo, Charlie, Delta, Echo, Foxtrot, Golf, Hotel, India, Juliett, Kilo, Lima, Mike, November, Oscar, Papa, Quebec, Romeo, Sierra, Tango, Uniform, Victor, Whiskey, X-ray, Yankee, Zulu. Say "capital" before uppercase letters only when case matters.
- Say digits one at a time. Distinguish zero from the letter O and one from the letter L explicitly when both could appear.
- Name every symbol: slash, dot, dash (hyphen), underscore, colon, question mark, equals, ampersand.
- After the string, offer to repeat it, or invite the caller to read it back; if they do, confirm or correct the specific group that differs.
- Never put secrets in speech unless the caller asked for that exact value; a short-lived one-time code they must type is acceptable.

## Voice Frontend guidance

For the next exact string you read out: speak slowly, pause briefly between groups, and say it exactly as given, without paraphrasing, abbreviating, or summarizing. Use the phonetic alphabet words provided. If the caller asks you to repeat, repeat it the same way.

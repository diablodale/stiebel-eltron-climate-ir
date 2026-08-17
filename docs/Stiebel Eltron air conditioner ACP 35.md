# Stiebel Eltron ACP 35 air conditioner

The goal is to control the Stiebel Eltron ACP 35 air conditioner from Home
Assistant, over its infrared remote interface, using the
[infrared entity platform](https://developers.home-assistant.io/blog/2026/03/30/infrared-entity-platform/)
added in Home Assistant 2026.6. An emitter integration — ESPHome on the KC868-AG —
exposes an `InfraredEmitterEntity`, and a consumer integration builds commands
with `infrared_protocols.commands.Command` and sends them through it. The
implementation plan is in [docs/ha_ir_platform/plan.md](ha_ir_platform/plan.md).

A "clean room" black-box approach was used. Open source tools like ESPHome and a generic
IR receiver were used to decode the IR codes; avoiding proprietary tools or methods.
Methodologies of IR decoding are well documented and commonplace,
e.g. <http://www.harctoolbox.org/>, <https://blog.depau.eu/2021/06/12/ir-remote-reveng/>,
and <https://blog.flipper.net/infrared/>.

## ⚠️ Warning and Disclaimer ⚠️

Controlling a climate device using a method not approved or supported by the manufacturer
may void its warranty, interfere with normal operation and communication, decreased
performance, cause damage to the device, cause property or bodily harm, or even cause death.

The author(s) of this project (including, but not limited to, its methodology, content, and code)
are not responsible for any damage or injury caused. USE AT YOUR OWN RISK!

This project is not intended to be used as a substitute for professional advice or guidance.
Always consult a qualified technician or the manufacturer before attempting to control, modify,
or repair any device.

## Equipment

* [KinCony KC868-AG Infrared controller](https://www.kincony.com/kc868-ag-iot-ir-controller.html)
* Raspberry Pi 4 running Home Assistant and the ESPHome addon
* [Connected the KC868-AG to ESPHome](https://devices.esphome.io/devices/KinCony-KC868-AG)
* VS Code, with Copilot and Claude Code for editing and assistance
* Custom Python tooling in this repository to decode the IR signals captured by
  ESPHome — see [Tooling](#tooling)
* Stiebel Eltron ACP 35 air conditioner —
  [info](https://www.stiebel-eltron.de/content/dam/ste/de/de/home/produkte/klima/ACP35_Produktinformation.pdf),
  [manual](https://www.stiebel-eltron.de/static/ste/docportal/manual/DM0000040581-fbg.pdf)

## IR signal basics

The ACP 35 IR remote control sampled was the manufacturer's remote control.
Inside the battery compartment is a sticker `TZ20160122`.

ESPHome identifies the ACP 35 IR signals with Pronto raw codes.

The ACP 35 does not use discrete IR codes for button presses. Instead, each IR transmission
sends the entire state of the air conditioner. This is common for climate devices,
e.g. Mitsubishi: <https://esphome.io/components/climate/climate_ir.html#mitsubishi>,
<https://esphome.io/api/mitsubishi_8cpp_source>

### Pronto raw IR semantics

Credit to Bengt Mårtensson <http://www.harctoolbox.org/Glossary.html#ProntoSemantics> for
documenting the Pronto raw IR semantics. Reproduced with permission below.

In general, an IR signal consists of three IR sequences, called

1. Start sequence (or "intro", or "beginning sequence"), sent exactly once at the beginning
   of the transmission of the IR signal,
2. Repeat sequence, sent "while the button is held down", i.e. zero or more times during the transmission
   of the IR signal (although some protocols may require at least one copy to be transmitted),
3. Ending sequence, sent exactly once at the end of the transmission of the IR signal,
   "when the button has been released". Only present in a few protocols.

Any sequence can be empty, but not both the start and the repeat. A non-empty ending sequence
is only meaningful with a non-empty repeat.

An IR signal in Pronto CCF form consists of a number of 4-digit hexadecimal numbers. For example:

```text
0000 006C 0022 0002 015B 00AD 0016 0041 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0041 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0041 0016 0041 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0016 0041 0016 0041 0016 0041 0016 0041 0016 0041 0016 0041 0016 06FB 015B 0057 0016 0E6C
```

The first number, here `0000`, denotes the type of the signal. `0000` denotes a raw IR signal with modulation,
while `0100` denotes a non-modulated raw IR signal. There are also a small number of other allowed values,
denoting signals in protocol/parameter form, notably `5000` for RC5-protocols, `6000` for RC6-protocols,
and `900A` for NEC1-protocols.

The second number, here `006C`, denotes a frequency code. For the frequency f in Hertz,
this is the number `1000000 / (f * 0.241246)` expressed as a four-digit hexadecimal number. In the example,
`006C` corresponds to `1000000 / (0x006c * 0.241246) = 38381 Hertz`.
(It can be conveniently computed by the Time/Frequency Calculator in
[IrScrutinizer](https://github.com/bengtmartensson/IrScrutinizer), available under the Tools menu.)

The third `0022` and fourth `0002` numbers denote the number of pairs (= half the number of durations) in the start
and the repeat sequence respectively. In the example, there are `0x0022` = 34 start pairs and `0x0002` = 2 repeat pairs.

Next the start and repeat sequences follow; their length being given by the third and the fourth numbers,
as per above. The numbers therein are all time durations, the ones with odd numbers on-periods, the other
ones off-periods. These are all expressed as multiples of the period time; the inverse value of the frequency
given as the second number. For this reason, "frequency" must be a non-zero number also for the non-modulated
case, denoted by the first number being `0100`. In the example, the fifth number `0x015B` denotes
an on-period of `0x015B * periodtime = 347/f = 347/38381 = 0.009041 seconds = 9041 microseconds`.

In particular, all sequences start with an on-period and end with an off-period.

In the Pronto representation, there is no way to express an ending sequence.

## IR protocol analysis

> **This section was rewritten in 2026.** The original analysis described a
> 69-bit frame with a 5-bit `10101` preamble. That frame does not exist; it was an
> artifact of two bugs in the analysis script. The real frame is 72 bits, nine
> bytes, byte-aligned. See [Appendix: the superseded 69-bit
> interpretation](#appendix-the-superseded-69-bit-interpretation) for what went
> wrong and how it was caught, and why the old reading nevertheless appeared to
> validate.

### Transmissions

Every transmission begins with the same four Pronto header words,
`0000 006D 004A 0000`:

* `0000` — raw IR signal with modulation
* `006D` — frequency code, see below
* `004A` = 74 pairs in the start sequence
* `0000` — no repeat sequence

**The frequency code carries no information.** ESPHome's
`ProntoProtocol::decode()` hardcodes `uint16_t frequency = 38000U` and writes
`REFERENCE_FREQUENCY / 38000` back out, so `006D` appears in every capture from
every device regardless of the actual carrier. Converting it back gives
38028.9 Hz, which is that constant round-tripped through a four-digit hex code,
not a measurement. **Treat the carrier as 38 kHz assumed, not measured.** If the
unit ever refuses a transmission, the carrier is one of the things to vary.

Buttons were never held down during capture, so no repeat sequence was recorded.
Holding the up/down button does change the remote's own display, but it transmits
nothing until release, and then sends a single frame carrying the final
temperature rather than one frame per increment. The protocol appears to have no
repeat form at all.

Values are transmitted MSB first, and this document writes them MSB first
throughout: the leftmost bit is the earliest in time.

### Pronto code analyzer

ESPHome has built-in IR receiver code which detects IR transmissions and decodes them.
I used this to capture the IR transmissions when I pressed buttons and retrieved the
corresponding Pronto codes using the ESPHome log for the KC868-AG Infrared controller.

The following transmission from the IR remote was captured while power was on,
mode was cool, fan was high, no timer, and up button pressed once to achieve 19c.

```text
[21:02:45][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 0013 0016 004B 0017 004B 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:02:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:02:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[21:02:45][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

`tools/acp35_cli.py` decodes that capture:

```console
$ ./tools/acp35_cli.py < capture.txt
frame 1 (147 timings)
  bytes       55 32 00 07 00 00 31 C0 7F
  power       on
  mode        cool
  fan         high
  temperature 19 C / 66 F  (displaying C)
  timer       off
  b7          0xC0  [TEMP_CHANGED]
```

It accepts an ESPHome log line, a bare Pronto code, or a list of raw signed
microseconds, and `--document` decodes every capture in this file at once.

### Frame structure

Each capture is 151 Pronto words: four header words and **147 durations**.

That count is odd, and it is not truncation. ESPHome writes the pair count as
`(data.size() + 1) / 2` and then dumps every element of its receive buffer, so an
odd-length buffer rounds the declared pair count *up* — which is why 74 pairs are
declared for 147 durations. Reading the two as inconsistent was the original
analysis's first mistake.

The odd count is also the key to the encoding. The final duration is the
receive-idle timeout, which must be a **space**; with 147 durations that forces
element 0 to be a space too, and the marks to the odd indices. So the marks are
the constant element and the spaces carry the data:

**Pulse distance, MSB first, byte 0 first, no repeat.**

| element | µs | measured spread | n |
| ------- | -- | --------------- | - |
| header mark | *unknown, see below* | | |
| header space | 5100 | 5024–5102 | 39 |
| bit mark | 576 | 540–644 | 2808 |
| space = `0` | 481 | 474–500 | 2097 |
| space = `1` | 1928 | 1904–1956 | 711 |
| trailer mark | 555 | 540–566 | 39 |

Figures are averaged over all 39 captures using ESPHome's actual integer timebase
(`1000000 / 38000 = 26` µs, not 26.296) with its ±20 µs `MARK_EXCESS_MICROS`
compensation removed: `true_mark = printed + 20`, `true_space = printed − 20`.
Bit mark and trailer mark agree within tolerance, so one constant serves for both.

The 10.1 ms element that ends every capture is **not** a protocol value. It is
identical to the microsecond in all 39 captures, because it is ESPHome's default
`idle: 10ms` receive timeout. A gap the remote actually emitted would jitter like
everything else does.

#### The one unmeasured value

Every capture's receive buffer *begins* at the header space. The mark that must
precede it was never recorded — the buffer starts at the first edge it can
measure from, so the leading mark is gone before the dumper sees it. A fresh
`dump: raw` capture would very likely lose it the same way.

`HEADER_MARK` in `acp35.py` is therefore a tunable constant, to be bisected
against the real unit: `5100` (symmetric with the space) first, then `4400`,
`3000`, `9000`, and `0` for the hypothesis that there is no header mark at all
and the 5100 µs element is simply the idle gap before the frame.

### Frame contents

Seventy-two bits, nine bytes:

```text
55  32  00  07  00  00  31  C0  7F      power on, cool, high fan, 19 °C / 66 °F
b0  b1  b2  b3  b4  b5  b6  b7  ck
```

| byte | contents |
| ---- | -------- |
| `b0` | constant `0x55` |
| `b1` | bits 7-4 = °C − 16 (`1`..`14` → 17..30 °C) · bit 3 = the pending timer will switch the unit **off** · bit 1 = power on · bits 2, 0 always `0` |
| `b2` | timer hours, plain binary `0`..`24` |
| `b3` | °F − 59 (`3`..`27` → 62..86 °F) |
| `b4` | always `0` |
| `b5` | always `0` |
| `b6` | bits 7-4 = fan (`1` low, `2` medium, `3` high) · bits 3-0 = mode (`0` auto, `1` cool, `2` dry, `3` fan) |
| `b7` | flags, see below |
| `ck` | `sum(b0..b7) & 0xFF` |

`b1` bit 3 and the hours count are **independent**. The bit is emitted at zero
hours while the timer entry display is open, and stays clear at three hours while
the unit is off, so it cannot be collapsed into "set if hours > 0". It reports
which direction the pending timer will switch the unit — see
[The timer runs in both directions](#the-timer-runs-in-both-directions-2026-08-13).

Selecting dry forces the fan to low, and the fan button will not move it while
dry is selected, so no frame the remote can emit pairs dry with medium or high.
Whether the *unit* would accept one is untested.

#### Fan speed per mode

| mode | speeds the fan button offers |
| ---- | ---------------------------- |
| auto | low, medium, high |
| cool | low, medium, high |
| dry | **low only** — the button is ignored |
| fan | low, medium, high |
| *(off)* | none — the button emits nothing |

**Each mode stores its own speed**, and a mode press transmits the speed stored
for the mode being entered rather than the one the previous mode was running.
Removing the batteries brings every mode back on high except dry, which returns
on low, so that is where each slot starts and dry's restriction is a rule the
remote applies rather than a value it remembers.

A consumer that keeps one shared speed will therefore disagree with the remote
on the first mode change: setting cool to medium and then selecting fan-only
sends medium where the remote would send whatever fan-only was last left on.

**While the unit is off, the remote responds to two buttons only: power and
timer.** Fan, mode, temperature and the °C/°F switch are all ignored and emit
nothing, so no frame exists for a setting changed while off. A consumer that
offers those controls in the off state invents an interaction the hardware does
not have, and whatever the user picks then appears at the next power-on.

Fan `0` and the °C value `16` (a `b1` nibble of `0`) are both representable but
are never emitted. The fan button was cycled through a full lap and a repeat and
produces only high → medium → low; the temperature range starts at 17 °C. Whether
the *unit* would accept fan `0` as an auto speed is a separate, untested question.

### b7 — one state bit, the rest per-press event bits

`b7` is the only byte that is not a pure function of the unit's state. With the
machine in an identical state — 22 °C, cool, high fan, °C display — it differs by
which button produced the frame:

| b7 | produced by |
| -- | ----------- |
| `0xC0` | a temperature up/down press |
| `0x88` | the power button |
| `0x80` | the fan, mode or unit button |
| `0x02` | any press while the timer entry UI is open |

| bit | kind | meaning |
| --- | ---- | ------- |
| 7 `0x80` | state | display unit: `1` = °C, `0` = °F. The one genuinely persistent bit, and the unit acts on it: pressing C/F changes the appliance's own display panel. |
| 6 `0x40` | event | set by an up/down press that moved the **temperature setpoint**. |
| 3 `0x08` | event | set only in frames from the power button, for both on and off. |
| 1 `0x02` | event | set while the timer entry display is open. |
| 0 `0x01` | event | set when a TIMER press reopened the display on a timer already set. |
| 5, 4, 2 | — | never set. |

Bit 6 is narrower than either "the button" or "the value". A mode press changes
the transmitted temperature without setting it, and up/down inside the timer
display changes the hours without setting it. Both conditions have to hold.

Bit 1 reports the **display**, not a pending timer. An ordinary press while a
timer counts down leaves it clear, so an encoder must not derive it from
`hours > 0`. Bit 0 does not encode the hours: it was captured identically at 5 h
and 7 h.

"Never set" for bits 5, 4 and 2 is now evidenced rather than assumed. The remote
has seven buttons — timer, °C/°F, up, down, fan, mode, power — and every one has
been captured. No press exists that could set them. The same argument covers `b4`,
`b5`, and `b1` bits 2 and 0.

Whether the unit *requires* the event bits, or acts on `b1`/`b2`/`b3`/`b6`
regardless, is untested. Until that is known the encoder reproduces what the
remote sends, byte for byte.

### Temperature

Both temperature fields are always populated. Whichever unit the user selected is
authoritative and the other is its paired value — and the two mappings are **not
inverses of each other**, so neither can be derived from the other by formula:

* **°C → °F** is `round(°C × 9/5 + 32)` at all 14 values **except 17 °C**, which
  ships as 62 °F where rounding gives 63. The scales' endpoints are pinned to each
  other: 17 °C / 62 °F are both the remote's minimum, 30 °C / 86 °F both its
  maximum. It is not `floor()` — that would also change 21, 22, 26 and 27 °C, and
  the captures show it does not.
* **°F → °C** is `round((°F − 32) × 5/9)` at all 25 values.

So 17 °C pairs out to 62 °F, while 63 °F pairs back to 17 °C. A clamp is a no-op
in both directions across the whole valid domain, and no input lands on an exact
`.5`, so rounding mode never matters.

**Every pairing in both directions is confirmed by a capture.** All 14 °C → °F
and all 25 °F → °C, from sweeping the remote through its full range in each
display unit. Nothing above is inference.

#### Two scales, not one value shown twice

Because the mappings are not inverses, the two fields are not one temperature in
two notations. They are **two scales of different length**: Celsius is 14 steps
and Fahrenheit is 25, and up/down walks whichever one is displayed. The 11 °F
values that pair with no whole Celsius degree — 63, 65, 67, 69, 71, 74, 76, 78,
80, 83, 85 — are reachable only from a remote displaying °F.

A consumer therefore has to pick which scale its user drives on, and populate
that field from the user's choice with the other as its pair. Deriving the wrong
way round moves the number by a degree: 63 °F pairs to 17 °C, but 17 °C pairs
back out to 62 °F.

Picking the scale by what the **appliance** is displaying looks right and is not.
The user's own value has to survive a round trip through the consumer's display
layer, and an arbitrary value on one scale is not representable on the other:
22 °C is 71.6 °F, which no frame can carry, so it ships as 72 and reads back as
22.2. Picking the scale the **user** is reading avoids the round trip entirely,
and costs nothing, because the scales' endpoints are pinned to each other — 17 °C
and 62 °F are both the minimum, 30 °C and 86 °F both the maximum — so the bounds
are whole numbers either way.

`b7` bit 7 still has to be settable and followable independently, because it is
what the appliance's own panel shows.

#### Only cool owns the setpoint

The two temperature fields are populated in every frame, but in three of the four
modes they are not a setpoint. The remote only allows up/down to change the
temperature in **cool**, and hides the number in the others.

| mode | what `b1`/`b3` carry |
| ---- | -------------------- |
| cool | the setpoint |
| fan | the same value as cool, following it as it changes |
| dry | a fixed 22 °C / 72 °F |
| auto | a fixed 22 °C / 72 °F |

Setting cool to 18 °C and cycling the modes leaves dry and auto reporting 22 °C
while fan reports 18 °C; repeating with cool at 30 °C moves fan to 30 °C and again
leaves dry and auto at 22 °C. Removing the remote's batteries brings every mode
back to 22 °C, so it is the firmware default rather than a value stored from use.
The same reset brings every mode back on **high fan except dry, which returns on
low** — the dry restriction is enforced, not remembered. Fan *speed*, by contrast, genuinely is stored per
mode. The manual explains auto having no setpoint: the unit chooses cooling or
fan-only itself from the room temperature, at a 25 °C threshold — a number that
never appears in a frame.

A consumer displaying `b1` as a target temperature will therefore show 22 °C in
dry and auto, which is not a value the user chose and cannot change.

### Checksum

`ck` is the low byte of the sum of the eight bytes before it:

```python
checksum = sum(state[:8]) & 0xFF
```

**This validates on all 39 captures.** The original analysis described it as a sum
seeded with a magic `0x55`, which is the same arithmetic seen through the
misaligned frame: what it called a seed is simply `b0`, a constant byte that the
old reading had pushed outside the message.

### Tooling

`tools/acp35_cli.py` decodes captures; `tools/pronto.py` converts Pronto codes to
raw signed timings; `custom_components/stiebel_eltron_ir/acp35.py` holds the
encoder and decoder and has no Home Assistant dependency.

```bash
./tools/acp35_cli.py < capture.txt          # ESPHome log, Pronto, or raw timings
./tools/acp35_cli.py --document             # every capture below
./tools/acp35_cli.py --document --format table
```

The 39 captures in this document are the test corpus. `tests/conftest.py` parses
them straight out of this file, so adding a capture here extends the regression
suite with no code change:

```bash
uv run pytest        # 433 tests, no hardware touched
```

The superseded `pronto_analyzer.py`, `checksum.py` and `decode.py` were deleted;
they are in the git history.

## Transmission captures

### Power

Power is `b1` bit 1. Pressing the power button also sets `b7` bit 3, in both
directions.

```text
On    55 62 00 0D 00 00 31 88 7D
Off   55 60 00 0D 00 00 31 88 7B
On    55 62 00 0D 00 00 31 88 7D
         ^^                 ^^
         |                  b7 bit 3, this frame came from the power button
         b1 bit 1, 1 = on, 0 = off
```

Nothing else moves: 22 °C / 72 °F, cool, high fan are unchanged across all three.

Remote not being used -> On

```text
[21:45:23][I][remote.pronto:233]: 0000 006D 004A 0000 00C2 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0016 0013 0016 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:45:24][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:45:24][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[21:45:24][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181 
```

On -> Off

```text
[22:19:46][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0016 0013 0015 0013 0015 0013 0015 0013 0015 0013 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:19:46][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:19:46][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 
[22:19:46][I][remote.pronto:233]: 004C 0016 004C 0016 004C 0014 0014 0016 004C 0016 004C 0014 0181 
```

Off -> On

```text
[22:22:02][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004A 0017 0013 0016 004B 0016 0013 0016 004B 0017 004B 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:22:02][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:22:02][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[22:22:02][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181 
```

### Timer

Remote UI has a 2-step sequence; pressing timer button sends the below code and then remote
has a UI that wants a time period. Period is in hours 0-24.
At any time, pressing timer button cancels the existing/new timer and returns to default UI.
Otherwise, select the number of hours with up/down and then wait several seconds
for the default UI to reappear. The remote UI now has a timer indicator.
Pressing timer button will show the number of hours ?remaining?. To keep the timer
active, press nothing. Pressing the timer button again will cancel the timer and return to default UI.

The timer uses two fields that move independently: `b1` bit 3 arms it, `b2` counts
the hours. `b7` bit 1 marks frames sent while the entry UI is open.

```text
press timer     55 8A 00 10 00 00 31 02 22   armed, 0 h, timer UI
up = 1 h        55 8A 01 10 00 00 31 02 23   armed, 1 h
up = 2 h        55 8A 02 10 00 00 31 02 24   armed, 2 h
up = 24 h       55 8A 18 10 00 00 31 02 3A   armed, 24 h  (0x18 = 24)
press timer     55 8A 18 10 00 00 31 03 3B   ...then immediately...
press timer     55 82 00 10 00 00 31 00 18   disarmed, hours cleared, default UI
                   ^^ ^^                ^^
                   |  |                 b7: bit 1 = display open, bit 0 = reopened
                   |  b2 = hours, plain binary
                   b1 bit 3 = armed  (0x8A = armed + on, 0x82 = on only)
```

Note the first frame: **armed with zero hours**. Arming and the hour count are
genuinely separate, so they cannot be collapsed into "armed if hours > 0".

#### What the bits mean, settled 2026-08-11

A capture session with the original remote answered the three questions this
section previously left open.

**`b7` bit 1 means the entry display is open, not that a timer is pending.** With
3 hours counting down and the display closed, an ordinary fan press sends
`55 2A 03 05 00 00 21 80` — bit 1 clear, while `b1` bit 3 stays armed and `b2`
keeps its hours. Any encoder deriving bit 1 from `hours > 0` puts it on frames the
remote never would.

**`b1` bit 3 and `b2` survive a press that is not a timer press.** Same frame:
armed, 3 hours, in a frame caused by the fan button.

**`b7` bit 0 marks a TIMER press that reopened the display on a timer already
set.** It appeared twice, identically, at two different hour counts — `0x83` with
5 hours pending and again with 7 — so it does not encode the hours. It is not a
cancel marker either; the 2025 corpus caught it during a cancel only because a
cancel begins with that same reopening press.

**Nothing is emitted when the entry display times out.** Four presses produced
four frames. There is no separate acceptance frame: the last frame sent while the
display is open is what commits the value.

**The two cancel routes leave different states.** Pressing TIMER twice disarms
`b1` bit 3 and clears `b2`. Winding the hours down to zero instead leaves the
remote *armed at zero hours* with bit 1 still set. Only the first produces an
unambiguous "no timer" frame.

**Up/down inside the display does not set `b7` bit 6.** The hours change, the
setpoint does not, and bit 6 tracks the setpoint.

press timer with no timer set, arms at 0 h

```text
[15:41:05][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0015 0013 0016 004B 0016 0013 0016 004C 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:05][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:05][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017
[15:41:05][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0181
```

up to 5 h, entry display still open

```text
[15:41:15][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004C 0015 0013 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:15][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:15][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017
[15:41:15][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0181
```

press timer with 5 h running, reopens the entry display

```text
[15:41:23][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:23][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:23][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017
[15:41:23][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181
```

press timer again, cancel method 1: disarmed, hours cleared

```text
[15:41:25][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:25][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:25][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:25][I][remote.pronto:233]: 0013 0017 004B 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181
```

up to 7 h, entry display still open

```text
[15:41:48][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004B 0015 0013 0016 004C 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:48][I][remote.pronto:233]: 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:48][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017
[15:41:48][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0181
```

press timer with 7 h running, reopens at a different hour count

```text
[15:41:57][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:57][I][remote.pronto:233]: 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:41:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017
[15:41:57][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

down to 0 h, cancel method 2: still armed, zero hours

```text
[15:42:11][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:42:11][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:42:11][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017
[15:42:11][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0181
```

fan pressed while 3 h counts down, entry display closed

```text
[15:34:42][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0015 0013 0015 0013 0017 004B 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:34:42][I][remote.pronto:233]: 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[15:34:42][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017
[15:34:42][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0181
```

From normal operation cool 75f high fan, press timer (and its waiting on the length in the remote UI)

```text
[23:43:26][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0015 0013 0016 004B 0015 0013 0016 004C 0015 0013 0014 0014 0015 0013 0014 0014 0014 0014 0014 
[23:43:26][I][remote.pronto:233]: 0014 0015 0013 0015 0013 0014 0014 0015 0013 0014 0014 0014 0014 0017 004B 0015 0013 0014 0014 0015 0013 0014 0014 0014 0014 0014 0014 0015 0013 0014 0014 0015 0013 0014 0014 0015 0013 0014 0014 0014 0014 0015 0013 0014 0014 0014 
[23:43:26][I][remote.pronto:233]: 0014 0015 0013 0014 0014 0015 0013 0014 0014 0014 0014 0014 0014 0017 004B 0017 004B 0015 0013 0014 0014 0014 0014 0017 004B 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0017 004B 0015 0013 0014 0014 0014 0014 0017 
[23:43:26][I][remote.pronto:233]: 004B 0014 0014 0014 0014 0014 0014 0017 004B 0014 0014 0015 0181
```

press timer, up, up, wait

```text
[01:05:12][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0015 0013 0016 004B 0015 0013 0016 004B 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0014 
[01:05:12][I][remote.pronto:233]: 0014 0015 0013 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0014 0014 0014 0014 0014 0014 0014 0014 0015 
[01:05:12][I][remote.pronto:233]: 0013 0014 0014 0014 0014 0014 0014 0015 0013 0015 0013 0014 0014 0017 004B 0017 004B 0014 0014 0014 0014 0014 0014 0017 004B 0015 0013 0015 0013 0014 0014 0014 0014 0015 0013 0015 0013 0017 004B 0014 0014 0014 0014 0014 0014 0017 
[01:05:12][I][remote.pronto:233]: 004B 0014 0014 0014 0014 0014 0014 0017 004B 0014 0014 0015 0181

[01:05:13][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0016 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0015 
[01:05:13][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0014 0014 0014 0014 0015 0013 0014 0014 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[01:05:13][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0014 0014 0014 0014 0014 0014 0014 0014 0017 004B 0017 004B 0014 0014 0014 0014 0015 0013 0017 004B 0014 0014 0014 0014 0015 0013 0014 0014 0015 0013 0014 0014 0017 004B 0014 0014 0014 0014 0014 0014 0017 
[01:05:13][I][remote.pronto:233]: 004B 0014 0014 0014 0014 0014 0014 0017 004B 0017 004B 0014 0181

[01:05:14][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0015 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[01:05:14][I][remote.pronto:233]: 0013 0015 0013 0017 004B 0014 0014 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0014 0014 0014 0014 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0015 0013 0015 
[01:05:14][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0014 0014 0015 0013 0014 0014 0014 0014 0017 004B 0017 004B 0015 0013 0014 0014 0014 0014 0017 004B 0014 0014 0014 0014 0014 0014 0015 0013 0014 0014 0015 0013 0017 004B 0014 0014 0014 0014 0014 0014 0017 
[01:05:14][I][remote.pronto:233]: 004B 0014 0014 0014 0014 0017 004B 0014 0014 0014 0014 0014 0181
```

power off, power on, (it is at cool 75f max fan), press timer
press up 24 times to get 24 hrs, wait

24th...

```text
[01:26:03][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0015 0013 0015 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[01:26:03][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[01:26:03][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 
[01:26:03][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0181
```

...then timer, timer, to cancel the existin 24hr timer

```text
[01:35:53][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0015 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[01:35:53][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[01:35:53][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017 
[01:35:53][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0014 0181 

[01:35:54][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[01:35:54][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[01:35:54][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0014 0014 0017 004B 0017 004B 0014 0014 0015 0013 0014 0014 0017 004B 0015 0013 0014 0014 0015 0013 0015 0013 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0015 0013 0014 
[01:35:54][I][remote.pronto:233]: 0014 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0181
```

#### The timer runs in both directions, 2026-08-13

Every timer capture above was taken with the unit **on**. Repeating the sequence
with it **off** shows the remote answering the timer button exactly as it does
when running -- and `b1` bit 3 behaving differently:

```text
press timer     55 60 00 0d 00 00 21 82 65   0 h, entry display open
up = 1 h        55 60 01 0d 00 00 21 82 66
up = 2 h        55 60 02 0d 00 00 21 82 67
up = 3 h        55 60 03 0d 00 00 21 82 68   3 h pending
press timer     55 60 03 0d 00 00 21 83 69   reopens, b7 bit 0 set
press timer     55 60 00 0d 00 00 21 80 63   cancelled, hours cleared
                   ^^ ^^
                   |  b2 = hours, exactly as when running
                   b1 = 0x60: bit 3 clear, bit 1 clear
```

**`b1` bit 3 does not mean "a timer is set".** With three hours pending and the
unit off it is clear, while `b2` carries the hours. With the unit on the same
situation sets it. So the bit means **the pending timer will switch the unit
off**: an off-delay while running, an on-delay while stopped, which is what the
manual describes.

Tested across all 76 decodable frames, `bit 3 == power AND (hours > 0 OR entry
display open)` fits every one. The rule this replaces, `bit 3 == hours > 0`,
mismatches eight.

Nothing else about the timer changes when the unit is off: the entry display
opens at zero hours, up counts the same way, `b7` bit 1 marks the display and bit
0 marks a reopen, and TIMER-then-TIMER cancels by clearing both fields.

press timer with the unit off, opens the entry display at 0 h

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0181
```

up to 1 h, unit still off

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0181
```

up to 2 h, unit still off

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0017 004B 0015 0181
```

up to 3 h, unit still off

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0015 0013 0016 004B 0015 0013 0016 004C 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0181
```

press timer with 3 h pending and the unit off, reopens the display

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0015 0013 0016 004C 0016 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181
```

press timer again, cancels: hours cleared, no event bits

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004A 0017 0013 0017 004A 0016 0013 0016 004A 0017 004A 0016 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0181
```

#### b2 counts down, 2026-08-13

Whether `b2` reports the timer as it was set or the time still to run decides
whether a consumer can model a running timer at all. **It counts down.**

A 5 hour timer was set, then left for 90 minutes before an ordinary fan press was
captured with the entry display closed:

```text
21:14:00  55 6a 05 0d 00 00 21 82 74   5 h, committed
22:44:09  55 6a 04 0d 00 00 11 80 61   90 minutes later, an ordinary fan press
             ^^                        b2 = 4, not 5
22:44:13  55 6a 04 0d 00 00 11 83 64   reopened: the same 4, and the remote's
                                       own display read 4
22:44:22  55 62 00 0d 00 00 11 80 55   cancelled
```

The remote's display and `b2` agree, so the remote is not holding a finer value
it declines to transmit. Every frame carries the hours still to run.

**The resolution is one whole hour, and that is a floor on what any consumer can
do.** At 90 minutes into a 5 hour timer the field reads 4, not 3.5, so a frame
only locates the expiry within an hour. Re-transmitting that 4 tells the unit
"four hours from now" and moves the expiry by the remainder -- half an hour, in
this capture. A full-state protocol cannot leave a running timer untouched.

Whether the count is `ceil(remaining)` or a decrement on each whole hour since it
was set cannot be told apart here: both predict 4. They differ only in the first
minutes after setting.

What this does settle is that a consumer following the remote can hold a deadline
rather than a number, count its own copy down, and reach zero. That removes the
worst failure -- replaying a stale value forever, and re-arming a timer that has
already fired -- and leaves a bounded error in its place. The integration in this
repository takes the simpler route instead and transmits no timer at all; see
*The timer is read-only and never replayed* in
[ha_ir_platform/plan.md](ha_ir_platform/plan.md).

press timer, the countdown run begins at 0 h

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004A 0017 004A 0016 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0014 0014 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0014 0014 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

winding to 1 h

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004B 0017 004B 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0181
```

winding to 2 h

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004B 0017 004B 0015 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0181
```

winding to 3 h

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004B 0017 004B 0015 0013 0016 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0181
```

winding to 4 h

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0017 004B 0015 0013 0016 004C 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0181
```

winding to 5 h, committed at 21:14:00 UTC

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004A 0018 004A 0016 0013 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0181
```

fan pressed 90 minutes later, entry display closed: b2 reads 4

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004C 0016 004C 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0181
```

press timer, reopening after 90 minutes: still 4, and the remote showed 4

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0015 0013 0016 004C 0016 004C 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0181
```

press timer, reopening a second time

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0016 004C 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0181
```

press timer, cancelling the countdown run

```text
[remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0181
```

### Celsius or Fahrenheit units

Press C -> F

```text
[21:48:36][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0016 0013 0016 004B 0017 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:48:36][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:48:36][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 
[21:48:36][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0181 
```

Press F -> C

```text
[21:54:24][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:54:24][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:54:24][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[21:54:24][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0181 
```

The unit button moves `b7` bit 7 and nothing else — both temperature fields keep
their values, because the remote always transmits both.

```text
C -> F    55 62 00 0D 00 00 31 00 F5
F -> C    55 62 00 0D 00 00 31 80 75
             ^^ ^^                ^^
             |  |                 b7 bit 7: 1 = °C, 0 = °F
             |  b3 = 0x0D = 72 °F, unchanged
             b1 high nibble = 6 = 22 °C, unchanged
```

### Temperature values

Celsius ranges 17–30 °C in 1 °C steps, held in the high nibble of `b1` as
`°C − 16`, so decimal 1–14. A nibble of `0`, meaning 16 °C, was never observed.

Every value below is a real capture. Note `b3` tracking `b1`, and `b7` = `0xC0`
throughout — bit 7 for the °C display, bit 6 because a temperature press produced
the frame.

```text
        b0 b1 b2 b3 b4 b5 b6 b7 ck      paired
17 °C   55 12 00 03 00 00 31 C0 5B      62 °F
18 °C   55 22 00 05 00 00 31 C0 6D      64 °F
19 °C   55 32 00 07 00 00 31 C0 7F      66 °F
20 °C   55 42 00 09 00 00 31 C0 91      68 °F
21 °C   55 52 00 0B 00 00 31 C0 A3      70 °F
22 °C   55 62 00 0D 00 00 31 C0 B5      72 °F
23 °C   55 72 00 0E 00 00 31 C0 C6      73 °F
24 °C   55 82 00 10 00 00 31 C0 D8      75 °F
25 °C   55 92 00 12 00 00 31 C0 EA      77 °F
26 °C   55 A2 00 14 00 00 31 C0 FC      79 °F
27 °C   55 B2 00 16 00 00 31 C0 0E      81 °F
28 °C   55 C2 00 17 00 00 31 C0 1F      82 °F
29 °C   55 D2 00 19 00 00 31 C0 31      84 °F
30 °C   55 E2 00 1B 00 00 31 C0 43      86 °F
           ^        ^           ^
           |        |           b7 bit 7 = °C displayed
           |        b3 = °F − 59
           b1 high nibble = °C − 16
```

Fahrenheit ranges 62–86 °F in 1 °F steps, held in `b3` as `°F − 59`, so decimal
3–27. Values below 62 °F were never observed. Here `b7` = `0x40`: bit 7 clear for
the °F display, bit 6 still set by the temperature press.

```text
        b0 b1 b2 b3 b4 b5 b6 b7 ck      paired
62 °F   55 12 00 03 00 00 31 40 DB      17 °C
63 °F   55 12 00 04 00 00 31 40 DC      17 °C
64 °F   55 22 00 05 00 00 31 40 ED      18 °C
75 °F   55 82 00 10 00 00 31 40 58      24 °C
86 °F   55 E2 00 1B 00 00 31 40 C3      30 °C
```

**62 °F and 63 °F both pair with 17 °C, but 17 °C pairs back to 62 °F.** The two
mappings are not inverses; see [Temperature](#temperature) above. These five plus
72 °F were the only values in the original corpus; the remaining 19 were added by
the Fahrenheit sweep below, so all 25 are now captured.

#### Encoding process

Celsius and Fahrenheit values are both always transmitted.

1. Choose the temperature unit: 0 bit = Fahrenheit, 1 bit = Celsius
2. Given a unit, choose the temperature value ranging from 17-30c or 62-86f.
3. Convert that value to the other unit
   * Celsius to Fahrenheit `value = (temperature value * 9/5) + 32` and round to nearest integer
   * Fahrenheit to Celsius `value = (temperature value - 32) * 5/9` and round to nearest integer
4. Convert the temperature values to offsets
   * Celsius `offset = temperature value - 16`
   * Fahrenheit `offset = temperature value - 59`
5. Convert the offsets to bits, most significant bit first

#### Celsius samples

When in cool mode, caused 22c -> 23

```text
[21:56:57][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 004B 0017 004B 0016 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:56:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:56:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 
[21:56:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0181 
```

When in cool mode, caused 23c -> 22

```text
[21:58:24][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004B 0016 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:58:24][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0015 
[21:58:24][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0014 0014 0017 004B 0017 004B 0017 004B 0014 0014 0014 0014 0014 0014 0015 0013 0014 0014 0014 0014 0017 004B 0014 0014 0017 
[21:58:24][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0014 0014 0017 004B 0014 0181 
```

When in cool mode, was at 17c, kept pressing down and it kept at 17c

```text
[20:47:54][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 0013 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[20:47:54][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[20:47:54][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 
[20:47:54][I][remote.pronto:233]: 0014 0016 004C 0016 004C 0014 0014 0016 004C 0016 004C 0014 0181
```

up once to 18c

```text
[20:58:43][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004A 0016 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[20:58:43][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[20:58:43][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[20:58:43][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181
```

up once to 19c

```text
[21:02:45][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 0013 0016 004B 0017 004B 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:02:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:02:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[21:02:45][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

up to 20c

```text
[21:17:33][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0016 0013 0016 004B 0016 0013 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:17:33][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:17:33][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 
[21:17:33][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0181 
```

up to 21c

```text
[21:27:58][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004B 0016 0013 0016 004B 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:27:58][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:27:58][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 
[21:27:58][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0181 
```

up to 24c

```text
[21:30:49][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0015 0013 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:30:49][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:30:49][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 
[21:30:49][I][remote.pronto:233]: 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0181
```

up to 25c

```text
[21:33:33][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0016 004A 0016 0013 0015 0013 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:33:33][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:33:33][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0016 
[21:33:33][I][remote.pronto:233]: 004C 0014 0014 0016 004C 0014 0014 0016 004C 0014 0014 0014 0181
```

up to 26c

```text
[21:36:46][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 004A 0016 0013 0015 0013 0015 0013 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:36:46][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:36:46][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0016 
[21:36:46][I][remote.pronto:233]: 004C 0016 004C 0016 004C 0016 004C 0014 0014 0014 0014 0014 0181
```

up to 27c

```text
[21:38:20][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 004A 0017 004B 0016 0013 0015 0013 0016 004B 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:38:20][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:38:20][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:38:20][I][remote.pronto:233]: 0014 0014 0014 0016 004C 0016 004C 0016 004C 0014 0014 0014 0181
```

up to 28c

```text
[21:39:47][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0018 004A 0016 0013 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:39:47][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:39:47][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:39:47][I][remote.pronto:233]: 0013 0017 004B 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

up to 29c

```text
[21:41:25][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0018 004A 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:41:25][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[21:41:25][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 
[21:41:25][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0181
```

up to 30c

```text
[21:42:55][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0018 004A 0018 004A 0016 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0014 0014 0015 0013 0015 0013 0014 
[21:42:55][I][remote.pronto:233]: 0014 0015 0013 0014 0014 0015 0013 0015 0013 0014 0014 0015 0013 0017 004B 0017 004B 0014 0014 0017 004B 0017 004B 0015 0013 0014 0014 0015 0013 0015 0013 0015 0013 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0015 
[21:42:55][I][remote.pronto:233]: 0013 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0017 004B 0014 0014 0014 0014 0014 0014 0017 004B 0017 004B 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 
[21:42:55][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0181 
```

#### Farenheit samples

Down to 62f

```text
[21:57:50][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 0013 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:57:50][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[21:57:50][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 
[21:57:50][I][remote.pronto:233]: 0014 0016 004C 0016 004C 0014 0014 0016 004C 0016 004C 0014 0181```
```

Up to 63f

```text
[22:03:56][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 0013 0015 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:03:56][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:03:56][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 
[22:03:56][I][remote.pronto:233]: 0014 0016 004C 0017 004B 0016 004C 0014 0014 0014 0014 0014 0181 
```

Up to 64f

```text
[22:13:45][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0017 004A 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0015 0013 0016 004B 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:13:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:13:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 
[22:13:45][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181
```

Jump up to 86f

```text
[22:21:00][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0018 004A 0017 004B 0016 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:21:00][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0014 0014 0015 
[22:21:00][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0014 0014 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0014 0014 0014 0014 0015 0013 0015 0013 0017 004B 0017 004B 0014 
[22:21:00][I][remote.pronto:233]: 0014 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0014 0181
```

Jump down to 75f

```text
[22:26:23][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0017 0013 0016 0013 0016 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0014 0014 0014 0014 0014 0014 0014 
[22:26:23][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:26:23][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0014 
[22:26:23][I][remote.pronto:233]: 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0181 
```

#### Fahrenheit sweep, 2026-08-11

The captures above were all taken with the remote displaying °C, which left
19 of the 25 `b3` values as inference. This sweep steps the remote through its
whole Fahrenheit range with the display in °F, so `b7` bit 7 is clear
throughout, and confirms every remaining pairing. `°C = round((°F − 32) × 5/9)`
holds at all 25 values with no endpoint pinning, unlike the other direction.

68 °F, displayed in °F, pairs to 20 °C

```text
[13:58:57][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:58:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:58:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015
[13:58:57][I][remote.pronto:233]: 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0181
```

67 °F, displayed in °F, pairs to 19 °C

```text
[13:59:01][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:01][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:01][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017
[13:59:01][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0181
```

66 °F, displayed in °F, pairs to 19 °C

```text
[13:59:04][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0015 0013 0016 004B 0016 004C 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:04][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:04][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017
[13:59:04][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

65 °F, displayed in °F, pairs to 18 °C

```text
[13:59:06][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:06][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:06][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015
[13:59:06][I][remote.pronto:233]: 0013 0017 004B 0017 004B 0017 004B 0017 004B 0015 0013 0015 0181
```

69 °F, displayed in °F, pairs to 21 °C

```text
[13:59:31][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004C 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:31][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:31][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:31][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0181
```

70 °F, displayed in °F, pairs to 21 °C

```text
[13:59:33][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0017 004B 0015 0013 0016 004C 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:33][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:33][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:33][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0181
```

71 °F, displayed in °F, pairs to 22 °C

```text
[13:59:35][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004C 0015 0013 0016 004C 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:35][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:35][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017
[13:59:35][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0181
```

73 °F, displayed in °F, pairs to 23 °C

```text
[13:59:39][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004C 0015 0013 0016 004C 0015 0013 0016 004C 0016 004C 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:39][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:39][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017
[13:59:39][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0181
```

74 °F, displayed in °F, pairs to 23 °C

```text
[13:59:41][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004C 0016 004C 0016 004C 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:41][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:41][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017
[13:59:41][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0017 004B 0017 004B 0015 0181
```

76 °F, displayed in °F, pairs to 24 °C

```text
[13:59:45][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004C 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:45][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015
[13:59:45][I][remote.pronto:233]: 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0181
```

77 °F, displayed in °F, pairs to 25 °C

```text
[13:59:47][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004C 0016 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:47][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:47][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015
[13:59:47][I][remote.pronto:233]: 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0181
```

78 °F, displayed in °F, pairs to 26 °C

```text
[13:59:49][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0017 004B 0016 004C 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:49][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:49][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017
[13:59:49][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0015 0013 0017 004B 0017 004B 0015 0181
```

79 °F, displayed in °F, pairs to 26 °C

```text
[13:59:51][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 004B 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:51][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:51][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017
[13:59:51][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0181
```

80 °F, displayed in °F, pairs to 27 °C

```text
[13:59:53][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0017 004B 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:53][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:53][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017
[13:59:53][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181
```

81 °F, displayed in °F, pairs to 27 °C

```text
[13:59:55][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0017 004B 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:55][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:55][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017
[13:59:55][I][remote.pronto:233]: 004B 0017 004B 0017 004B 0017 004B 0017 004B 0015 0013 0015 0181
```

82 °F, displayed in °F, pairs to 28 °C

```text
[13:59:57][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 004C 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015
[13:59:57][I][remote.pronto:233]: 0013 0015 0013 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

83 °F, displayed in °F, pairs to 28 °C

```text
[13:59:59][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0017 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004A 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:59][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[13:59:59][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015
[13:59:59][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0181
```

84 °F, displayed in °F, pairs to 29 °C

```text
[14:00:01][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0016 004C 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:00:01][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:00:01][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017
[14:00:01][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0181
```

85 °F, displayed in °F, pairs to 29 °C

```text
[14:00:03][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004C 0016 004C 0016 004C 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:00:03][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:00:03][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017
[14:00:03][I][remote.pronto:233]: 004B 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0181
```

### Fan

Remote's fan button cycles from high -> medium -> low.
I pressed button on remote and received codes below.

Fan speed is the high nibble of `b6`.

```text
High     55 62 00 0D 00 00 31 80 75
Medium   55 62 00 0D 00 00 21 80 65
Low      55 62 00 0D 00 00 11 80 55
                           ^
                           3 = high, 2 = medium, 1 = low
```

`0` is representable but the button never produces it, so a possible "auto" fan
is untested. The low nibble stays `1` (cool) throughout, and `b7` stays `0x80` —
a fan press sets no event bit.

High -> Medium

```text
[22:17:22][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0016 0013 0015 0013 0015 0013 0016 004B 0015 0013 0015 0013 0014 0014 0014 0014 0015 0013 0014 
[22:17:22][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0015 0013 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:17:22][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0017 004B 0014 0014 0014 0014 0014 0014 0014 0014 0017 004B 0017 004B 0014 0014 0014 0014 0015 0013 0014 0014 0015 0013 0014 0014 0015 0013 0015 0013 0017 004B 0017 
[22:17:22][I][remote.pronto:233]: 004B 0014 0014 0014 0014 0017 004B 0015 0013 0017 004B 0014 0181
```

Medium -> Low

```text
[22:18:07][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004B 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:18:07][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:18:07][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 
[22:18:07][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0181
```

Low -> High

```text
[22:18:37][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0016 0013 0015 0013 0015 0013 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:18:37][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 
[22:18:37][I][remote.pronto:233]: 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0016 004C 0016 004C 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0014 0016 004C 0016 
[22:18:37][I][remote.pronto:233]: 004C 0016 004C 0014 0014 0016 004C 0014 0014 0016 004C 0014 0181 
```

### Mode

Remote started with mode=cool and 22c and high fan

Mode is the low nibble of `b6`, sharing the byte with the fan speed.

```text
Cool     55 62 00 0D 00 00 31 80 75
Fan      55 62 00 0D 00 00 33 80 77
Dry      55 62 00 0D 00 00 12 80 56
Auto     55 62 00 0D 00 00 30 80 74
                           ^^
                           |low nibble: 0 = auto, 1 = cool, 2 = dry, 3 = fan
                           high nibble = fan speed
```

**Entering dry mode drops the fan to low** — `b6` goes `0x33` → `0x12`, changing
both nibbles at once. Leaving dry for auto restores it to high (`0x30`). Whether
the unit would accept a non-low fan in dry mode, or whether the remote is merely
being helpful, is untested.

Cool -> Fan

```text
[22:28:11][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0016 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:28:11][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:28:11][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[22:28:11][I][remote.pronto:233]: 004B 0017 004B 0014 0014 0017 004B 0017 004B 0017 004B 0015 0181 
```

Fan -> Dry

The fan speed jumped to low

```text
[22:28:43][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0018 004A 0016 0013 0015 0013 0015 0013 0016 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:28:43][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:28:43][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 
[22:28:43][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0181 
```

Dry -> Auto

The fan speed jumped to high

```text
[22:29:36][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0016 004A 0017 004B 0016 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:29:36][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:29:36][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[22:29:36][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0181 
```

Auto -> Cool

```text
[22:30:37][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 0013 0017 004A 0017 004A 0016 0013 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:30:37][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 
[22:30:37][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 
[22:30:37][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0015 0013 0017 004B 0015 0181
```

#### One setpoint, not one per mode, 2026-08-11

A single mode press rewrites `b1`, `b3` and `b6`'s fan nibble together, which
made it look as though each mode stored its own temperature. It does not.
There is one setpoint and cool owns it; fan mode transmits the same value, and
dry and auto transmit a fixed 22 °C / 72 °F regardless. The remote only lets
the setpoint be changed in cool, and hides the number in the other three.

Below, cool had just been set to 18 °C. Fan follows it; dry and auto do not.
Fan *speed* is genuinely per-mode. Note `b7` = `0x80` on all four: a mode press
changes the transmitted temperature without setting bit 6.

mode press to fan, carrying 18 °C and low fan

```text
[14:18:57][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004B 0016 0013 0016 004C 0015 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:18:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:18:57][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:18:57][I][remote.pronto:233]: 0013 0015 0013 0017 004B 0017 004B 0017 004B 0017 004B 0015 0181
```

mode press to dry, carrying 22 °C and low fan

```text
[14:18:58][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004C 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:18:58][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:18:58][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015
[14:18:58][I][remote.pronto:233]: 0013 0017 004B 0015 0013 0017 004B 0017 004B 0015 0013 0015 0181
```

mode press to auto, carrying 22 °C and high fan

```text
[14:19:00][I][remote.pronto:233]: 0000 006D 004A 0000 00C5 0016 0013 0016 004A 0016 0013 0016 004A 0016 0013 0016 004B 0016 0013 0016 004C 0015 0013 0016 004C 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:19:00][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:19:00][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017
[14:19:00][I][remote.pronto:233]: 004B 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0181
```

mode press to cool, carrying 18 °C and high fan

```text
[14:19:01][I][remote.pronto:233]: 0000 006D 004A 0000 00C4 0017 0013 0016 004A 0017 0013 0016 004A 0017 0013 0016 004A 0016 0013 0016 004B 0016 0013 0015 0013 0016 004C 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:19:01][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0015 0013 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015
[14:19:01][I][remote.pronto:233]: 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0017 004B 0017 004B 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0015 0013 0017
[14:19:01][I][remote.pronto:233]: 004B 0015 0013 0017 004B 0017 004B 0015 0013 0017 004B 0015 0181
```

#### Which modes accept a fan speed, 2026-08-11

The captures above show each mode carrying its own stored speed, but they were
all produced by mode presses, so they do not say whether the fan button is live
in a given mode. Operating the remote settles it: **auto, cool and fan-only all
accept low, medium and high. Dry is the only restriction.** Combined with the
per-mode storage above, that is the whole rule, and there is no state in which
the remote can emit dry with anything but low.

## Appendix: the superseded 69-bit interpretation

The original 2025 analysis of these same captures described a 69-bit frame with a
5-bit `10101` preamble and fields straddling byte boundaries:

| sz | name       | description |
|----|:-----------|:------------|
| 5  | preamble   | `10101` |
| 4  | celsius    | offset from 16 (range 17-30c) MSB first |
| 1  | timer      | `0` = off/cancelled, `1` = active with given hours |
| 1  | 0          | `0` |
| 1  | power      | `0` = off, `1` = on |
| 4  | 0          | `0000` |
| 5  | hours      | timer duration in hours (range 0-24) MSB first |
| 3  | 0          | `000` |
| 5  | fahrenheit | offset from 59 (range 62-86f) MSB first |
| 16 | 0          | `0000000000000000` |
| 2  | 0          | `00` |
| 2  | fan        | `01` = low, `10` = medium, `11` = high |
| 2  | 0          | `00` |
| 2  | mode       | `00` = auto, `01` = cool, `10` = dry (also forces fan to low), `11` = fan |
| 1  | temp units | `0` = fahrenheit, `1` = celsius |
| 5  | 0          | `00000` |
| 1  | timer ui   | `0` = display standard ui, `1` = display timer ui |
| 1  | 0          | `0` |
| 8  | checksum   | ignore preamble, init with `01010101` 0x55, sum further all bytes MSB first, truncate to 8 bits |

That frame does not exist. It is kept here because the correction is worth
recording, and because the old reading was self-consistent enough to look right.

### What went wrong

Two bugs in `pronto_analyzer.py` compounded:

1. **`decode_to_binary()` paired timings across the mark/space boundary.** It
   started at a hardcoded index and classified each pair by the *sum* of its two
   durations. Summing works by luck — the long element dominates whichever slot
   it lands in — so bits came out mostly right while the alignment was wrong. The
   misalignment dropped the leading bit and silently merged a bit wherever two
   short elements were adjacent, losing three bits in total. The phantom `10101`
   preamble and the split-nibble fields are both products of that shift.

2. **The length check was inverted and then ignored.** The script printed
   `Sequence length validation: FAILED — expected 152 codes, got 151`, and that
   warning was treated as capture noise. It was the opposite: the captures are
   complete and the *expectation* was wrong. ESPHome writes the pair count as
   `(data.size() + 1) / 2` and dumps every buffer element, so an odd-length buffer
   rounds the declared pair count up. `expected = 4 + pairs * 2` cannot hold.

### Why it appeared to validate

The checksum. `checksum.py` searched a large space of algorithms and found "sum
with initial value `0x55`" matching every capture — which is true, and is exactly
what a correct sum over all nine bytes looks like when the frame has been shifted
so that the constant byte `b0` = `0x55` falls outside the message. A magic seed
that happens to equal a byte you have excluded is a strong hint that the framing
is off by a byte. Both readings compute the same arithmetic; only one explains it.

### How it was caught

Re-deriving the mark/space parity from first principles. The final duration of
every capture is the receive-idle timeout and must be a space; with an odd number
of durations that forces element 0 to be a space too, and the marks to the odd
indices. Under that parity the variable elements number exactly 72 on all 39
captures, the frame is byte-aligned, `b0` is `0x55` in every one, and
`sum(b0..b7) & 0xFF` matches the ninth byte in every one.

Nothing about the captures changed. Only their interpretation did.

### What was lost

A few timer observations in the original document existed only as decoded bit
strings, with no accompanying Pronto capture — a fan press during the timer UI,
and a 15 hour setting. Those bits came from the superseded decoder and cannot be
recovered, so they were dropped rather than reprinted wrongly.

The image `ir_signal_on_cool_high_notimer_19c.png` plotted the incorrect bit
decode and was deleted along with `pronto_analyzer.py`, `checksum.py` and
`decode.py`. All are in the git history.

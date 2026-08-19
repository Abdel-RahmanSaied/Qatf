---
name: Measurement
about: Report a number — a benchmark, a quality score, a provider that actually replied
labels: measurement
---

<!-- This project treats measurements as first-class. Much of what it "knows"
     is unverified, and a single honest number can retire an assumption. -->

## What you measured

## The numbers

```text

```

<!-- Negative results are as valuable as positive ones. Several settings are
     documented as tried-and-rejected specifically so nobody rediscovers them. -->

## How you measured it

<!-- Enough for someone else to reproduce: source material, its length and
     language, the exact command or config, the hardware. -->

## What it changes

<!-- Does this contradict a documented default, or fill one of the gaps listed
     under "Not verified" in the README? -->

---

### Two traps this project has already fallen into

- [ ] **Scored over the whole file, not a slice.** `initial_prompt` once looked
      like a 14-errors-to-0 win because the test slice began exactly where the
      prompt applied. On the full file the errors came straight back. If the
      thing you measured could decay over time, report the error count past 300s
      separately.
- [ ] **Checked the word count.** A config that "reduces errors" by dropping
      speech must show up as a drop in words.

If this lands, the number belongs in `docs/quality.md` or `CLAUDE.md` — and
nowhere else. A number copied into a third place is a number that will disagree
with itself.

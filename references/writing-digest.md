# Digesting the writing log (playbook)

Follow this only when the user asks for it ("digest my writing log", "turn my feedback into rules"). There is no schedule: once a month is a rough guide, and `rapport writing log` says how much has come in since the last digest.

The writing log (`[writing] log = true`) holds how the user corrected the agent's writing across all their reports. Your job: find the patterns, propose a few small, well-evidenced changes to their writing rules, and apply only the ones they approve. You are distilling their taste, not inventing a style.

## 1. Read the material

```sh
rapport writing digest            # entries since the last digest, with counts and recurring cuts
rapport writing digest --all      # everything, when there's too little new to judge
```

The header gives counts by action, phrases the user took out again and again (whole log; times and reports), the rules in effect and the proposals already decided. Then one block per entry:

| entry | what it tells you | weight |
|---|---|---|
| `edited, probably by the reader` | the user rewrote a block themselves between rounds: *before* is what they didn't want, *after* is their own wording | strongest; but it may be a content fix, or rarely an agent's edit |
| `REOPENED (fix rejected)` | the agent's fix (`rejected fix`) didn't satisfy them | the rejected fix is a negative example as clear as the original |
| a resolved item (`AI slop`, `Too verbose`, `Delete`, `Unclear`, `Comment`…) | *before* was flagged; *after (agent)* is the fix they accepted by not reopening it | *before*: a sure negative. *after*: accepted, not necessarily loved |
| `comment:` | the user's reason in their own words | the most valuable line in an entry |
| `(after: unchanged?/fuzzy/gone)` | rapport couldn't pin the fix down | weak; lean on the comment |

The heading path and neighbouring text say where it happened: a summary, a methods section, a caption. Many preferences only hold in one kind of place.

Ignore content corrections (a wrong number, a renamed thing, a factual fix): they say nothing about style. When unsure, leave the entry out and say so.

## 2. Find patterns

- **Evidence first.** A pattern needs at least **3 entries from at least 2 reports**. The exception is a comment that already states a general rule ("never start with 'In this report'"): one is enough.
- **Check against counterevidence.** Look for entries where the user kept, or wrote themselves, what the rule would forbid. One clear counterexample means the rule needs a scope ("in summaries") or isn't a rule.
- **Prefer the user's own words.** If they wrote the *after*, the rule should describe what they did, not what the agent did.
- **Name the place.** "Captions are bare" is better than "be concise". Scope a rule to where its evidence came from (summaries, captions, bullet lists, Confluence pages).

## 3. Decide what kind of change it is

1. **A banned phrase** (`writing.avoid`): wording deleted or flagged repeatedly. `lint` then catches it mechanically. Use the "taken out again and again" list, but only phrases that are a choice of wording (not "the cache", which is content).
2. **A rule** (`writing.rules`): one sentence, specific enough to check against a paragraph. When a pattern resists abstraction, put a short contrast in the rule: *"Lead with the result: 'The cache cut p99 to 310 ms', not 'It's worth noting that the cache…'."*
3. **A change to an existing rule:** merge two that overlap, narrow one the evidence contradicts somewhere, or reword one the user keeps flagging despite it (repeat flags after a rule exists mean it isn't working). Retire a rule only on evidence, never because it had no recent flags: a rule that works produces none.

Keep the whole set small: about **20 rules** in total across config and learned rules. Past that, merge before you add. Propose at most **5–7 changes** per digest; the user decides each one.

Never regenerate the rule list as a whole. Rewriting everything loses what was carefully worded before. Change one rule at a time.

Rules are generic: no customer names, project names, numbers or quotes from confidential reports in them.

## 4. Propose, and ask

Show all proposals at once, numbered, each like this:

```
1. Add rule (all reports): "Bold lead-ins in bullets stay under a line; details follow."
   Evidence: e:3f9a1c, e:77b2d0, e:a01c9e (3 entries, 2 reports): you cut long bold lead-ins each time.
   Counterevidence: none found.
2. Add avoid phrase (all reports): "it's worth noting"
   Evidence: cut ×7 in 4 reports.
3. Narrow rule "Figure captions are just 'Figure N'" to Confluence pages
   Evidence: e:5c… you added a description to a caption in a personal note.
```

Then ask which to apply. Say where each would go: **all their reports** (`--scope user`) or **this project** (`--scope project`, beside the nearest `rapport.toml`). Nothing is applied without their yes, item by item.

## 5. Apply what they approved

- New rule: `rapport writing add "<rule>" --approved [--scope user|project]` (for `project`, add `-C <a report folder in that project>`)
- New avoid phrase: `rapport writing add "<phrase>" --avoid --approved [--scope …]`
- Turned down: `rapport writing reject "<rule>"`, so it isn't proposed again.
- Changing or retiring an existing rule: find where it lives (`rapport config show --origin --key writing.rules`) and edit that file, only for the changes they approved. Rules in `learned.toml` are an inbox: say that the user may want to move keepers into their config.

Finish with:

```sh
rapport writing digest --done     # the next digest starts after this one
```

Tell the user in two or three lines what changed, and how many entries the digest covered. If the counts in the header can be compared with an earlier digest (fewer flags of a kind the rules address), mention it; don't claim an effect from one month.

## Don'ts

- Don't save anything without an explicit yes; don't batch-apply.
- Don't propose a rule from one entry (unless its comment states the rule), from content fixes, or one the user already rejected.
- Don't copy entries into files, reports, commits or messages beyond this conversation: they quote confidential reports.
- To remove entries (a report that shouldn't be in the log): `rapport writing forget --report <dir>`, `--before YYYY-MM-DD` or `--entry <id>`, when the user asks.

# The invocation corpus

Every phrasing the skill is held to, and what each one must resolve to. `cases.json` is the
whole of it; the suite reads it and `tests/test_the_skill.py` is where the assertions live.

## What each family pins

| Family       | Pins                                                                                          |
| ------------ | ----------------------------------------------------------------------------------------------- |
| `canonical`  | each capability's ordinary phrasings, at least four apiece                                    |
| `ask`        | one case per reason in the ask list, so no reason is unreachable and none is only theoretical |
| `adversarial`| the three shapes doc 15 names by hand, and the traps around consent                           |
| `occasion`   | which reading a trigger takes, whether it is disclosed, and what each direction costs         |
| `repository` | the target resolved from the request, and every refusal                                       |
| `consent`    | what the ledger does with a reply, and which replies are not the ledger's to judge            |

A consent case carrying `"judged_by": "session"` declares a reply that **must not reach a
start at all** — an acknowledgement, a refusal — and states what the ledger does if one
arrives anyway, which is spend the offer. That is not a gap to be closed: the reply reaches
`consent.spend` as the session's own `--reply` argument, so any word list compared against it
would sit downstream of the judgement it claimed to make. `SKILL.md` carries the rule, the
session keeps it, and the paid suite is what measures whether it did.

## What a case carries

```json
{
  "id": "run-canonical-repository",
  "family": "canonical",
  "utterance": "run offline-export against /Users/me/src/product",
  "reading": { "verbs": ["executing"], "subjects": ["workflow"], "qualifiers": ["repository"] },
  "expect": { "capability": "run", "ask": null, "readings": [] },
  "why": "one verb, one object, a repository given: the canonical Run"
}
```

`utterance` is the human phrasing. `reading` is the structure a reader of `SKILL.md` would
extract from it — **the corpus declares it rather than deriving it**, and that is the honest
boundary of what this suite proves.

## What this corpus does and does not prove

It proves the **rules**: that they are disjoint and total, that every ask is reachable, that
the readings offered in a question come from the same table the answers do, and — the hard
gate — that nothing in it can start a run without a qualifying acceptance.

It does **not** prove that a model reads an English sentence into the right `reading`. No
offline suite can; that is a measurement over real sessions and it needs a model in the loop.
`utterance` is here so that harness has an input and so a human reviewer can judge whether
the declared reading is the fair one. The number is owed by doc 17, in the same way the
divergence rate is owed by the worked example.

## Adding a case

Give it an `id` nothing else has, a `why` that says what it is for rather than what it does,
and values drawn only from the frozen vocabularies — the suite refuses free text anywhere but
`utterance` and `why`.

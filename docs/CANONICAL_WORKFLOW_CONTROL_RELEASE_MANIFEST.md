# Canonical Workflow Control Candidate Manifest

Status: GO for local source/package acceptance

Generated: 2026-07-18

This manifest binds the reviewed implementation commits to their dependency
locks and locally built artifacts. It is not a publication, deployment, tag, or
claim about oldmac. The AWK implementation commit below is the source candidate;
the later documentation-only commit containing this manifest is intentionally
not self-referential.

## Candidate source identities

| Repository | Distribution | Version | Candidate commit | State when built/tested |
|---|---|---:|---|---|
| `agent-workflow-kernel` | `agent-workflow-kernel` | 0.4.0 | `7553798612dee442e56daa851acf65252f41ff50` | clean committed tree |
| `lathi` | `lathi` | 0.1.0 | `8d6a6a3e40189502c2f30f14189323a87f13f397` | clean committed tree |
| `lathi-bus` | `lathi-bus` | 0.2.0 | `74a2fd6b44b1f626459a78b3d0b716f320c97ea5` | clean committed tree |
| `decision-lanes-engine` | `decision-lanes` | 0.1.0 | `0f1f3439e8674bfe7d84f17f5ecad6aac693c612` | clean committed tree |
| `decision-lanes-site` | npm application | 1.0.0 | `c0337a6781d014dfad9807ca0e7a735062061199` | clean committed tree |

The isolated wheel and installed-case proofs used CPython 3.13.7. Decision's
verification harness was subsequently hardened in commit `331f5f1` to force
all install and probe subprocesses to execute from the temporary environment;
that harness-only commit does not change the packaged `decision_lanes` code.

Dependency wheels needed for the clean Lathi and Decision proofs were built
from these exact sibling commits:

| Repository | Distribution | Commit |
|---|---|---|
| `agent-broker` | `agent-broker` 0.4.1 | `acfc1b8c3a83a7a247f0010eb5891aa35321e251` |
| `memory_core` | `memory-core` 0.2.1 | `fc782d36ee0bd3eec450325ea856304ac4f4b731` |
| `browser-agent` | `browser-agent-client` 0.1.0 | `20420a940af83e2eba457c91177efab3826c892d` |
| `lathi-packs` | `lathi-packs` 0.1.0 | `91e0c1d7aa9d6eb5785697b4ba9239b6dab68561` |

## Lock identities

| Consumer | Lock file SHA-256 |
|---|---|
| Lathi `uv.lock` | `25bcd6b5bd6057a48205099e6e6ae2532745d770f5ac21083a1677119e1e6230` |
| Decision Lanes `uv.lock` | `ddcf280a51b09d97c21b36ae17dad18695fc3e131bb4b744265871fddd6279b8` |
| Decision Site `package-lock.json` | `5e5fc8023e2b7d7829820bc0d2d20e86e02bc13585057baef3d4469776ace098` |

Both Python locks resolve `agent-workflow-kernel` 0.4.0. Lathi resolves
`lathi-bus` 0.2.0. Decision resolves `agent-broker` 0.4.1.

## Candidate wheel hashes

The local-only wheelhouse is
`.super-goal/canonical-workflow-control/artifacts/wheelhouse/`. It is ignored by
Git and contains no credentials.

| Wheel | SHA-256 |
|---|---|
| `agent_workflow_kernel-0.4.0-py3-none-any.whl` | `9c2ed582a66a9286602e030e10595544c538329a57b3d801b75009b9a8ef7b14` |
| `lathi_bus-0.2.0-py3-none-any.whl` | `2f67a829816c38e6ac7938421917d88a1807557f307c6a85ef68c5a6e1beb3ee` |
| `lathi-0.1.0-py3-none-any.whl` | `1f3a76c5867854e6e2dda6cfa9d7725a3d5ff1cfbe778182455a6453a2be3b05` |
| `decision_lanes-0.1.0-py3-none-any.whl` | `b004ed3df98e631fbd8d6f80afdf1a33f12741f136b52a74c89a4b47092398a5` |
| `agent_broker-0.4.1-py3-none-any.whl` | `a66884a4b9218d6a6a0c6f41a46602b6b0a44ec768544a700eaec678f2bc7777` |
| `memory_core-0.2.1-py3-none-any.whl` | `719d83326a8a2c66416320167e35ec2b703fdee0f30905689bf0e33e18aeb331` |
| `browser_agent_client-0.1.0-py3-none-any.whl` | `edeec5480a750a775cb0e21ebed18a6c9c54dd7cf5d928984e4ab6ad12005f03` |
| `lathi_packs-0.1.0-py3-none-any.whl` | `e070fbb117df5143f76ba55199a937dc58fe0aace4aa950315aea95e158acc57` |
| `pyyaml-6.0.3` platform wheel | `2283a07e2c21a2aa78d9c4442724ec1eb15f5e42a723b99cb3d822d48f5f7ad1` |
| `openpyxl-3.1.5-py2.py3-none-any.whl` | `5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2` |
| `et_xmlfile-2.0.0-py3-none-any.whl` | `7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa` |

## Wheel allowlists and installed readback

- AWK wheel top-level packages are `agent_workflow_kernel` plus the official
  A2A, artifact-validation, Codex CLI, Codex SDK, Ivy, OpenClaw, and X Digest
  adapter packages, and one distribution metadata directory.
- Decision wheel contains only `decision_lanes` and one distribution metadata
  directory. It contains neither `engine` nor `agent_workflow_kernel`.
- Lathi wheel contains only `lathi`; Bus wheel contains only `lathi_bus`, each
  plus its distribution metadata directory.
- An empty Python 3.13 environment outside all checkouts installed Decision with
  `--no-index` and empty `PYTHONPATH`. `pip check` passed, and origin readback
  showed Decision 0.1.0, AWK 0.4.0, and Agent Broker 0.4.1 in that environment's
  `site-packages`. A real installed-wheel `CaseRuntime` case recorded AWK
  candidate identity
  `sha256:dist-record:5d093538da73efaf40daae0fd0ae9fc13058dd62b4057674e5b9556eb4da3f6f`.
- A second empty Python 3.13 environment outside all checkouts installed Lathi
  and all declared dependencies from the wheelhouse. `pip check` passed, and
  origin readback showed Lathi 0.1.0, AWK 0.4.0, and Bus 0.2.0 in that
  environment's `site-packages`.

## Test and maintenance receipts

| Surface | Candidate result |
|---|---|
| AWK | 285 pytest tests and 158 unittest subtests passed; import lint, wheel verification, and PRD audit passed |
| Lathi | 386 passed, including the integrated AWK to Lathi to Bus to Lathi to AWK round trip and complete-envelope rebinding regression |
| Lathi Bus | 174 passed, including correction-after-ACK reconciliation and unknown-ACK rejection |
| Decision Lanes | 96 passed; clean offline wheel install, installed-case provenance, and package allowlist passed |
| Decision Lanes Site | Clean Git archive ran `npm ci --no-audit --no-fund` and `npm run build`; one static page built |

From recorded baseline `1c5d2e3` to unvendor commit `0ce1b41`, Decision changes
101 files with 583 insertions and 18,562 deletions. The removed vendored AWK
alone was 20 files and 15,464 lines. Tracked files fell from 201 to 147. The
removed source remains recoverable from Git history. Provenance hardening in
later commit `0f1f343` does not reintroduce the vendor or generic `engine`
package.

Every candidate committed tree returns no paths for:

```text
git ls-tree -r --name-only HEAD -- .super-goal .supervisor .supervisor-lane
```

Repository scans find no consumer or official-adapter import from an AWK
submodule and no live Decision dependency on the removed vendored path. The
single old path in `tests/fixtures/legacy_awk_v0_contract.json` is explicit
migration provenance, not executable code.

## Boundary of this proof

These are local source, package, fixture, and clean-build receipts. No push,
tag, package publication, public send, authentication change, live operator
write, oldmac deployment, daemon restart, money action, or trading action was
performed. Oldmac runtime behavior is unverified by this candidate gate.

## Independent implementation verdict

Sol's final read-only evaluator returned **GO for local source/package
acceptance** with CWC-01 through CWC-06 Proven and no unresolved critical/high
software defect. During review it reproduced three false-greens before the GO:

1. a card could rebind omitted authority fields until Lathi required the whole
   collected envelope to match AWK's durable completed publish envelope;
2. wheel-installed Decision cases had no AWK identity until they recorded the
   installed distribution `RECORD` digest; and
3. direct ledger callers could omit `owner_id` until authoritative mutations
   defaulted to, and validated, their mutation actor.

The evaluator reran those exact regressions plus the full suite totals recorded
above, verified the candidate wheel hashes and clean installs, and reviewed the
final manifest for SHA, hash, version, count, and dirty-state consistency.

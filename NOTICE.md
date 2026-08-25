# NOTICE — upstream attribution and licence status

`eval-scicode` packages an evaluation from **inspect_evals**.

- **Upstream:** https://github.com/UKGovernmentBEIS/inspect_evals (`src/inspect_evals/scicode`)
- **inspect_evals licence:** MIT (Copyright (c) UK AI Security Institute)
- **OpenEvalz wrapper code in this repository:** Business Source License 1.1, see `LICENSE`

## Two licences, two scopes — do not conflate them

BSL 1.1 applies to **OpenEvalz-authored packaging only**: `openevalz.json`, `bundle.template.json`,
`redaction.yaml`, `tr/`, `scaffolds/`, `k8s/`, CI and documentation written here.

BSL does **not** apply to anything obtained from upstream. Files copied from or derived from
inspect_evals remain **MIT** under the UK AI Security Institute's copyright, and datasets and
container images remain under whatever terms their own publishers set. Applying BSL to upstream
work would be a licence violation, not a business decision.

## The eval, its dataset and its images are NOT relicensed here

Harness licence and dataset terms are different things, and the distinction has bitten
this project before. Known examples: the `princeton-nlp/SWE-bench_Verified` dataset card
states **no licence field at all**, and SWE-bench spans 12 repositories including
GPL-2.0 `pylint` — so published traces can embed copyleft source. GPQA ships as a
password-protected archive *deliberately*, to resist contamination.

### External assets declared upstream

- `direct_url`: `https://raw.githubusercontent.com/scicode-bench/SciCode/{SHA}/eval/data/`
- `direct_url`: `https://drive.google.com/uc?id=17G_k65N_6yFFZ2O-jQH00Lh6iaw3z-AW`

### Clearance checklist — must be complete before this repo publishes anything

- [ ] Dataset licence identified and recorded
- [ ] Redistribution rights confirmed for any mirrored image
- [ ] Trace-publication rights confirmed for the model providers in scope
- [ ] `redaction.yaml` reviewed against this eval's specific answer surface
- [ ] Dual-use review (skip only if clearly not applicable)

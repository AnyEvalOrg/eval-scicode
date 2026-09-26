SciCode dataset and original comparison/HDF5 code: Copyright 2024 The SciCode
Authors. Apache License 2.0; the unmodified license is LICENSE. Dataset revision
69a8cfc829fe8788a426ce8b5de6292366dce7ef from scicode-bench/SciCode.
Paper: SciCode: A Research Coding Benchmark Curated by Scientists,
Minyang Tian et al., arXiv:2407.13168. UPSTREAM-README.md retains the supplied
benchmark clone's author list, citation and historical leaderboard.

Inspect protocol source: inspect_evals/scicode, UK AI Security Institute,
Apache-2.0. prompt_templates.py is copied without changes. test_util.py and
process_data.py retain their original copyright and adaptation notices
(xantheocracy, 2024/2025). Frozen comparison and prompt fixtures retain those
source bytes. This adapter changes sample disclosure, execution boundaries,
serialization, scoring/publication and packaging as described in README.md.

The AnyEval adapter is distributed under Apache-2.0. Its trusted supervisor,
publication suppression, sandbox-state attribution, chart and regression tests
are adapted from the supplied eval-cobolcodebench template (Apache-2.0), whose
supervisor/publication/chart in turn derive from eval-cobol-javatrans. COBOL
datasets and unrelated obsolete OpenEvalz stub content are not redistributed.

SciCode-Verified (task scicode/scicode_verified): corrected test problems and grading
targets by Sihan Hu, Lyuhan Huang, Youjin Deng and Kun Chen, "SciCode-Verified: How
Benchmark Defects Underestimated the Scientific-Coding Ability of Language Models",
arXiv:2608.04975. Derived from SciCode and redistributed under Apache-2.0 (the same
license text as LICENSE, shipped as the release's LICENSE on Hugging Face and as
eval_clean/vendor/LICENSE in the repository). scicode/data/problems_verified_test.jsonl.gz
is the byte-preserved scicode_verified/problems_test.jsonl from
github.com/flyingwagner/scicode-verified at ddab4a92f8d80a7113ab946628e994b52354d838
(identical to data/problems_test.jsonl of Hugging Face dataset shhu2001/SciCode-Verified
at eea11a866be6860725258702b39ef8651ed26abd). The eval-scicode-verified-sandbox image
bakes that release's test_data_cleaned.h5 unchanged. Neither file is modified here.

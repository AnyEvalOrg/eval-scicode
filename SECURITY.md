# Security Policy

## Reporting

Report vulnerabilities to **security@openevalz.com**. Do not open a public issue.

## Threat model for this repository

This repo describes how an evaluation is executed on OpenEvalz infrastructure, where
**untrusted model-generated code runs inside a sandbox**. Two boundaries matter:

1. **The sandbox** — gVisor (runsc), pod-per-sample, deny-by-default egress enforced at
   the kernel by a per-sandbox Cilium policy. In July 2026 an eval agent escaped a
   sandbox via a *public* benchmark harness and reached third-party infrastructure. A
   public harness is exactly what this is, so sandbox findings are treated as critical.
2. **The runner** — holds the TrustedRouter credential and executes the eval's own
   Python (dataset loaders, solvers, scorers). Third-party eval code is semi-trusted;
   `register/` evals are unaudited by definition.

## Out of scope

Model outputs being wrong, offensive, or low-scoring. That is data, not a vulnerability.

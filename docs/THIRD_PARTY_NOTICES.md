# Third-party data and document notices

The application code is separate from the third-party materials listed below.
Each retained source keeps its upstream license and attribution. No project
benchmark result should be interpreted as an official upstream leaderboard
submission.

## Edge Delta RootCauseBench subset

- Source: <https://github.com/edgedelta/root-cause-bench>
- License: Apache License 2.0
- Local location: `data/public_datasets/rootcausebench/`
- Changes: the repository keeps 32 public scenarios and excludes four large
  scenarios to reduce download size. Ground truth is loaded only after model
  prediction for scoring.

The upstream license is retained at
`data/public_datasets/rootcausebench/LICENSE`, and acquisition details are in
`data/public_datasets/rootcausebench/SOURCE.md`.

## Kubernetes Chinese documentation

- Source: <https://github.com/kubernetes/website>
- License: CC BY 4.0
- Local location: `data/public_sources/kubernetes/`
- Changes: original retained pages are unmodified. The project-specific Chinese
  runbook in `sample_knowledge/kubernetes-ops/` is an excerpted and reorganized
  derivative with source attribution.

The license text is retained at
`data/public_sources/kubernetes/LICENSE-CC-BY-4.0.txt`.

## NIST publications

- NIST SP 800-61 Rev. 3: <https://doi.org/10.6028/NIST.SP.800-61r3>
- NIST SP 800-53 Rev. 5: <https://doi.org/10.6028/NIST.SP.800-53r5>
- Local location: `data/public_sources/nist/`

The project-specific Chinese security approval runbook is a summary and policy
implementation for this demonstration; it is not an official NIST translation.
